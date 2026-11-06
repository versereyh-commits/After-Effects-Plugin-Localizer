import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import agent_core,agent_files,agent_tools,patch_loader
from agent_core import Agent,Tool,check_args,parse_reply,trimmed
from llm_client import ChatError

class Events:
    def __init__(self):self.states=[];self.said=[]
    def state(self,name,detail):self.states.append((name,detail))
    def say(self,text):self.said.append(text)
class Host:
    def __init__(self,tools,level='data',answer=True):self.t=tools;self.lvl=level;self.answer=answer;self.asked=[]
    def level(self):return self.lvl
    def all_tools(self):return self.t
    def tools(self):return [t for t in self.t if agent_core.level_at_least(self.lvl,t.level)]
    def prompt(self):return 'system'
    def config(self):return {}
    def usage(self,tokens):pass
    def confirm(self,tool,question):self.asked.append(question);return self.answer
def script(*replies):
    """chat_turn replaced by a list of replies; the messages it saw are kept."""
    seen=[];queue=list(replies)
    def turn(system,messages,config,**options):
        seen.append([dict(m) for m in messages]);value=queue.pop(0)
        if isinstance(value,Exception):raise value
        return value if isinstance(value,tuple) else (value,False)
    return turn,seen
def run(host,*replies,text='hi'):
    events=Events();agent=Agent(host,events);turn,seen=script(*replies)
    with patch('agent_core.chat_turn',turn):agent.run(text)
    return agent,events,seen
def J(**v):
    # A reply that acts says first what it understood, or the loop sends it back.
    if 'calls' in v:v.setdefault('understood','测试')
    return json.dumps(v,ensure_ascii=False)

class IntentTests(unittest.TestCase):
    TOOLS=lambda self,log:[Tool('change','',lambda a:log.append('changed') or 'ok',level='data',kind='write',changes=True)]
    def test_acting_without_saying_what_it_understood_is_sent_back_unexecuted(self):
        log=[];raw=json.dumps({'calls':[{'tool':'change'}]});agent,events,seen=run(Host(self.TOOLS(log)),raw,J(understood='要改动',calls=[{'tool':'change'}]),J(say='好了'))
        self.assertEqual(log,['changed']);self.assertIn('understood',seen[1][-1]['content']);self.assertNotIn(raw,[m['content'] for m in agent.messages]);self.assertIn(('understood','要改动'),events.states)
    def test_it_is_only_asked_once(self):
        log=[];raw=json.dumps({'calls':[{'tool':'change'}]});_,_e,seen=run(Host(self.TOOLS(log)),raw,raw,J(say='好了'));self.assertEqual(log,['changed']);self.assertEqual(len(seen),3)
    def test_understanding_shows_before_the_answer(self):
        _,events,seen=run(Host([]),J(understood='想知道怎么用',say='这样用'));self.assertEqual(events.said,['这样用']);self.assertEqual(len(seen),1);names=[n for n,_ in events.states];self.assertLess(names.index('understood'),names.index('responding'))
    def test_understanding_shows_once_a_turn(self):
        log=[];_,events,_s=run(Host(self.TOOLS(log)),J(understood='要改动',calls=[{'tool':'change'}]),J(understood='要改动的',say='好了'));self.assertEqual([d for n,d in events.states if n=='understood'],['要改动'])
    def test_a_plain_answer_is_not_sent_back(self):
        _,events,seen=run(Host([]),J(say='你好'),text='你好');self.assertEqual(len(seen),1)
        _,events,seen=run(Host([]),'只是一段文字');self.assertEqual(events.said,['只是一段文字']);self.assertEqual(len(seen),1)
    def test_later_steps_need_not_repeat_it(self):
        log=[];_,_e,seen=run(Host(self.TOOLS(log)),J(calls=[{'tool':'change'}]),json.dumps({'calls':[{'tool':'change'}]}),J(say='好了'));self.assertEqual(log,['changed','changed']);self.assertEqual(len(seen),3)
    def test_options_end_the_turn_and_nothing_runs_with_them(self):
        log=[];reply=json.dumps({'understood':'想翻译','question':'翻译哪些？','options':[{'title':'翻译勾选的','detail':'共 3 个'},{'title':'翻译全部'}],'calls':[{'tool':'change'}]},ensure_ascii=False);_,events,seen=run(Host(self.TOOLS(log)),reply)
        self.assertEqual(log,[]);self.assertEqual(len(seen),1);shown=json.loads(dict(events.states)['options']);self.assertEqual(shown['question'],'翻译哪些？');self.assertEqual([o['title'] for o in shown['options']],['翻译勾选的','翻译全部']);self.assertEqual(events.states[-1],('finished','curious'))
    def test_a_reply_with_only_options_is_not_broken(self):
        reply=json.dumps({'question':'哪个？','options':['甲','乙']},ensure_ascii=False);self.assertFalse(agent_core.is_broken(reply));_,_e,seen=run(Host([]),reply);self.assertEqual(len(seen),1)
    def test_leaked_markup_in_the_new_fields_is_caught(self):
        self.assertTrue(agent_core.is_broken(json.dumps({'understood':'好</｜｜DSML｜｜ invoke>'})));self.assertTrue(agent_core.is_broken(json.dumps({'question':'q','options':[{'title':'<tool_call>'}]})))
    def test_a_question_without_real_choices_is_just_said(self):
        _,events,_s=run(Host([]),json.dumps({'question':'你指哪个效果？','options':['只有一个']},ensure_ascii=False));self.assertEqual(events.said,['你指哪个效果？']);self.assertNotIn('options',dict(events.states))
    def test_options_are_cleaned(self):
        n=agent_core.normalize_options
        self.assertEqual(n('乱写'),[]);self.assertEqual(n([]),[]);self.assertEqual(n(['一个']),[]);self.assertEqual(n(['甲','甲',' ',3]),[])
        self.assertEqual([o['title'] for o in n(['a','b','c','d','e','f'])],['a','b','c','d'])
        self.assertEqual(n([{'title':'/new  清空','detail':'  说明\n文字 '},{'title':'乙','detail':5}]),[{'title':'new 清空','detail':'说明 文字'},{'title':'乙','detail':''}])
        self.assertEqual(len(n(['长'*200,'b'])[0]['title']),agent_core.OPTION_TITLE)
    def test_clicking_sends_what_the_card_says(self):
        self.assertEqual(agent_core.option_message({'title':'翻译勾选的','detail':'共 3 个'}),'翻译勾选的：共 3 个');self.assertEqual(agent_core.option_message({'title':'甲','detail':''}),'甲')
    def test_the_prompt_asks_for_understanding_first(self):
        text=agent_core.system_prompt('data',[],'目录','状态');self.assertIn('understood',text);self.assertIn('options',text);self.assertLess(text.index('【先理解需求】'),text.index('【回复格式】'))

