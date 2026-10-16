import os,tempfile,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from PySide6.QtWidgets import QApplication,QWidget
import ae_path_dialog,core

class PathDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.owner=QWidget();self.owner.reload_installations=Mock();self.addCleanup(self.owner.deleteLater)
        self.report={'name':'AE 2025','version':'25.3.0.71','exe':str(self.root/'AfterFX.exe'),'plugins':str(self.root/'Plug-ins'),'sources':['手动定位'],'status':'未适配实时写入','effects':False,'parameters':False,'hostVerified':False}
        for module,name,value in ((core,'installations',lambda:[{}]),(ae_path_dialog,'compatibility',lambda item,reader:dict(self.report)),(ae_path_dialog,'data_root',lambda:self.root)):
            handle=patch.object(module,name,value);handle.start();self.addCleanup(handle.stop)
        self.dialog=ae_path_dialog.AEPathDialog(self.owner);self.addCleanup(self.dialog.deleteLater)
        self.assertTrue(self.dialog.job.wait(3000));self.app.processEvents()
    def test_worker_delivers_path_and_status_and_enables_buttons(self):
        self.assertEqual(self.dialog.table.item(0,1).text(),self.report['exe']);self.assertIn('未适配',self.dialog.table.item(0,3).text())
        self.assertTrue(all(button.isEnabled() for button in self.dialog.buttons))
    def test_choose_detected_ae_rebinds_owner(self):
        self.dialog.use_selected();self.owner.reload_installations.assert_called_once_with(self.report['exe'])
    def test_export_only_contains_readonly_report(self):
        path=self.root/'report.json'
        with patch.object(ae_path_dialog.QFileDialog,'getSaveFileName',return_value=(str(path),'')):self.dialog.export_report()
        import json
        value=json.loads(path.read_text(encoding='utf8'));self.assertFalse(value['hostVerified']);self.assertEqual(value['mode'],'read_only')
        self.assertEqual(value['installations'],[self.report])
    def test_empty_detection_disables_selection_but_keeps_manual_locator(self):
        self.dialog.loaded([]);self.dialog.finish_job()
        self.assertFalse(self.dialog.use.isEnabled());self.assertFalse(self.dialog.export.isEnabled());self.assertTrue(self.dialog.folder.isEnabled())
    def test_escape_keeps_active_detection_alive(self):
        rejected=[];self.dialog.rejected.connect(lambda:rejected.append(True));self.dialog.job=SimpleNamespace(isRunning=lambda:True)
        self.dialog.reject();self.assertEqual(rejected,[]);self.assertIn('等待',self.dialog.status.text());self.dialog.job=None

if __name__=='__main__':unittest.main()
