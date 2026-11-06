"""No live host, credentials, registry or network: regression cases from the 1.4.3 review."""
import codecs,json,os,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import agent_core,agent_files,patch_loader,assistant_store,llm_client
from test_assistant import Host,Events
from test_gui import GuiCase
from app_live import VersionSpace
from PySide6.QtGui import QCloseEvent

class PermissionChecks(unittest.TestCase):
    def test_permission_is_rechecked_after_confirmation(self):
        called=[];tool=agent_core.Tool('write','',lambda a:called.append(1),level='ae',confirm=lambda a:'write?');host=Host([tool],'ae')
        def confirm(*args):host.lvl='read';return True
        host.confirm=confirm
        self.assertFalse(agent_core.Agent(host,Events()).call({'tool':'write'})['ok']);self.assertEqual(called,[])
    def test_cancel_during_confirmation(self):
        called=[];tool=agent_core.Tool('write','',lambda a:called.append(1),level='ae',confirm=lambda a:'write?');host=Host([tool],'ae');agent=agent_core.Agent(host,Events())
        def confirm(*args):agent.stop=True;return True
        host.confirm=confirm
        self.assertFalse(agent.call({'tool':'write'})['ok']);self.assertEqual(called,[])

class Files(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.data=self.root/'data';self.data.mkdir()
        p=patch('agent_files.data_root',lambda:self.data);p.start();self.addCleanup(p.stop)
    def test_key_parent_cannot_be_moved_deleted_or_backed_up(self):
        (self.data/'api-settings.json').write_text('fake')
        for call in (lambda:agent_files.move_path(str(self.data),str(self.root/'moved')),lambda:agent_files.delete_path(str(self.data)),lambda:agent_files.backup(self.data)):
            with self.assertRaises(PermissionError):call()
        self.assertEqual((self.data/'api-settings.json').read_text(),'fake');self.assertFalse((self.root/'moved').exists())
    def test_key_hard_link_is_blocked(self):
        key=self.data/'api-settings.json';key.write_text('fake');alias=self.root/'alias'
        try:os.link(key,alias)
        except OSError:self.skipTest('hard links unavailable')
        with self.assertRaises(PermissionError):agent_files.read_file(str(alias))
        d=self.root/'tree';d.mkdir();os.link(key,d/'alias')
        with self.assertRaises(PermissionError):agent_files.move_path(str(d),str(self.root/'out'))
    def test_backup_cannot_include_itself_even_without_key_file(self):
        with patch('agent_files.closed_paths',lambda:[]):
            with self.assertRaises(PermissionError):agent_files.backup(self.data)
        self.assertFalse(agent_files.backups().exists())
    def test_backup_tree_cannot_be_modified(self):
        p=agent_files.backups()/'old';p.parent.mkdir();p.write_text('keep')
        with self.assertRaises(PermissionError):agent_files.delete_path(str(p))
        self.assertEqual(p.read_text(),'keep')
    def test_bom_old_encoding_and_line_endings_survive_replace(self):
        for i,raw in enumerate((codecs.BOM_UTF8+b'old\r\n',b'old\r\n','old中文\r\n'.encode('gbk'))):
            f=self.root/f'{i}.jsx';f.write_bytes(raw);agent_files.replace_in_file(str(f),'old','new');self.assertEqual(f.read_bytes(),raw.replace(b'old',b'new'))
    def test_read_and_replace_refuse_large_input(self):
        f=self.root/'large';f.write_bytes(b'x'*65)
        with patch('agent_files.MAX_FILE_BYTES',64):
            for call in (lambda:agent_files.read_file(str(f)),lambda:agent_files.replace_in_file(str(f),'x','y')):
                with self.assertRaises(ValueError):call()
        self.assertEqual(f.stat().st_size,65)
    def test_read_clamps_negative_slice_arguments(self):
        f=self.root/'x';f.write_text('abc');self.assertEqual(agent_files.read_file(str(f),-2,-1)['text'],'a')
    def test_directory_backup_limit_fails_before_copy(self):
        d=self.root/'tree';d.mkdir();(d/'file').write_text('abc')
        with patch('agent_files.MAX_BACKUP_BYTES',1),patch('agent_files.shutil.copytree') as copy:
            with self.assertRaises(ValueError):agent_files.delete_path(str(d))
            copy.assert_not_called()
        self.assertTrue(d.exists())

class Patches(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.src=self.root/'src';self.src.mkdir();(self.src/'modx.py').write_text('VALUE=1')
        for p in (patch('patch_loader.data_root',lambda:self.root/'data'),patch('patch_loader.source_folder',lambda:self.src)):
            p.start();self.addCleanup(p.stop)
    def test_patch_and_removal_can_be_undone_in_order(self):
        patch_loader.apply_patch('modx','1','VALUE=1','VALUE=2');r=patch_loader.apply_patch('modx','1','VALUE=2','VALUE=3')
        self.assertEqual(Path(r['backup']).read_text(),'VALUE=2')
        patch_loader.remove_patch('modx','1');patch_loader.restore_patch('modx','1');self.assertEqual(patch_loader.read_source('modx','1'),'VALUE=3')
        patch_loader.restore_patch('modx','1');self.assertEqual(patch_loader.read_source('modx','1'),'VALUE=2')
        patch_loader.restore_patch('modx','1');self.assertEqual(patch_loader.read_source('modx','1'),'VALUE=1')
    def test_external_change_and_damaged_backup_are_not_overwritten(self):
        patch_loader.apply_patch('modx','1','VALUE=1','VALUE=2');r=patch_loader.apply_patch('modx','1','VALUE=2','VALUE=3');f=patch_loader.folder('1')/'modx.py'
        f.write_text('VALUE=4')
        with self.assertRaises(ValueError):patch_loader.restore_patch('modx','1')
        f.write_text('VALUE=3');Path(r['backup']).write_text('corrupt')
        with self.assertRaises(ValueError):patch_loader.restore_patch('modx','1')
        self.assertEqual(f.read_text(),'VALUE=3')
    def test_journal_failure_prevents_patch_change(self):
        patch_loader.apply_patch('modx','1','VALUE=1','VALUE=2')
        with patch('patch_loader.write_json_atomic',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):patch_loader.apply_patch('modx','1','VALUE=2','VALUE=3')
        self.assertEqual(patch_loader.read_source('modx','1'),'VALUE=2')
    def test_remove_and_restore_validate_module_names(self):
        for name in ('../elsewhere','a/b','runtime','patch_loader'):
            for fn in (patch_loader.remove_patch,patch_loader.restore_patch):
                with self.assertRaises((ValueError,PermissionError)):fn(name,'1')

class History(unittest.TestCase):
    def test_index_reuses_metadata_and_detects_external_changes(self):
        with tempfile.TemporaryDirectory() as t:
            store=assistant_store.ChatStore(Path(t));store.save({'id':'a','title':'old','messages':[],'items':[{'kind':'user'}]})
            original=Path.read_text;reads=[]
            def read(p,*a,**k):
                if p.suffix=='.json':reads.append(p.name)
                return original(p,*a,**k)
            with patch.object(Path,'read_text',read):
                self.assertEqual(store.list()[0]['title'],'old');cold=assistant_store.ChatStore(Path(t));self.assertEqual(cold.list()[0]['count'],1)
                self.assertEqual(reads,[])
                store.path('a').write_text(json.dumps({'title':'changed externally','items':[],'updated':10}))
                self.assertEqual(cold.list()[0]['title'],'changed externally');self.assertEqual(reads,['a.json'])
    def test_damaged_index_rebuilds_and_does_not_delete_damaged_chats(self):
        with tempfile.TemporaryDirectory() as t:
            s=assistant_store.ChatStore(Path(t));s.save({'id':'a','messages':[],'items':[]});s.index.write_text('bad');(s.folder/'bad.json').write_text('bad')
            self.assertEqual(len(assistant_store.ChatStore(Path(t)).list()),1);self.assertTrue((s.folder/'bad.json').exists())

class Network(unittest.TestCase):
    def test_cancel_returns_without_accepting_late_network_response(self):
        entered=threading.Event();release=threading.Event();stopped=threading.Event();finished=threading.Event();results=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):finished.set()
            def read(self,n):entered.set();release.wait(3);return b'late'
        def run():
            try:results.append(llm_client._chat_response(None,lambda *a,**k:Response(),10,stopped.is_set))
            except llm_client.ChatError as error:results.append(error.kind)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(2));stopped.set();thread.join(1);self.assertFalse(thread.is_alive());self.assertEqual(results,['stopped'])
        finally:release.set();thread.join(3);finished.wait(3)
    def test_inflight_requests_are_bounded(self):
        with patch('llm_client._assistant_requests',threading.BoundedSemaphore(0)):
            with self.assertRaises(llm_client.ChatError):llm_client._chat_response(None,None,1,lambda:False)

