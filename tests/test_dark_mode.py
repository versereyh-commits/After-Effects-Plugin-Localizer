"""Dark mode: off by default, switches the whole interface at once and is remembered in ui.json."""
import json,re,unittest
from pathlib import Path
from PySide6.QtGui import QColor,QPalette
from test_gui import GuiCase
from app import apply_theme
import app_live,ui_kit
from app_live import CANDIDATE,KEY,ORIGINAL

def luminance(hex_color):
    def channel(value):
        value/=255;return value/12.92 if value<=.03928 else ((value+.055)/1.055)**2.4
    color=QColor(hex_color);return .2126*channel(color.red())+.7152*channel(color.green())+.0722*channel(color.blue())
def contrast(a,b):
    high,low=sorted((luminance(a),luminance(b)),reverse=True);return (high+.05)/(low+.05)

def solid_colours(pixmap):
    image=pixmap.toImage();return {image.pixelColor(x,y).name() for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x,y).alpha()==255}

class DarkModeCase(GuiCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda:(ui_kit.set_theme(False),apply_theme(self.app,ui_kit.stylesheet())))
    def prefs(self):return json.loads((Path(self.temp.name)/'ui.json').read_text(encoding='utf8'))

class DarkModeTests(DarkModeCase):
    def test_off_by_default(self):
        w=self.window;self.assertFalse(w.dark_box.isChecked());self.assertFalse(ui_kit.is_dark());self.assertEqual(ui_kit.INK,'#18181b')
        self.assertEqual(w.dark_box.text(),'深色模式')
    def test_switching_recolours_everything_and_is_remembered(self):
        w=self.window;apply_theme(self.app,ui_kit.stylesheet());light=self.app.styleSheet()
        row=self.rows_for('tc Shine')[0];self.assertEqual(self.table.item(row,ORIGINAL).foreground().color().name(),ui_kit.LIGHT['INK'])
        self.assertEqual(self.table.item(row,KEY).foreground().color().name(),ui_kit.LIGHT['MUTED'])
        w.dark_box.setChecked(True)
        self.assertTrue(ui_kit.is_dark());self.assertEqual(ui_kit.INK,ui_kit.DARK['INK']);self.assertIs(self.prefs()['darkMode'],True)
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window).name(),'#131316')
        sheet=self.app.styleSheet();self.assertIn(ui_kit.DARK['CANVAS'],sheet)
        # No light colour is left behind in the stylesheet: every literal comes from the dark palette.
        dark_values={value for value in ui_kit.DARK.values() if isinstance(value,str)}
        self.assertEqual({c for c in re.findall(r'#[0-9a-fA-F]{6}\b',sheet) if c not in dark_values},set())
        self.assertNotRegex(sheet,r'\bwhite\b')
        # Table text set from the light palette now uses the dark one.
        self.assertEqual(self.table.item(row,ORIGINAL).foreground().color().name(),ui_kit.DARK['INK'])
        self.assertEqual(self.table.item(row,KEY).foreground().color().name(),ui_kit.DARK['MUTED'])
        self.assertIn('切换为深色',w.status.text())
        # Remembered icons are redrawn in the new colours.
        spec=w.refresh_btn.property('themeIcon');self.assertTrue(spec)
        expected=ui_kit.icon('refresh',ui_kit.DARK['INK_2'],16).pixmap(16).toImage()
        self.assertEqual(w.refresh_btn.icon().pixmap(16).toImage(),expected)
        current=w.nav_buttons[w.pages.currentIndex()];self.assertEqual(solid_colours(current.glyph.pixmap()),{ui_kit.DARK['ON_INK']})
        # And back: the light stylesheet is exactly what it was.
        w.dark_box.setChecked(False)
        self.assertFalse(ui_kit.is_dark());self.assertEqual(self.app.styleSheet(),light);self.assertIs(self.prefs()['darkMode'],False)
        self.assertEqual(self.table.item(row,ORIGINAL).foreground().color().name(),ui_kit.LIGHT['INK'])
        self.assertEqual(w.refresh_btn.icon().pixmap(16).toImage(),ui_kit.icon('refresh',ui_kit.LIGHT['INK_2'],16).pixmap(16).toImage())
    def test_each_cell_is_recoloured_once_in_a_sorted_table(self):
        # Recolouring re-sorts the table; a cell must not be mapped twice (light FAINT -> dark FAINT == light MUTED -> dark MUTED).
        w=self.window;w.show_live({'pid':1,'effects':[{'matchName':'S_Glow','displayName':'S_Glow','category':'c'}]});self.assertTrue(self.table.isSortingEnabled())
        cell=self.table.item(self.rows_for('Atom Key')[0],app_live.CURRENT);self.assertEqual(cell.foreground().color().name(),ui_kit.LIGHT['FAINT'])
        w.dark_box.setChecked(True);self.assertEqual(cell.foreground().color().name(),ui_kit.DARK['FAINT'])
        w.dark_box.setChecked(False);self.assertEqual(cell.foreground().color().name(),ui_kit.LIGHT['FAINT'])
    def test_rows_coloured_after_switching_use_the_dark_palette(self):
        w=self.window;w.dark_box.setChecked(True)
        row=self.rows_for('Atom Key')[0];self.table.item(row,CANDIDATE).setText('抠像');w.color_current(row)
        self.assertIn(self.table.item(row,app_live.CURRENT).foreground().color().name(),{ui_kit.DARK['INK'],ui_kit.DARK['INK_2'],ui_kit.DARK['FAINT']})
    def test_switching_does_not_touch_ae_or_reload(self):
        w=self.window
        from unittest.mock import patch
        with patch.object(w,'start_scan') as scan,patch.object(w,'work') as work:w.dark_box.setChecked(True)
        scan.assert_not_called();work.assert_not_called()

