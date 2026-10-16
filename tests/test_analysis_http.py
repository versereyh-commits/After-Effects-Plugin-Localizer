from http.server import ThreadingHTTPServer
import json,threading,unittest
import llm_client
from test_llm import Handler

class AnalysisHttpTests(unittest.TestCase):
    def setUp(self):
        Handler.responses=[];Handler.calls=0;Handler.reply={'tool':'inspect','args':{}}
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.config={'service':'ollama','base_url':f'http://127.0.0.1:{self.server.server_port}/v1','model':'test','key':''}
        self.messages=[{'role':'user','content':'test'}];self.waits=[]
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def call(self):return llm_client.analysis_turn('fixed analysis prompt',self.messages,self.config,wait=self.waits.append)
    def test_analysis_uses_its_own_prompt_and_no_translation_contract(self):
        self.assertEqual(self.call(),Handler.reply);self.assertEqual(Handler.received['messages'][0]['content'],'fixed analysis prompt')
        self.assertNotIn('test-only',json.dumps(Handler.received));self.assertEqual(Handler.path,'/v1/chat/completions')
    def test_claude_analysis_uses_messages_protocol(self):
        self.config.update(service='anthropic',key='test-only')
        Handler.responses=[{'body':json.dumps({'content':[{'type':'text','text':json.dumps(Handler.reply)}],'stop_reason':'end_turn'}).encode()}]
        self.assertEqual(self.call(),Handler.reply);self.assertEqual(Handler.received['system'],'fixed analysis prompt');self.assertEqual(Handler.path,'/v1/messages')
        self.assertNotIn('test-only',json.dumps(Handler.received))
    def test_non_retryable_http_error_hides_response_and_key(self):
        self.config['key']='test-only';Handler.responses=[{'status':401,'body':b'secret-body'}]
        with self.assertRaises(RuntimeError) as error:self.call()
        self.assertNotIn('secret-body',str(error.exception));self.assertNotIn('test-only',str(error.exception));self.assertEqual(Handler.calls,1)
    def test_retry_then_success(self):
        Handler.responses=[{'status':429},{}];self.assertEqual(self.call(),Handler.reply);self.assertEqual(self.waits,[1])
    def test_truncation_rejected(self):
        Handler.responses=[{'finish_reason':'length'}]
        with self.assertRaisesRegex(RuntimeError,'截断'):self.call()
    def test_non_object_rejected(self):
        Handler.reply=[]
        with self.assertRaisesRegex(RuntimeError,'格式无效'):self.call()
