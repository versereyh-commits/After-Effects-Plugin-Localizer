"""“临时翻译” (0.7.9): restore-on-exit is off by default, remembered, and only it triggers an exit restore."""
import json
from pathlib import Path
from unittest.mock import patch
from PySide6.QtGui import QCloseEvent
import app_live
from test_gui import GuiCase

class TemporaryTranslationTests(GuiCase):
    def journal(self,entries):
        for session in ('session','instance_session','parameter_session'):
            getattr(self.window,session).journal=lambda value={'entries':entries}:value
    def test_named_temporary_and_off_by_default(self):
        self.assertEqual(self.window.exit_restore.text(),'临时翻译');self.assertFalse(self.window.exit_restore.isChecked())
    def test_choice_is_remembered_without_dropping_other_preferences(self):
        root=Path(self.temp.name);self.window.save_motion(False);self.window.exit_restore.setChecked(True)
        self.assertEqual(json.loads((root/'ui.json').read_text(encoding='utf8')),{'motion':False,'temporaryTranslation':True})
        again=app_live.Window();self.addCleanup(again.deleteLater);self.assertTrue(again.exit_restore.isChecked())
        again.exit_restore.setChecked(False);third=app_live.Window();self.addCleanup(third.deleteLater);self.assertFalse(third.exit_restore.isChecked())
    def test_off_closes_without_restoring_written_names(self):
        self.journal({'CC Drizzle':{}})
        with patch.object(self.window,'work') as work:
            event=QCloseEvent();self.window.closeEvent(event);work.assert_not_called()
        self.assertTrue(event.isAccepted())
    def test_on_restores_before_closing(self):
        self.journal({'CC Drizzle':{}});self.window.exit_restore.setChecked(True)
        with patch.object(self.window,'work') as work:
            event=QCloseEvent();self.window.closeEvent(event);work.assert_called_once()
        self.assertFalse(event.isAccepted());self.assertTrue(self.window.close_after);self.window.close_after=False
    def test_damaged_preferences_fall_back_to_off(self):
        (Path(self.temp.name)/'ui.json').write_text('[1]',encoding='utf8')
        window=app_live.Window();self.addCleanup(window.deleteLater);self.assertFalse(window.exit_restore.isChecked())
