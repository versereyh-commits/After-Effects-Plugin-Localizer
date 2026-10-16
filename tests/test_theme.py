import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import tempfile
import unittest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QFontDatabase,QPalette
from PySide6.QtWidgets import QApplication,QCheckBox,QDialog,QLabel,QLineEdit,QTableWidget,QTableWidgetItem,QVBoxLayout
from app import apply_theme
import ui_kit


def luminance(color):
    channels=(color.redF(),color.greenF(),color.blueF())
    linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
    return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))

def contrast(first,second):
    low,high=sorted((luminance(first),luminance(second)))
    return (high+.05)/(low+.05)


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        # Windows 的 offscreen 插件可能没有自动枚举字体；只为截图加载系统已有的字体。
        if os.name=='nt' and not QFontDatabase.families():
            path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc'
            if path.is_file():
                font=QFontDatabase.addApplicationFont(str(path))
                if font>=0:cls.addClassCleanup(QFontDatabase.removeApplicationFont,font)
    def setUp(self):
        self.palette=QPalette(self.app.palette());self.style=self.app.style().objectName() or 'Fusion';self.sheet=self.app.styleSheet()
        self.scheme=self.app.styleHints().colorScheme()
        self.addCleanup(self.restore_theme)
        self.app.setStyleSheet('');self.app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
        dark=QPalette()
        for role,color in ((QPalette.ColorRole.Window,'#202020'),(QPalette.ColorRole.Base,'#121212'),
                           (QPalette.ColorRole.WindowText,'#eeeeee'),(QPalette.ColorRole.Text,'#eeeeee')):
            dark.setColor(role,QColor(color))
        self.app.setPalette(dark)
        # The window's real theme: palette plus the ui_kit stylesheet (the legacy app.py stylesheet is gone).
        apply_theme(self.app,ui_kit.stylesheet())
    def restore_theme(self):
        self.app.setStyleSheet('');self.app.styleHints().setColorScheme(self.scheme)
        self.app.setStyle(self.style);self.app.setPalette(self.palette);self.app.setStyleSheet(self.sheet)
    def test_palette_is_light_with_readable_text_in_all_groups(self):
        palette=self.app.palette()
        pairs=(('Window','WindowText'),('Base','Text'),('Button','ButtonText'),
               ('ToolTipBase','ToolTipText'),('Highlight','HighlightedText'))
        for group in (QPalette.ColorGroup.Active,QPalette.ColorGroup.Inactive,QPalette.ColorGroup.Disabled):
            for background,foreground in pairs:
                with self.subTest(group=group.name,background=background):
                    base=palette.color(group,getattr(QPalette.ColorRole,background))
                    text=palette.color(group,getattr(QPalette.ColorRole,foreground))
                    self.assertGreater(luminance(base),.75)
                    self.assertLess(luminance(text),.2)
                    self.assertGreaterEqual(contrast(base,text),4.5)
    def test_stylesheet_is_only_what_is_passed(self):
        extra='QLabel#badge { color:#245b3e; background:#deeee3; }'
        apply_theme(self.app,extra)
        self.assertEqual(self.app.styleSheet(),extra)
        badge=QLabel('已验证');self.addCleanup(badge.deleteLater);badge.setObjectName('badge');badge.ensurePolished()
        self.assertEqual(badge.palette().color(QPalette.ColorRole.WindowText).name(),'#245b3e')
        self.assertEqual(badge.palette().color(QPalette.ColorRole.Window).name(),'#deeee3')
    def test_checkbox_and_table_keep_visible_check_marks(self):
        checkbox=QCheckBox('启用翻译');checkbox.resize(180,40)
        table=QTableWidget(1,1);table.resize(180,100)
        item=QTableWidgetItem();item.setCheckState(Qt.CheckState.Unchecked);table.setItem(0,0,item)
        for widget,set_state in ((checkbox,checkbox.setCheckState),(table,item.setCheckState)):
            with self.subTest(widget=type(widget).__name__):
                self.addCleanup(widget.deleteLater);self.addCleanup(widget.close)
                widget.show();self.app.processEvents()
                unchecked=widget.grab().toImage()
                set_state(Qt.CheckState.Checked);self.app.processEvents()
                checked=widget.grab().toImage()
                changed=sum(abs(luminance(unchecked.pixelColor(x,y))-luminance(checked.pixelColor(x,y)))>.2
                            for y in range(checked.height()) for x in range(checked.width()))
                self.assertGreater(changed,5)
    def test_dialog_grab_has_visible_label_and_light_background(self):
        dialog=QDialog();self.addCleanup(dialog.deleteLater);self.addCleanup(dialog.close)
        dialog.setWindowTitle('DeepSeek 设置');dialog.resize(420,180)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(20,20,20,20)
        label=QLabel('API 密钥 / DeepSeek 设置');layout.addWidget(label)
        checkbox=QCheckBox('启用 DeepSeek 翻译');checkbox.setChecked(True);layout.addWidget(checkbox)
        edit=QLineEdit('离屏主题验证');layout.addWidget(edit)
        dialog.show();self.app.processEvents()
        screenshot=dialog.grab()
        # Set AE_LOCALIZER_THEME_SHOT to a folder to keep a screenshot for visual review.
        if os.environ.get('AE_LOCALIZER_THEME_SHOT'):self.assertTrue(screenshot.save(str(Path(os.environ['AE_LOCALIZER_THEME_SHOT'])/'theme-dialog.png')))
        image=screenshot.toImage();scale=screenshot.devicePixelRatio()
        background=luminance(image.pixelColor(int(5*scale),int(5*scale)))
        self.assertGreater(background,.75)
        rect=label.geometry()
        pixels=[luminance(image.pixelColor(x,y))
                for y in range(int(rect.top()*scale),int((rect.bottom()+1)*scale))
                for x in range(int(rect.left()*scale),int((rect.right()+1)*scale))]
        # 字体、抗锯齿和缩放因机器而异，只检查标签区域确实有明显的深色字形。
        self.assertGreater(sum(background-value>.25 for value in pixels),20)
    def test_no_legacy_navy_left(self):
        sheet=ui_kit.stylesheet().lower()
        for colour in ('#253445','#245b83','#f3f5f7','#566578','#172c42'):self.assertNotIn(colour,sheet)
        box=QCheckBox('启用');self.addCleanup(box.deleteLater);box.ensurePolished()
        self.assertEqual(box.palette().color(QPalette.ColorRole.WindowText).name(),ui_kit.INK)


if __name__=='__main__':unittest.main()
