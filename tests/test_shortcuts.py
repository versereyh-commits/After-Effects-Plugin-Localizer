"""Keyboard shortcuts: user keys, conflicts, the master switch and live re-keying of the window."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction,QKeySequence
from test_gui import GuiCase
import app_live,shortcuts
from shortcuts import Keymap,ShortcutDialog,problem,sequence

def keys(target)->list[str]:
    found=target.shortcuts() if isinstance(target,QAction) else target.keys()
    return [k.toString(QKeySequence.SequenceFormat.PortableText) for k in found]

class KeymapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'shortcuts.json'
    def test_defaults_and_only_changes_are_saved(self):
        k=Keymap(self.path);self.assertTrue(k.enabled);self.assertEqual(k.keys['translate'],'Ctrl+T');self.assertEqual(k.customized(),0)
        self.assertEqual(k.assign('translate',sequence('Ctrl+Shift+T')),'')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf8')),{'enabled':True,'keys':{'translate':'Ctrl+Shift+T'}})
        again=Keymap(self.path);self.assertEqual(again.keys['translate'],'Ctrl+Shift+T');self.assertEqual(again.customized(),1)
    def test_damaged_or_unknown_entries_fall_back_to_defaults(self):
        self.path.write_text('{not json',encoding='utf8');self.assertEqual(Keymap(self.path).keys['find'],'Ctrl+F')
        self.path.write_text(json.dumps({'enabled':'yes','keys':{'find':'Q','nope':'Ctrl+Q','refresh':5}}),encoding='utf8')
        k=Keymap(self.path);self.assertTrue(k.enabled);self.assertEqual((k.keys['find'],k.keys['refresh']),('Ctrl+F','Ctrl+R'))
    def test_a_saved_key_taken_from_another_command_clears_that_one(self):
        self.path.write_text(json.dumps({'keys':{'refresh':'Ctrl+T'}}),encoding='utf8')
        k=Keymap(self.path);self.assertEqual(k.keys['refresh'],'Ctrl+T');self.assertEqual(k.keys['translate'],'')
    def test_conflicts_treat_keypad_enter_as_return(self):
        k=Keymap(self.path);self.assertEqual(k.conflict('find',sequence('Ctrl+Enter')),'apply');self.assertIsNone(k.conflict('apply',sequence('Ctrl+Return')))
        k.assign('find',sequence('Ctrl+Enter'));self.assertEqual(k.keys['apply'],'');self.assertEqual(k.keys['find'],'Ctrl+Enter')
    def test_keys_that_would_get_in_the_way_of_typing_are_refused(self):
        self.assertIn('Ctrl',problem('translate',sequence('T')));self.assertIn('Ctrl',problem('translate',sequence('Shift+T')));self.assertIn('Ctrl',problem('translate',sequence('Delete')))
        self.assertIn('Esc',problem('translate',sequence('Esc')));self.assertEqual(problem('translate',sequence('F6')),'');self.assertEqual(problem('translate',sequence('Alt+T')),'')
        self.assertEqual(problem('delete',sequence('Delete')),'');self.assertEqual(problem('load',sequence('Space')),'');self.assertIn('Ctrl',problem('rename',sequence('R')))
    def test_switch_clears_bound_keys_and_restores_them(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        k=Keymap(self.path);action=k.bind('apply',QAction());self.assertEqual(keys(action),['Ctrl+Return','Ctrl+Enter'])
        k.set_enabled(False);self.assertEqual(keys(action),[]);self.assertEqual(k.hint('apply'),'');self.assertEqual(k.tip('apply','应用'),'应用')
        self.assertFalse(Keymap(self.path).enabled)
        k.set_enabled(True);self.assertEqual(keys(action),['Ctrl+Return','Ctrl+Enter']);self.assertTrue(k.tip('apply','应用').startswith('应用（'))
    def test_reset_puts_defaults_back(self):
        k=Keymap(self.path);k.assign('refresh',sequence('Ctrl+T'));k.assign('find',QKeySequence())
        k.reset('translate');self.assertEqual((k.keys['translate'],k.keys['refresh']),('Ctrl+T',''))
        k.reset();self.assertEqual(k.customized(),0)

class WindowShortcutTests(GuiCase):
    def test_actions_follow_changes_and_the_switch(self):
        w=self.window;k=w.keymap;self.assertEqual(keys(w.refresh_action),['Ctrl+R']);self.assertIn('Ctrl+R',w.refresh_btn.toolTip())
        self.assertEqual(keys(w.translate_action),[])    # The row menu entry no longer owns Ctrl+T.
        k.assign('refresh',sequence('Ctrl+Alt+R'));self.assertEqual(keys(w.refresh_action),['Ctrl+Alt+R']);self.assertIn('Ctrl+Alt+R',w.refresh_btn.toolTip())
        k.assign('page2',sequence('Alt+3'));self.assertIn('Alt+3',w.nav_buttons[app_live.SCHEMES].toolTip())
        w.shortcut_box.setChecked(False);self.assertFalse(k.enabled);self.assertEqual(keys(w.refresh_action),[]);self.assertNotIn('Ctrl',w.refresh_btn.toolTip())
        self.assertIn('停用',w.shortcut_info.text());self.assertNotIn('Ctrl+Z',w.list_hint.text()+w.edit_hint.text())
        w.shortcut_box.setChecked(True);self.assertEqual(keys(w.refresh_action),['Ctrl+Alt+R'])
        self.assertTrue((Path(self.temp.name)/'shortcuts.json').exists())
    def test_local_keys_reach_lists_and_the_parameter_record(self):
        w=self.window;k=w.keymap;undo=k.targets['undo'];self.assertEqual(len(undo),2)
        self.assertTrue(all(keys(s)==['Ctrl+Z'] for s in undo));self.assertIn('F2 重命名',w.list_hint.text())
        k.assign('undo',sequence('Ctrl+Alt+Z'));self.assertTrue(all(keys(s)==['Ctrl+Alt+Z'] for s in undo))
        self.assertIn('Ctrl+Alt+Z',w.parameter_page.history_delete.toolTip());self.assertIn('Ctrl+Alt+Z',w.undo_note())
        k.assign('rename',QKeySequence());self.assertNotIn('重命名',w.list_hint.text())
        self.assertEqual(keys(k.targets['load'][0]),['Return','Enter'])
    def test_key_drives_the_panel_button(self):
        w=self.window;self.table.item(self.rows_for('Atom Key')[0],app_live.CHECK).setCheckState(Qt.CheckState.Checked)
        with patch.object(w,'translate_rows') as rows:next(a for a in w.actions() if 'translate' in w.keymap.targets and a in w.keymap.targets['translate']).trigger()
        rows.assert_called_once()
    def test_reset_button_asks_first(self):
        w=self.window;w.keymap.assign('find',sequence('Ctrl+Alt+F'));self.assertTrue(w.shortcut_reset_btn.isEnabled())
        with patch.object(app_live,'confirm',return_value=False):w.shortcut_reset_btn.click()
        self.assertEqual(w.keymap.keys['find'],'Ctrl+Alt+F')
        with patch.object(app_live,'confirm',return_value=True):w.shortcut_reset_btn.click()
        self.assertEqual(w.keymap.keys['find'],'Ctrl+F');self.assertFalse(w.shortcut_reset_btn.isEnabled())

class ShortcutDialogTests(GuiCase):
    def setUp(self):
        super().setUp();self.dialog=ShortcutDialog(self.window.keymap,self.window);self.addCleanup(self.dialog.deleteLater);self.dialog.show();self.addCleanup(self.dialog.hide)
    def key_text(self,command):return self.dialog.table.item(self.dialog.row_of(command),shortcuts.K_KEY).text()
    def test_recording_assigns_at_once(self):
        d=self.dialog;d.record('find');self.assertIsNotNone(d.capture)
        d.capture.captured.emit(sequence('Ctrl+Alt+F'));self.app.processEvents();self.assertEqual(self.window.keymap.keys['find'],'Ctrl+Alt+F')
        self.assertEqual(self.key_text('find'),QKeySequence('Ctrl+Alt+F').toString(QKeySequence.SequenceFormat.NativeText))
        self.assertIsNotNone(d.table.cellWidget(d.row_of('find'),shortcuts.K_RESET));d.reset('find');self.assertEqual(self.window.keymap.keys['find'],'Ctrl+F')
    def test_invalid_key_is_explained_and_not_saved(self):
        d=self.dialog;d.chosen('translate',sequence('T'));self.assertEqual(self.window.keymap.keys['translate'],'Ctrl+T');self.assertIn('不能用于',d.message.text())
    def test_conflict_asks_before_taking_the_key(self):
        d=self.dialog
        with patch.object(shortcuts,'confirm',return_value=False):d.chosen('find',sequence('Ctrl+T'))
        self.assertEqual(self.window.keymap.keys['translate'],'Ctrl+T')
        with patch.object(shortcuts,'confirm',return_value=True):d.chosen('find',sequence('Ctrl+T'))
        self.assertEqual((self.window.keymap.keys['find'],self.window.keymap.keys['translate']),('Ctrl+T',''));self.assertEqual(self.key_text('translate'),'未设置')
    def test_backspace_clears_and_escape_cancels(self):
        from PySide6.QtTest import QTest
        d=self.dialog;d.show();p=self.app.processEvents
        d.record('find');QTest.keyClick(d.capture,Qt.Key.Key_Escape);p();self.assertEqual(self.window.keymap.keys['find'],'Ctrl+F');self.assertIsNone(d.capture);self.assertTrue(d.isVisible())
        d.record('find');QTest.keyClick(d.capture,Qt.Key.Key_Backspace);p();self.assertEqual(self.window.keymap.keys['find'],'')
        d.record('find');QTest.keyClick(d.capture,Qt.Key.Key_G,Qt.KeyboardModifier.ControlModifier);p();self.assertEqual(self.window.keymap.keys['find'],'Ctrl+G')
    def test_recorder_is_never_deleted_inside_its_own_handler(self):
        """Clicking the title-bar close button while recording took focus from the recorder, whose handler rebuilt
        the table and so deleted the recorder mid-event: the application crashed. The result now waits a turn."""
        from PySide6.QtGui import QFocusEvent
        from PySide6.QtCore import QEvent
        d=self.dialog;d.record('find');capture=d.capture
        capture.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut));self.assertIs(d.capture,capture);self.assertIs(d.table.cellWidget(d.row_of('find'),shortcuts.K_KEY),capture)
        d.reject();self.app.processEvents();self.assertFalse(d.isVisible());self.assertEqual(self.window.keymap.keys['find'],'Ctrl+F')
    def test_closing_while_a_key_waits_changes_nothing(self):
        d=self.dialog;d.record('find');d.capture.captured.emit(sequence('Ctrl+Alt+F'));d.reject();self.app.processEvents()
        self.assertEqual(self.window.keymap.keys['find'],'Ctrl+F')
        d.show();d.record('find');d.capture.captured.emit(sequence('Ctrl+Alt+F'));self.app.processEvents();self.assertEqual(self.window.keymap.keys['find'],'Ctrl+Alt+F')
    def test_clicking_another_row_while_recording_switches_to_it(self):
        from PySide6.QtGui import QFocusEvent
        from PySide6.QtCore import QEvent
        d=self.dialog;d.record('find');d.capture.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        d.clicked(d.row_of('refresh'),shortcuts.K_KEY);self.app.processEvents()
        self.assertIsNotNone(d.capture);self.assertIs(d.table.cellWidget(d.row_of('refresh'),shortcuts.K_KEY),d.capture)
        d.capture.captured.emit(sequence('Ctrl+Alt+R'));self.app.processEvents();self.assertEqual((self.window.keymap.keys['refresh'],self.window.keymap.keys['find']),('Ctrl+Alt+R','Ctrl+F'))
    def test_search_and_switched_off_notice(self):
        d=self.dialog;d.search.setText('刷新');shown=[c for r,c in enumerate(d.rows) if c and not d.table.isRowHidden(r)];self.assertEqual(shown,['refresh'])
        self.assertTrue(d.off.isHidden());self.window.keymap.set_enabled(False);d.fill();self.assertFalse(d.off.isHidden())
