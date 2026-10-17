"""The update notice: shown once the first time a new version opens, and the notes behind it."""
import json,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QPushButton
from test_gui import GuiCase
import app_live,release_notes,ui_kit,updater

SAMPLE=[('1.2',['甲']),('1.1',['乙']),('1.0.1',['丙']),('1.0',['丁'])]

class NotesTests(unittest.TestCase):
    def test_every_entry_is_usable_and_the_current_version_has_one(self):
        versions=[updater.parse_version(v) for v,_ in release_notes.NOTES]
        self.assertEqual(versions,sorted(versions,reverse=True));self.assertEqual(len(set(versions)),len(versions))
        for version,items in release_notes.NOTES:
            self.assertTrue(items and all(isinstance(i,str) and i.strip() for i in items),version)
        # Releasing a version means writing what is new in it (publish_update.py also needs it).
        self.assertTrue(release_notes.newer(None,app_live.APP_VERSION))
        updater.validate({'schemaVersion':1,'version':app_live.APP_VERSION,'notes':release_notes.text(release_notes.newer(None,app_live.APP_VERSION),headings=False),
                          'installer':{'name':'a.exe','bytes':1,'sha256':'0'*64}})   # fits the update manifest
    def test_which_versions_are_shown(self):
        with patch.object(release_notes,'NOTES',SAMPLE):
            newer=release_notes.newer
            self.assertEqual([v for v,_ in newer(None,'1.1')],['1.1'])              # nothing seen: this version only
            self.assertEqual([v for v,_ in newer('1.0','1.2')],['1.2','1.1','1.0.1'])   # every skipped version
            self.assertEqual([v for v,_ in newer('1.0.1','1.1')],['1.1'])
            self.assertEqual(newer('1.1','1.1'),[]);self.assertEqual(newer('1.2','1.1'),[])   # seen, or a downgrade
            self.assertEqual([v for v,_ in newer('garbage','1.1')],['1.1']);self.assertEqual(newer(None,'2.0'),[])
            self.assertEqual(release_notes.text(newer(None,'1.1')),'· 乙')
            self.assertEqual(release_notes.text(newer('1.0.1','1.2')),'1.2\n· 甲\n\n1.1\n· 乙')
            # The notice's list: escaped, and no version heading for a single entry.
            self.assertEqual(release_notes.html([('1.0',['a<b'])]),'<ul style="margin:0 0 0 -24px"><li style="margin-bottom:6px">a&lt;b</li></ul>')

class NoticeTests(GuiCase):
    def prefs(self):return json.loads((Path(self.temp.name)/'ui.json').read_text(encoding='utf-8'))
    def test_fresh_install_is_not_told_what_is_new(self):
        w=self.window;self.assertFalse(w.returning)
        with patch.object(app_live,'inform') as shown:w.show_release_notice()
        shown.assert_not_called();self.assertEqual(self.prefs()['seenVersion'],app_live.APP_VERSION)
    def test_returning_user_sees_it_once(self):
        w=self.window;w.returning=True
        with patch.object(app_live,'inform') as shown:w.show_release_notice();w.show_release_notice()
        shown.assert_called_once();title,text=shown.call_args.args[1:3]
        self.assertEqual(title,f'{app_live.APP_NAME} {app_live.APP_VERSION} 更新内容');self.assertEqual(text,release_notes.html(release_notes.newer(None,app_live.APP_VERSION)))
        self.assertEqual(self.prefs()['seenVersion'],app_live.APP_VERSION)
    def test_skipped_versions_are_shown_together(self):
        w=self.window;w.save_ui_preference('seenVersion','1.0')
        with patch.object(release_notes,'NOTES',SAMPLE),patch.object(app_live,'APP_VERSION','1.2'),patch.object(app_live,'inform') as shown:w.show_release_notice()
        shown_text=shown.call_args.args[2]
        for version,item in (('1.2','甲'),('1.1','乙'),('1.0.1','丙')):self.assertIn(f'>{version}</p>',shown_text);self.assertIn(f'<li style="margin-bottom:6px">{item}</li>',shown_text)
        self.assertNotIn('丁',shown_text);self.assertEqual(self.prefs()['seenVersion'],'1.2')
    def test_settings_page_shows_the_notes_again(self):
        w=self.window;self.assertFalse(w.notes_btn.isHidden())
        with patch.object(app_live,'inform') as shown:w.notes_btn.click()
        self.assertIn(release_notes.NOTES[0][1][0],shown.call_args.args[2])
    def test_notice_dialog_has_a_single_button(self):
        dialog=ui_kit.Prompt(self.window,'标题','内容','知道了',cancel=False);self.addCleanup(dialog.deleteLater)
        self.assertEqual([b.text() for b in dialog.findChildren(QPushButton)],['知道了'])

if __name__=='__main__':unittest.main()
