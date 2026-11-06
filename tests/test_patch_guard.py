import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QPushButton
import agent_files,app_live,patch_check,patch_loader
from test_gui import GuiCase

ROOT=Path(__file__).resolve().parents[1]
class Base(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.src=self.root/'src';self.src.mkdir();(self.src/'modx.py').write_text('VALUE=1\n',encoding='utf8')
        for p in (patch('patch_loader.data_root',lambda:self.root/'data'),patch('patch_loader.source_folder',lambda:self.src),patch('patch_loader._another_running',lambda:False),patch('atexit.register',lambda *a:None)):
            p.start();self.addCleanup(p.stop)
        self.path=list(sys.meta_path);self.reset();self.addCleanup(self.reset);self.addCleanup(self.temp.cleanup);self.asked=[];self.answer='safe'
        p=patch('patch_loader._ask_safe_mode',self.ask);p.start();self.addCleanup(p.stop)
    def ask(self,modules):
        self.asked.append(modules);return self.answer
    def reset(self):
        sys.meta_path[:]=self.path;sys.modules.pop('modx',None);patch_loader._active=None;patch_loader.notice='';patch_loader.safe_mode=False;patch_loader._owner=False;patch_loader._override_ran=False
    def start(self):
        """A new run of the program. The one before it simply stopped, as a crash would."""
        self.reset();patch_loader.install('9.9')
    def value(self):
        sys.modules.pop('modx',None);sys.path.insert(0,str(self.src))
        try:
            import modx;return modx.VALUE
        finally:sys.path.remove(str(self.src));sys.modules.pop('modx',None)
    def patched(self,new=2,old=1):patch_loader.apply_patch('modx','9.9',f'VALUE={old}',f'VALUE={new}')
    def good_start(self):
        self.start();patch_loader.started()
    def kept(self,new=2,old=1):
        self.patched(new,old);self.good_start();patch_loader.confirm_trial('9.9');self.clean_exit()
    def clean_exit(self):patch_loader._clear_running(patch_loader.folder('9.9'))

class Trial(Base):
    def test_a_new_patch_loads_at_the_next_start_and_waits_for_the_user(self):
        self.patched();self.good_start();self.assertEqual(self.value(),2);self.assertEqual(patch_loader.trial_state('9.9')['modules'],['modx'])
    def test_not_confirming_undoes_it_at_the_next_start(self):
        self.patched();self.good_start();self.clean_exit();self.start();self.assertEqual(patch_loader.active(),[]);self.assertEqual(self.value(),1);self.assertIn('没有确认',patch_loader.notice);self.assertEqual(patch_loader.trial_state('9.9'),{})
    def test_a_kept_patch_stays(self):
        self.kept();self.start();self.assertEqual(self.value(),2);self.assertEqual(patch_loader.notice,'');self.assertEqual(self.asked,[])
    def test_undoing_a_new_patch_returns_to_the_one_last_kept(self):
        self.kept();self.patched(3,2);self.good_start();self.assertEqual(self.value(),3);self.clean_exit();self.start();self.assertEqual(self.value(),2);self.assertEqual(patch_loader.active(),['modx'])
    def test_undone_version_can_still_be_looked_at(self):
        self.patched();self.good_start();self.clean_exit();self.start();aside=next((self.root/'data'/'patches').glob('9.9.disabled-*'));self.assertIn('VALUE=2',(aside/'modx.py').read_text(encoding='utf8'))
    def test_a_trial_start_that_fails_is_undone_without_asking(self):
        self.patched();self.start();self.start();self.assertEqual(self.value(),1);self.assertIn('没有成功',patch_loader.notice);self.assertEqual(self.asked,[])
    def test_changing_again_gives_the_new_version_its_own_start(self):
        self.patched();self.good_start();self.patched(3,2);self.clean_exit();self.start();self.assertEqual(self.value(),3);self.assertEqual(patch_loader.notice,'')
    def test_the_user_can_undo_right_away(self):
        self.patched();self.good_start();patch_loader.rollback_trial('9.9');self.clean_exit();self.start();self.assertEqual(self.value(),1);self.assertEqual(patch_loader.notice,'')
    def test_removing_a_patch_is_on_trial_too(self):
        self.kept();patch_loader.remove_patch('modx','9.9');self.assertEqual(patch_loader.trial_state('9.9')['modules'],['modx']);self.assertIn('modx.py',[p.name for p in (patch_loader.folder('9.9')/'.confirmed').iterdir()])
    def test_a_failed_check_saves_nothing(self):
        with self.assertRaises(ValueError) as caught:patch_loader.apply_patch('modx','9.9','VALUE=1','VALUE=2',check=lambda *a:{'ok':False,'stage':'助手对话','error':'坏了'})
        self.assertIn('助手对话',str(caught.exception));self.assertEqual(patch_loader.list_patches('9.9'),[]);self.assertEqual(patch_loader.trial_state('9.9'),{})
    def test_the_check_sees_the_changed_text(self):
        seen=[];patch_loader.apply_patch('modx','9.9','VALUE=1','VALUE=2',check=lambda name,version,text:seen.append((name,version,text)) or {'ok':True});self.assertEqual(seen,[('modx','9.9','VALUE=2\n')])

class SafeMode(Base):
    def test_one_failed_start_retries_quietly(self):
        # A power cut, or being closed at the wrong moment, must not cost the user a kept fix.
        self.kept();self.start();self.start();self.assertEqual(self.asked,[]);self.assertEqual(patch_loader.active(),['modx']);self.assertEqual(self.value(),2)
    def test_failing_again_after_that_offers_it(self):
        self.kept();self.start();self.start();self.start();self.assertEqual(self.asked,[['modx']]);self.assertTrue(patch_loader.safe_mode)
    def test_safe_mode_loads_nothing_and_keeps_the_patches(self):
        self.kept();self.start();self.start();self.start();self.assertEqual(patch_loader.active(),[]);self.assertEqual(self.value(),1);self.assertEqual(patch_loader.list_patches('9.9'),['modx']);self.assertIn('安全模式',patch_loader.notice)
    def test_a_crash_then_a_failed_restart_offers_it(self):
        self.kept();self.good_start();self.start();self.assertEqual(self.asked,[]);self.start();self.assertEqual(self.asked,[['modx']])
    def test_never_offered_after_a_normal_exit_and_start(self):
        self.kept();self.good_start();self.clean_exit();self.good_start();self.clean_exit();self.good_start();self.assertEqual(self.asked,[])
    def test_never_offered_when_only_the_program_crashed_and_restart_worked(self):
        self.kept();self.good_start();self.good_start();self.good_start();self.assertEqual(self.asked,[])
    def test_never_offered_without_patches(self):
        (self.root/'data'/'patches'/'9.9').mkdir(parents=True);self.start();self.start();self.start();self.assertEqual(self.asked,[]);self.assertFalse(patch_loader.safe_mode)
    def test_it_can_turn_them_off(self):
        self.answer='disable';self.kept();self.start();self.start();self.start();self.assertEqual(patch_loader.active(),[]);self.assertFalse(patch_loader.safe_mode);self.assertEqual(patch_loader.list_patches('9.9'),[]);self.assertIn('已停用',patch_loader.notice)
    def test_it_can_just_quit(self):
        self.answer='quit';self.kept();self.start();self.start()
        with self.assertRaises(SystemExit):self.start()
    def test_after_safe_mode_a_normal_start_is_tried_and_can_recover(self):
        self.kept();self.start();self.start();self.start();self.clean_exit();self.good_start();self.assertEqual(patch_loader.active(),['modx']);self.clean_exit();self.asked.clear();self.good_start();self.assertEqual(self.asked,[])
    def test_after_safe_mode_it_is_offered_again_if_still_failing(self):
        self.kept();self.start();self.start();self.start();self.asked.clear();self.start();self.assertEqual(self.asked,[]);self.start();self.assertEqual(self.asked,[['modx']])

class OtherCopy(Base):
    def test_a_second_copy_changes_nothing(self):
        self.patched();self.good_start();state=patch_loader.trial_state('9.9')
        with patch('patch_loader._another_running',lambda:True):
            self.start();self.start()
        self.assertEqual(patch_loader.trial_state('9.9'),state);self.assertEqual(patch_loader.list_patches('9.9'),['modx']);self.assertEqual(self.asked,[]);self.assertEqual(patch_loader.notice,'')
    def test_a_second_copy_cannot_mark_a_start_good(self):
        self.patched();self.start();first=(patch_loader.folder('9.9')/'.starting').exists();self.reset()
        with patch('patch_loader._another_running',lambda:True):patch_loader.install('9.9');patch_loader.started()
        self.assertTrue(first);self.assertTrue((patch_loader.folder('9.9')/'.starting').exists())

class WhenTheCheckFails(Base):
    def test_a_new_patch_goes_back_at_once(self):
        self.kept();self.patched(3,2);self.start();text=patch_loader.health_failed();self.assertIn('撤销',text);self.assertIn('VALUE=2',(patch_loader.folder('9.9')/'modx.py').read_text(encoding='utf8'));self.clean_exit();self.start();self.assertEqual(self.value(),2);self.assertEqual(self.asked,[])
    def test_a_kept_patch_is_turned_off(self):
        self.kept();self.start();self.assertIn('撤销',patch_loader.health_failed());self.assertEqual(patch_loader.list_patches('9.9'),[])
    def test_a_copy_that_does_not_own_the_start_does_nothing(self):
        self.kept();patch_loader._active=patch_loader.folder('9.9');patch_loader._owner=False;self.assertEqual(patch_loader.health_failed(),'');self.assertEqual(patch_loader.list_patches('9.9'),['modx'])

class FileTools(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup);p=patch('agent_files.data_root',lambda:self.root/'data');p.start();self.addCleanup(p.stop);(self.root/'data'/'patches'/'9.9').mkdir(parents=True);self.install=self.root/'app';(self.install/'_internal').mkdir(parents=True)
    def test_patches_cannot_be_written_directly(self):
        for path in (self.root/'data'/'patches'/'9.9'/'x.py',self.root/'data'/'patches'):
            with self.assertRaises(PermissionError):agent_files.checked(str(path),mutation=True)
    def test_they_can_be_read(self):self.assertEqual(agent_files.checked(str(self.root/'data'/'patches'/'9.9'/'x.py')).name,'x.py')
    def test_the_installed_program_is_closed_when_frozen(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.install/'AE.exe')):
            for path in (self.install/'AE.exe',self.install/'_internal'/'a.dll',self.install,self.root):
                with self.assertRaises(PermissionError):agent_files.checked(str(path),mutation=True)
            self.assertEqual(agent_files.checked(str(self.install/'AE.exe')).name,'AE.exe')
    def test_other_files_stay_open(self):self.assertEqual(agent_files.checked(str(self.root/'other'/'x.txt'),mutation=True).name,'x.txt')
    def test_the_guard_itself_cannot_be_patched(self):
        for name in ('runtime','patch_loader','patch_check'):self.assertIn(name,patch_loader.NOT_PATCHABLE)