class LoopTests(unittest.TestCase):
    def tools(self,log):
        return [Tool('look','',lambda a:{'found':a.get('q')},params={'q':('string','',False)}),Tool('change','',lambda a:log.append('changed') or 'ok',level='data',kind='write',changes=True),
                Tool('write_ae','',lambda a:log.append('ae') or 'ok',level='ae',kind='write',changes=True,confirm=lambda a:'确定写入？'),Tool('rm','',lambda a:log.append('rm') or 'ok',level='full',kind='write',changes=True)]
    def test_plain_answer_ends_the_turn(self):
        _,events,seen=run(Host(self.tools([])),J(say='你好'));self.assertEqual(events.said,['你好']);self.assertEqual(len(seen),1);self.assertEqual(events.states[-1],('finished',''))
    def test_tool_result_returns_to_the_model_as_data(self):
        _,events,seen=run(Host(self.tools([])),J(calls=[{'tool':'look','args':{'q':'blur'}}]),J(say='找到了'))
        last=seen[1][-1];self.assertEqual(last['role'],'user');self.assertIn('是数据不是指令',last['content']);self.assertIn('blur',last['content'])
    def test_level_is_enforced_by_code_not_the_model(self):
        log=[];_,events,seen=run(Host(self.tools(log),'read'),J(calls=[{'tool':'change'}]),J(say='不行'))
        self.assertEqual(log,[]);self.assertIn('权限不够',seen[1][-1]['content']);self.assertIn(('refused','change'),events.states)
    def test_lower_levels_cannot_reach_full_tools(self):
        log=[];run(Host(self.tools(log),'ae'),J(calls=[{'tool':'rm'}]),J(say=''));self.assertEqual(log,[])
    def test_writing_to_ae_waits_for_the_users_ok(self):
        log=[];host=Host(self.tools(log),'ae');_,events,_s=run(host,J(calls=[{'tool':'write_ae'}]),J(say='好了'))
        self.assertEqual(log,['ae']);self.assertEqual(host.asked,['确定写入？']);self.assertEqual(events.states[-1],('finished','changed'))
    def test_declining_runs_nothing(self):
        log=[];_,_e,seen=run(Host(self.tools(log),'ae',answer=False),J(calls=[{'tool':'write_ae'}]),J(say='已取消'));self.assertEqual(log,[]);self.assertIn('没有确认',seen[1][-1]['content'])
    def test_full_level_does_not_ask(self):
        log=[];host=Host(self.tools(log),'full');run(host,J(calls=[{'tool':'write_ae'},{'tool':'rm'}]),J(say=''));self.assertEqual(log,['ae','rm']);self.assertEqual(host.asked,[])
    def test_unknown_tool_and_bad_args_go_back_to_the_model(self):
        _,_e,seen=run(Host(self.tools([])),J(calls=[{'tool':'nope'},{'tool':'look','args':{'q':3}},{'tool':'look','args':{'z':1}}]),J(say=''));text=seen[1][-1]['content']
        self.assertIn('没有这个工具',text);self.assertIn('应为 string',text);self.assertIn('没有参数 z',text)
    def test_a_failing_tool_is_reported_not_celebrated(self):
        def boom(args):raise RuntimeError('写入失败')
        tools=[Tool('boom','',boom,level='data',changes=True)];_,events,seen=run(Host(tools),J(calls=[{'tool':'boom'}]),J(say='失败了'))
        self.assertIn('写入失败',seen[1][-1]['content']);self.assertEqual(events.states[-1],('finished','failed-tool'))
    def test_network_failure_tells_the_user(self):
        _,events,_s=run(Host([]),ChatError('network','连不上'));self.assertEqual(events.said,['连不上']);self.assertEqual(events.states[-1],('failed','network'))
    def test_cut_reply_is_not_executed(self):
        log=[];_,_e,seen=run(Host(self.tools(log)),(J(calls=[{'tool':'change'}]),True),J(say='短一点'));self.assertEqual(log,[]);self.assertIn('截断',seen[1][-1]['content'])
    def test_step_limit_stops_a_loop(self):
        with patch('agent_core.MAX_STEPS',3):
            _,events,seen=run(Host(self.tools([])),*[J(calls=[{'tool':'look'}])]*3)
        self.assertEqual(len(seen),3);self.assertTrue(events.said)
    def test_stop_ends_between_calls(self):
        log=[];events=Events();host=Host(self.tools(log));agent=Agent(host,events);tools=[Tool('stopper','',lambda a:setattr(agent,'stop',True)),*self.tools(log)];host.t=tools;turn,_=script(J(calls=[{'tool':'stopper'},{'tool':'change'}]))
        with patch('agent_core.chat_turn',turn):agent.run('x')
        self.assertEqual(log,[]);self.assertEqual(events.states[-1],('stopped',''))

