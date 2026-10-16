"""Parameter labels are translated with their own prompt (31-byte limit, AE wording); effect names keep theirs."""
import io,json,unittest
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
