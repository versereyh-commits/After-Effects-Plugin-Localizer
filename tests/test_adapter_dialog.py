from types import SimpleNamespace
import threading
from unittest.mock import patch,Mock
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QPushButton
from test_gui import GuiCase
from adapter_dialog import AdapterAnalysisDialog

class AdapterDialogTests(GuiCase):
    def setUp(self):
        super().setUp();self.dialog=AdapterAnalysisDialog(self.window,self.window.space);self.addCleanup(self.dialog.deleteLater)
    def test_entry_exists_for_unverified_versions(self):
        self.window.space.live=False;self.window.update_buttons();self.assertTrue(self.window.analyze_btn.isEnabled())
    def test_unknown_connection_opens_analysis_without_runtime_access(self):
        self.window.space.live=False
        with patch.object(self.window,'analyze_adapter') as analyze,patch.object(self.window,'work') as work:
            self.window.connect_live();analyze.assert_called_once_with(self.window.space);work.assert_not_called()
    def test_start_snapshots_config_and_uses_shared_worker(self):
        self.window.settings.key='test-only'
        fake=SimpleNamespace(progress=SimpleNamespace(connect=lambda callback:None))
        def work(fn,callback,**kwargs):self.window.job=fake
        self.dialog.offline_failure={'summary':'test','failureStage':'test','evidence':[]}
        with patch.object(self.dialog,'can_analyze',return_value=True),patch.object(self.window,'work',side_effect=work) as job:
            self.dialog.start();self.assertEqual(job.call_count,1);self.assertTrue(self.dialog.running)
            self.assertFalse(self.dialog.start_btn.isEnabled());self.assertIn('failure',job.call_args.kwargs)
        self.window.job=None;self.dialog.stopped()
    def test_cancel_and_close_wait_for_active_worker(self):
        self.dialog.running=True;self.dialog.cancel();self.assertTrue(self.dialog.cancel_event.is_set())
        event=QCloseEvent();self.dialog.closeEvent(event);self.assertFalse(event.isAccepted());self.dialog.stopped()
    def test_failure_reenables_dialog(self):
        self.dialog.running=True;self.dialog.failed('test failure');self.assertFalse(self.dialog.running);self.assertTrue(self.dialog.start_btn.isEnabled())
    def test_close_button_rejects_idle_dialog(self):
        self.dialog.show();self.app.processEvents();self.assertTrue(self.dialog.isVisible())
        next(b for b in self.dialog.findChildren(QPushButton) if b.text()=='关闭').click()
        self.assertFalse(self.dialog.isVisible())
    def test_candidate_does_not_enable_version(self):
        report={'state':'candidate','summary':'需要验证','candidateReadConfig':{'match_offset':64},'missing':['尚未自检'],'trace':[]}
        self.window.space.live=False
        with patch('adapter_dialog.save_report',return_value='test-report'):
            self.dialog.analysis_done(report)
        self.assertFalse(self.window.space.live);self.assertIn('未启用',self.dialog.status.text());self.assertTrue(self.dialog.export_btn.isEnabled())
        self.assertIn('可能有误',self.dialog.output.toPlainText());self.assertEqual(self.window.status.text(),self.dialog.status.text())
        self.assertTrue(self.dialog.validate_btn.isEnabled())
    def test_review_enables_native_checklist_without_enabling_build(self):
        self.window.space.live=False;self.dialog.validation_ready({'stage':'native_review'})
        self.assertTrue(self.dialog.native_check.isEnabled());self.assertTrue(self.dialog.finish_btn.isEnabled());self.assertFalse(self.window.space.live)
    def test_native_checks_are_required_before_finish_worker(self):
        with patch.object(self.dialog,'validation_work') as work:self.dialog.finish_validation();work.assert_not_called()
        self.assertIn('全部四项',self.dialog.status.text())
    def test_single_confirmation_records_every_native_observation(self):
        from local_adapters import NATIVE
        self.dialog.validation=Mock();self.dialog.native_check.setEnabled(True);self.dialog.native_check.setChecked(True)
        with patch.object(self.dialog,'validation_work') as work:self.dialog.finish_validation()
        work.assert_called_once();work.call_args.args[0](None)
        self.assertEqual(self.dialog.validation.finish.call_args.args[0],{key:True for key in NATIVE})
    def test_closing_a_held_review_requests_restoration(self):
        self.dialog.validation=SimpleNamespace(held=True)
        with patch.object(self.dialog,'restore_validation') as restore:
            self.dialog.reject();restore.assert_called_once()
    def test_validation_cancel_is_routed_to_its_safe_checkpoint(self):
        self.dialog.task_kind='validation';self.dialog.validation=SimpleNamespace(cancel_requested=threading.Event());self.dialog.cancel()
        self.assertTrue(self.dialog.validation.cancel_requested.is_set());self.assertFalse(self.dialog.cancel_event.is_set())
    def test_stale_worker_completion_does_not_enable_new_job_controls(self):
        old=object();self.window.job=SimpleNamespace(isRunning=lambda:True)
        with patch.object(self.window,'update_buttons') as update:self.window.finished(old);update.assert_not_called()
        self.window.job=None
    def test_version_reload_waits_for_the_validation_worker(self):
        self.window.job=SimpleNamespace(isRunning=lambda:True)
        with patch('app_live.QTimer.singleShot') as timer:self.window.reload_installations(self.window.space.exe);self.assertEqual(timer.call_args.args[0],50)
        self.window.job=None
    def test_main_window_close_restores_held_validation_first(self):
        restore=Mock();self.window.adapter_dialog=SimpleNamespace(validation=SimpleNamespace(held=True),restore_validation=restore)
        event=QCloseEvent();self.window.closeEvent(event);self.assertFalse(event.isAccepted());restore.assert_called_once();self.window.adapter_dialog=None
    def test_missing_cached_candidate_does_not_enable_validation(self):
        self.dialog.running=True;self.dialog.reused(None);self.assertFalse(self.dialog.running);self.assertFalse(self.dialog.validate_btn.isEnabled());self.assertIn('没有找到',self.dialog.status.text())

    def test_code_start_does_not_require_model_or_validate(self):
        fake=SimpleNamespace(progress=SimpleNamespace(connect=lambda callback:None))
        def work(fn,callback,**kwargs):self.window.job=fake
        with patch.object(self.dialog,'can_analyze',return_value=True),patch.object(self.window,'work',side_effect=work) as job,patch.object(self.window,'configure_api') as api,patch.object(self.dialog,'validate') as validate:
            self.dialog.start_code();self.assertEqual(job.call_count,1);api.assert_not_called();validate.assert_not_called()
        self.window.job=None;self.dialog.stopped()
    def test_ai_cannot_start_before_offline_failure(self):
        with patch.object(self.window,'work') as work,patch.object(self.window,'configure_api') as api:
            self.dialog.start();work.assert_not_called();api.assert_not_called()
    def test_blocked_code_exposes_optional_ai_without_calling_it(self):
        report={'state':'blocked','analysisSource':'code','summary':'unknown','candidateReadConfig':{},'missing':[],'trace':[]}
        with patch('adapter_dialog.save_report',return_value='test'),patch('adapter_dialog.run_analysis') as ai:
            self.dialog.analysis_done(report);ai.assert_not_called()
        self.assertTrue(self.dialog.ai_btn.isEnabled());self.assertFalse(self.dialog.validate_btn.isEnabled())
    def test_late_candidate_after_cancel_is_discarded(self):
        self.dialog.cancel_event.set()
        with patch('adapter_dialog.save_report') as save:
            self.dialog.analysis_done({'state':'candidate'});save.assert_not_called()
        self.assertIsNone(self.dialog.report);self.assertFalse(self.dialog.validate_btn.isEnabled())
    def test_busy_code_start_does_not_schedule_work(self):
        self.dialog.running=True
        with patch.object(self.window,'work') as work:self.dialog.start_code();work.assert_not_called()
    def test_selfcheck_failure_does_not_expose_ai(self):
        self.dialog.task_kind='validation';self.dialog.validation=SimpleNamespace(held=False,review_ready=False)
        with patch.object(self.window,'error'),patch('adapter_dialog.run_analysis') as ai:self.dialog.validation_failed('timeout');ai.assert_not_called()
        self.assertFalse(self.dialog.ai_btn.isEnabled());self.assertIn('自检失败',self.dialog.status.text())
    def test_ai_candidate_requires_static_completion(self):
        self.dialog.offline_failure={'summary':'unknown','failureStage':'static','evidence':[]}
        report={'state':'candidate','candidateReadConfig':{'name_get':1},'fieldEvidence':{'name_get':'E1'}}
        with patch('adapter_dialog.run_analysis',return_value=report),patch('adapter_dialog.Resolver') as resolver:
            resolver.return_value.complete_static.side_effect=ValueError('unknown')
            result=self.dialog.ai_work(None,{})
        self.assertEqual(result['state'],'blocked');self.assertIn('更新识别规则',result['summary'])
    def test_pending_recovery_prevents_analysis(self):
        pending=SimpleNamespace(held=True)
        with patch('adapter_validation.recover_pending',return_value=pending),patch.object(self.window,'work') as work:
            self.dialog.start_code();work.assert_not_called()
        self.assertIs(self.dialog.validation,pending);self.assertFalse(self.dialog.start_btn.isEnabled())
    def test_background_connection_is_the_recommended_fourth_step(self):
        from PySide6.QtWidgets import QLabel
        texts=[l.text() for l in self.dialog.findChildren(QLabel)]
        self.assertEqual(len(self.dialog.step_badges),4);self.assertIn('后台连接',texts);self.assertIn('推荐安装',texts)
        self.assertTrue(any('看不见的扩展' in t and '带空格的文件夹' in t for t in texts))
        # The settings page keeps its own controls.
        self.assertTrue(hasattr(self.window,'bridge_btn') and hasattr(self.window,'bridge_remove'))
    def test_fourth_step_follows_the_extension_state(self):
        import cep_bridge
        for state,install,remove,badge in (('missing',True,False,'todo'),('outdated',True,True,'todo'),('installed',False,True,'done'),('connected',False,True,'done')):
            with self.subTest(state),patch.object(cep_bridge,'status',return_value=state):
                self.dialog.update_bridge()
                self.assertEqual(self.dialog.bridge_note.text(),self.dialog.BRIDGE_STATE[state])
                self.assertEqual((self.dialog.bridge_install.isVisibleTo(self.dialog),self.dialog.bridge_remove.isVisibleTo(self.dialog)),(install,remove))
                self.assertEqual(self.dialog.step_badges[3].property('state'),badge)
    def test_fourth_step_installs_through_the_main_window_with_the_dialog_as_parent(self):
        import cep_bridge
        with patch.object(cep_bridge,'status',return_value='missing'):self.dialog.update_bridge()
        with patch.object(self.window,'install_bridge',return_value=True) as install,patch.object(cep_bridge,'status',return_value='installed'):
            self.dialog.bridge_install.click()
        install.assert_called_once_with(self.dialog);self.assertEqual(self.dialog.bridge_state,'installed')
        with patch.object(self.window,'remove_bridge') as remove,patch.object(cep_bridge,'status',return_value='missing'):
            self.dialog.bridge_remove.click()
        remove.assert_called_once_with(self.dialog);self.assertEqual(self.dialog.bridge_state,'missing')
    def test_supported_connect_never_calls_analysis(self):
        self.window.space.live=True
        with patch.object(self.window,'work') as work,patch.object(self.window,'analyze_adapter') as ai:
            self.window.connect_live();self.assertEqual(work.call_count,1);ai.assert_not_called()
