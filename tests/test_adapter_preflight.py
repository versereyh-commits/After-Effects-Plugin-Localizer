"""Read-only preflight speedups must keep host and certificate gates intact."""
import ctypes as C,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import adapter_host,host_identity
from adapter_host import HostBridge
from test_gui import GuiCase
from PySide6.QtWidgets import QDialog

class PidEnumerationTests(unittest.TestCase):
    def scan(self,entries,*,first_error=None,next_error=18):
        closed=[];position=[0]
        def fill(pointer,index):
            entry=pointer._obj;pid,name,mode=entries[index];entry.th32ProcessID=pid;entry.szExeFile=name;return 1
        def first(snapshot,pointer):
            if first_error is not None:C.set_last_error(first_error);return 0
            if not entries:C.set_last_error(18);return 0
            return fill(pointer,0)
        def next_(snapshot,pointer):
            position[0]+=1
            if position[0]>=len(entries):C.set_last_error(next_error);return 0
            return fill(pointer,position[0])
        def open_(access,inherit,pid):
            self.assertEqual(access,0x1000);mode=next(row[2] for row in entries if row[0]==pid)
            if mode in ('denied','gone'):C.set_last_error(5 if mode=='denied' else 87);return 0
            return pid+1000
        def exit_(handle,pointer):
            mode=next(row[2] for row in entries if row[0]==handle-1000)
            pointer._obj.value=0 if mode=='ended' else 259;return 0 if mode=='unknown' else 1
        kernel=SimpleNamespace(CreateToolhelp32Snapshot=Mock(return_value=99),Process32FirstW=Mock(side_effect=first),Process32NextW=Mock(side_effect=next_),OpenProcess=Mock(side_effect=open_),GetExitCodeProcess=Mock(side_effect=exit_),CloseHandle=Mock(side_effect=closed.append))
        with patch.object(host_identity.C,'WinDLL',return_value=kernel):
            try:return host_identity.running_pids()
            finally:self.assertIn(99,closed)
    def test_only_afterfx_case_insensitive(self):self.assertEqual(self.scan([(1,'AfterFX.exe','live'),(2,'other.exe','live'),(3,'AFTERFX.EXE','live')]),{1,3})
    def test_inaccessible_and_unknown_liveness_remain_ambiguous(self):self.assertEqual(self.scan([(1,'AfterFX.exe','denied'),(2,'AfterFX.exe','unknown')]),{1,2})
    def test_confirmed_exited_entries_can_be_ignored(self):self.assertEqual(self.scan([(1,'AfterFX.exe','ended'),(2,'AfterFX.exe','gone')]),set())
    def test_empty_snapshot(self):self.assertEqual(self.scan([]),set())
    def test_enumeration_errors_are_not_empty_success(self):
        with self.assertRaises(OSError):self.scan([],first_error=5)
        with self.assertRaises(OSError):self.scan([(1,'AfterFX.exe','live')],next_error=5)

class BoundHostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        with patch('adapter_host.bridge_root',return_value=self.root),patch('adapter_host.file_version',return_value='99.0'):
            self.bridge=HostBridge(self.root/'AfterFX.exe',self.root/'adapter-qa/run')
        self.bridge.identity=(123,456);self.target=Mock(pid=123,image=str(self.bridge.exe),creation_time=456)
        for name,value in [('Process',self.target),('file_version','99.0')]:
            p=patch.object(adapter_host,name,return_value=value);p.start();self.addCleanup(p.stop)
    def test_bound_single_host_skips_expensive_probe(self):
        with patch.object(adapter_host,'running_pids',return_value={123}),patch.object(adapter_host,'native_probe') as probe:
            self.assertIs(self.bridge.process(),self.target);probe.assert_not_called()
    def test_first_bind_still_uses_full_discovery(self):
        self.bridge.identity=None
        with patch.object(adapter_host,'running_pids') as pids,patch.object(adapter_host,'native_probe',return_value={'processes':[{'pid':123,'imagePath':str(self.bridge.exe)}]}) as probe:
            self.bridge.process();pids.assert_not_called();probe.assert_called_once()
    def test_second_host_is_still_rejected_before_open(self):
        with patch.object(adapter_host,'running_pids',return_value={123,789}),patch.object(adapter_host,'native_probe',return_value={'processes':[{'pid':123},{'pid':789,'error':'denied'}]}),patch.object(adapter_host,'Process') as opened:
            with self.assertRaisesRegex(RuntimeError,'只运行一个'):self.bridge.process()
            opened.assert_not_called()
    def test_failed_lightweight_snapshot_uses_original_guard(self):
        with patch.object(adapter_host,'running_pids',side_effect=OSError('denied')),patch.object(adapter_host,'native_probe',return_value={'processes':[]}):
            with self.assertRaisesRegex(RuntimeError,'没有检测到'):self.bridge.process()
    def test_pid_reuse_never_rebinds(self):
        self.target.creation_time=999
        with patch.object(adapter_host,'running_pids',return_value={123}):
            with self.assertRaisesRegex(RuntimeError,'重新启动'):self.bridge.process()
        self.target.close.assert_called_once()
    def test_bound_path_change_is_rejected(self):
        self.target.image=str(self.root/'other/AfterFX.exe')
        with patch.object(adapter_host,'running_pids',return_value={123}):
            with self.assertRaisesRegex(RuntimeError,'路径或版本'):self.bridge.process()
    def test_bound_version_change_is_rejected(self):
        with patch.object(adapter_host,'running_pids',return_value={123}),patch.object(adapter_host,'file_version',return_value='100'):
            with self.assertRaisesRegex(RuntimeError,'路径或版本'):self.bridge.process()

class ExistingAdapterTests(GuiCase):
    def setUp(self):
        super().setUp()
        from adapter_dialog import AdapterAnalysisDialog
        self.dialog=AdapterAnalysisDialog(self.window,self.window.space);self.addCleanup(self.dialog.deleteLater)
    def test_existing_profile_offers_connect_without_starting_work(self):
        self.assertTrue(self.dialog.existing_btn.isVisibleTo(self.dialog))
        with patch.object(self.dialog,'can_analyze',return_value=True),patch.object(self.window,'work') as work:
            self.dialog.connect_existing();work.assert_not_called()
        self.assertEqual(self.dialog.result(),QDialog.DialogCode.Accepted)
    def test_unknown_profile_cannot_use_direct_connect(self):
        self.dialog.space.live=False
        with patch.object(self.dialog,'can_analyze') as guard:self.dialog.connect_existing();guard.assert_not_called()
        self.assertNotEqual(self.dialog.result(),QDialog.DialogCode.Accepted)
    def test_pending_recovery_prevents_direct_connect(self):
        with patch('adapter_validation.recover_pending',return_value=SimpleNamespace(held=True)):
            self.dialog.connect_existing()
        self.assertNotEqual(self.dialog.result(),QDialog.DialogCode.Accepted);self.assertTrue(self.dialog.restore_qa_btn.isEnabled())
    def test_busy_does_not_accept_direct_connect(self):
        self.dialog.running=True;self.dialog.connect_existing();self.assertNotEqual(self.dialog.result(),QDialog.DialogCode.Accepted);self.dialog.running=False
    def test_owner_uses_existing_connection_and_not_validation(self):
        with patch('adapter_dialog.AdapterAnalysisDialog') as factory,patch.object(self.window,'connect_live') as connect,patch.object(self.window,'choose_version') as choose:
            factory.return_value.exec.return_value=QDialog.DialogCode.Accepted
            self.window.analyze_adapter(self.window.space);connect.assert_called_once();choose.assert_called_once()
    def test_owner_cancel_never_connects(self):
        with patch('adapter_dialog.AdapterAnalysisDialog') as factory,patch.object(self.window,'connect_live') as connect:
            factory.return_value.exec.return_value=QDialog.DialogCode.Rejected
            self.window.analyze_adapter(self.window.space);connect.assert_not_called()

if __name__=='__main__':unittest.main()