DSML='\n\n    {"checkedOnly": true, "limit": 50}</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'   # recorded from DeepSeek
class BrokenReplyTests(unittest.TestCase):
    def test_what_counts(self):
        for bad in (DSML,'','  ','{"checkedOnly":true}','{"say":"x"','<tool_call>{"a":1}</tool_call>','<｜tool▁calls▁begin｜>',J(say='好</｜｜DSML｜｜ invoke>')):self.assertTrue(agent_core.is_broken(bad),bad)
        for good in (J(say='好'),J(calls=[]),'```json\n{"say":"好"}\n```','普通的一句回答','效果的 parameter 是参数','<b>粗体</b>写法'):self.assertFalse(agent_core.is_broken(good),good)
    def test_retried_and_never_shown_or_kept(self):
        log=[];tools=[Tool('look','',lambda a:log.append(1) or 'ok')];agent,events,seen=run(Host(tools),DSML,J(calls=[{'tool':'look'}]),J(say='好了'))
        self.assertEqual(log,[1]);self.assertEqual(events.said,['好了']);self.assertFalse(any('DSML' in s for s in events.said));self.assertIn('不是约定的格式',seen[1][-1]['content']);self.assertFalse(any('DSML' in m['content'] for m in agent.messages))
    def test_gives_up_and_says_so(self):
        log=[];tools=[Tool('look','',lambda a:log.append(1) or 'ok')];agent,events,seen=run(Host(tools),DSML,DSML,DSML);self.assertEqual(len(seen),3);self.assertEqual(log,[]);self.assertEqual(events.states[-1],('failed','format'));self.assertIn('没有执行',events.said[-1]);self.assertFalse(any('DSML' in s for s in events.said))
    def test_last_retry_turns_thinking_off(self):
        configs=[];queue=[DSML,DSML,J(say='好')]
        def turn(system,messages,config,**options):configs.append(config.get('effort'));return queue.pop(0),False
        host=Host([]);host.config=lambda:{'effort':'default'}
        with patch('agent_core.chat_turn',turn):Agent(host,Events()).run('x')
        self.assertEqual(configs,['default','default','off'])
    def test_a_good_reply_resets_the_count(self):
        tools=[Tool('look','',lambda a:'ok')];_,events,seen=run(Host(tools),DSML,DSML,J(calls=[{'tool':'look'}]),DSML,DSML,J(say='好'));self.assertEqual(events.said,['好'])
    def test_plain_words_still_answer_without_a_retry(self):
        _,events,seen=run(Host([]),'直接说话');self.assertEqual(events.said,['直接说话']);self.assertEqual(len(seen),1)

