"""Shared Qt helpers: the background job thread and the light or dark application theme.

The first-generation window and its stylesheet used to live here; the interface is app_live.py and its
stylesheet is ui_kit.stylesheet(), passed to apply_theme as `extra`."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QColor, QPalette


class Job(QThread):
    result=Signal(object)
    failure=Signal(str)
    progress=Signal(str)
    def __init__(self,work):
        super().__init__()
        self.work=work
    def run(self):
        try:
            self.result.emit(self.work(self.progress.emit))
        except Exception as error:
            self.failure.emit(str(error))


PALETTES={
    False:{
        'Window':'#f4f4f5','WindowText':'#18181b','Base':'#ffffff','AlternateBase':'#fafafa',
        'Text':'#18181b','Button':'#ffffff','ButtonText':'#18181b','BrightText':'#ffffff',
        'Light':'#ffffff','Midlight':'#f4f4f5','Dark':'#a1a1aa','Mid':'#d4d4d8','Shadow':'#52525b',
        'Highlight':'#ececef','HighlightedText':'#18181b','ToolTipBase':'#ffffff','ToolTipText':'#18181b',
        'Link':'#18181b','LinkVisited':'#3f3f46','PlaceholderText':'#8a8a93','Accent':'#18181b',
        'disabled':'#52525b','disabledBase':'#f4f4f5','disabledButton':'#efeff1',
    },
    True:{
        'Window':'#131316','WindowText':'#ececee','Base':'#1c1c20','AlternateBase':'#202024',
        'Text':'#ececee','Button':'#1c1c20','ButtonText':'#ececee','BrightText':'#ffffff',
        'Light':'#2e2e34','Midlight':'#26262b','Dark':'#0c0c0e','Mid':'#34343a','Shadow':'#000000',
        'Highlight':'#2e2e34','HighlightedText':'#ececee','ToolTipBase':'#ececee','ToolTipText':'#18181b',
        'Link':'#ececee','LinkVisited':'#c4c4ca','PlaceholderText':'#7c7c85','Accent':'#ececee',
        'disabled':'#6b6b74','disabledBase':'#131316','disabledButton':'#1a1a1e',
    },
}

def apply_theme(app,extra='',dark=False):
    """Light (or, when asked, dark) Fusion palette for every widget, whatever the Windows colour scheme, plus the given
    stylesheet. extra=None keeps the application's stylesheet as it is (a theme switch restyles the windows instead)."""
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
    palette=QPalette();colors=PALETTES[bool(dark)]
    for group in (QPalette.ColorGroup.Active,QPalette.ColorGroup.Inactive,QPalette.ColorGroup.Disabled):
        for role,color in colors.items():
            if not role.startswith('disabled'):palette.setColor(group,getattr(QPalette.ColorRole,role),QColor(color))
    for role in ('WindowText','Text','ButtonText','ToolTipText','PlaceholderText','HighlightedText'):
        palette.setColor(QPalette.ColorGroup.Disabled,getattr(QPalette.ColorRole,role),QColor(colors['disabled']))
    palette.setColor(QPalette.ColorGroup.Disabled,QPalette.ColorRole.Base,QColor(colors['disabledBase']))
    palette.setColor(QPalette.ColorGroup.Disabled,QPalette.ColorRole.Button,QColor(colors['disabledButton']))
    app.setPalette(palette)
    # Setting the style again rebuilds and re-polishes every widget, so it is done only once.
    if app.style().name().lower()!='fusion':app.setStyle('Fusion')
    if extra is not None:app.setStyleSheet(extra)
