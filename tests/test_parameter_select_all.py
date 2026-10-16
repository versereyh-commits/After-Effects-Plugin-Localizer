from unittest.mock import patch,Mock
from types import SimpleNamespace
from PySide6.QtCore import Qt
from test_gui import GuiCase

class ParameterSelectAllTests(GuiCase):
    def setUp(self):
        super().setUp();self.panel=self.window.parameter_page
        self.rows=[{'id':'A::1','name':'Size','effectMatchName':'A','matchName':'1'},{'id':'A::2','name':'Width','effectMatchName':'A','matchName':'2'},{'id':'B::1','name':'Size','effectMatchName':'B','matchName':'1'}]
        self.window.parameter_enabled.clear();self.window.parameter_pending.clear();self.window.parameter_pending['B::1']='大小';self.panel.loaded({'parameters':self.rows})
    def test_all_includes_effect_and_search_hidden_rows_and_saves_once(self):
        self.panel.effects.setCurrentIndex(self.panel.effects.findData('A'));self.panel.search.setText('Width')
        self.assertTrue(self.panel.table.isRowHidden(0));self.assertTrue(self.panel.table.isRowHidden(2))
        with patch.object(self.window,'save_parameters') as save,patch.object(self.window,'work') as work:
            self.panel.check_all_btn.click();save.assert_called_once();work.assert_not_called()
        self.assertEqual(self.window.parameter_enabled,{'A::1','A::2','B::1'});self.assertEqual(self.panel.search.text(),'Width');self.assertEqual(self.panel.effects.currentData(),'A')
        self.assertEqual(self.window.parameter_pending,{'B::1':'大小'})
        self.assertTrue(all(self.panel.table.item(i,0).checkState()==Qt.CheckState.Checked for i in range(3)))
    def test_cancel_all_includes_hidden_rows(self):
        self.panel.check_all(True);self.panel.search.setText('no matches')
        next(a for a in self.panel.check_tool.menu().actions() if a.text()=='取消全部参数').trigger()
        self.assertEqual(self.window.parameter_enabled,set())
    def test_repeat_all_is_idempotent_and_retains_other_unloaded_choices(self):
        self.window.parameter_enabled.add('not-loaded')
        self.panel.check_all(True);self.panel.check_all(True)
        self.assertEqual(self.window.parameter_enabled,{'not-loaded','A::1','A::2','B::1'})
    def test_current_result_keeps_its_original_scope(self):
        self.panel.search.setText('Width');self.panel.check(True)
        self.assertEqual(self.window.parameter_enabled,{'A::2'})
    def test_empty_and_busy_disable_all(self):
        self.panel.loaded({'parameters':[]});self.window.update_buttons();self.assertFalse(self.panel.check_all_btn.isEnabled())
        self.panel.loaded({'parameters':self.rows});self.window.job=SimpleNamespace(isRunning=lambda:True)
        self.addCleanup(lambda:setattr(self.window,'job',None));self.panel.update_check_all();self.assertFalse(self.panel.check_all_btn.isEnabled())
        self.panel.check_all(True);self.assertFalse(self.window.parameter_enabled)
    def test_all_choice_persists_without_changing_candidates(self):
        self.panel.check_all(True);self.window.bind_parameter_stores(self.window.space);self.panel.loaded({'parameters':self.rows})
        self.assertEqual(self.window.parameter_enabled,{'A::1','A::2','B::1'});self.assertEqual(self.window.parameter_pending['B::1'],'大小')
    def test_unverified_version_disables_all_without_changing_saved_choices(self):
        self.window.space.live=False;self.window.update_buttons();self.assertFalse(self.panel.check_all_btn.isEnabled())
        self.panel.check_all(True);self.assertFalse(self.window.parameter_enabled)
    def test_all_choices_stay_in_the_original_version_store(self):
        from pathlib import Path
        original=self.window.space;self.panel.check_all(True)
        self.window.bind_parameter_stores(SimpleNamespace(folder=Path(self.temp.name)/'other-version'));self.panel.clear()
        self.assertFalse(self.window.parameter_enabled);self.assertFalse(self.panel.rows)
        self.window.bind_parameter_stores(original);self.assertEqual(self.window.parameter_enabled,{'A::1','A::2','B::1'})