class ParsingTests(unittest.TestCase):
    def test_fenced_and_plain_replies(self):
        self.assertEqual(parse_reply('```json\n{"say":"好","calls":[]}\n```')[0],'好');self.assertEqual(parse_reply('直接说话')[0],'直接说话')
    def test_unknown_mood_is_dropped(self):self.assertEqual(parse_reply(J(say='x',mood='evil'))[1],'')
    def test_args_checks_types_and_required(self):
        t=Tool('t','',None,params={'a':('integer','',True)});self.assertIsNone(check_args(t,{'a':1}));self.assertIn('缺少',check_args(t,{}));self.assertIn('应为',check_args(t,{'a':True}));self.assertIn('对象',check_args(t,[]))
    def test_history_starts_at_a_user_message(self):
        messages=[{'role':'user','content':'x'*40000},{'role':'assistant','content':'y'*30000},{'role':'user','content':'z'}]
        out=trimmed(messages);self.assertEqual(out[0]['role'],'user');self.assertEqual(out[-1]['content'],'z')
    def test_prompt_lists_only_this_levels_tools(self):
        tools=[Tool('a_read','读',None)];text=agent_core.system_prompt('read',tools,'目录','状态');self.assertIn('a_read',text);self.assertIn('完全访问权限',text)

class ToolLevelTests(unittest.TestCase):
    def test_no_tool_can_return_the_key(self):
        source=(Path(agent_tools.__file__)).read_text(encoding='utf8');self.assertNotIn('settings_store',source)
        self.assertNotIn("'key':",source.split('def app_status')[1].split('def list_versions')[0])

