"""Adapter behaviour on machines unlike the development host (0.7.8)."""
from pathlib import Path
from types import SimpleNamespace
import tempfile,unittest
from unittest.mock import Mock,patch
from adapter_resolver import Resolver,run_static_analysis
from adapter_validation import ValidationRun,identity,DRIZZLE_LABELS
from adapter_host import HostBridge
from runtime import legacy_text_problem
from test_gui import GuiCase

class FailureKindTests(unittest.TestCase):
    def resolver(self):
        r=Mock();r.version='99.0.0.1';r.modules={'FLT.dll':{'sha256':'a'},'TDB.dll':{'sha256':'b'}}
        r.evidence={'E1':{'field':'name_get','value':1,'evidenceId':'E1'}};r.getters.return_value=list(r.evidence.values());return r
    def test_each_failure_stage_has_a_kind(self):
        self.assertEqual(run_static_analysis('x',resolver_factory=Mock(side_effect=ValueError('missing')))['failureKind'],'module')
        r=self.resolver();r.complete_static.side_effect=ValueError('layout')
        self.assertEqual(run_static_analysis('x',resolver_factory=lambda _:r)['failureKind'],'structure')
        r=self.resolver();r.verify_files.side_effect=ValueError('changed')
        self.assertEqual(run_static_analysis('x',resolver_factory=lambda _:r)['failureKind'],'transient')
    def test_missing_interface_is_named_instead_of_a_bare_key(self):
        r=Resolver.__new__(Resolver);r.getters=lambda:[{'field':'name_get','value':1}]
        with self.assertRaisesRegex(ValueError,'未找到唯一的名称接口：.*count_get'):r.complete_static('')

class FailureGuideTests(GuiCase):
    def setUp(self):
        super().setUp()
        from adapter_dialog import AdapterAnalysisDialog
        self.dialog=AdapterAnalysisDialog(self.window,self.window.space);self.addCleanup(self.dialog.deleteLater)
    def done(self,kind):
        report={'state':'blocked','analysisSource':'code','summary':'failed','candidateReadConfig':{},'missing':[],'trace':[],'failureKind':kind}
        with patch('adapter_dialog.save_report',return_value='r'):self.dialog.analysis_done(report)
    def test_structure_gap_offers_ai_only_as_a_diagnosis(self):
        self.done('structure');self.assertTrue(self.dialog.ai_btn.isEnabled());self.assertEqual(self.dialog.ai_btn.text(),'AI 诊断原因')
        self.assertIn('更新识别规则',self.dialog.status.text());self.assertIn('不能启用',self.dialog.output.toPlainText())
    def test_unreadable_or_changing_modules_do_not_offer_ai(self):
        for kind,text in (('module','AfterFX.exe'),('transient','重试自动识别')):
            self.done(kind);self.assertFalse(self.dialog.ai_btn.isEnabled());self.assertFalse(self.dialog.ai_btn.isVisibleTo(self.dialog));self.assertIn(text,self.dialog.status.text())
            self.assertEqual(self.dialog.start_btn.text(),'重试自动识别')
    def test_ai_explanation_survives_a_failed_completion(self):
        self.dialog.offline_failure={'summary':'x','failureStage':'s','evidence':[]}
        report={'state':'candidate','summary':'AI 认为容器偏移改变','candidateReadConfig':{'name_get':1},'fieldEvidence':{'name_get':'E1'}}
        with patch('adapter_dialog.run_analysis',return_value=report),patch('adapter_dialog.Resolver') as resolver:
            resolver.return_value.complete_static.side_effect=ValueError('layout');result=self.dialog.ai_work(None,{})
        self.assertEqual(result['state'],'blocked');self.assertIn('更新识别规则',result['summary']);self.assertIn('容器偏移改变',result['summary'])

class LocaleTests(unittest.TestCase):
    def test_non_chinese_code_page_is_reported_with_the_fix(self):
        message=legacy_text_problem(['适配验证效果'],encoding='cp1252')
        self.assertIn('中文(简体，中国)',message);self.assertIn('未写入',message)
        self.assertIsNone(legacy_text_problem(['适配验证效果'],encoding='gbk'))

class LabelTests(unittest.TestCase):
    def run_object(self,fx='CC Drizzle',match='CC Drizzle-0001'):
        run=ValidationRun.__new__(ValidationRun);run.fixture={'parameterEffect':fx,'parameterMatch':match};return run
    def test_only_streams_present_in_this_build_are_renamed(self):
        available={identity('CC Drizzle','CC Drizzle-'+k) for k in ('0001','0002','0005')}
        labels=self.run_object().parameter_labels(available)
        self.assertEqual(set(labels),available);self.assertEqual(labels[identity('CC Drizzle','CC Drizzle-0001')],DRIZZLE_LABELS['0001'])
    def test_missing_edit_target_stops_before_writing(self):
        with self.assertRaisesRegex(RuntimeError,'不可识别'):self.run_object().parameter_labels({identity('CC Drizzle','CC Drizzle-0002')})
        with self.assertRaisesRegex(RuntimeError,'不可识别'):self.run_object('ADBE Gaussian Blur 2','ADBE Gaussian Blur 2-0001').parameter_labels(set())
    def test_fallback_effect_keeps_its_single_label(self):
        key=identity('ADBE Gaussian Blur 2','ADBE Gaussian Blur 2-0001')
        self.assertEqual(self.run_object('ADBE Gaussian Blur 2','ADBE Gaussian Blur 2-0001').parameter_labels({key}),{key:'适配参数'})

class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'ae-bridge'
        for target,value in (('adapter_host.bridge_root',self.root),('adapter_host.file_version','24.2.1.2')):
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
    def test_read_only_install_folder_has_a_plain_message(self):
        with patch('pathlib.Path.mkdir',side_effect=PermissionError(13,'denied')):
            with self.assertRaisesRegex(RuntimeError,'不可写'):HostBridge('AfterFX.exe',self.root/'adapter-qa'/'run')
    def test_script_never_entered_points_at_dialogs_and_file_access(self):
        bridge=HostBridge('AfterFX.exe',self.root/'adapter-qa'/'run')
        process=Mock();process.__enter__=Mock(return_value=SimpleNamespace(pid=42));process.__exit__=Mock(return_value=False)
        relay=Mock(pid=7);relay.poll.return_value=0;clock=iter([0,100,100,100])
        with patch.object(bridge,'process',return_value=process),patch('adapter_host.subprocess.Popen',return_value=relay),patch('adapter_host.time.monotonic',side_effect=lambda:next(clock)):
            with self.assertRaisesRegex(TimeoutError,'没有开始执行验证脚本.*允许脚本写入文件和访问网络'):bridge.call('status')
        relay.terminate.assert_not_called()

if __name__=='__main__':unittest.main()