class SwitchCostTests(DarkModeCase):
    def test_recolouring_is_not_an_edit(self):
        # Each recoloured candidate cell used to reach item_changed: a candidates save and a recount per row.
        w=self.window
        from unittest.mock import patch
        with patch.object(w,'save_translations') as save,patch.object(w,'update_counts') as counts,patch.object(w,'sync_rows') as sync:
            w.dark_box.setChecked(True);w.dark_box.setChecked(False)
        save.assert_not_called();counts.assert_not_called();sync.assert_not_called()
        self.assertTrue(self.table.isSortingEnabled());self.assertFalse(self.table.signalsBlocked())

class CrossfadeTests(DarkModeCase):
    def setUp(self):
        super().setUp()
        from unittest.mock import patch
        motion=patch.object(ui_kit,'motion',return_value=True);motion.start();self.addCleanup(motion.stop)
        self.window.setAttribute(app_live.Qt.WidgetAttribute.WA_DontShowOnScreen,True);self.window.resize(1200,720);self.window.show();self.app.processEvents()
    def test_old_look_fades_into_the_new_one(self):
        w=self.window;tints=[]
        from unittest.mock import patch
        with patch.object(ui_kit,'tint_windows',side_effect=lambda app:tints.append(ui_kit.is_dark())):
            w.dark_box.setChecked(True);fade=w.theme_fade
            self.assertIsInstance(fade,ui_kit.Crossfade);self.assertIs(fade.parentWidget(),w.centralWidget());self.assertEqual(fade.geometry(),w.centralWidget().rect())
            self.assertFalse(fade.old.isNull());self.assertFalse(fade.new.isNull());self.assertTrue(fade.isVisible())
            self.assertTrue(fade.testAttribute(app_live.Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            self.assertEqual(tints,[])    # the title bar waits for the midpoint
            fade.step(.6);self.assertEqual(tints,[True]);fade.step(.8);self.assertEqual(tints,[True])
            fade.finish();self.assertFalse(fade.isVisible());self.assertTrue(fade.done)
        self.assertTrue(ui_kit.is_dark())
    def test_switching_again_ends_the_running_fade(self):
        w=self.window;w.dark_box.setChecked(True);first=w.theme_fade
        w.dark_box.setChecked(False);self.assertTrue(first.done);self.assertIsNot(w.theme_fade,first);self.assertFalse(ui_kit.is_dark())
        w.theme_fade.finish()
    def test_resizing_ends_the_fade(self):
        w=self.window;w.dark_box.setChecked(True);fade=w.theme_fade
        w.resize(1300,760);self.app.processEvents();self.assertTrue(fade.done)
    def test_no_fade_without_motion(self):
        from unittest.mock import patch
        with patch.object(ui_kit,'motion',return_value=False):self.window.dark_box.setChecked(True)
        self.assertIsNone(self.window.theme_fade);self.assertTrue(ui_kit.is_dark())

class DarkStartupTests(DarkModeCase):
    def test_saved_choice_is_read_at_start(self):
        (Path(self.temp.name)/'ui.json').write_text(json.dumps({'darkMode':True}),encoding='utf8')
        self.assertIs(app_live.ui_prefs()['darkMode'],True)
        ui_kit.set_theme(app_live.ui_prefs().get('darkMode') is True);window=app_live.Window();self.addCleanup(window.deleteLater)
        self.assertTrue(window.dark_box.isChecked())
    def test_damaged_preferences_mean_light(self):
        (Path(self.temp.name)/'ui.json').write_text('[1]',encoding='utf8');self.assertEqual(app_live.ui_prefs(),{})

class DarkPaletteTests(unittest.TestCase):
    def test_same_names_in_both_palettes(self):self.assertEqual(set(ui_kit.LIGHT),set(ui_kit.DARK))
    def test_text_colours_differ_between_palettes(self):
        # Cells are recoloured by value; a value in both palettes could be mapped twice.
        self.assertFalse({ui_kit.LIGHT[r] for r in ui_kit.TEXT_ROLES}&{ui_kit.DARK[r] for r in ui_kit.TEXT_ROLES})
    def test_readable_contrast(self):
        dark=ui_kit.DARK
        for text in ('INK','INK_2','MUTED'):
            for ground in ('PAPER','CANVAS','SELECT'):self.assertGreaterEqual(contrast(dark[text],dark[ground]),4.5,(text,ground))
        self.assertGreaterEqual(contrast(dark['ON_INK'],dark['INK']),4.5);self.assertGreaterEqual(contrast(dark['DANGER'],dark['PAPER']),4.5)
        self.assertGreaterEqual(contrast(dark['ON_INK'],dark['INK_HOVER']),4.5)
    def test_light_values_unchanged(self):
        self.assertEqual({k:ui_kit.LIGHT[k] for k in ('INK','INK_2','MUTED','FAINT','LINE','LINE_SOFT','CANVAS','PAPER','HOVER','SELECT','DANGER')},
                         {'INK':'#18181b','INK_2':'#3f3f46','MUTED':'#6b6b74','FAINT':'#a1a1aa','LINE':'#e4e4e7','LINE_SOFT':'#efeff1',
                          'CANVAS':'#f4f4f5','PAPER':'#ffffff','HOVER':'#f4f4f5','SELECT':'#ececef','DANGER':'#b42318'})

if __name__=='__main__':unittest.main()