class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.data=patch('agent_files.data_root',lambda:self.root/'data');self.data.start();(self.root/'data').mkdir()
    def tearDown(self):self.data.stop();self.temp.cleanup()
    def test_write_backs_up_and_logs(self):
        f=self.root/'a.txt';f.write_text('old',encoding='utf8');result=agent_files.write_file(str(f),'new')
        self.assertEqual(f.read_text(encoding='utf8'),'new');self.assertEqual(Path(result['backup']).read_text(encoding='utf8'),'old')
        self.assertEqual(json.loads((self.root/'data/assistant-backups/changes.jsonl').read_text(encoding='utf8').splitlines()[0])['action'],'write')
    def test_delete_keeps_a_copy(self):
        f=self.root/'a.txt';f.write_text('keep',encoding='utf8');result=agent_files.delete_path(str(f));self.assertFalse(f.exists());self.assertEqual(Path(result['backup']).read_text(encoding='utf8'),'keep')
    def test_delete_folder_keeps_a_copy(self):
        d=self.root/'d';d.mkdir();(d/'x').write_text('1',encoding='utf8');result=agent_files.delete_path(str(d));self.assertFalse(d.exists());self.assertTrue((Path(result['backup'])/'x').is_file())
    def test_key_file_is_closed(self):
        (self.root/'data/api-settings.json').write_text('{}',encoding='utf8')
        for call in (lambda p:agent_files.read_file(p),lambda p:agent_files.write_file(p,'x'),lambda p:agent_files.delete_path(p),lambda p:agent_files.replace_in_file(p,'{','[')):
            with self.assertRaises(PermissionError):call(str(self.root/'data/api-settings.json'))
    def test_relative_path_refused(self):
        with self.assertRaises(ValueError):agent_files.read_file('a.txt')
    def test_replace_needs_exactly_one_match(self):
        f=self.root/'a.txt';f.write_text('aa',encoding='utf8')
        with self.assertRaises(ValueError):agent_files.replace_in_file(str(f),'a','b')
        self.assertEqual(f.read_text(encoding='utf8'),'aa');agent_files.replace_in_file(str(f),'aa','b');self.assertEqual(f.read_text(encoding='utf8'),'b')
    def test_binary_is_refused_and_gbk_is_read(self):
        f=self.root/'b.bin';f.write_bytes(b'\0\1');g=self.root/'g.txt';g.write_bytes('中文'.encode('gbk'))
        with self.assertRaises(ValueError):agent_files.read_file(str(f))
        self.assertEqual(agent_files.read_file(str(g))['text'],'中文')
    def test_move_never_overwrites(self):
        a=self.root/'a';b=self.root/'b';a.write_text('1',encoding='utf8');b.write_text('2',encoding='utf8')
        with self.assertRaises(FileExistsError):agent_files.move_path(str(a),str(b))
    def test_listing(self):
        (self.root/'x.txt').write_text('1',encoding='utf8');self.assertIn('x.txt',[i['name'] for i in agent_files.list_dir(str(self.root))['items']])

class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.src=self.root/'src';self.src.mkdir();(self.src/'modx.py').write_text('VALUE=1\n',encoding='utf8')
        self.p=[patch('patch_loader.data_root',lambda:self.root/'data'),patch('patch_loader.source_folder',lambda:self.src)]
        for p in self.p:p.start()
        patch_loader._active=None;patch_loader.notice='';self.path=list(sys.meta_path)
    def tearDown(self):
        sys.meta_path[:]=self.path;sys.modules.pop('modx',None);patch_loader._active=None;patch_loader.notice=''
        for p in self.p:p.stop()
        self.temp.cleanup()
    def test_patch_replaces_and_compiles(self):
        patch_loader.apply_patch('modx','9.9','VALUE=1','VALUE=2');self.assertIn('VALUE=2',patch_loader.read_source('modx','9.9'))
        with self.assertRaises(ValueError):patch_loader.apply_patch('modx','9.9','VALUE=2','VALUE=(')
        self.assertIn('VALUE=2',patch_loader.read_source('modx','9.9'))
    def test_must_match_exactly_once(self):
        with self.assertRaises(ValueError):patch_loader.apply_patch('modx','9.9','nothing','x')
    def test_loader_files_cannot_be_patched(self):
        with self.assertRaises(PermissionError):patch_loader.apply_patch('runtime','9.9','a','b')
    def test_patched_module_is_imported_for_that_version_only(self):
        patch_loader.apply_patch('modx','9.9','VALUE=1','VALUE=2');patch_loader.install('9.8');self.assertIsNone(patch_loader._active)
        patch_loader.install('9.9');import modx;self.assertEqual(modx.VALUE,2)
    def test_a_start_that_never_finished_sets_patches_aside(self):
        patch_loader.apply_patch('modx','9.9','VALUE=1','VALUE=2');patch_loader.install('9.9');self.assertEqual(patch_loader.active(),['modx'])
        sys.meta_path[:]=self.path;patch_loader._active=None;patch_loader.install('9.9')
        self.assertEqual(patch_loader.active(),[]);self.assertIn('撤销',patch_loader.notice);self.assertEqual(patch_loader.list_patches('9.9'),[])
    def test_a_good_start_keeps_them(self):
        patch_loader.apply_patch('modx','9.9','VALUE=1','VALUE=2');patch_loader.install('9.9');patch_loader.started();patch_loader.confirm_trial('9.9')
        sys.meta_path[:]=self.path;patch_loader._active=None;patch_loader.install('9.9');self.assertEqual(patch_loader.active(),['modx'])
    def test_removing(self):
        patch_loader.apply_patch('modx','9.9','VALUE=1','VALUE=2');self.assertTrue(patch_loader.remove_patch('modx','9.9'));self.assertFalse(patch_loader.remove_patch('modx','9.9'))

