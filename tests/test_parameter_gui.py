import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
import app_live,settings_store,live_names,parameter_names,effect_instances
from parameter_names import identity

class ParameterGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        for module in (app_live,settings_store,live_names,parameter_names,effect_instances):
            handle=patch.object(module,'data_root',lambda:root);handle.start();self.addCleanup(handle.stop)
        # A verified build, so the parameter page is available.
        install=[{'name':'Adobe After Effects 2026','exe':str(root/'AE'/'AfterFX.exe'),'plugins':str(root/'AE'/'Plug-ins')}]
        with patch.object(app_live.QTimer,'singleShot'),patch.object(app_live,'installations',return_value=install),patch.object(app_live,'shared_roots',return_value=[]),patch.object(app_live,'file_version',return_value='26.0.0.67'),patch.object(app_live.cep_bridge,'_appdata',lambda:Path(self.temp.name)/'appdata'):self.window=app_live.Window()
        self.addCleanup(self.window.deleteLater);self.dialog=self.window.parameter_page;self.window.update_buttons()
        self.keys=[identity('S_Glow','Brightness'),identity('Other','Brightness')]
        self.rows=[{'id':key,'name':'Brightness','effectMatchName':effect,'matchName':'Brightness','instances':1} for key,effect in zip(self.keys,['S_Glow','Other'])]
        self.dialog.loaded({'parameters':self.rows})
    def test_filter_checkbox_only_changes_visible_effect(self):
        self.dialog.effects.setCurrentIndex(self.dialog.effects.findData('S_Glow'));self.dialog.check(True)
        self.assertEqual(self.window.parameter_enabled,{self.keys[0]})
        self.assertEqual(self.dialog.table.item(0,0).checkState(),Qt.CheckState.Checked)
        self.assertEqual(self.dialog.table.item(1,0).checkState(),Qt.CheckState.Unchecked)
    def test_manual_candidate_is_saved_under_parameter_identity(self):
        self.dialog.table.item(1,2).setText('亮度')
        self.assertEqual(self.window.parameter_pending[self.keys[1]],'亮度');self.assertNotIn(self.keys[0],self.window.parameter_pending)
        saved=app_live.CandidateStore(self.window.parameter_store.path)
        self.assertEqual(saved.values[self.keys[1]],'亮度')
    def test_effect_apply_leaves_parameter_labels_alone(self):
        self.window.parameter_pending.update({k:'亮度' for k in self.keys});self.window.parameter_enabled.update(self.keys)
        with patch.object(self.window.session,'apply',return_value={'applied':{'S_Glow':'辉光'}}),patch.object(self.window.instance_session,'apply',return_value={'changed':1,'customSkipped':0}) as titles,patch.object(self.window.parameter_session,'apply') as apply,patch.object(app_live,'with_live',lambda x,*args:x):
            result=self.window.apply_names({'S_Glow':'辉光'})
        apply.assert_not_called();titles.assert_called_once();self.assertEqual(result['applied'],{'S_Glow':'辉光'});self.assertEqual(result['instanceResult']['changed'],1)
    def test_closing_does_not_destroy_an_active_host_worker(self):
        self.window.job=SimpleNamespace(isRunning=lambda:True);event=QCloseEvent();self.window.closeEvent(event)
        self.assertFalse(event.isAccepted());self.assertIn('等待',self.window.status.text());self.window.job=None
    def test_page_work_shares_the_window_worker(self):
        calls=[]
        with patch.object(self.window,'work',lambda work,done:calls.append(work)):self.dialog.load()
        self.assertEqual(len(calls),1)
    def test_normal_read_reuses_cache_and_full_scan_is_explicit(self):
        from contextlib import nullcontext
        calls=[]
        with patch.object(self.window,'work',lambda fn,done:calls.append(fn)),patch('parameter_dialog.read_parameters',return_value={'parameters':[]}) as read:
            self.dialog.load();calls.pop()(None);self.assertFalse(read.call_args.kwargs['rescan'])
            self.dialog.load(rescan=True);calls.pop()(None);self.assertTrue(read.call_args.kwargs['rescan'])
        self.assertEqual([a.text() for a in self.dialog.scan_menu.menu().actions()],['选中图层','整个工程'])
        self.assertEqual(read.call_args.kwargs['scope'],'selected')
    def test_scope_menu_is_exclusive_defaults_selected_and_never_reads(self):
        self.assertEqual(self.dialog.read_scope,'selected');self.assertTrue(self.dialog.scope_actions['selected'].isChecked())
        self.window.parameter_pending[self.keys[0]]='已有译文';self.window.parameter_enabled.add(self.keys[0])
        before=dict(self.window.parameter_pending);enabled=set(self.window.parameter_enabled)
        with patch.object(self.window,'work') as work:
            self.dialog.scope_actions['project'].trigger();work.assert_not_called()
        self.assertEqual(self.dialog.read_scope,'project');self.assertEqual(self.dialog.scan_menu.text(),'整个工程')
        self.assertTrue(self.dialog.scope_actions['project'].isChecked());self.assertFalse(self.dialog.scope_actions['selected'].isChecked())
        self.assertEqual(self.dialog.rows,[]);self.assertEqual(self.window.parameter_pending,before);self.assertEqual(self.window.parameter_enabled,enabled)
    def test_scope_cannot_change_while_busy(self):
        with patch.object(self.window,'busy',return_value=True):self.dialog.scope_actions['project'].trigger()
        self.assertEqual(self.dialog.read_scope,'selected');self.assertTrue(self.dialog.scope_actions['selected'].isChecked())
    def test_empty_selection_states_and_persistent_write_warning(self):
        for reason,message in [('noActiveComp','没有活动合成'),('noSelectedLayers','没有选中图层'),('noSupportedParameters','没有可读取')]:
            self.dialog.loaded({'parameters':[],'emptyReason':reason});self.assertIn(message,self.dialog.status.text())
        self.assertIn('整个工程',self.dialog.write_scope_note.text())
    def test_project_read_and_post_apply_restore_reads_use_chosen_scope(self):
        calls=[];self.dialog.set_read_scope('project');self.dialog.loaded({'parameters':self.rows})
        self.window.parameter_enabled.add(self.keys[0]);self.window.parameter_pending[self.keys[0]]='亮度'
        with patch.object(self.window,'work',lambda fn,done:calls.append(fn)),patch('parameter_dialog.read_parameters',return_value={'parameters':[]}) as read,patch.object(self.window.parameter_session,'apply',return_value={}) as apply,patch.object(self.window.parameter_session,'restore',return_value={}) as restore:
            self.dialog.load();calls.pop()(None);self.assertEqual(read.call_args.kwargs['scope'],'project')
            self.dialog.apply();calls.pop()(None);self.assertEqual(read.call_args.kwargs['scope'],'project');apply.assert_called_once()
            self.dialog.restore();calls.pop()(None);self.assertEqual(read.call_args.kwargs['scope'],'project');restore.assert_called_once()
    def test_page_apply_from_the_heap_schedules_one_background_check(self):
        calls=[];dones=[];self.window.parameter_enabled.add(self.keys[0]);self.window.parameter_pending[self.keys[0]]='亮度'
        with patch.object(self.window,'work',lambda fn,done:(calls.append(fn),dones.append(done))),patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows}),patch.object(self.window.parameter_session,'apply',return_value={'changed':1,'parameters':1,'heapOnly':True}) as apply:
            self.dialog.apply();result=calls.pop()(None);self.assertFalse(apply.call_args.kwargs['full']);dones.pop()(result)
        self.assertTrue(self.window.completion.isActive());self.assertEqual(self.window.completion_keys,{self.keys[0]:'亮度'})
        threads=[]
        with patch.object(self.window.parameter_session,'complete',return_value={'missed':0,'checked':True}) as complete,patch('threading.Thread',lambda target,**kw:threads.append(target) or type('T',(),{'start':lambda self:None,'join':lambda self,**kw:None})()):
            with patch.object(self.window,'busy',return_value=True):self.window.completion_pass()
            self.assertEqual(threads,[]);self.assertTrue(self.window.completion.isActive())   # waits while another job runs
            self.window.completion_pass();threads.pop()()
        complete.assert_called_once_with({self.keys[0]:'亮度'});self.assertEqual(self.window.completion_keys,{})
    def test_background_check_reports_what_it_found(self):
        self.window.completion_done({'missed':0,'checked':True},3.2);self.assertIn('都在默认堆里',self.window.log.toPlainText())
        self.window.completion_done({'missed':2,'checked':True,'changed':4},3.4);self.assertIn('默认堆以外找到 2 组',self.window.log.toPlainText())
        self.window.completion_done('AE 已关闭',0.1);self.assertIn('补充完整扫描未完成',self.window.log.toPlainText())
    def test_apply_from_a_full_structure_needs_no_check(self):
        calls=[];dones=[];self.window.parameter_enabled.add(self.keys[0]);self.window.parameter_pending[self.keys[0]]='亮度'
        with patch.object(self.window,'work',lambda fn,done:(calls.append(fn),dones.append(done))),patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows}),patch.object(self.window.parameter_session,'apply',return_value={'changed':1,'parameters':1}):
            self.dialog.apply();dones.pop()(calls.pop()(None))
        self.assertFalse(self.window.completion.isActive())
    def test_busy_read_does_not_start(self):
        with patch.object(self.window,'busy',return_value=True),patch.object(self.window,'work') as work:self.dialog.load(rescan=True);work.assert_not_called()
    def test_scan_options_hidden_in_history(self):
        self.dialog.view_buttons[1].setChecked(True);self.dialog.show_view();self.assertTrue(self.dialog.scan_menu.isHidden())
    def test_table_timing_and_updates_reenabled(self):
        with patch('parameter_dialog.record_read_timing') as record:
            self.dialog.loaded({'parameters':self.rows,'timings':{'totalMs':100,'readId':'test'}})
        self.assertTrue(self.dialog.table.updatesEnabled());self.assertIn('tableMs',record.call_args.args[0]);self.assertIn('0.10',self.dialog.status.text())
    def test_effect_page_opens_parameters_filtered_to_one_effect(self):
        self.window.open_parameters('Other')
        self.assertEqual(self.window.pages.currentIndex(),app_live.PARAMETERS);self.assertEqual(self.dialog.effects.currentData(),'Other')
        self.assertTrue(self.dialog.table.isRowHidden(0));self.assertFalse(self.dialog.table.isRowHidden(1))
    def test_unloaded_effect_is_read_first_then_filtered(self):
        self.dialog.loaded({'parameters':[]});started=[]
        with patch.object(self.window,'work',lambda work,done:started.append(done)):self.window.open_parameters('S_Glow')
        started[0]({'parameters':self.rows});self.assertEqual(self.dialog.effects.currentData(),'S_Glow')
    def test_apply_without_candidates_does_not_touch_ae(self):
        self.window.parameter_enabled.add(self.keys[0])
        with patch.object(self.window,'work',side_effect=AssertionError('must not apply')):self.dialog.apply()
        self.assertIn('还没有中文候选',self.dialog.status.text())
    def test_switching_version_clears_rows(self):
        self.dialog.clear();self.assertEqual((self.dialog.rows,self.dialog.table.rowCount()),([],0))
    def test_worker_starts_delivers_result_and_reenables_controls(self):
        results=[];self.window.work(lambda progress:{'ok':True},results.append)
        self.assertFalse(self.dialog.refresh.isEnabled())
        self.assertTrue(self.window.job.wait(3000));self.app.processEvents()
        self.assertEqual(results,[{'ok':True}]);self.assertTrue(self.dialog.refresh.isEnabled());self.assertTrue(self.dialog.table.isEnabled())
    def test_worker_failure_is_visible_and_reenables_controls(self):
        def fail(progress):raise RuntimeError('模拟参数读取失败')
        self.window.work(fail,lambda value:self.fail('unexpected success'))
        self.assertTrue(self.window.job.wait(3000));self.app.processEvents()
        self.assertEqual(self.window.status.text(),'模拟参数读取失败');self.assertTrue(self.window.toast.full.endswith('模拟参数读取失败'));self.assertTrue(self.dialog.refresh.isEnabled())
    def test_partial_restore_shows_error_instead_of_full_success(self):
        operation={'restored':{'CC Drizzle':'CC Drizzle'},'restoreErrors':['图层效果标题恢复失败：手动改名']}
        with patch.object(self.window,'show_reread'),patch.object(self.window.toast,'notify') as notify:
            self.window.restored((operation,None,''))
        self.assertEqual(notify.call_count,1);self.assertTrue(notify.call_args.kwargs['error'])
        self.assertIn('部分原名未恢复',self.window.status.text());self.assertIn('效果目录已恢复的部分',self.window.status.text())

if __name__=='__main__':unittest.main()
