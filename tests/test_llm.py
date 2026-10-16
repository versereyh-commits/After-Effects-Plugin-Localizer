from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io,json,threading,time,unittest
import ssl
from unittest.mock import patch
from urllib.error import URLError
import llm_client
from llm_client import TranslationError,translate_names

class Handler(BaseHTTPRequestHandler):
    reply={'translations':[{'id':'S_Glow','name':'辉光'}]}
    received=None
    responses=[]
    calls=0
    def log_message(self,*args):pass
    def do_POST(self):
        type(self).received=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).path=self.path;type(self).headers_seen={k.lower():v for k,v in self.headers.items()}
        type(self).calls+=1
        response=type(self).responses.pop(0) if type(self).responses else {}
        status=response.get('status',200)
        body=response.get('body')
        if body is None:
            body=json.dumps({'choices':[{'finish_reason':response.get('finish_reason','stop'),'message':{'content':json.dumps(response.get('reply',type(self).reply),ensure_ascii=False)}}]}).encode()
        self.send_response(status)
        self.send_header('Content-Length',str(len(body)+response.get('missing_bytes',0)))
        self.end_headers();self.wfile.write(body)
        self.close_connection=True

class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        Handler.reply={'translations':[{'id':'S_Glow','name':'辉光'}]}
        Handler.responses=[];Handler.calls=0;Handler.received=None
        self.waits=[]
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
    # The fake server answers in arrival order, so these tests send one batch at a time; ParallelTests covers several.
    def call(self,items=None,**kwargs):return translate_names(items if items is not None else [{'id':'S_Glow','sourceName':'S_Glow','category':'Sapphire Lighting'}],'test-only',self.base,wait=self.waits.append,**{'workers':1,**kwargs})
    def batches(self):
        items=[{'id':f'S_Test{i}','sourceName':f'Test {i}','category':'Sapphire'} for i in range(11)]
        first={item['id']:'辉光' for item in items[:10]}
        Handler.responses=[{'reply':{'translations':[{'id':identity,'name':name} for identity,name in first.items()]}},{'reply':{'translations':[{'id':items[10]['id'],'name':'光晕'}]}}]
        return items,first
    def test_valid_reply_and_json_contract(self):
        self.assertEqual(self.call(),{'S_Glow':'辉光'})
        self.assertEqual(Handler.received['response_format'],{'type':'json_object'})
        self.assertEqual(Handler.received['thinking'],{'type':'disabled'})
    def test_unknown_identity_is_rejected(self):
        Handler.reply={'translations':[{'id':'another','name':'辉光'}]}
        with self.assertRaises(RuntimeError):self.call()
    def test_duplicate_identity_is_rejected(self):
        Handler.reply={'translations':[{'id':'S_Glow','name':'辉光'},{'id':'S_Glow','name':'光晕'}]}
        with self.assertRaises(RuntimeError):self.call()
    def test_too_long_translation_is_rejected(self):
        Handler.reply={'translations':[{'id':'S_Glow','name':'辉'*16}]}
        with self.assertRaises(RuntimeError):self.call()
    def test_missing_identity_is_rejected(self):
        Handler.reply={'translations':[]}
        with self.assertRaises(RuntimeError):self.call()

    def test_second_batch_failure_preserves_first(self):
        items,first=self.batches();Handler.responses[1]={'status':400,'body':b'test-only response secret'}
        with self.assertRaises(TranslationError) as caught:self.call(items)
        self.assertIsInstance(caught.exception,RuntimeError)
        self.assertEqual(caught.exception.partial,first)
        self.assertIn('已完成 10/11 项',str(caught.exception))
        self.assertIn('HTTP 400',str(caught.exception))
        self.assertNotIn('test-only',str(caught.exception))
        self.assertNotIn('response secret',str(caught.exception))
        self.assertEqual(Handler.calls,2);self.assertEqual(self.waits,[])

    def test_429_retry_succeeds(self):
        Handler.responses=[{'status':429}]
        self.assertEqual(self.call(),{'S_Glow':'辉光'})
        self.assertEqual(Handler.calls,2);self.assertEqual(self.waits,[1])

    def test_400_401_403_do_not_retry(self):
        for status in (400,401,403):
            with self.subTest(status=status):
                Handler.calls=0;Handler.responses=[{'status':status,'body':b'test-only response secret'}]
                with self.assertRaises(TranslationError) as caught:self.call()
                self.assertEqual(caught.exception.partial,{})
                self.assertIn(f'HTTP {status}',str(caught.exception))
                self.assertNotIn('test-only',str(caught.exception))
                self.assertNotIn('response secret',str(caught.exception))
                self.assertEqual(Handler.calls,1);self.assertEqual(self.waits,[])

    def test_retries_exhausted_preserve_first_batch(self):
        items,first=self.batches();Handler.responses[1:]=[{'status':503,'body':b'test-only response secret'}]*3
        with self.assertRaises(TranslationError) as caught:self.call(items)
        self.assertEqual(caught.exception.partial,first)
        self.assertIn('已完成 10/11 项',str(caught.exception))
        self.assertNotIn('test-only',str(caught.exception))
        self.assertNotIn('response secret',str(caught.exception))
        self.assertEqual(Handler.calls,4);self.assertEqual(self.waits,[1,2])

    def test_retry_limit_resets_for_each_batch(self):
        items,first=self.batches();one,two=Handler.responses;progress=[]
        Handler.responses=[{'status':500},{'status':502},one,{'status':429},{'status':599},two]
        self.assertEqual(self.call(items,progress=lambda done,total:progress.append((done,total))),dict(first,S_Test10='光晕'))
        self.assertEqual(Handler.calls,6);self.assertEqual(self.waits,[1,2,1,2])
        self.assertEqual(progress,[(10,11),(11,11)])

    def test_cancel_before_first_batch(self):
        with self.assertRaises(TranslationError) as caught:self.call(cancelled=lambda:True)
        self.assertEqual(caught.exception.partial,{})
        self.assertIn('取消',str(caught.exception));self.assertIn('已完成 0/1 项',str(caught.exception))
        self.assertEqual(Handler.calls,0);self.assertEqual(self.waits,[])

    def test_cancel_between_batches_preserves_first(self):
        items,first=self.batches();checks=[];progress=[]
        def cancelled():
            checks.append(Handler.calls);return Handler.calls==1
        with self.assertRaises(TranslationError) as caught:self.call(items,cancelled=cancelled,progress=lambda done,total:progress.append((done,total)))
        self.assertEqual(caught.exception.partial,first)
        self.assertIn('已完成 10/11 项',str(caught.exception))
        self.assertEqual(checks,[0,1]);self.assertEqual(progress,[(10,11)])
        self.assertEqual(Handler.calls,1)

    def test_progress_reports_validated_batches(self):
        items,first=self.batches();progress=[]
        self.assertEqual(self.call(items,progress=lambda done,total:progress.append((done,total))),dict(first,S_Test10='光晕'))
        self.assertEqual(progress,[(10,11),(11,11)])

    def test_validation_failure_does_not_commit_batch_or_retry(self):
        items,first=self.batches();progress=[]
        items.append({'id':'S_Test11','sourceName':'Test 11','category':'Sapphire'})
        Handler.responses[1]['reply']['translations'].append({'id':'S_Test11','name':'辉'*16})
        with self.assertRaises(TranslationError) as caught:self.call(items,progress=lambda done,total:progress.append((done,total)))
        self.assertEqual(caught.exception.partial,first)
        self.assertEqual(progress,[(10,12)])
        self.assertEqual(Handler.calls,2);self.assertEqual(self.waits,[])

    def test_truncated_and_malformed_responses_do_not_retry(self):
        for response in ({'finish_reason':'length'},{'body':b'test-only malformed secret'},{'body':b'{','missing_bytes':10},{'body':b'{"choices":["test-only secret"]}'}):
            with self.subTest(response=response):
                Handler.calls=0;Handler.responses=[response]
                with self.assertRaises(TranslationError) as caught:self.call()
                self.assertEqual(caught.exception.partial,{})
                self.assertNotIn('test-only',str(caught.exception))
                self.assertNotIn('secret',str(caught.exception))
                self.assertEqual(Handler.calls,1);self.assertEqual(self.waits,[])

    def test_connection_errors_retry_then_succeed(self):
        real_open=llm_client.urlopen
        for error in (URLError(TimeoutError('test-only secret')),URLError(ConnectionRefusedError('test-only secret')),TimeoutError('test-only secret'),ConnectionResetError('test-only secret')):
            with self.subTest(error=type(error).__name__):
                Handler.calls=0;self.waits.clear()
                def open_request(*args,**kwargs):
                    if mocked.call_count<=2:raise error
                    return real_open(*args,**kwargs)
                with patch('llm_client.urlopen',side_effect=open_request) as mocked:
                    self.assertEqual(self.call(),{'S_Glow':'辉光'})
                self.assertEqual(mocked.call_count,3)
                self.assertEqual(Handler.calls,1);self.assertEqual(self.waits,[1,2])

    def test_connection_retry_exhaustion_is_sanitized(self):
        with patch('llm_client.urlopen',side_effect=URLError(TimeoutError('test-only secret'))) as mocked:
            with self.assertRaises(TranslationError) as caught:self.call()
        self.assertEqual(mocked.call_count,3);self.assertEqual(self.waits,[1,2])
        self.assertEqual(caught.exception.partial,{})
        self.assertIn('已完成 0/1 项',str(caught.exception))
        self.assertNotIn('test-only',str(caught.exception));self.assertNotIn('secret',str(caught.exception))

    def test_non_connection_url_errors_do_not_retry(self):
        for reason in ('test-only secret',ssl.SSLCertVerificationError('test-only secret')):
            with self.subTest(reason=type(reason).__name__):
                with patch('llm_client.urlopen',side_effect=URLError(reason)) as mocked:
                    with self.assertRaises(TranslationError) as caught:self.call()
                self.assertEqual(mocked.call_count,1);self.assertEqual(self.waits,[])
                self.assertNotIn('test-only',str(caught.exception));self.assertNotIn('secret',str(caught.exception))

    def test_response_read_errors_preserve_partial_without_retry(self):
        real_open=llm_client.urlopen
        for error in (TimeoutError('test-only secret'),ConnectionResetError('test-only secret')):
            with self.subTest(error=type(error).__name__):
                items,first=self.batches();Handler.calls=0
                def open_request(*args,**kwargs):
                    response=real_open(*args,**kwargs)
                    def failed_read():raise error
                    if mocked.call_count==2:response.read=failed_read
                    return response
                with patch('llm_client.urlopen',side_effect=open_request) as mocked:
                    with self.assertRaises(TranslationError) as caught:self.call(items)
                self.assertEqual(caught.exception.partial,first)
                self.assertIn('已完成 10/11 项',str(caught.exception))
                self.assertNotIn('test-only',str(caught.exception));self.assertNotIn('secret',str(caught.exception))
                self.assertEqual(mocked.call_count,2)
                self.assertEqual(Handler.calls,2);self.assertEqual(self.waits,[])