class HelpTests(unittest.TestCase):
    def test_document_covers_the_main_topics(self):
        titles=[t for t,_ in agent_tools.help_sections()]
        for wanted in ('效果名称页','下拉选项','AE 版本与适配','AI 助手的权限','常见问题','用 AI 修复程序问题'):self.assertIn(wanted,titles)
    def test_search_finds_the_right_section(self):
        self.assertEqual(agent_tools.help_search('下拉选项为什么不显示')[0]['section'],'下拉选项')
        self.assertEqual(agent_tools.help_search('快捷键怎么改')[0]['section'],'快捷键')
    def test_no_match_is_empty(self):self.assertEqual(agent_tools.help_search('zzzqqq'),[])

class ThinkingTests(unittest.TestCase):
    def body(self,service,effort):
        from llm_client import build_request,effort_fields,provider
        chosen=provider(service);return json.loads(build_request(chosen,'https://x','k','m',[],system_prompt='s',messages=[{'role':'user','content':'x'}],fields=effort_fields(chosen,effort)).data)
    def test_off_changes_nothing(self):self.assertEqual(self.body('deepseek','off')['thinking'],{'type':'disabled'})
    def test_default_thinks_at_medium(self):self.assertEqual(self.body('openai','default')['reasoning_effort'],'medium');self.assertEqual(self.body('deepseek','default')['thinking'],{'type':'enabled'})
    def test_host_names_can_be_encoded_in_a_thread(self):
        import threading
        box=[];thread=threading.Thread(target=lambda:box.append('example.com'.encode('idna')));thread.start();thread.join();self.assertEqual(box,[b'example.com'])
    def test_each_service_gets_its_own_field(self):
        self.assertEqual(self.body('deepseek','high')['thinking'],{'type':'enabled'});self.assertEqual(self.body('openai','low')['reasoning_effort'],'low');self.assertEqual(self.body('openrouter','high')['reasoning'],{'effort':'high'});self.assertEqual(self.body('openai','max')['reasoning_effort'],'high');self.assertEqual(self.body('deepseek','max')['reasoning_effort'],'max');self.assertEqual(self.body('anthropic','max')['thinking']['budget_tokens'],32000)
        claude=self.body('anthropic','high');self.assertEqual(claude['thinking']['budget_tokens'],16000);self.assertGreater(claude['max_tokens'],16000);self.assertTrue(self.body('qwen','low')['enable_thinking'])
    def test_model_that_refuses_gets_asked_again_without(self):
        import io
        from urllib.error import HTTPError
        from llm_client import chat_turn
        seen=[];notes=[]
        class Reply(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*a):pass
        def opener(request,timeout):
            body=json.loads(request.data);seen.append(body)
            if body['thinking']['type']=='enabled':raise HTTPError('u',400,'bad',{},io.BytesIO())
            return Reply(json.dumps({'choices':[{'message':{'content':'好'},'finish_reason':'stop'}],'usage':{'prompt_tokens':7,'total_tokens':9}}).encode())
        counted=[];text,cut=chat_turn('s',[{'role':'user','content':'x'}],{'service':'deepseek','key':'k','base_url':'https://x','model':'m','effort':'high'},opener=opener,notice=notes.append,prompt_tokens=counted.append,wait=lambda s:None)
        self.assertEqual(text,'好');self.assertEqual(len(seen),2);self.assertEqual(len(notes),1);self.assertEqual(counted,[7])

