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
        # As main() sets up: no application sheet, the windows carry it (ui_kit.restyle). Both at once is slow.
        self.addCleanup(lambda:(ui_kit.set_theme(False),apply_theme(self.app,''),setattr(ui_kit,'WINDOW_SHEET',None)))
    def prefs(self):return json.loads((Path(self.temp.name)/'ui.json').read_text(encoding='utf8'))

class DarkModeTests(DarkModeCase):
    def test_off_by_default(self):
        w=self.window;self.assertFalse(w.dark_box.isChecked());self.assertFalse(ui_kit.is_dark());self.assertEqual(ui_kit.INK,'#18181b')
        self.assertEqual(w.dark_box.text(),'深色模式')
    def test_switching_recolours_everything_and_is_remembered(self):
        w=self.window;apply_theme(self.app,'');ui_kit.restyle(self.app,ui_kit.stylesheet());light=ui_kit.sheet_in_effect(w)
        row=self.rows_for('tc Shine')[0];self.assertEqual(self.table.item(row,ORIGINAL).foreground().color().name(),ui_kit.LIGHT['INK'])
        self.assertEqual(self.table.item(row,KEY).foreground().color().name(),ui_kit.LIGHT['MUTED'])
        w.dark_box.setChecked(True)
        self.assertTrue(ui_kit.is_dark());self.assertEqual(ui_kit.INK,ui_kit.DARK['INK']);self.assertIs(self.prefs()['darkMode'],True)
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window).name(),'#131316')
        sheet=ui_kit.sheet_in_effect(w);self.assertIn(ui_kit.DARK['CANVAS'],sheet)
        self.assertEqual(self.app.styleSheet(),'')    # the application never carries the sheet (ui_kit.restyle)
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
        self.assertFalse(ui_kit.is_dark());self.assertEqual(ui_kit.sheet_in_effect(w),light);self.assertIs(self.prefs()['darkMode'],False)
        self.assertEqual(self.table.item(row,ORIGINAL).foreground().color().name(),ui_kit.LIGHT['INK'])
        self.assertEqual(w.refresh_btn.icon().pixmap(16).toImage(),ui_kit.icon('refresh',ui_kit.LIGHT['INK_2'],16).pixmap(16).toImage())
    def test_restyle_puts_the_sheet_on_parentless_windows_only(self):
        # A theme switch restyles the windows, not the application (about a third of the time).
        from PySide6.QtWidgets import QDialog,QLabel,QWidget
        before=self.app.styleSheet();loose=QWidget();child=QLabel(loose);self.addCleanup(loose.deleteLater)
        sheet=ui_kit.stylesheet();ui_kit.restyle(self.app,sheet)
        self.assertEqual(self.app.styleSheet(),before);self.assertEqual(loose.styleSheet(),sheet);self.assertEqual(child.styleSheet(),'')
        self.assertEqual(self.window.styleSheet(),sheet);self.assertEqual(ui_kit.sheet_in_effect(child),sheet)
        # A parentless window made afterwards takes the current sheet when first polished; one with a parent inherits.
        styler=ui_kit.PopupStyler(self.app);self.app.installEventFilter(styler);self.addCleanup(lambda:self.app.removeEventFilter(styler))
        late=QDialog();owned=QDialog(self.window);self.addCleanup(late.deleteLater);self.addCleanup(owned.deleteLater)
        late.ensurePolished();owned.ensurePolished()
        self.assertEqual(late.styleSheet(),sheet);self.assertEqual(owned.styleSheet(),'')
        # A new main window carries it from the start.
        again=app_live.Window();self.addCleanup(again.deleteLater);self.assertEqual(again.styleSheet(),sheet)
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
        # The title bars fade with the picture: every frame tints them part of the way, in step with its progress.
        with patch.object(ui_kit,'tint_between',side_effect=lambda app,old,new,t,dark:tints.append((round(t,2),old['PAPER'],new['PAPER'],dark))),\
             patch.object(ui_kit,'tint_windows',side_effect=lambda app:self.fail('title bars must not jump during the fade')):
            w.dark_box.setChecked(True);fade=w.theme_fade
            self.assertIsInstance(fade,ui_kit.Crossfade);self.assertIs(fade.parentWidget(),w.centralWidget());self.assertEqual(fade.geometry(),w.centralWidget().rect())
            self.assertFalse(fade.old.isNull());self.assertFalse(fade.new.isNull());self.assertTrue(fade.isVisible())
            self.assertTrue(fade.testAttribute(app_live.Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            fade.step(.25);fade.step(.6);fade.finish();self.assertFalse(fade.isVisible());self.assertTrue(fade.done)
        light,dark=ui_kit.LIGHT['PAPER'],ui_kit.DARK['PAPER']
        self.assertEqual(tints[-3:],[(.25,light,dark,True),(.6,light,dark,True),(1.0,light,dark,True)])
        self.assertTrue(ui_kit.is_dark())
    def test_title_bar_colours_are_mixed_part_of_the_way(self):
        from unittest.mock import patch
        self.assertEqual(ui_kit.mix('#000000','#ffffff',.5).name(),'#808080');self.assertEqual(ui_kit.mix('#102030','#102030',.7).name(),'#102030')
        calls=[]
        with patch.object(ui_kit,'tint_title_bar',side_effect=lambda w,caption,text,border,dark:calls.append((QColor(caption).name(),dark)) if w is self.window else None):
            ui_kit.tint_between(self.app,ui_kit.LIGHT,ui_kit.DARK,0.0,True);ui_kit.tint_between(self.app,ui_kit.LIGHT,ui_kit.DARK,1.0,True)
        # Caption buttons can only be light or dark: they turn at the midpoint.
        self.assertEqual(calls,[(QColor(ui_kit.LIGHT['PAPER']).name(),False),(QColor(ui_kit.DARK['PAPER']).name(),True)])
    def test_without_fade_the_title_bars_follow_the_repainted_window(self):
        from unittest.mock import patch
        order=[]
        with patch.object(ui_kit,'motion',return_value=False),patch.object(self.window,'repaint',side_effect=lambda:order.append('window')),\
             patch.object(ui_kit,'tint_windows',side_effect=lambda app:order.append('title bars')):
            self.window.dark_box.setChecked(True)
        self.assertEqual(order,['window','title bars'])
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
