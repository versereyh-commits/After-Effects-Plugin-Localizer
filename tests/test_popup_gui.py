import json,os,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from tests import test_parameter_gui as parameter_gui
from parameter_dialog import OPTION_ROLE
from popup_names import BUILD,option_key

class PopupGuiTests(unittest.TestCase):
    """Dropdown choices are rows of the parameter page, read after the labels."""
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        parameter_gui.ParameterGuiTests.setUp(self)
        self.window.space.version=BUILD
        self.popup={'id':self.keys[0],'effectMatchName':'S_Glow','name':'Method','original':'Frame Analysis|Temporal Smooth RGB|Temporal Smooth YCC','current':'Frame Analysis|Temporal Smooth RGB|Temporal Smooth YCC','options':['Frame Analysis','Temporal Smooth RGB','Temporal Smooth YCC'],'currentOptions':['Frame Analysis','Temporal Smooth RGB','Temporal Smooth YCC'],'capacity':54}
        self.report={'host':{},'projectToken':'qa','scope':'selected','parameters':[self.popup]}
        self.calls=[]
        def work(fn,done,failure=None):
            self.calls.append(fn)
            try:done(fn(lambda _:None))
            except Exception as error:
                if failure:failure(str(error))
                else:raise
        h=patch.object(self.window,'work',work);h.start();self.addCleanup(h.stop)
        self.page=self.dialog;self.page.popups_loaded(self.report,'')
    def options(self):return [i for i,row in enumerate(self.page.rows) if self.page.is_option(row)]
    def test_choices_follow_their_parameter_with_a_marker(self):
        self.assertEqual([r['id'] for r in self.page.rows[:4]],[self.keys[0]]+[option_key(self.popup,i) for i in range(3)])
        self.assertEqual(self.options(),[1,2,3])
        self.assertTrue(self.page.table.item(1,1).data(OPTION_ROLE));self.assertFalse(self.page.table.item(0,1).data(OPTION_ROLE))
        self.assertEqual(self.page.table.item(1,1).text(),'Frame Analysis');self.assertIn('Method',self.page.table.item(1,1).toolTip())
        self.assertEqual(len(self.page.params),2)
    def test_labels_and_choices_come_from_one_read(self):
        with patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows,'popups':self.report}) as read:self.page.load()
        self.assertEqual(read.call_count,1);self.assertIsNotNone(read.call_args.kwargs['extra']);self.assertEqual(len(self.options()),3);self.assertIs(self.page.popup_report,self.report)
    def test_other_adapted_builds_read_choices(self):
        self.window.space.version='15.1.2.69';self.window.space.live=True
        with patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows,'popups':self.report}) as read:self.page.load()
        self.assertIsNotNone(read.call_args.kwargs['extra']);self.assertEqual(len(self.options()),3)
    def test_choice_read_failure_keeps_the_labels(self):
        with patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows,'popupError':'结构不符'}):self.page.load()
        self.assertEqual(len(self.page.rows),2);self.assertIn('下拉选项未读取：结构不符',self.page.status.text())
    def test_choice_candidates_and_checks_are_stored_apart_from_labels(self):
        self.page.table.item(1,2).setText('逐帧分析');self.page.table.item(1,0).setCheckState(Qt.CheckState.Checked)
        pending,checked=self.page.popup_stores()
        self.assertEqual(json.loads(pending.path.read_text(encoding='utf8'))[option_key(self.popup,0)],'逐帧分析')
        self.assertIn(option_key(self.popup,0),json.loads(checked.path.read_text(encoding='utf8')))
        self.assertNotIn(option_key(self.popup,0),self.window.parameter_pending);self.assertNotIn(option_key(self.popup,0),self.window.parameter_enabled)
    def test_apply_writes_checked_choices_and_keeps_the_others(self):
        self.page.table.item(1,2).setText('逐帧分析');self.page.table.item(2,2).setText('RGB平滑');self.page.table.item(1,0).setCheckState(Qt.CheckState.Checked)
        with patch.object(self.window.popup_session,'apply',return_value={'parameters':1}) as apply,patch.object(self.window.parameter_session,'apply') as labels,patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows}):
            self.page.apply()
        labels.assert_not_called()
        self.assertEqual(apply.call_args.args[0],{self.keys[0]:['逐帧分析','Temporal Smooth RGB','Temporal Smooth YCC']});self.assertIs(apply.call_args.args[1],self.report)
    def test_labels_and_choices_apply_together(self):
        self.window.parameter_pending[self.keys[1]]='亮度';self.window.parameter_enabled.add(self.keys[1])
        self.page.table.item(1,2).setText('逐帧分析');self.page.table.item(1,0).setCheckState(Qt.CheckState.Checked)
        with patch.object(self.window.popup_session,'apply',return_value={'parameters':1}) as apply,patch.object(self.window.parameter_session,'apply',return_value={'parameters':1}) as labels,patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows}):
            self.page.apply()
        apply.assert_called_once();self.assertEqual(labels.call_args.args[0],{self.keys[1]:'亮度'})
    def test_nothing_already_shown_is_written_again(self):
        self.window.parameter_pending[self.keys[1]]='Brightness';self.window.parameter_enabled.add(self.keys[1])
        self.page.table.item(1,2).setText('Frame Analysis');self.page.table.item(1,0).setCheckState(Qt.CheckState.Checked)
        self.calls.clear()
        with patch.object(self.window.popup_session,'apply') as apply,patch.object(self.window.parameter_session,'apply') as labels:self.page.apply()
        apply.assert_not_called();labels.assert_not_called();self.assertEqual(self.calls,[]);self.assertIn('无需写入',self.page.status.text())
    def test_a_label_with_differing_copies_is_still_written(self):
        self.page.params[1]['mixedNames']=True;self.window.parameter_pending[self.keys[1]]='Brightness';self.window.parameter_enabled.add(self.keys[1])
        with patch.object(self.window.parameter_session,'apply',return_value={'parameters':1}) as labels,patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows}):self.page.apply()
        self.assertEqual(labels.call_args.args[0],{self.keys[1]:'Brightness'})
    def test_overlong_choices_are_not_written(self):
        self.page.table.item(1,2).setText('汉'*40);self.page.table.item(1,0).setCheckState(Qt.CheckState.Checked)
        with patch.object(self.window.popup_session,'apply') as apply:self.page.apply()
        apply.assert_not_called();self.assertIn('超出',self.page.status.text())
    def test_candidates_are_bound_to_the_original_text(self):
        self.page.table.item(1,2).setText('已保存')
        other=dict(self.popup,original='Different|Options',options=['Different','Options'],currentOptions=['Different','Options'])
        self.page.popups_loaded({**self.report,'parameters':[other]},'')
        self.assertEqual(self.page.table.item(1,2).text(),'')
    def test_restore_checked_choice_restores_its_parameter(self):
        self.page.table.item(2,0).setCheckState(Qt.CheckState.Checked)
        with patch.object(self.window.popup_session,'restore',return_value={'parameters':1}) as restore,patch.object(self.window.parameter_session,'restore') as labels,patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows}):
            self.page.restore()
        restore.assert_called_once();self.assertEqual(restore.call_args.args[0],[self.keys[0]]);labels.assert_not_called()
    def test_restore_all_includes_choices(self):
        with patch.object(self.window.popup_session,'restore',return_value={'parameters':2}) as restore,patch.object(self.window.parameter_session,'restore',return_value={'restored':1}),patch('parameter_dialog.read_parameters',return_value={'parameters':self.rows}):
            self.page.restore_all()
        self.assertIsNone(restore.call_args.args[0]);self.assertIn('2 个下拉参数',self.page.status.text())
    def test_global_restore_includes_popups(self):
        with patch.object(self.window,'restore_effect_names',return_value={}),patch.object(self.window.parameter_session,'restore',return_value={}),patch.object(self.window.category_session,'restore',return_value={}),patch.object(self.window.popup_session,'restore',return_value={'parameters':1}) as restore:
            report=self.window.restore_names(None)
        restore.assert_called_once();self.assertEqual(report['popupResult']['parameters'],1)
    def test_scheme_refresh_keeps_choices(self):
        self.page.refresh_values();self.assertEqual(len(self.options()),3)
    def test_nav_counts_labels_only(self):
        self.assertEqual(len(self.page.params),2);self.assertEqual(len(self.page.rows),5)

if __name__=='__main__':unittest.main()
