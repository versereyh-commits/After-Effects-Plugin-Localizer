"""Shared JSON writing, the trimmed app.py, and closing without waiting for an update check."""
import json,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_gui import GuiCase
import app,app_live,runtime,updater

class WriteJsonTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_writes_through_a_temporary_file_and_creates_folders(self):
        path=self.root/'a'/'b'/'设置.json';runtime.write_json_atomic(path,{'名称':'辉光'})
        self.assertEqual(json.loads(path.read_text(encoding='utf8')),{'名称':'辉光'});self.assertIn('辉光',path.read_text(encoding='utf8'))
        runtime.write_json_atomic(path,{'名称':'辉光'},ensure_ascii=True,indent=None)
        self.assertEqual(path.read_text(encoding='utf8'),json.dumps({'\u540d\u79f0':'\u8f89\u5149'}));self.assertTrue(path.read_text(encoding='utf8').isascii())
        self.assertEqual([p.name for p in path.parent.iterdir()],['设置.json'])
    def test_a_failed_write_keeps_the_old_file_and_leaves_no_temporary(self):
        path=self.root/'journal.json';runtime.write_json_atomic(path,{'entries':1})
        with patch.object(runtime.os,'replace',side_effect=OSError('disk full')),self.assertRaises(OSError):runtime.write_json_atomic(path,{'entries':2})
        with self.assertRaises(TypeError):runtime.write_json_atomic(path,{'bad':object()})
        self.assertEqual(json.loads(path.read_text(encoding='utf8')),{'entries':1});self.assertEqual([p.name for p in self.root.iterdir()],['journal.json'])
    def test_every_writer_uses_it(self):
        import live_names,shortcuts,token_usage,settings_store,local_adapters,ae_paths,adapter_host,adapter_analysis
        self.assertIs(live_names._save,runtime.write_json_atomic);self.assertIs(app_live.write_json,runtime.write_json_atomic)
        for module in (shortcuts,token_usage,settings_store,local_adapters,ae_paths,adapter_host,adapter_analysis):
            self.assertIs(module.write_json_atomic,runtime.write_json_atomic,module.__name__)

class AppModuleTests(unittest.TestCase):
    def test_only_the_shared_helpers_remain(self):
        self.assertFalse(hasattr(app,'Window'));self.assertFalse(hasattr(app,'STYLE'))
        self.assertTrue(callable(app.apply_theme));self.assertTrue(callable(app.Job))

class CloseDuringUpdateCheckTests(GuiCase):
    def test_close_does_not_wait_for_a_slow_check(self):
        w=self.window;release=threading.Event();self.addCleanup(release.set)
        with patch.object(app_live.updater,'configured',return_value=True),patch.object(app_live.updater,'check',side_effect=lambda version:release.wait(10) and None):
            w.check_update(manual=False);self.assertTrue(w.update_job.isRunning())
            started=time.monotonic();w.close();self.assertLess(time.monotonic()-started,1.0)
        self.assertTrue(w.update_job.thread.daemon)
        release.set();self.assertTrue(w.update_job.wait(3000))
    def test_results_arrive_on_the_window_thread(self):
        w=self.window;seen=[]
        with patch.object(app_live.updater,'configured',return_value=True),patch.object(app_live.updater,'check',return_value=None),\
             patch.object(w,'found_update',side_effect=lambda offer,manual,save=False:seen.append(threading.current_thread() is threading.main_thread())):
            w.check_update(manual=False);self.assertTrue(w.update_job.wait(3000));self.app.processEvents()
        self.assertEqual(seen,[True])

if __name__=='__main__':unittest.main()