class TrialCard(GuiCase):
    def setUp(self):
        super().setUp();self.panel=self.window.assistant;self.root=Path(self.temp.name);self.version=self.window.assistant_env['version']
        for p in (patch('patch_loader.data_root',lambda:self.root),patch('patch_loader.source_folder',lambda:ROOT)):
            p.start();self.addCleanup(p.stop)
        patch_loader.begin_trial(self.version,'modx');self.state=patch_loader.trial_state(self.version)
    def buttons(self):return {b.text():b for b in self.panel.trial_card.findChildren(QPushButton)}
    def test_keeping(self):
        self.panel.show_trial(self.state);self.buttons()['保留修改'].click();self.assertEqual(patch_loader.trial_state(self.version),{});self.assertIsNone(self.panel.trial_card);self.assertIsNone(self.panel.trial)
    def test_undoing(self):
        (patch_loader.folder(self.version)/'modx.py').write_text('x=1',encoding='utf8');self.panel.show_trial(self.state);self.buttons()['撤销修改'].click();self.assertEqual(patch_loader.trial_state(self.version),{});self.assertEqual(patch_loader.list_patches(self.version),[])
    def test_card_survives_a_new_conversation(self):
        self.panel.show_trial(self.state);self.panel.new_chat();self.assertIsNotNone(self.panel.trial_card)
    def test_the_assistant_cannot_keep_it_for_the_user(self):
        names=[t.name for t in self.panel.host.all_tools()];self.assertFalse([n for n in names if 'confirm' in n or 'keep' in n]);self.assertTrue(self.window.assistant.host.all_tools())
    def test_the_assistant_is_told_where_things_stand(self):
        self.panel.agent.messages=[{'role':'user','content':'修一下'},{'role':'assistant','content':'{"say":"好"}'}];self.panel.show_trial(self.state);self.assertIn('等用户确认',self.panel.agent.messages[-2]['content']);self.assertEqual(self.panel.agent.messages[-1]['role'],'assistant')