class ServiceTests(unittest.TestCase):
    """Other services: Claude's Messages API, OpenAI-compatible endpoints without a JSON mode, local models."""
    def setUp(self):
        Handler.reply={'translations':[{'id':'S_Glow','name':'辉光'}]}
        Handler.responses=[];Handler.calls=0;Handler.received=None;Handler.path=None;Handler.headers_seen=None
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
    item=[{'id':'S_Glow','sourceName':'S_Glow','category':'Sapphire Lighting'}]
    def call(self,service,key='test-only',model='m'):return translate_names(self.item,key,self.base,model,service=service,wait=lambda s:None)
    def claude(self,text,stop='end_turn'):
        return {'body':json.dumps({'content':[{'type':'text','text':text}],'stop_reason':stop},ensure_ascii=False).encode()}
    def test_claude_uses_messages_api_and_its_headers(self):
        Handler.responses=[self.claude(json.dumps(Handler.reply,ensure_ascii=False))]
        self.assertEqual(self.call('anthropic'),{'S_Glow':'辉光'})
        self.assertEqual(Handler.path,'/messages')
        self.assertEqual(Handler.headers_seen.get('x-api-key'),'test-only');self.assertEqual(Handler.headers_seen.get('anthropic-version'),'2023-06-01')
        self.assertNotIn('authorization',Handler.headers_seen)
        self.assertIn('After Effects',Handler.received['system']);self.assertEqual(Handler.received['messages'][0]['role'],'user')
        self.assertNotIn('response_format',Handler.received)
    def test_claude_cut_off_at_token_limit_is_rejected(self):
        Handler.responses=[self.claude('{"translations":[','max_tokens')]
        with self.assertRaises(TranslationError) as caught:self.call('anthropic')
        self.assertIn('Anthropic Claude 译文被截断',str(caught.exception))
    def test_reply_in_code_fence_is_accepted_without_json_mode(self):
        Handler.reply='```json\n'+json.dumps(Handler.reply,ensure_ascii=False)+'\n```'
        Handler.responses=[{'body':json.dumps({'choices':[{'finish_reason':'stop','message':{'content':Handler.reply}}]},ensure_ascii=False).encode()}]
        self.assertEqual(self.call('siliconflow'),{'S_Glow':'辉光'})
        self.assertEqual(Handler.path,'/chat/completions')
        self.assertNotIn('response_format',Handler.received);self.assertNotIn('thinking',Handler.received)
    def test_openai_gets_json_mode_and_its_token_field(self):
        self.assertEqual(self.call('openai'),{'S_Glow':'辉光'})
        self.assertEqual(Handler.received['response_format'],{'type':'json_object'})
        self.assertIn('max_completion_tokens',Handler.received);self.assertNotIn('max_tokens',Handler.received)
        self.assertEqual(Handler.headers_seen.get('authorization'),'Bearer test-only')
    def test_local_service_needs_no_key(self):
        self.assertEqual(self.call('ollama',key=''),{'S_Glow':'辉光'})
        self.assertNotIn('authorization',Handler.headers_seen)
    def test_missing_key_model_or_address_is_reported_before_any_request(self):
        for service,key,model,base,text in (('qwen','','m',self.base,'通义千问'),('qwen','k',' ',self.base,'模型名称'),('custom','k','m','','接口地址'),('zhipu','k','m','http://example.com','HTTPS')):
            with self.subTest(service=service,text=text):
                with self.assertRaises(ValueError) as caught:translate_names(self.item,key,base,model,service=service)
                self.assertIn(text,str(caught.exception))
        self.assertEqual(Handler.calls,0)
    def test_errors_name_the_chosen_service(self):
        Handler.responses=[{'status':401,'body':b'test-only secret'}]
        with self.assertRaises(TranslationError) as caught:self.call('moonshot')
        self.assertIn('月之暗面 Kimi 请求失败（HTTP 401）',str(caught.exception));self.assertNotIn('secret',str(caught.exception))
    def test_presets_are_complete(self):
        for key,service in llm_client.PROVIDERS.items():
            with self.subTest(service=key):
                self.assertEqual(service.key,key)
                if key=='custom':continue
                self.assertTrue(service.models)
                llm_client.check_endpoint(service.base_url)
                self.assertTrue(service.key_page or not service.needs_key)

