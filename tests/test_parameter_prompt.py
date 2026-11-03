"""Parameter labels are translated with their own prompt (31-byte limit, AE wording); effect names keep theirs."""
import io,json,threading,unittest
from unittest.mock import patch
from test_gui import GuiCase
import llm_client,parameter_dialog
from parameter_names import identity

class PromptTests(unittest.TestCase):
    def prompts(self,**kwargs):
        sent=[]
        def capture(request,timeout):
            body=json.loads(request.data);sent.append(body)
            ids=[item['id'] for item in json.loads(body['messages'][1]['content'])['items']]
            content=json.dumps({'translations':[{'id':i,'name':'中文'} for i in ids]})
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':content}}]}).encode())
        items=[{'id':f'E{i}','sourceName':f'Effect {i}','category':'c'} for i in range(3)]
        with patch.object(llm_client,'urlopen',capture):llm_client.translate_names(items,'test-only','https://example.invalid',**kwargs)
        return sent
    def test_default_is_the_effect_prompt(self):
        self.assertEqual(self.prompts()[0]['messages'][0],{'role':'system','content':llm_client.SYSTEM_PROMPT})
    def test_parameter_prompt_replaces_it(self):
        body=self.prompts(system_prompt=llm_client.PARAMETER_PROMPT)[0]
        self.assertEqual(body['messages'][0]['content'],llm_client.PARAMETER_PROMPT);self.assertNotIn(llm_client.SYSTEM_PROMPT,json.dumps(body,ensure_ascii=False))
        self.assertIn('31个UTF-8字节',llm_client.PARAMETER_PROMPT);self.assertNotIn('47',llm_client.PARAMETER_PROMPT)
    def test_claude_gets_it_as_system(self):
        request=llm_client.build_request(llm_client.provider('anthropic'),'https://api.anthropic.com/v1','test-only','m',[],system_prompt=llm_client.PARAMETER_PROMPT)
        self.assertEqual(json.loads(request.data)['system'],llm_client.PARAMETER_PROMPT)

class ParameterPageTests(GuiCase):
    def test_parameter_page_sends_the_parameter_prompt(self):
        w=self.window;key=identity('CC Drizzle','CC Drizzle-0001')
        w.parameter_page.loaded({'parameters':[{'id':key,'name':'Drip Rate','effectMatchName':'CC Drizzle','matchName':'CC Drizzle-0001'}]})
        w.parameter_enabled.add(key)
        with patch.object(w.settings,'ready',return_value=True),patch.object(parameter_dialog,'translate_names',return_value={key:'滴落率'}) as translate,\
             patch.object(w,'work',lambda work,done:done(work(lambda text:None))):w.parameter_page.translate()
        self.assertIs(translate.call_args.kwargs['system_prompt'],llm_client.PARAMETER_PROMPT)
        self.assertEqual(translate.call_args.args[0],[{'id':key,'sourceName':'Drip Rate','category':'所属效果 CC Drizzle'}])
        self.assertEqual(w.parameter_pending[key],'滴落率')
    def side_by_side(self,fake):
        w=self.window;key=identity('CC Drizzle','CC Drizzle-0001');lines=[]
        w.parameter_page.loaded({'parameters':[{'id':key,'name':'Drip Rate','effectMatchName':'CC Drizzle','matchName':'CC Drizzle-0001'}]})
        w.parameter_enabled.add(key)
        option={'id':'choice-1','source':'Linear','effectMatchName':'CC Drizzle','popup':{'name':'Mode','capacity':40}}
        with patch.object(w.settings,'ready',return_value=True),patch.object(parameter_dialog,'translate_names',fake),\
             patch.object(w.parameter_page,'selected_options',return_value=[option]),\
             patch.object(w,'work',lambda work,done:done(work(lines.append))):w.parameter_page.translate()
        return key,lines
    def test_labels_and_dropdowns_translate_at_the_same_time(self):
        together=threading.Barrier(2,timeout=5);slowdowns=[]
        def fake(items,*args,progress=None,slowdown=None,**options):
            together.wait();slowdowns.append(slowdown);progress(1,1)
            return {item['id']:'中' for item in items}
        key,lines=self.side_by_side(fake)
        self.assertIs(slowdowns[0],slowdowns[1]);self.assertEqual(self.window.parameter_pending[key],'中')
        self.assertEqual(self.window.parameter_page.popup_stores()[0].values['choice-1'],'中')
        self.assertEqual(sorted(lines[-1].split('，')),['下拉选项翻译 1/1','参数翻译 1/1'])
    def test_a_failed_side_keeps_the_other_sides_results(self):
        def fake(items,*args,**options):
            if items[0]['id']=='choice-1':return {'choice-1':'线性'}
            raise llm_client.TranslationError('服务失败',{},1)
        self.side_by_side(fake)
        self.assertEqual(self.window.parameter_page.popup_stores()[0].values['choice-1'],'线性')
        self.assertIn('服务失败',self.window.parameter_page.status.text())
    def test_the_same_failure_on_both_sides_shows_once(self):
        def fake(items,*args,**options):raise llm_client.TranslationError('服务失败',{},1)
        self.side_by_side(fake)
        self.assertEqual(self.window.parameter_page.status.text().count('服务失败'),1)