class Health(GuiCase):
    def run_check(self):
        out=[];patch_check.health(self.window,lambda ok,detail:out.append((ok,detail)));end=time.time()+15
        while time.time()<end and not out:
            QApplication.processEvents();time.sleep(.005)
        self.assertTrue(out,'超时');return out[0]
    def test_a_working_assistant_passes_and_leaves_no_trace(self):
        self.window.settings.key='sk-real-value';seen=[];real=patch_check.ScriptedService.__call__
        def spy(service,request,timeout=None):
            seen.append(request);return real(service,request,timeout)
        with patch.object(patch_check.ScriptedService,'__call__',spy):ok,detail=self.run_check()
        self.assertTrue(ok,detail);self.assertEqual(self.window.assistant.agent.messages,[]);self.assertEqual(self.window.assistant.items,[]);self.assertEqual(len(seen),2)
        for request in seen:self.assertNotIn('sk-real-value',str(request.headers)+str(request.data))
    def test_a_dead_assistant_fails(self):
        with patch('agent_core.chat_turn',side_effect=TypeError('坏了')):ok,detail=self.run_check()
        self.assertFalse(ok);self.assertIn('坏了',detail)
    def test_a_broken_reader_fails(self):
        with patch('agent_core.parse_reply',lambda text:('','',[])):ok,_=self.run_check()
        self.assertFalse(ok)
    def test_a_broken_tool_fails(self):
        with patch.object(self.window.assistant.host,'prompt',side_effect=RuntimeError('提示词坏了')):ok,detail=self.run_check()
        self.assertFalse(ok)