if __name__=='__main__':unittest.main()

class ParallelTests(unittest.TestCase):
    """Several batches in flight: a fake service that answers by content, records concurrency, and can refuse batches."""
    def setUp(self):
        self.lock=threading.Lock();self.active=0;self.peak=0;self.starts=[];self.refuse={};self.slow=set()
    def items(self,count):return [{'id':f'E{i:03d}','sourceName':f'Effect {i}','category':'c'} for i in range(count)]
    def batch_of(self,ids):return int(ids[0][1:])//10
    def opener(self,request,timeout):
        ids=[item['id'] for item in json.loads(json.loads(request.data)['messages'][1]['content'])['items']];batch=self.batch_of(ids)
        with self.lock:
            self.active+=1;self.peak=max(self.peak,self.active);self.starts.append((batch,self.active))
            code=self.refuse.get(batch,[None]).pop(0) if self.refuse.get(batch) else None
        try:
            if code:raise llm_client.HTTPError(request.full_url,code,'refused',{},None)
            if batch in self.slow:time.sleep(.15)
            else:time.sleep(.03)
            body=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'translations':[{'id':i,'name':'中'+i} for i in ids]})}}],'usage':{'total_tokens':7}}).encode()
            return io.BytesIO(body)
        finally:
            with self.lock:self.active-=1
    def call(self,count,**kwargs):
        with patch.object(llm_client,'urlopen',self.opener):return translate_names(self.items(count),'test-only','https://example.invalid',wait=lambda s:None,**kwargs)
    def test_three_batches_at_a_time(self):
        progress=[];tokens=[]
        result=self.call(65,progress=lambda done,total:progress.append((done,total)),usage=tokens.append)
        self.assertEqual(result,{f'E{i:03d}':f'中E{i:03d}' for i in range(65)})
        self.assertEqual(self.peak,3);self.assertEqual(len(self.starts),7);self.assertEqual(tokens,[7]*7)
        self.assertEqual(progress[-1],(65,65));self.assertEqual([d for d,_ in progress],sorted(d for d,_ in progress))
    def test_rate_limit_drops_to_one_batch_at_a_time(self):
        self.refuse={0:[429]};self.slow={1,2}
        self.assertEqual(len(self.call(70)),70)
        later=[active for batch,active in self.starts if batch>=3]
        self.assertEqual(later,[1]*4)
    def test_failure_keeps_finished_batches_and_starts_no_more(self):
        self.refuse={1:[400]};self.slow={0,2}
        with self.assertRaises(TranslationError) as caught:self.call(60)
        self.assertIn('HTTP 400',str(caught.exception))
        self.assertEqual(set(caught.exception.partial),{f'E{i:03d}' for i in list(range(10))+list(range(20,30))})
        self.assertEqual({batch for batch,_ in self.starts},{0,1,2})
    def test_cancel_stops_new_batches(self):
        checks=[]
        def cancelled():checks.append(1);return len(checks)>1
        with self.assertRaises(TranslationError) as caught:self.call(50,cancelled=cancelled)
        self.assertIn('已取消',str(caught.exception));self.assertEqual(set(caught.exception.partial),{f'E{i:03d}' for i in range(10)})
        self.assertEqual([batch for batch,_ in self.starts],[0])
    def test_duplicate_ids_are_refused_before_any_request(self):
        with self.assertRaisesRegex(TranslationError,'标识重复'):
            with patch.object(llm_client,'urlopen',self.opener):translate_names(self.items(12)+self.items(1),'test-only','https://example.invalid')
        self.assertEqual(self.starts,[])