class VisionWireTests(unittest.TestCase):
    message=[{'role':'user','content':'这是什么','images':[{'media':'image/jpeg','data':'QUJD'}]},{'role':'assistant','content':'好'}]
    def body(self,service):
        from llm_client import build_request,provider
        return json.loads(build_request(provider(service),'https://x','k','m',[],system_prompt='s',messages=self.message).data)
    def test_openai_form(self):
        parts=self.body('deepseek')['messages'][1]['content'];self.assertEqual(parts[0],{'type':'text','text':'这是什么'});self.assertEqual(parts[1]['image_url']['url'],'data:image/jpeg;base64,QUJD')
    def test_claude_form(self):
        parts=self.body('anthropic')['messages'][0]['content'];self.assertEqual(parts[0]['source'],{'type':'base64','media_type':'image/jpeg','data':'QUJD'});self.assertEqual(parts[1]['text'],'这是什么')
    def test_plain_messages_stay_plain(self):self.assertEqual(self.body('deepseek')['messages'][2],{'role':'assistant','content':'好'})
    def refuse(self,effort):
        import io
        from urllib.error import HTTPError
        from llm_client import chat_turn
        tries=[];notes=[]
        def opener(request,timeout):
            tries.append(json.loads(request.data));raise HTTPError('u',400,'bad',{},io.BytesIO())
        with self.assertRaises(ChatError) as caught:chat_turn('s',self.message,{'service':'deepseek','key':'k','base_url':'https://x','model':'m','effort':effort},opener=opener,notice=notes.append,wait=lambda s:None)
        self.assertEqual(caught.exception.kind,'vision');self.assertEqual(notes,[]);return tries
    def test_refused_picture_is_named_not_blamed_on_thinking(self):self.assertEqual(len(self.refuse('default')),2);self.assertEqual(len(self.refuse('off')),1)

class ContextTests(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(agent_core.context_window('moonshot-v1-32k'),32000);self.assertEqual(agent_core.context_window('claude-sonnet-5-5'),200000);self.assertEqual(agent_core.context_window('unknown'),agent_core.DEFAULT_WINDOW)
    def test_estimate(self):self.assertEqual(agent_core.estimate_tokens('中文中文'),4);self.assertEqual(agent_core.estimate_tokens('abcdefg'),2)
    def test_every_prompt_mentions_json_for_services_in_json_mode(self):
        # DeepSeek answers HTTP 400 otherwise; found on the real service, a fake one would not care.
        self.assertIn('json',agent_core.SUMMARY_PROMPT.casefold());self.assertIn('json',agent_core.system_prompt('read',[],'','').casefold())
    def test_compact_reads_the_summary_and_rejects_a_cut_one(self):
        for reply,want in ((J(summary='要点'),'要点'),('直接说的摘要','直接说的摘要')):
            agent=Agent(Host([]),Events());agent.messages=[{'role':'user','content':'a'},{'role':'assistant','content':'b'}];turn,_=script(reply)
            with patch('agent_core.chat_turn',turn):agent.compact()
            self.assertIn(want,agent.messages[0]['content'])
        agent=Agent(Host([]),Events());agent.messages=[{'role':'user','content':'a'},{'role':'assistant','content':'b'}];kept=list(agent.messages);turn,_=script((J(summary='半'),True))
        with patch('agent_core.chat_turn',turn),self.assertRaises(ChatError):agent.compact()
        self.assertEqual(agent.messages,kept)
    def test_near_full_compresses_before_the_turn(self):
        host=Host([]);events=Events();agent=Agent(host,events);agent.messages=[{'role':'user','content':'字'*70000},{'role':'assistant','content':'x'},{'role':'user','content':'y'},{'role':'assistant','content':'z'}];turn,seen=script('摘要',J(say='好'))
        with patch('agent_core.chat_turn',turn):agent.run('下一句')
        self.assertIn(('compressing',''),events.states);self.assertIn('摘要',agent.messages[0]['content']);self.assertEqual(agent.messages[2]['content'],'下一句')

class StoreTests(unittest.TestCase):
    def test_round_trip_and_bad_names(self):
        from assistant_store import ChatStore
        with tempfile.TemporaryDirectory() as folder:
            s=ChatStore(Path(folder));s.save({'id':'a1','title':'t','messages':[{'role':'user','content':'x'}],'items':[{'kind':'user','text':'x'}]});self.assertEqual(s.list()[0]['count'],1);self.assertEqual(s.load('a1')['title'],'t')
            for bad in ('..\\x','a/b','a.b',''):
                with self.assertRaises(ValueError):s.path(bad)
            (s.folder/'b.json').write_text(json.dumps({'messages':[{'role':'tool','content':'x'}],'items':[]}),encoding='utf8')
            with self.assertRaises(ValueError):s.load('b')

if __name__=='__main__':unittest.main()