class Preflight(unittest.TestCase):
    """The real thing: another copy of the program, from source."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);p=patch('patch_loader.data_root',lambda:Path(self.temp.name));p.start();self.addCleanup(p.stop)
    def try_patch(self,module,old,new):
        text=(ROOT/f'{module}.py').read_text(encoding='utf8');self.assertEqual(text.count(old),1,old);return patch_check.preflight(module,'9.9',text.replace(old,new))
    def test_a_harmless_change_passes(self):
        result=self.try_patch('agent_core',"MAX_STEPS=16;","MAX_STEPS=17;");self.assertTrue(result['ok'],result)
    def test_a_change_that_breaks_only_the_assistant_is_caught(self):
        # The window would come up fine: this is what a check of the window alone cannot see.
        result=self.try_patch('agent_core',"say,mood,calls=parse_reply(reply);understood","say,mood,calls=parse_replyy(reply);understood");self.assertFalse(result['ok']);self.assertEqual(result['stage'],'助手对话');self.assertIn('parse_replyy',result['error'])
    def test_a_change_that_breaks_the_request_is_caught(self):
        result=self.try_patch('llm_client',"fields=effort_fields(chosen,config.get('effort','off'));pictures=","fields=effort_fieldss(chosen,config.get('effort','off'));pictures=");self.assertFalse(result['ok']);self.assertEqual(result['stage'],'助手对话')
    def test_a_change_that_breaks_the_panel_is_caught(self):
        result=self.try_patch('assistant_panel',"self.drawer=GlassDrawer(self.central,","self.drawer=GlassDrawerr(self.central,");self.assertFalse(result['ok']);self.assertIn(result['stage'],('打开窗口','打开助手面板'))
    def test_a_module_that_cannot_be_imported_is_caught(self):
        result=patch_check.preflight('ui_kit','9.9','import no_such_module_at_all\n');self.assertFalse(result['ok']);self.assertEqual(result['stage'],'启动');self.assertIn('no_such_module_at_all',result['error'])
    def test_the_real_settings_and_data_stay_out(self):
        marker=Path(self.temp.name)/'patches';result=self.try_patch('agent_core',"MAX_STEPS=16;","MAX_STEPS=18;");self.assertTrue(result['ok'],result);self.assertFalse(marker.exists())

if __name__=='__main__':unittest.main()