class GuiHardening(GuiCase):
    def other_space(self):return VersionSpace('other','Other','99',None,False,Path(self.temp.name)/'other')
    def test_cross_version_undo_refuses_without_touching_current_data(self):
        w=self.window;h=w.assistant.host;old=w.space;h.snapshot();w.pending['Atom Key']='A';h.end_turn();w.bind_space(self.other_space());w.pending['B-ONLY']='keep'
        self.assertFalse(h.undo()['undone']);self.assertEqual(w.pending['B-ONLY'],'keep');w.bind_space(old);self.assertTrue(h.undo()['undone']);self.assertNotIn('Atom Key',w.pending)
    def test_turn_cannot_follow_version_switch(self):
        p=self.window.assistant;h=p.host;h.begin_turn();p.in_turn=True;self.addCleanup(lambda:setattr(p,'in_turn',False));self.window.bind_space(self.other_space());called=[]
        with self.assertRaises(RuntimeError):h.dispatch(agent_core.Tool('x','',lambda a:called.append(1)),{})
        self.assertEqual(called,[])
    def test_turn_cannot_follow_host_restart(self):
        p=self.window.assistant;w=self.window;w.live={'pid':10,'creationTime':1};p.host.begin_turn();p.in_turn=True;self.addCleanup(lambda:setattr(p,'in_turn',False));w.live={'pid':10,'creationTime':2}
        with self.assertRaises(RuntimeError):p.host.guard()
    def test_cancelled_queued_ui_operation_never_runs_late(self):
        p=self.window.assistant;h=p.host;queued=[];errors=[];called=[];h.bridge=SimpleNamespace(call=SimpleNamespace(emit=queued.append))
        def work():
            try:h.on_main(lambda:called.append(1))
            except RuntimeError as error:errors.append(str(error))
        t=threading.Thread(target=work);t.start()
        deadline=time.monotonic()+2
        while not queued and time.monotonic()<deadline:time.sleep(.01)
        p.agent.stop=True;t.join(2)
        self.assertFalse(t.is_alive());queued[0]();self.assertEqual(called,[]);self.assertTrue(errors)
    def test_queued_ui_write_rechecks_permission_before_execution(self):
        p=self.window.assistant;h=p.host;p.level='ae';h.dispatch_tool=agent_core.Tool('write','',None,level='ae');queued=[];errors=[];called=[]
        h.bridge=SimpleNamespace(call=SimpleNamespace(emit=queued.append))
        def work():
            try:h.on_main(lambda:called.append(1))
            except PermissionError as error:errors.append(str(error))
        t=threading.Thread(target=work);t.start();deadline=time.monotonic()+2
        while not queued and time.monotonic()<deadline:time.sleep(.01)
        try:p.level='read';queued[0]();t.join(2);self.assertFalse(t.is_alive());self.assertEqual(called,[]);self.assertTrue(errors)
        finally:p.agent.stop=True;t.join(2)
    def test_exit_waits_for_assistant_before_accepting_close(self):
        w=self.window;p=w.assistant;w.tray=None;w.quitting=True;p.in_turn=True;p.running=True;event=QCloseEvent()
        w.closeEvent(event);self.assertFalse(event.isAccepted());self.assertTrue(p.agent.stop);self.assertTrue(w._stopping.is_set());p.in_turn=False;p.running=False

if __name__=='__main__':unittest.main()
