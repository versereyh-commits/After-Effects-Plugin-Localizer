"""Design system: a monochrome ink-on-paper language with soft depth and purposeful motion.

Everything is drawn with QPainter (icons, shadows, loaders, keyframe diamonds), so the frozen
build needs no image plugins. Motion follows the Windows "show animations" setting and can be
switched off in Settings; with motion off every widget jumps straight to its final state."""
from __future__ import annotations
import ctypes,math,os,re,sys,tempfile,time
from pathlib import Path
from PySide6.QtCore import Qt,QEvent,QObject,QEasingCurve,QPointF,QPropertyAnimation,QRect,QRectF,QSize,QTimer,QVariantAnimation,Signal
from PySide6.QtGui import QColor,QCursor,QFont,QFontMetrics,QIcon,QPainter,QPainterPath,QPen,QPixmap,QPolygonF
from PySide6.QtWidgets import (QAbstractItemView,QApplication,QComboBox,QDialog,QMainWindow,QListWidget,QFrame,QGraphicsOpacityEffect,QHBoxLayout,QHeaderView,QLabel,QLineEdit,QMenu,QPushButton,QSizePolicy,QStackedWidget,
                               QStyle,QStyledItemDelegate,QStyleOptionViewItem,QToolButton,QVBoxLayout,QWidget)

# Tokens: one ink, one paper, greys between. Colour appears only for errors.
# Dark mode swaps the two: chalk on slate, the ink blocks turn light with dark text on them.
LIGHT=dict(INK='#18181b',INK_2='#3f3f46',MUTED='#6b6b74',FAINT='#a1a1aa',LINE='#e4e4e7',LINE_SOFT='#efeff1',
           CANVAS='#f4f4f5',PAPER='#ffffff',HOVER='#f4f4f5',SELECT='#ececef',DANGER='#b42318',DANGER_HOVER='#9a1c13',
           ON_INK='#ffffff',ON_INK_MUTED='#a1a1aa',INK_HOVER='#2c2c31',INK_PRESS='#000000',INK_DIVIDER='#3f3f46',
           BORDER_HOVER='#c9c9cf',BUTTON_HOVER='#fafafa',ROW_HOVER='#f8f8f9',CARD_HOVER='#f7f7f8',SCROLL='#d4d4d8',BOX='#b8b8c0',TILE_HOVER='#d4d4d8',
           INK_DISABLED='#d9d9de',ON_INK_DISABLED='#f6f6f7',CHECK_DISABLED='#a1a1aa',TIP_KEY_FG='#d4d4d8',TIP_KEY_BG='#2f2f35',
           SHADOW_RGB=(24,24,27),SHADOW_GAIN=1.0)
DARK=dict(INK='#ececee',INK_2='#c4c4ca',MUTED='#9a9aa3',FAINT='#6e6e77',LINE='#34343a',LINE_SOFT='#2a2a2f',
          CANVAS='#131316',PAPER='#1c1c20',HOVER='#26262b',SELECT='#2e2e34',DANGER='#ec5d61',DANGER_HOVER='#f2777a',
          ON_INK='#18181b',ON_INK_MUTED='#6b6b74',INK_HOVER='#d4d4d8',INK_PRESS='#ffffff',INK_DIVIDER='#c4c4ca',
          BORDER_HOVER='#4a4a52',BUTTON_HOVER='#232327',ROW_HOVER='#222226',CARD_HOVER='#232327',SCROLL='#3f3f46',BOX='#55555d',TILE_HOVER='#4a4a52',
          INK_DISABLED='#3a3a40',ON_INK_DISABLED='#6b6b74',CHECK_DISABLED='#55555d',TIP_KEY_FG='#3f3f46',TIP_KEY_BG='#d4d4d8',
          SHADOW_RGB=(0,0,0),SHADOW_GAIN=2.6)
INK=INK_2=MUTED=FAINT=LINE=LINE_SOFT=CANVAS=PAPER=HOVER=SELECT=DANGER=DANGER_HOVER=ON_INK=ON_INK_MUTED=INK_HOVER=INK_PRESS=INK_DIVIDER=''
BORDER_HOVER=BUTTON_HOVER=ROW_HOVER=CARD_HOVER=SCROLL=BOX=TILE_HOVER=INK_DISABLED=ON_INK_DISABLED=CHECK_DISABLED=TIP_KEY_FG=TIP_KEY_BG=''
SHADOW_RGB=(24,24,27);SHADOW_GAIN=1.0;DARK_MODE=False
def set_theme(dark:bool):
    """Points every colour name at the light or the dark palette; widgets pick it up when they next paint."""
    global DARK_MODE
    DARK_MODE=bool(dark);globals().update(DARK if DARK_MODE else LIGHT)
def is_dark()->bool:return DARK_MODE
def tone(role:str)->str:
    """The current value of a colour name such as 'INK_2'."""
    return (DARK if DARK_MODE else LIGHT)[role]
def shadow(alpha:float)->QColor:
    r,g,b=SHADOW_RGB;return QColor(r,g,b,max(0,min(255,int(alpha*SHADOW_GAIN))))
set_theme(False)
UI_FAMILIES=('Microsoft YaHei UI',);NUMERAL_FAMILIES=('Segoe UI Variable Display','Segoe UI','Microsoft YaHei UI');MONO="'Cascadia Mono'"

# Motion.
_motion=None
def system_motion()->bool:
    if os.environ.get('QT_QPA_PLATFORM')=='offscreen' or os.environ.get('AE_LOCALIZER_NO_MOTION'):return False
    try:
        value=ctypes.c_int(1);ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(value),0);return bool(value.value)
    except Exception:return True
def motion()->bool:
    global _motion
    if _motion is None:_motion=system_motion()
    return _motion
def set_motion(enabled:bool):
    global _motion
    _motion=bool(enabled) and system_motion()

def animate(target,prop,end,duration=220,curve=QEasingCurve.Type.OutCubic,start=None,finished=None):
    """Runs a property animation, or sets the end value at once when motion is off."""
    if not motion():
        target.setProperty(prop.decode() if isinstance(prop,bytes) else prop,end)
        if finished:finished()
        return None
    animation=QPropertyAnimation(target,prop if isinstance(prop,bytes) else prop.encode(),target)
    animation.setDuration(duration);animation.setEasingCurve(curve)
    if start is not None:animation.setStartValue(start)
    animation.setEndValue(end)
    if finished:animation.finished.connect(finished)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped);return animation

# Icons on a 24-unit grid with round strokes.
def _stroke(painter,color,width=1.7):
    pen=QPen(QColor(color),width);pen.setCapStyle(Qt.PenCapStyle.RoundCap);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin);painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush)
def _poly(painter,*points,closed=False):
    polygon=QPolygonF([QPointF(x,y) for x,y in points])
    painter.drawPolygon(polygon) if closed else painter.drawPolyline(polygon)
def _diamond(cx,cy,r)->QPolygonF:return QPolygonF([QPointF(cx,cy-r),QPointF(cx+r,cy),QPointF(cx,cy+r),QPointF(cx-r,cy)])

def _draw(name,painter,color):
    _stroke(painter,color)
    if name=='effects':
        path=QPainterPath(QPointF(11,3));path.cubicTo(11.5,8.5,14.5,11.5,20,12);path.cubicTo(14.5,12.5,11.5,15.5,11,21);path.cubicTo(10.5,15.5,7.5,12.5,2,12);path.cubicTo(7.5,11.5,10.5,8.5,11,3)
        painter.drawPath(path);painter.drawLine(QPointF(19,3),QPointF(19,7));painter.drawLine(QPointF(17,5),QPointF(21,5))
    elif name=='params':
        for y in (6,12,18):painter.drawLine(QPointF(3,y),QPointF(21,y))
        painter.setBrush(QColor(PAPER))
        for y,x in ((6,8),(12,16),(18,10)):painter.drawEllipse(QPointF(x,y),2.4,2.4)
    elif name=='schemes':_poly(painter,(6,3.5),(18,3.5),(18,20.5),(12,16),(6,20.5),closed=True)
    elif name=='versions':
        _poly(painter,(12,3),(21,8),(12,13),(3,8),closed=True);_poly(painter,(3,12),(12,17),(21,12));_poly(painter,(3,16),(12,21),(21,16))
    elif name=='settings':
        painter.drawEllipse(QPointF(12,12),3,3);painter.drawEllipse(QPointF(12,12),7.2,7.2)
        for step in range(8):
            angle=step*math.pi/4;painter.drawLine(QPointF(12+7.2*math.cos(angle),12+7.2*math.sin(angle)),QPointF(12+9.6*math.cos(angle),12+9.6*math.sin(angle)))
    elif name=='search':painter.drawEllipse(QPointF(10.5,10.5),6.2,6.2);painter.drawLine(QPointF(15.2,15.2),QPointF(20,20))
    elif name=='clear':
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(color));painter.drawEllipse(QPointF(12,12),9,9)
        _stroke(painter,PAPER,2.0);painter.drawLine(QPointF(8.8,8.8),QPointF(15.2,15.2));painter.drawLine(QPointF(15.2,8.8),QPointF(8.8,15.2))
    elif name=='chevron':_poly(painter,(7,9.5),(12,14.5),(17,9.5))
    elif name=='chevron_up':_poly(painter,(7,14.5),(12,9.5),(17,14.5))
    elif name=='chevron_right':_poly(painter,(9.5,7),(14.5,12),(9.5,17))
    elif name=='tick':_stroke(painter,color,3.0);_poly(painter,(5.5,12.5),(10,17),(18.5,7.5))
    elif name=='check':_stroke(painter,color,2.3);_poly(painter,(5,12.5),(10,17),(19,7.5))
    elif name=='refresh':painter.drawArc(QRectF(4.5,4.5,15,15),40*16,290*16);_poly(painter,(19.5,4.5),(19.6,9.6),(14.5,9.4))
    elif name=='restore':painter.drawArc(QRectF(4.5,4.5,15,15),110*16,-290*16);_poly(painter,(4.5,4.5),(4.4,9.6),(9.5,9.4))
    elif name=='warning':
        _poly(painter,(12,3.5),(21.5,20),(2.5,20),closed=True);painter.drawLine(QPointF(12,9.5),QPointF(12,14));painter.setBrush(QColor(color));painter.drawEllipse(QPointF(12,17),.9,.9)
    elif name=='info':
        painter.drawEllipse(QPointF(12,12),9,9);painter.drawLine(QPointF(12,11),QPointF(12,16.5));painter.setBrush(QColor(color));painter.drawEllipse(QPointF(12,7.6),.9,.9)
    elif name=='sparkle':
        path=QPainterPath(QPointF(10,4));path.cubicTo(10.4,8.6,12.4,10.6,17,11);path.cubicTo(12.4,11.4,10.4,13.4,10,18);path.cubicTo(9.6,13.4,7.6,11.4,3,11);path.cubicTo(7.6,10.6,9.6,8.6,10,4)
        painter.drawPath(path);painter.drawEllipse(QPointF(18.5,18.5),1.6,1.6)
    elif name=='folder':_poly(painter,(3,6),(9,6),(11,8.5),(21,8.5),(21,19),(3,19),closed=True)
    elif name=='arrow':painter.drawLine(QPointF(4,12),QPointF(19,12));_poly(painter,(13.5,6.5),(19,12),(13.5,17.5))
    elif name=='more':
        painter.setBrush(QColor(color))
        for x in (6,12,18):painter.drawEllipse(QPointF(x,12),1.2,1.2)
    elif name=='inbox':
        _poly(painter,(3,13),(8,13),(9.5,16),(14.5,16),(16,13),(21,13));_poly(painter,(3,13),(6,5),(18,5),(21,13),(21,19),(3,19),closed=True)
    elif name=='diamond':painter.drawPolygon(_diamond(12,12,8))
    elif name=='trash':
        painter.drawLine(QPointF(4,6.5),QPointF(20,6.5));_poly(painter,(9.5,6.5),(9.5,4),(14.5,4),(14.5,6.5));_poly(painter,(6,6.5),(7,20),(17,20),(18,6.5))
        painter.drawLine(QPointF(10,10.5),QPointF(10,16.5));painter.drawLine(QPointF(14,10.5),QPointF(14,16.5))
    elif name=='plug':
        _poly(painter,(8,3),(8,8));_poly(painter,(16,3),(16,8));_poly(painter,(5,8),(19,8),(19,12),(15.5,16),(8.5,16),(5,12),closed=True);painter.drawLine(QPointF(12,16),QPointF(12,21))

_cache:dict={}
def pixmap(name,color=None,size=16)->QPixmap:
    color=color or INK_2;key=(name,color,PAPER if name in ('params','clear') else '',size)
    if key not in _cache:
        ratio=2.0;image=QPixmap(int(size*ratio),int(size*ratio));image.fill(Qt.GlobalColor.transparent);image.setDevicePixelRatio(ratio)
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.scale(size/24,size/24);_draw(name,painter,color);painter.end()
        _cache[key]=image
    return _cache[key]

def icon(name,color=None,size=16)->QIcon:
    result=QIcon(pixmap(name,color,size));result.addPixmap(pixmap(name,FAINT,size),QIcon.Mode.Disabled);return result

# Icons that follow the theme: the widget remembers which glyph and which colour name it shows,
# so retheme() can draw it again in the other palette.
def themed_icon(widget,name,role='INK_2',size=16):
    widget.setProperty('themeIcon',f'icon,{name},{role},{size}');widget.setIcon(icon(name,tone(role),size));return widget
def themed_pixmap(widget,name,role='INK_2',size=16):
    widget.setProperty('themeIcon',f'pixmap,{name},{role},{size}');widget.setPixmap(pixmap(name,tone(role),size));return widget
def themed_logo(widget,size=28):
    widget.setProperty('themeIcon',f'logo,,,{size}');widget.setPixmap(logo(size));return widget
def redraw_icon(widget)->bool:
    spec=widget.property('themeIcon')
    if not spec:return False
    kind,name,role,size=str(spec).split(',')
    if kind=='logo':widget.setPixmap(logo(int(size)))
    elif kind=='icon':widget.setIcon(icon(name,tone(role),int(size)))
    else:widget.setPixmap(pixmap(name,tone(role),int(size)))
    return True

class Crossfade(QWidget):
    """Covers a widget with a snapshot of its old look and fades into a snapshot of the new one.
    Only two pixmaps are drawn per frame, so nothing underneath repaints while it runs; clicks pass through."""
    def __init__(self,target:QWidget,old:QPixmap):
        super().__init__(target);self.old=old;self.new=None;self.progress=0.0;self.animation=None;self.halfway=None;self.done=False
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent);self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(target.rect());self.show();self.raise_();target.installEventFilter(self)
    def start(self,duration=300,halfway=None):
        """Takes the new look (with itself hidden) and starts fading; halfway runs once at the midpoint."""
        self.hide();self.new=self.parentWidget().grab();self.show();self.raise_();self.halfway=halfway
        animation=QVariantAnimation(self);animation.setStartValue(0.0);animation.setEndValue(1.0);animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.InOutQuad);animation.valueChanged.connect(self.step);animation.finished.connect(self.finish)
        self.animation=animation;animation.start()
    def step(self,value):
        self.progress=float(value)
        if self.progress>=.5:self.run_halfway()
        self.update()
    def run_halfway(self):
        task,self.halfway=self.halfway,None
        if task:task()
    def finish(self):
        """Ends at once (also when interrupted): the real widgets underneath already show the new look."""
        if self.done:return
        self.done=True;self.run_halfway()
        if self.animation is not None:self.animation.stop()
        parent=self.parentWidget()
        if parent is not None:parent.removeEventFilter(self)
        self.hide();self.deleteLater()
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.Resize:self.finish()
        return False
    def paintEvent(self,event):
        painter=QPainter(self)
        if self.new is not None:painter.drawPixmap(0,0,self.new)
        painter.setOpacity(1.0-self.progress);painter.drawPixmap(0,0,self.old)

TEXT_ROLES=('INK','INK_2','MUTED','FAINT')
def tint_windows(app):
    for widget in app.topLevelWidgets():
        if widget.isVisible() and isinstance(widget,(QMainWindow,QDialog)):tint_title_bar(widget)
def retheme(app,previous:dict,tint=True):
    """After set_theme(): redraws remembered icons, recolours table text set from the old palette, tints title bars."""
    from PySide6.QtWidgets import QListWidget as _List,QTableWidget as _Table
    swap={QColor(previous[role]).name():QColor(tone(role)) for role in TEXT_ROLES}
    for widget in app.allWidgets():
        redraw_icon(widget)
        if isinstance(widget,NavButton):widget.paint_state(widget.isChecked())
        elif isinstance(widget,(_Table,_List)):
            # A colour change is not an edit: signals stay blocked (itemChanged would save candidates and recount
            # for every cell) and sorting is paused (each change would re-sort and could visit an item twice).
            sorting=widget.isSortingEnabled();widget.setSortingEnabled(False);blocked=widget.blockSignals(True)
            try:
                if isinstance(widget,_Table):items=[widget.item(row,column) for row in range(widget.rowCount()) for column in range(widget.columnCount())]
                else:items=[widget.item(row) for row in range(widget.count())]
                for item in items:
                    if item is not None and item.data(Qt.ItemDataRole.ForegroundRole) is not None:
                        new=swap.get(item.foreground().color().name())
                        if new is not None:item.setForeground(new)
            finally:
                widget.blockSignals(blocked)
                if sorting:widget.setSortingEnabled(True)
            widget.viewport().update()
        widget.update()
    if tint:tint_windows(app)

def logo(size=28,ink=None,paper=None)->QPixmap:
    ratio=2.0;image=QPixmap(int(size*ratio),int(size*ratio));image.fill(Qt.GlobalColor.transparent);image.setDevicePixelRatio(ratio)
    painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ink or INK));painter.drawRoundedRect(QRectF(0,0,size,size),size*.3,size*.3)
    painter.setBrush(QColor(paper or PAPER));painter.drawPolygon(_diamond(size/2,size/2,size*.22));painter.end();return image

# Loaders: a small moving figure beside a verb says what the button is doing.
LOADER_PERIOD={'orbit':1.1,'arc':.9,'breathe':1.4,'merge':1.3,'fill':1.2,'rewind':1.0,'scan':1.1}
def loader_pixmap(kind,phase,color,size=16)->QPixmap:
    ratio=2.0;image=QPixmap(int(size*ratio),int(size*ratio));image.fill(Qt.GlobalColor.transparent);image.setDevicePixelRatio(ratio)
    p=QPainter(image);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.scale(size/24,size/24);ink=QColor(color);faint=QColor(color);faint.setAlphaF(.25)
    if kind=='orbit':
        p.setPen(QPen(faint,1.6));p.drawEllipse(QPointF(12,12),8,8);angle=phase*2*math.pi-math.pi/2
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(ink);p.drawEllipse(QPointF(12+8*math.cos(angle),12+8*math.sin(angle)),2.6,2.6)
    elif kind in ('arc','rewind'):
        pen=QPen(faint,2.2);p.setPen(pen);p.drawEllipse(QPointF(12,12),8,8)
        pen.setColor(ink);pen.setCapStyle(Qt.PenCapStyle.RoundCap);p.setPen(pen);start=int((-phase if kind=='rewind' else phase)*360*16)
        p.drawArc(QRectF(4,4,16,16),-start,100*16)
    elif kind=='breathe':
        wave=.5-.5*math.cos(phase*2*math.pi);p.setPen(Qt.PenStyle.NoPen);halo=QColor(ink);halo.setAlphaF(.18*(1-wave))
        p.setBrush(halo);p.drawEllipse(QPointF(12,12),4+6*wave,4+6*wave);p.setBrush(ink);p.drawEllipse(QPointF(12,12),3.2+1.6*wave,3.2+1.6*wave)
    elif kind=='merge':
        d=6*abs(math.cos(phase*math.pi));p.setPen(Qt.PenStyle.NoPen);p.setBrush(ink)
        p.drawEllipse(QPointF(12-d,12),3.2,3.2);p.drawEllipse(QPointF(12+d,12),3.2-.8*(1-d/6),3.2-.8*(1-d/6))
    elif kind=='fill':
        shape=QPainterPath();shape.addPolygon(_diamond(12,12,8.5));shape.closeSubpath()
        p.setPen(QPen(ink,1.6));p.drawPath(shape);level=20.5-17*(.5-.5*math.cos(phase*2*math.pi))
        p.setClipPath(shape);p.fillRect(QRectF(0,level,24,24),ink)
    elif kind=='scan':
        p.setPen(Qt.PenStyle.NoPen)
        for index in range(3):
            height=4+10*(.5-.5*math.cos((phase-index*.18)*2*math.pi));p.setBrush(ink);p.drawRoundedRect(QRectF(5+index*5.5,12-height/2,3.2,height),1.6,1.6)
    p.end();return image

def stylesheet()->str:
    folder=Path(tempfile.gettempdir())/'ae-localizer-ui';folder.mkdir(exist_ok=True)
    def asset(name,color):
        path=folder/f"{name}-{color.strip('#')}.png"
        if not path.exists():pixmap(name,color,12).save(str(path))
        return path.as_posix()
    down=asset('chevron',MUTED);down_light=asset('chevron',ON_INK);up=asset('chevron_up',MUTED);right=asset('chevron_right',MUTED);tick=asset('tick',ON_INK)
    return f"""
QWidget {{ font-size:13px; color:{INK}; }}
QMainWindow,QDialog,QMessageBox,QFileDialog {{ background:{CANVAS}; }}
QScrollArea#pageScroll,QScrollArea#pageScroll > QWidget,QWidget#scrollPage {{ background:transparent; border:none; }}
QDialog,QMessageBox,QFileDialog,QMenu,QCheckBox,QComboBox QAbstractItemView {{ color:{INK}; }}
QCheckBox:disabled {{ color:{FAINT}; }}
QLabel#muted,QLabel#hint {{ color:{MUTED}; font-size:12px; }}
QLabel#field {{ color:{MUTED}; }}
QLabel#mono {{ color:{INK_2}; font-family:{MONO}; font-size:12px; }}

QPushButton,QToolButton {{ background:{PAPER}; border:1px solid {LINE}; border-radius:16px; padding:0 16px; min-height:32px; color:{INK}; }}
QPushButton:hover,QToolButton:hover {{ border-color:{BORDER_HOVER}; background:{BUTTON_HOVER}; }}
QPushButton:pressed,QToolButton:pressed {{ background:{SELECT}; }}
QPushButton#primary,QToolButton#primary {{ background:{INK}; border-color:{INK}; color:{ON_INK}; font-weight:600; }}
QPushButton#primary:hover,QToolButton#primary:hover {{ background:{INK_HOVER}; border-color:{INK_HOVER}; }}
QPushButton#primary:pressed {{ background:{INK_PRESS}; }}
QPushButton#quiet,QToolButton#quiet {{ background:transparent; border-color:transparent; color:{INK_2}; }}
QPushButton#quiet:hover,QToolButton#quiet:hover {{ background:{HOVER}; }}
QPushButton#link {{ background:transparent; border:none; color:{INK}; padding:0 2px; min-height:24px; text-decoration:underline; text-align:left; }}
QPushButton#link:hover {{ color:{INK_2}; }}
QPushButton#danger {{ color:{DANGER}; }}
QPushButton#icon,QToolButton#icon {{ padding:0; min-width:34px; max-width:34px; min-height:32px; max-height:34px; border-radius:17px; }}
QPushButton#icon[attention="true"] {{ background:{INK}; border-color:{INK}; }}
QPushButton#icon[attention="true"]:hover {{ background:{INK_HOVER}; border-color:{INK_HOVER}; }}
QPushButton:disabled,QToolButton:disabled {{ background:{CANVAS}; color:{FAINT}; border-color:{LINE_SOFT}; }}
QPushButton#primary:disabled,QToolButton#primary:disabled {{ background:{INK_DISABLED}; color:{ON_INK_DISABLED}; border-color:{INK_DISABLED}; }}
QPushButton#link:disabled,QPushButton#quiet:disabled {{ background:transparent; border-color:transparent; }}
QToolButton::menu-indicator {{ image:url({down}); subcontrol-origin:padding; subcontrol-position:right center; right:12px; width:10px; height:10px; }}
QToolButton#primary::menu-indicator {{ image:url({down_light}); }}
QToolButton[popupMode="2"] {{ padding-right:32px; }}
QToolButton[popupMode="1"] {{ padding-right:36px; }}
QToolButton::menu-button {{ border:none; border-left:1px solid {LINE}; width:26px; border-top-right-radius:16px; border-bottom-right-radius:16px; }}
QToolButton#primary::menu-button {{ border-left:1px solid {INK_DIVIDER}; }}
QToolButton::menu-arrow {{ image:url({down}); width:10px; height:10px; }}
QToolButton#primary::menu-arrow {{ image:url({down_light}); }}

QLineEdit,QComboBox {{ background:{PAPER}; border:1px solid {LINE}; border-radius:16px; padding:0 14px; min-height:32px; selection-background-color:{SELECT}; selection-color:{INK}; }}
QLineEdit:hover,QComboBox:hover {{ border-color:{BORDER_HOVER}; }}
QLineEdit:focus,QComboBox:focus,QComboBox:on {{ border-color:{INK}; }}
QLineEdit:disabled,QComboBox:disabled {{ background:{CANVAS}; color:{FAINT}; }}

QFrame#searchField {{ background:{CANVAS}; border:1px solid {CANVAS}; border-radius:17px; }}
QFrame#searchField[focus="true"] {{ background:{PAPER}; border-color:{INK}; }}
QLineEdit#searchEdit {{ background:transparent; border:none; padding:0; min-height:32px; }}
QToolButton#searchClear, QToolButton#searchClear:hover, QToolButton#searchClear:pressed {{ background:transparent; border:none; padding:0; margin:0; }}
QLineEdit#field {{ border-radius:12px; min-height:38px; font-size:14px; }}
QComboBox {{ padding-right:32px; }}
QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:center right; width:30px; border:none; }}
QComboBox::down-arrow {{ image:url({down}); width:10px; height:10px; }}
QComboBox {{ combobox-popup:0; }}
QComboBoxPrivateContainer {{ background:{PAPER}; border:none; }}
QComboBox QAbstractItemView {{ background:{PAPER}; border:none; padding:6px; outline:0; selection-background-color:{HOVER}; selection-color:{INK}; }}
QComboBox QAbstractItemView::item {{ min-height:32px; padding:0 12px; border-radius:8px; color:{INK}; }}
QComboBox QAbstractItemView::item:hover {{ background:{HOVER}; }}
QComboBox QAbstractItemView::item:selected {{ background:{SELECT}; color:{INK}; }}
QMenu {{ background:{PAPER}; border:none; padding:6px; }}
QMenu::item {{ padding:8px 32px 8px 14px; border-radius:8px; min-width:120px; }}
QMenu::item:selected {{ background:{HOVER}; color:{INK}; }}
QMenu::item:disabled {{ color:{FAINT}; }}
QMenu::separator {{ height:1px; background:{LINE_SOFT}; margin:5px 8px; }}
QMenu::right-arrow {{ image:url({right}); width:10px; height:10px; right:10px; }}

QTableWidget,QTableView,QListWidget {{ background:{PAPER}; border:none; gridline-color:transparent; outline:0; selection-background-color:{SELECT}; selection-color:{INK}; alternate-background-color:transparent; }}
QTableView::item {{ padding:0 10px; border-bottom:1px solid {LINE_SOFT}; }}
QTableView::item:hover {{ background:{ROW_HOVER}; }}
QTableView::item:selected {{ background:{SELECT}; color:{INK}; }}
QHeaderView {{ background:transparent; }}
QHeaderView::section {{ background:transparent; color:{MUTED}; padding:6px 10px; border:none; border-bottom:1px solid {LINE}; font-size:12px; font-weight:600; }}
QHeaderView::section:hover {{ color:{INK}; }}
QHeaderView::down-arrow {{ image:url({down}); width:10px; height:10px; subcontrol-position:center right; right:8px; }}
QHeaderView::up-arrow {{ image:url({up}); width:10px; height:10px; subcontrol-position:center right; right:8px; }}
QTableView QLineEdit {{ border:1px solid {INK}; border-radius:6px; min-height:0; padding:0 6px; background:{PAPER}; }}
QTableCornerButton::section {{ background:transparent; border:none; }}
QPlainTextEdit {{ background:{CANVAS}; border:none; border-radius:12px; padding:10px 12px; font-family:{MONO}; font-size:12px; color:{INK_2}; }}
QSplitter::handle {{ background:transparent; }}
QScrollBar:vertical {{ background:transparent; width:10px; margin:4px 2px; }}
QScrollBar:horizontal {{ background:transparent; height:10px; margin:2px 4px; }}
QScrollBar::handle:vertical {{ background:{SCROLL}; min-height:32px; border-radius:3px; }}
QScrollBar::handle:horizontal {{ background:{SCROLL}; min-width:32px; border-radius:3px; }}
QScrollBar::handle:hover {{ background:{FAINT}; }}
QScrollBar::add-line,QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page,QScrollBar::sub-page {{ background:transparent; }}
QToolTip {{ background:{INK}; color:{ON_INK}; border:none; padding:7px 11px; font-size:12px; }}
QCheckBox {{ spacing:9px; }}
QCheckBox::indicator,QTableView::indicator,QListView::indicator {{ width:16px; height:16px; border-radius:5px; border:1px solid {BOX}; background:{PAPER}; }}
QCheckBox::indicator:hover,QTableView::indicator:hover {{ border-color:{INK}; }}
QCheckBox::indicator:checked,QTableView::indicator:checked,QListView::indicator:checked {{ background:{INK}; border-color:{INK}; image:url({tick}); }}
QCheckBox::indicator:disabled {{ background:{CANVAS}; border-color:{LINE}; }}
QCheckBox::indicator:checked:disabled,QTableView::indicator:checked:disabled {{ background:{CHECK_DISABLED}; border-color:{CHECK_DISABLED}; }}
QDialog#prompt {{ background:{PAPER}; }}
QLabel#promptTitle {{ font-size:17px; font-weight:600; }}
QLabel#promptText {{ color:{INK_2}; }}

QWidget#nav {{ background:{PAPER}; border-right:1px solid {LINE_SOFT}; }}
QLabel#brand {{ font-size:15px; font-weight:600; }}
QLabel#brandSub {{ color:{MUTED}; font-size:11px; }}
QLabel#navSection {{ color:{FAINT}; font-size:11px; padding:14px 26px 6px 26px; }}
QPushButton#navItem {{ background:transparent; border:none; border-radius:12px; margin:0; padding:0; min-height:42px; max-height:42px; }}
QPushButton#navItem:hover {{ background:{HOVER}; }}
QPushButton#navItem:checked,QPushButton#navItem:checked:hover {{ background:transparent; }}
QFrame#navIndicator {{ background:{INK}; border-radius:12px; }}
QLabel#navName {{ color:{INK_2}; background:transparent; }}
QLabel#navName[active="true"] {{ color:{ON_INK}; font-weight:600; }}
QLabel#navCount {{ color:{FAINT}; font-size:12px; background:transparent; }}
QLabel#navCount[active="true"] {{ color:{ON_INK_MUTED}; }}
QLabel#navCount[alert="true"] {{ color:{PAPER}; background:{DANGER}; border-radius:9px; padding:0 6px; font-weight:600; }}
QFrame#hostCard {{ background:{CANVAS}; border-radius:16px; }}
QLabel#hostTitle {{ font-weight:600; }}
QLabel#navFoot {{ color:{FAINT}; font-size:11px; }}

QLabel#pageTitle {{ font-size:22px; font-weight:600; }}
QLabel#pageNote {{ color:{MUTED}; font-size:12px; }}
QLabel#cardTitle {{ font-size:14px; font-weight:600; }}
QLabel#detailName {{ font-size:19px; font-weight:600; }}
QLabel#eyebrow {{ color:{MUTED}; font-size:12px; }}
QLabel#pill {{ color:{INK_2}; font-size:12px; padding:0 0 0 16px; background:transparent; }}
QLabel#pill[state="ok"] {{ color:{INK}; font-weight:600; }}
QLabel#pill[state="warn"] {{ color:{MUTED}; }}
QFrame#divider {{ background:{LINE_SOFT}; max-height:1px; min-height:1px; border:none; }}
QFrame#banner {{ background:{PAPER}; border:1px solid {LINE}; border-radius:14px; }}
QLabel#bannerText {{ color:{INK_2}; }}
QLabel#emptyTitle {{ font-size:16px; font-weight:600; }}
QLabel#emptyText {{ color:{MUTED}; }}
QPushButton#segment {{ border-radius:15px; min-height:30px; padding:0 14px; }}
QPushButton#segment:checked {{ background:{INK}; border-color:{INK}; color:{ON_INK}; font-weight:600; }}
QListWidget#picker::item {{ padding:7px 8px; border-radius:8px; }}
QListWidget#picker::item:hover {{ background:{HOVER}; }}
QListWidget#picker::item:selected {{ background:{SELECT}; color:{INK}; }}
QListWidget#schemes::item {{ padding:12px 14px; border-radius:12px; margin:2px 0; }}
QListWidget#schemes::item:hover {{ background:{ROW_HOVER}; }}
QListWidget#schemes::item:selected {{ background:{SELECT}; color:{INK}; }}
QLabel#toast {{ color:{ON_INK}; background:transparent; border:none; padding:9px 20px; font-weight:600; }}
QLabel#notice {{ background:transparent; border:none; padding:0; }}
QLabel#statusLine {{ color:{MUTED}; font-size:12px; }}
QLabel#stepIndex {{ color:{ON_INK}; background:{INK}; border-radius:11px; font-size:12px; font-weight:600; }}
QLabel#stepIndex[state="todo"] {{ color:{INK_2}; background:{SELECT}; }}
QLabel#stepIndex[state="done"] {{ background:{INK_2}; }}
QCheckBox#check {{ color:{INK_2}; }}
QLabel#legend {{ color:{MUTED}; font-size:12px; }}
QLabel#tipText {{ color:{ON_INK}; font-size:12px; background:transparent; }}
QLabel#tipKey {{ color:{TIP_KEY_FG}; background:{TIP_KEY_BG}; border-radius:5px; padding:1px 6px; font-size:11px; font-weight:600; }}
QLabel#statusTag {{ color:{INK_2}; }}
QLabel#tag {{ color:{ON_INK}; background:{INK}; border-radius:9px; padding:1px 8px; font-size:11px; font-weight:600; }}
QLabel#tag[kind="outline"] {{ color:{INK}; background:transparent; border:1px solid {INK}; }}
QLabel#menuCaption {{ color:{MUTED}; font-size:12px; padding:4px 14px 6px 14px; }}
"""

# Fonts: the system's Microsoft YaHei UI; any font files placed under fonts/ are registered first.
def load_fonts(folder=None)->list[str]:
    from PySide6.QtGui import QFontDatabase
    folder=Path(folder) if folder else Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'fonts';families=[]
    for path in sorted([*folder.glob('*.ttf'),*folder.glob('*.otf')]):
        index=QFontDatabase.addApplicationFont(str(path))
        if index>=0:families+=QFontDatabase.applicationFontFamilies(index)
    return families

def ui_font(size=13)->QFont:
    font=QFont();font.setFamilies(list(UI_FAMILIES));font.setPixelSize(size);font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting);return font

# Popups: Windows 11 rounds and shadows menus, drop-down lists and tooltips natively.
def round_window(widget,small=False,border=None)->bool:
    if sys.platform!='win32':return False
    try:
        hwnd=int(widget.winId());dwm=ctypes.windll.dwmapi
        corner=ctypes.c_int(3 if small else 2);color=QColor(border or LINE);colorref=ctypes.c_uint(color.red()|color.green()<<8|color.blue()<<16)
        ok=dwm.DwmSetWindowAttribute(hwnd,33,ctypes.byref(corner),4)==0
        dwm.DwmSetWindowAttribute(hwnd,34,ctypes.byref(colorref),4);return ok
    except Exception:return False

def tint_title_bar(window,caption=None,text=None,border=None)->bool:
    """Windows 11: the native title bar takes the paper colour so the window reads as one piece."""
    if sys.platform!='win32':return False
    caption=caption or PAPER;text=text or INK;border=border or LINE
    try:
        hwnd=int(window.winId());dwm=ctypes.windll.dwmapi;ok=True
        dark=ctypes.c_int(1 if DARK_MODE else 0);dwm.DwmSetWindowAttribute(hwnd,20,ctypes.byref(dark),4)    # caption buttons
        for attribute,value in ((35,caption),(36,text),(34,border)):
            color=QColor(value);ref=ctypes.c_uint(color.red()|color.green()<<8|color.blue()<<16)
            ok=dwm.DwmSetWindowAttribute(hwnd,attribute,ctypes.byref(ref),4)==0 and ok
        return ok
    except Exception:return False

SHORTCUT=re.compile(r'[（(]((?:Ctrl|Shift|Alt|F\d+)[^）)]*)[）)]\s*$')
class Tooltip(QWidget):
    """The app's own tooltip: an ink pill with wrapped text and the shortcut as a key cap."""
    def __init__(self):
        super().__init__(None,Qt.WindowType.ToolTip|Qt.WindowType.FramelessWindowHint|Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground);self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating);self.target=None
        row=QHBoxLayout(self);row.setContentsMargins(14+6,9+4,12+6,10+8);row.setSpacing(10)
        self.text=QLabel();self.text.setObjectName('tipText');self.text.setWordWrap(True);self.text.setTextFormat(Qt.TextFormat.PlainText);row.addWidget(self.text,1)
        self.key=QLabel();self.key.setObjectName('tipKey');row.addWidget(self.key,0,Qt.AlignmentFlag.AlignTop)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.hide)
    def show_for(self,target,text,pos):
        text=text.strip()
        if not text:self.hide();return
        match=SHORTCUT.search(text.splitlines()[0]) if '\n' not in text else SHORTCUT.search(text)
        key=match.group(1) if match else ''
        if match:text=(text[:match.start()]+text[match.end():]).rstrip()
        self.target=target;self.text.setText(text);self.key.setText(key);self.key.setVisible(bool(key))
        metrics=QFontMetrics(self.text.font());widest=max(metrics.horizontalAdvance(line) for line in text.splitlines() or [''])
        self.text.setFixedWidth(min(widest+2,340));self.adjustSize()
        screen=QApplication.screenAt(pos) or QApplication.primaryScreen();area=screen.availableGeometry()
        x=min(max(pos.x()-14,area.left()+4),area.right()-self.width()-4);y=pos.y()+18
        if y+self.height()>area.bottom():y=pos.y()-self.height()-6
        self.move(x,y);was=self.isVisible();self.show();self.raise_()
        if not was:fade_in(self,140)
        self.timer.start(9000)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);body=QRectF(self.rect()).adjusted(6,4,-6,-8)
        for step,alpha in ((5,10),(3,16),(1,24)):
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(0,0,0,int(alpha*(2 if DARK_MODE else 1))));painter.drawRoundedRect(body.adjusted(-step+1,step,step-1,step+1),10+step,10+step)
        painter.setBrush(QColor(INK));painter.drawRoundedRect(body,10,10)

class PopupStyler(QObject):
    """App-wide polish: rounded native popups, styled combo items and the custom tooltip."""
    WATCH={QEvent.Type.Polish,QEvent.Type.Show,QEvent.Type.ToolTip,QEvent.Type.Enter,QEvent.Type.Leave,QEvent.Type.MouseButtonPress,QEvent.Type.Wheel,QEvent.Type.Hide,QEvent.Type.KeyPress}
    # Qt shows the next tooltip about 20 ms after the last one closes; every tip here waits for a real pause instead.
    HOVER_MS=1000
    def __init__(self,app):
        super().__init__(app);self.tip=Tooltip();self.since=time.monotonic();self.spot=None;self.pending=None
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.reveal)
    def cancel(self):self.timer.stop();self.pending=None
    def reveal(self):
        """Shows the waiting tip only if the pointer is still resting on the same thing."""
        if not self.pending:return
        target,text,spot=self.pending;self.pending=None;pos=QCursor.pos()
        try:under=QApplication.widgetAt(pos)
        except RuntimeError:return
        if under is None or not (under is target or target.isAncestorOf(under)):return
        if spot is not None and self.spot_at(target,target.mapFromGlobal(pos))!=spot:return
        self.tip.show_for(target,text,pos)
    def spot_at(self,obj,point):
        parent=obj.parentWidget()
        if isinstance(parent,QHeaderView):return ('section',parent.logicalIndexAt(point))
        index=parent.indexAt(point);return ('cell',index.row(),index.column())
    def eventFilter(self,obj,event):
        kind=event.type()
        if kind not in self.WATCH:return False
        if kind==QEvent.Type.Polish:
            if isinstance(obj,QComboBox) and not obj.property('styledItems'):obj.setProperty('styledItems',True);obj.setItemDelegate(QStyledItemDelegate(obj))
            return False
        if kind==QEvent.Type.Show:
            if isinstance(obj,QWidget) and obj.isWindow() and obj is not self.tip:
                if isinstance(obj,QMenu) or obj.inherits('QComboBoxPrivateContainer'):round_window(obj)
                elif obj.inherits('QTipLabel'):round_window(obj,True,INK)
                elif isinstance(obj,(QMainWindow,QDialog)):tint_title_bar(obj)
            return False
        if kind==QEvent.Type.Enter:
            self.since=time.monotonic();return False
        if kind==QEvent.Type.ToolTip:
            text,spot=self.tooltip_for(obj,event)
            if text is None:return False
            if not text:
                if spot is None:return False  # let the parent widget offer its own tip
                self.cancel();self.tip.hide();return True
            if self.tip.isVisible() and self.tip.target is obj and spot==self.spot:return True
            if spot is not None and spot!=self.spot:self.since=time.monotonic()
            self.spot=spot;self.tip.hide();waited=(time.monotonic()-self.since)*1000
            self.pending=(obj,text,spot);self.timer.start(int(max(120,self.HOVER_MS-waited)))
            return True
        if kind in (QEvent.Type.Leave,QEvent.Type.Hide) and self.pending and obj is self.pending[0]:self.cancel()
        if kind in (QEvent.Type.MouseButtonPress,QEvent.Type.Wheel,QEvent.Type.KeyPress):self.cancel()
        if self.tip.isVisible() and (obj is self.tip.target or kind in (QEvent.Type.MouseButtonPress,QEvent.Type.Wheel,QEvent.Type.KeyPress)):self.tip.hide()
        return False
    def tooltip_for(self,obj,event):
        """(text, spot): spot names the hovered cell or header section, or None for a plain widget.
        Text None leaves the event to Qt."""
        parent=obj.parentWidget() if isinstance(obj,QWidget) else None
        if isinstance(parent,QHeaderView) and obj is parent.viewport():
            section=parent.logicalIndexAt(event.pos());model=parent.model()
            return (str(model.headerData(section,parent.orientation(),Qt.ItemDataRole.ToolTipRole) or '') if model and section>=0 else ''),('section',section)
        if isinstance(parent,QAbstractItemView) and obj is parent.viewport():
            index=parent.indexAt(event.pos());return (str(index.data(Qt.ItemDataRole.ToolTipRole) or '') if index.isValid() else ''),('cell',index.row(),index.column())
        if isinstance(obj,QWidget) and not obj.isWindow():
            return obj.toolTip(),None
        return None,None

def install(app):
    """Fonts, fades and popup polish for the whole application."""
    load_fonts();app.setFont(ui_font())
    mark=QIcon()
    for size in (16,24,32,48,64,128):mark.addPixmap(logo(size,LIGHT['INK'],LIGHT['PAPER']))    # the taskbar icon keeps one look
    app.setWindowIcon(mark)
    for effect,enabled in ((Qt.UIEffect.UI_FadeTooltip,True),(Qt.UIEffect.UI_FadeMenu,True),(Qt.UIEffect.UI_AnimateCombo,True)):app.setEffectEnabled(effect,enabled and motion())
    styler=PopupStyler(app);app.installEventFilter(styler);app._popup_styler=styler;return styler

# Basic builders.
def label(text,name,wrap=False)->QLabel:
    widget=QLabel(text);widget.setObjectName(name);widget.setWordWrap(wrap);return widget

def elastic(widget:QLabel)->QLabel:
    """Long status text is cut by the layout instead of widening the window."""
    widget.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);return widget

def repolish(widget,**properties):
    for key,value in properties.items():widget.setProperty(key,value)
    widget.style().unpolish(widget);widget.style().polish(widget)

def divider()->QFrame:
    line=QFrame();line.setObjectName('divider');return line

def icon_label(name,role='INK_2',size=16)->QLabel:
    widget=themed_pixmap(QLabel(),name,role,size);widget.setFixedSize(size,size);return widget

def button(text,kind=None,icon_name=None,tip=None)->QPushButton:
    widget=QPushButton(text)
    if kind:widget.setObjectName(kind)
    if icon_name:themed_icon(widget,icon_name,'ON_INK' if kind=='primary' else 'INK_2',16)
    if tip:widget.setToolTip(tip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor);return widget

def menu_button(text,entries,primary=False,default=None,icon_name=None)->QToolButton:
    """A drop-down; None separates groups and (title,[...]) nests a submenu.
    With a default action the button runs it and its arrow opens the rest."""
    widget=QToolButton();menu=QMenu(widget)
    def fill(target,items):
        for entry in items:
            if entry is None:target.addSeparator()
            elif isinstance(entry,tuple):fill(target.addMenu(entry[0]),entry[1])
            else:target.addAction(entry)
    fill(menu,entries)
    if default:widget.setDefaultAction(default);widget.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    else:widget.setText(text);widget.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    widget.setMenu(menu);widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:themed_icon(widget,icon_name,'INK_2',16);widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    if primary:widget.setObjectName('primary')
    return widget

def page_head(title:str,note:str='')->tuple[QWidget,QHBoxLayout,QLabel]:
    head=QWidget();row=QHBoxLayout(head);row.setContentsMargins(0,0,0,0);row.setSpacing(10)
    texts=QVBoxLayout();texts.setSpacing(2);texts.addWidget(label(title,'pageTitle'));note_label=elastic(label(note,'pageNote'));texts.addWidget(note_label)
    row.addLayout(texts,1);return head,row,note_label

def soft_path(path)->str:
    """Lets long Windows paths wrap after separators instead of mid-word."""
    return str(path).replace('\\','\\​').replace('/','/​')

class SearchField(QFrame):
    """A pill with a search glyph; the line edit inside is the real input (field.edit)."""
    def __init__(self,placeholder):
        super().__init__();self.setObjectName('searchField');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        row=QHBoxLayout(self);row.setContentsMargins(12,0,8,0);row.setSpacing(8);row.addWidget(icon_label('search','MUTED',15))
        self.edit=QLineEdit();self.edit.setObjectName('searchEdit');self.edit.setPlaceholderText(placeholder);row.addWidget(self.edit,1)
        # Our own clear button: Qt's built-in one places itself off-centre once the edit is restyled; the row centres this one.
        self.clear_btn=QToolButton();self.clear_btn.setObjectName('searchClear');themed_icon(self.clear_btn,'clear','FAINT',16);self.clear_btn.setIconSize(QSize(16,16))
        self.clear_btn.setFixedSize(24,24);self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor);self.clear_btn.setToolTip('清除');self.clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_btn.clicked.connect(lambda:(self.edit.clear(),self.edit.setFocus()));self.clear_btn.hide();row.addWidget(self.clear_btn,0,Qt.AlignmentFlag.AlignVCenter)
        self.edit.textChanged.connect(lambda text:self.clear_btn.setVisible(bool(text)))
        self.edit.installEventFilter(self);self.clear_btn.installEventFilter(self)
    def eventFilter(self,watched,event):
        if watched is self.clear_btn:
            # Darkens under the pointer, like the rest of the quiet controls.
            if event.type() in (QEvent.Type.Enter,QEvent.Type.Leave):themed_icon(self.clear_btn,'clear','MUTED' if event.type()==QEvent.Type.Enter else 'FAINT',16)
            return False
        if event.type() in (QEvent.Type.FocusIn,QEvent.Type.FocusOut):repolish(self,focus=event.type()==QEvent.Type.FocusIn)
        return False

def draw_mark(painter,mark,cx,cy,radius=4.5,color=None):
    """The keyframe vocabulary: solid, hollow, faint (dashed) diamond or a dash."""
    ink=QColor(color or INK);soft=QColor(FAINT)
    if mark=='solid':painter.setPen(QPen(ink,1.3));painter.setBrush(ink);painter.drawPolygon(_diamond(cx,cy,radius))
    elif mark=='hollow':painter.setPen(QPen(ink,1.5));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawPolygon(_diamond(cx,cy,radius))
    elif mark=='faint':painter.setPen(QPen(soft,1.3,Qt.PenStyle.DashLine));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawPolygon(_diamond(cx,cy,radius))
    else:painter.setPen(QPen(soft,1.4));painter.drawLine(QPointF(cx-radius+.5,cy),QPointF(cx+radius-.5,cy))

class StatusTag(QLabel):
    """A painted keyframe mark followed by its text."""
    def __init__(self):super().__init__();self.mark=None;self.setObjectName('statusTag');self.setIndent(0)
    def set(self,mark,text):
        self.mark=mark;self.setText(text);self.setContentsMargins(18 if mark else 0,0,0,0);self.update()
    def paintEvent(self,event):
        super().paintEvent(event)
        if self.mark:painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);draw_mark(painter,self.mark,6,self.height()/2,4.5)

class Legend(QWidget):
    """Explains the marks once, in the same drawing as the table."""
    def __init__(self,entries):
        super().__init__();row=QHBoxLayout(self);row.setContentsMargins(0,0,0,0);row.setSpacing(14)
        for mark,text in entries:
            tag=StatusTag();tag.set(mark,text);tag.setObjectName('legend');row.addWidget(tag)
        row.addStretch()

# Prompts: confirmation and one-line input in the app's own look.
class Prompt(QDialog):
    def __init__(self,parent,title,text,ok_text='确定',danger=False,value=None,label_text='',extra=()):
        super().__init__(parent);self.setObjectName('prompt');self.setWindowTitle(title);self.setMinimumWidth(460);self.choice=None
        col=QVBoxLayout(self);col.setContentsMargins(28,24,28,22);col.setSpacing(10)
        col.addWidget(label(title,'promptTitle'));body=label(text,'promptText',True);body.setVisible(bool(text));col.addWidget(body)
        self.edit=None
        if value is not None:
            if label_text:col.addWidget(label(label_text,'field'))
            self.edit=QLineEdit(value);self.edit.setObjectName('field');self.edit.selectAll();col.addWidget(self.edit)
        col.addSpacing(8);row=QHBoxLayout();row.setSpacing(8);row.addStretch()
        cancel=button('取消');cancel.clicked.connect(self.reject);row.addWidget(cancel)
        for index,text_ in enumerate(extra):
            other=button(text_);other.clicked.connect(lambda _=False,i=index:(setattr(self,'choice',i),self.accept()));row.addWidget(other)
        ok=button(ok_text,'danger' if danger else 'primary');ok.clicked.connect(self.accept);ok.setDefault(True);row.addWidget(ok);col.addLayout(row)
        if danger:ok.setStyleSheet(f'QPushButton {{ background:{DANGER}; border-color:{DANGER}; color:{PAPER}; font-weight:600; }} QPushButton:hover {{ background:{DANGER_HOVER}; border-color:{DANGER_HOVER}; }}')
        (self.edit or ok).setFocus()

def confirm(parent,title,text,ok_text='确定',danger=False)->bool:
    return Prompt(parent,title,text,ok_text,danger).exec()==QDialog.DialogCode.Accepted

def choose(parent,title,text,choices:list[str])->int|None:
    """Asks for one of several answers; buttons follow 取消 left to right and the last is the default.
    Returns the chosen index, or None when cancelled."""
    dialog=Prompt(parent,title,text,choices[-1],extra=choices[:-1])
    if dialog.exec()!=QDialog.DialogCode.Accepted:return None
    return len(choices)-1 if dialog.choice is None else dialog.choice

def ask_text(parent,title,label_text,text='',ok_text='保存')->tuple[str,bool]:
    dialog=Prompt(parent,title,'',ok_text,value=text,label_text=label_text);ok=dialog.exec()==QDialog.DialogCode.Accepted
    return dialog.edit.text(),ok

# Scheme list rows: bold title, a "current" tag and a quiet second line.
def _tag(painter,right,top,text,font,filled=True)->float:
    """A small pill ending at x=right; returns its left edge."""
    metrics=QFontMetrics(font);width=metrics.horizontalAdvance(text)+14;box=QRectF(right-width,top,width,18)
    painter.setFont(font);painter.setPen(QPen(QColor(INK),1) if not filled else Qt.PenStyle.NoPen);painter.setBrush(QColor(INK) if filled else Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(box.adjusted(.5,.5,-.5,-.5),9,9);painter.setPen(QColor(ON_INK if filled else INK));painter.drawText(box,Qt.AlignmentFlag.AlignCenter,text);return box.left()

def _scheme_text(painter,rect,data,font,selected_fill=None):
    """Shared drawing for a scheme in the list and in the switcher menu:
    bold title with 当前 / 未保存 tags, then quiet lines (counts, time)."""
    title=QFont(font);title.setPixelSize(14);title.setWeight(QFont.Weight.DemiBold);small=QFont(font);small.setPixelSize(11);small.setWeight(QFont.Weight.DemiBold)
    right=rect.right()-10
    if data.get('dirty'):right=_tag(painter,right,rect.y()+10,'未保存',small,False)-6
    if data.get('current'):right=_tag(painter,right,rect.y()+10,'当前',small)-6
    x=rect.x()+14;painter.setFont(title);painter.setPen(QColor(INK));width=int(right-x-4)
    painter.drawText(QRectF(x,rect.y()+9,width,20),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,QFontMetrics(title).elidedText(data.get('title',''),Qt.TextElideMode.ElideRight,width))
    meta=QFont(font);meta.setPixelSize(12);painter.setFont(meta);painter.setPen(QColor(MUTED))
    for line,text in enumerate(data.get('lines',())):
        painter.drawText(QRectF(x,rect.y()+31+line*18,rect.width()-28,18),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,text)

class SchemeDelegate(QStyledItemDelegate):
    """Scheme list rows: bold title with tags, counts, then when it was saved."""
    def paint(self,painter,option,index):
        data=index.data(Qt.ItemDataRole.UserRole) or {};painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(option.rect).adjusted(2,2,-2,-2);selected=bool(option.state&QStyle.StateFlag.State_Selected);hover=bool(option.state&QStyle.StateFlag.State_MouseOver)
        if selected or hover:painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(SELECT if selected else CARD_HOVER));painter.drawRoundedRect(rect,12,12)
        if selected:painter.setBrush(QColor(INK));painter.drawRoundedRect(QRectF(rect.x()+4,rect.y()+14,3,rect.height()-28),1.5,1.5)
        _scheme_text(painter,rect,data,option.font);painter.restore()
    def sizeHint(self,option,index):return QSize(200,76)

class SchemeRow(QWidget):
    """One scheme inside the switcher menu. Clicking it emits picked; the menu is closed by its owner."""
    picked=Signal()
    def __init__(self,data,width=340):
        super().__init__();self.data=data;self.hovered=False;self.setFixedSize(width,58);self.setMouseTracking(True);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(data.get('tip',''))
    def enterEvent(self,event):self.hovered=True;self.update();super().enterEvent(event)
    def leaveEvent(self,event):self.hovered=False;self.update();super().leaveEvent(event)
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):self.picked.emit()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);rect=QRectF(self.rect()).adjusted(0,1,0,-1)
        if self.hovered:painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(HOVER));painter.drawRoundedRect(rect,8,8)
        _scheme_text(painter,rect.adjusted(0,-2,0,0),self.data,self.font())

class SchemeSwitcher(QToolButton):
    """The effects page's scheme control: 方案 · name · unsaved dot. Its menu is rebuilt each time it opens."""
    def __init__(self):
        super().__init__();self.title='';self.dirty=False;self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMenu(QMenu(self));self.setMinimumWidth(200);self.setMaximumWidth(280);self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Fixed)
    def set_scheme(self,title,dirty):
        self.title=title;self.dirty=dirty;self.setAccessibleName(f'方案：{title}'+('（未保存）' if dirty else ''));self.updateGeometry();self.update()
    def sizeHint(self):
        bold=QFont(self.font());bold.setWeight(QFont.Weight.DemiBold);small=QFont(self.font());small.setPixelSize(12)
        width=16+QFontMetrics(small).horizontalAdvance('方案')+8+QFontMetrics(bold).horizontalAdvance(self.title)+(14 if self.dirty else 0)+40
        return QSize(max(200,min(280,width)),34)
    def paintEvent(self,event):
        self.setText('');super().paintEvent(event);painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled=self.isEnabled();small=QFont(self.font());small.setPixelSize(12);bold=QFont(self.font());bold.setWeight(QFont.Weight.DemiBold)
        x=16.0;painter.setFont(small);painter.setPen(QColor(MUTED if enabled else FAINT));caption=QFontMetrics(small).horizontalAdvance('方案')
        painter.drawText(QRectF(x,0,caption+2,self.height()),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,'方案');x+=caption+8
        room=int(self.width()-x-36-(14 if self.dirty else 0));painter.setFont(bold);painter.setPen(QColor(INK if enabled else FAINT))
        text=QFontMetrics(bold).elidedText(self.title,Qt.TextElideMode.ElideRight,max(room,10));painter.drawText(QRectF(x,0,room,self.height()),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,text)
        if self.dirty:
            dot=x+QFontMetrics(bold).horizontalAdvance(text)+8;painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(INK if enabled else FAINT));painter.drawEllipse(QPointF(dot,self.height()/2+.5),3.5,3.5)

# Surfaces.
SHADOW=10
class Surface(QFrame):
    """White rounded paper with a soft two-part shadow painted underneath (no graphics effect,
    so tables inside scroll at full speed). Its layout margins already include the shadow."""
    def __init__(self,radius=18,padding=(18,16,18,16),spacing=10,fill=None,shadow=True):
        super().__init__();self.radius=radius;self.fill=QColor(fill) if fill else None;self.shadow=shadow;self.setObjectName('surface')
        pad=SHADOW if shadow else 0;self.box=QVBoxLayout(self);self.box.setContentsMargins(padding[0]+pad,padding[1]+pad-2,padding[2]+pad,padding[3]+pad+2);self.box.setSpacing(spacing)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pad=SHADOW if self.shadow else 0;body=QRectF(self.rect()).adjusted(pad,pad-2,-pad,-pad-2)
        if self.shadow:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for step in range(1,pad+1):
                color=shadow(int(9*(1-step/(pad+1))**2.2)+1 if step<pad else 0);pen=QPen(color,1);painter.setPen(pen)
                painter.drawRoundedRect(body.adjusted(-step+.5,-step+2.5,step-.5,step+1.5),self.radius+step,self.radius+step)
            painter.setPen(QPen(shadow(16),1));painter.drawRoundedRect(body.adjusted(-.5,.5,.5,1.5),self.radius,self.radius)
        painter.setPen(QPen(QColor(LINE_SOFT) if self.shadow else QColor(LINE),1));painter.setBrush(self.fill or QColor(PAPER));painter.drawRoundedRect(body,self.radius,self.radius)

def card(margins=(18,16,18,16),spacing=10)->tuple[Surface,QVBoxLayout]:
    frame=Surface(padding=margins,spacing=spacing);return frame,frame.box

class Banner(QFrame):
    """A full-width notice with an icon, wrapping text and an optional action."""
    def __init__(self,kind='warn',action_text='',action=None):
        super().__init__();self.setObjectName('banner');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        row=QHBoxLayout(self);row.setContentsMargins(16,12,12,12);row.setSpacing(12)
        row.addWidget(icon_label('warning' if kind!='info' else 'info','INK',18),0,Qt.AlignmentFlag.AlignTop)
        self.text=label('','bannerText',True);row.addWidget(self.text,1);self.button=None
        if action_text:self.button=button(action_text);self.button.clicked.connect(action);row.addWidget(self.button,0,Qt.AlignmentFlag.AlignVCenter)

class EmptyState(QWidget):
    """Shown in place of an empty table: what is missing and what to do next."""
    def __init__(self,icon_name='inbox'):
        super().__init__();col=QVBoxLayout(self);col.setContentsMargins(24,24,24,24);col.setSpacing(8);col.addStretch(2)
        self.glyph=QLabel();self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter);self.set_icon(icon_name);col.addWidget(self.glyph);col.addSpacing(6)
        self.title=label('','emptyTitle');self.title.setAlignment(Qt.AlignmentFlag.AlignCenter);col.addWidget(self.title)
        self.text=label('','emptyText',True);self.text.setAlignment(Qt.AlignmentFlag.AlignCenter);self.text.setFixedWidth(420)
        centred=QHBoxLayout();centred.addStretch();centred.addWidget(self.text);centred.addStretch();col.addLayout(centred)
        self.buttons=QHBoxLayout();self.buttons.setSpacing(8);self.buttons.addStretch();self.buttons.addStretch();col.addSpacing(8);col.addLayout(self.buttons);col.addStretch(3)
    def set_icon(self,name):themed_pixmap(self.glyph,name,'FAINT',40)
    def add_button(self,widget):self.buttons.insertWidget(self.buttons.count()-1,widget);return widget
    def show_message(self,title,text):self.title.setText(title);self.text.setText(text)

# Navigation rail: a black block slides to the chosen entry.
class NavButton(QPushButton):
    def __init__(self,text,icon_name):
        super().__init__();self.setObjectName('navItem');self.setCheckable(True);self.setCursor(Qt.CursorShape.PointingHandCursor);self.setFixedHeight(42)
        row=QHBoxLayout(self);row.setContentsMargins(14,0,12,0);row.setSpacing(11)
        self.icon_name=icon_name;self.glyph=QLabel();self.glyph.setFixedSize(18,18)
        self.name=QLabel(text);self.name.setObjectName('navName');self.count=QLabel();self.count.setObjectName('navCount');self.count.setAlignment(Qt.AlignmentFlag.AlignCenter);self.count.setFixedHeight(18)
        for widget in (self.glyph,self.name,self.count):widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(self.glyph);row.addWidget(self.name);row.addStretch();row.addWidget(self.count)
        self.toggled.connect(self.paint_state);self.paint_state(False)
    def paint_state(self,checked):
        self.glyph.setPixmap(pixmap(self.icon_name,ON_INK if checked else MUTED,18))
        repolish(self.name,active=checked);repolish(self.count,active=checked)
    def set_count(self,text,alert=False):
        self.count.setText(str(text));self.count.setMinimumWidth(18 if alert and text!='' else 0);repolish(self.count,alert=alert)

class NavRail(QWidget):
    def __init__(self):
        super().__init__();self.setObjectName('nav');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.col=QVBoxLayout(self);self.col.setContentsMargins(14,18,14,14);self.col.setSpacing(2);self.buttons=[];self.current=0
        # The block does not travel: the old one fades where it is and the new one unfolds from its icon to the right.
        self.indicator=QFrame(self);self.indicator.setObjectName('navIndicator');self.indicator.lower();self.indicator.hide()
        self.ghost=QFrame(self);self.ghost.setObjectName('navIndicator');self.ghost.hide();self.ghost_effect=QGraphicsOpacityEffect(self.ghost);self.ghost.setGraphicsEffect(self.ghost_effect)
        self.grow=self.fade=None;self.inking=set()
    def add_section(self,text):self.col.addWidget(label(text,'navSection'))
    def add_item(self,text,icon_name)->NavButton:
        widget=NavButton(text,icon_name);self.col.addWidget(widget);self.buttons.append(widget);return widget
    def stop_motion(self):
        for animation in (self.grow,self.fade):
            try:
                if animation is not None:animation.stop()
            except RuntimeError:pass    # Already finished and deleted.
        self.grow=self.fade=None;self.ghost.hide();self.settle()
    def settle(self,button=None):
        """Gives an item (or every item still waiting) the colours of its checked state."""
        for item in ([button] if button is not None else list(self.inking)):
            self.inking.discard(item);item.paint_state(item.isChecked())
    def select(self,index,animated=True):
        previous=self.current;self.stop_motion();self.current=index;target=self.buttons[index].geometry()
        if not target.isValid() or target.height()==0 or not self.isVisible():self.indicator.setGeometry(target);self.indicator.setVisible(target.height()>0);return
        old=self.indicator.geometry();was_shown=self.indicator.isVisible()
        self.indicator.show();self.indicator.lower()
        if not animated or index==previous or not was_shown or not motion():self.indicator.setGeometry(target);return
        # Text colours follow the blocks, so no grey-on-black frame: the old item stays white until its block is half gone,
        # the new one turns white once the unfolding block has passed its name.
        new,gone=self.buttons[index],self.buttons[previous];self.inking={new,gone};new.paint_state(False);gone.paint_state(True)
        new.glyph.setPixmap(pixmap(new.icon_name,ON_INK,18))    # The block starts as a square round the icon.
        edge=new.x()+new.name.geometry().right()
        self.ghost.setGeometry(old);self.ghost_effect.setOpacity(1.0);self.ghost.show();self.ghost.lower();self.indicator.lower()
        self.fade=animate(self.ghost_effect,b'opacity',0.0,140,QEasingCurve.Type.OutCubic,1.0,lambda:(self.ghost.hide(),self.settle(gone)))
        self.fade.valueChanged.connect(lambda value:self.settle(gone) if gone in self.inking and value<.5 else None)
        self.indicator.setGeometry(QRect(target.x(),target.y(),target.height(),target.height()))
        self.grow=animate(self.indicator,b'geometry',target,220,QEasingCurve.Type.OutCubic,finished=lambda:self.settle(new))
        self.grow.valueChanged.connect(lambda rect:self.settle(new) if new in self.inking and rect.right()>=edge else None)
    def resizeEvent(self,event):super().resizeEvent(event);QTimer.singleShot(0,lambda:self.select(self.current,False)) if self.buttons else None
    def showEvent(self,event):super().showEvent(event);QTimer.singleShot(0,lambda:self.select(self.current,False)) if self.buttons else None

# Page stack that crossfades.
class FadeStack(QStackedWidget):
    def setCurrentIndex(self,index):
        old=self.currentWidget()
        if motion() and old is not None and index!=self.currentIndex() and self.isVisible():
            shot=QLabel(self);shot.setPixmap(old.grab());shot.setGeometry(old.geometry());effect=QGraphicsOpacityEffect(shot);shot.setGraphicsEffect(effect);shot.show();shot.raise_()
            super().setCurrentIndex(index);shot.raise_()
            animate(effect,b'opacity',0.0,180,QEasingCurve.Type.OutCubic,1.0,shot.deleteLater)
        else:super().setCurrentIndex(index)

# Metric tiles: a count that doubles as a filter.
class MetricTile(QPushButton):
    def __init__(self,caption,mark=None):
        super().__init__();self.caption=caption;self.mark=mark;self.value=0;self.shown=0.0;self.setCheckable(True);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus);self.setMinimumHeight(78);self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed);self.setText(caption)
        self.anim=QVariantAnimation(self);self.anim.setDuration(520);self.anim.setEasingCurve(QEasingCurve.Type.OutCubic);self.anim.valueChanged.connect(self._shown)
        self.hovered=False;self.blank=False
    def _shown(self,value):self.shown=float(value);self.update()
    def set_blank(self):
        """Shows a dash: the figure does not apply (for example, data that was never saved)."""
        self.blank=True;self.anim.stop();self.setText(f'{self.caption} —');self.update()
    def set_value(self,value:int):
        self.blank=False;self.setText(f'{self.caption} {value}');old=self.value;self.value=value
        if motion() and self.isVisible() and old!=value:self.anim.stop();self.anim.setStartValue(float(self.shown));self.anim.setEndValue(float(value));self.anim.start()
        else:self.shown=float(value);self.update()
    def enterEvent(self,event):self.hovered=True;self.update();super().enterEvent(event)
    def leaveEvent(self,event):self.hovered=False;self.update();super().leaveEvent(event)
    def sizeHint(self):return QSize(120,78)
    def minimumSizeHint(self):return QSize(84,78)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);rect=QRectF(self.rect()).adjusted(1,1,-1,-3);on=self.isChecked()
        if not on:
            for step,alpha in ((3,6),(2,10),(1,14)):
                painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(shadow(alpha));painter.drawRoundedRect(rect.adjusted(-step+1,step,step-1,step),16,16)
        painter.setPen(QPen(QColor(INK if on else (TILE_HOVER if self.hovered else LINE_SOFT)),1));painter.setBrush(QColor(INK if on else PAPER));painter.drawRoundedRect(rect,16,16)
        if self.hasFocus():painter.setPen(QPen(QColor(INK),2));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(rect.adjusted(-1,-1,1,1),17,17)
        x=rect.x()+16;caption=QFont(self.font());caption.setPixelSize(12);painter.setFont(caption);fg=QColor(ON_INK_MUTED) if on else QColor(MUTED)
        if self.mark:
            mark_color=QColor(ON_INK if on else INK);painter.setPen(QPen(mark_color,1.3));painter.setBrush(mark_color if self.mark=='solid' else Qt.BrushStyle.NoBrush)
            if self.mark=='dash':painter.drawLine(QPointF(x,rect.y()+21),QPointF(x+7,rect.y()+21))
            else:painter.drawPolygon(_diamond(x+4,rect.y()+21,4))
            x+=14
        painter.setPen(fg);painter.drawText(QRectF(x,rect.y()+12,rect.width()-(x-rect.x())-10,18),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,self.caption)
        number=QFont();number.setFamilies(list(NUMERAL_FAMILIES));number.setPixelSize(26);number.setWeight(QFont.Weight.DemiBold);painter.setFont(number)
        painter.setPen(QColor(ON_INK if on else INK));painter.drawText(QRectF(rect.x()+16,rect.y()+32,rect.width()-24,36),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,'—' if self.blank else f'{round(self.shown):,}')

class UsageTile(QWidget):
    """A metric tile that only reports: the main figure on the left, two smaller period figures stacked on the right."""
    def __init__(self,caption):
        super().__init__();self.caption=caption;self.figure='0';self.lines:list[tuple[str,str]]=[]
        self.setMinimumHeight(78);self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
    def set_figures(self,figure:str,lines:list[tuple[str,str]]):self.figure=figure;self.lines=list(lines);self.update()
    def sizeHint(self):return QSize(120,78)
    def minimumSizeHint(self):return QSize(84,78)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);rect=QRectF(self.rect()).adjusted(1,1,-1,-3)
        for step,alpha in ((3,6),(2,10),(1,14)):
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(shadow(alpha));painter.drawRoundedRect(rect.adjusted(-step+1,step,step-1,step),16,16)
        painter.setPen(QPen(QColor(LINE_SOFT),1));painter.setBrush(QColor(PAPER));painter.drawRoundedRect(rect,16,16)
        small=QFont(self.font());small.setPixelSize(12);painter.setFont(small);metrics=QFontMetrics(small)
        painter.setPen(QColor(MUTED));painter.drawText(QRectF(rect.x()+16,rect.y()+12,rect.width()-26,18),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,self.caption)
        # Period figures first: the main number takes what is left and shrinks rather than overlap them.
        side=max((metrics.horizontalAdvance(f'{name}  {value}') for name,value in self.lines),default=0);right=rect.right()-14
        for index,(name,value) in enumerate(self.lines):
            line=QRectF(right-side,rect.y()+34+index*17,side,17)
            painter.setPen(QColor(MUTED));painter.drawText(line,Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,name)
            painter.setPen(QColor(INK_2));painter.drawText(line,Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignRight,value)
        room=right-side-12-(rect.x()+16);number=QFont();number.setFamilies(list(NUMERAL_FAMILIES));number.setWeight(QFont.Weight.DemiBold)
        size=26
        while size>14:
            number.setPixelSize(size)
            if QFontMetrics(number).horizontalAdvance(self.figure)<=room:break
            size-=2
        number.setPixelSize(size);painter.setFont(number);painter.setPen(QColor(INK))
        painter.drawText(QRectF(rect.x()+16,rect.y()+32,max(room,0),36),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,self.figure)

# Loader buttons.
class LoaderMixin:
    """While loading the button shows a moving figure and a verb, stays visually active and ignores clicks."""
    def _loader_setup(self):
        self._timer=QTimer(self);self._timer.setInterval(33);self._timer.timeout.connect(self._tick);self._phase=0.0;self._saved=None;self._kind='orbit'
    def loading(self)->bool:return self._saved is not None
    def start(self,kind,verb):
        if self._saved is None:self._saved=[self.text(),self.icon()]
        self._kind=kind;self._phase=0.0;self.setText(verb);self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True);self._paint_loader()
        if motion():self._timer.start()
    def stop(self):
        self._timer.stop()
        if self._saved is None:return
        text,saved_icon=self._saved;self._saved=None;self.setText(text);self.setIcon(saved_icon);redraw_icon(self);self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,False)
    def set_label(self,text):
        if self._saved is not None:self._saved[0]=text
        else:self.setText(text)
    def _tick(self):self._phase=(self._phase+.033/LOADER_PERIOD.get(self._kind,1.0))%1.0;self._paint_loader()
    def _paint_loader(self):self.setIcon(QIcon(loader_pixmap(self._kind,self._phase,ON_INK if self.objectName()=='primary' else INK)))

class LoaderButton(QPushButton,LoaderMixin):
    def __init__(self,text='',kind=None):
        super().__init__(text);self._loader_setup();self.setCursor(Qt.CursorShape.PointingHandCursor)
        if kind:self.setObjectName(kind)

class Spinner(QLabel):
    """A tiny standalone loader for the status line; show() starts it and hide() stops it."""
    def __init__(self,kind='arc'):
        super().__init__();self.kind=kind;self.phase=0.0;self.setFixedSize(16,16);self.timer=QTimer(self);self.timer.setInterval(33);self.timer.timeout.connect(self.tick);self.tick()
    def tick(self):self.phase=(self.phase+.033/LOADER_PERIOD.get(self.kind,1.0))%1.0;self.setPixmap(loader_pixmap(self.kind,self.phase,INK_2,16))
    def setVisible(self,visible):
        super().setVisible(visible)
        if visible and motion():self.timer.start()
        else:self.timer.stop()

# Connection state: a dot that pulses while connected.
class Pill(QLabel):
    def __init__(self,text=''):
        super().__init__(text);self.setObjectName('pill');self.pulse=0.0;self.anim=QVariantAnimation(self);self.anim.setStartValue(0.0);self.anim.setEndValue(1.0)
        self.anim.setDuration(2200);self.anim.setLoopCount(-1);self.anim.valueChanged.connect(self._pulse);self.setMinimumHeight(20)
    def _pulse(self,value):self.pulse=float(value);self.update()
    def set_state(self,state,text):
        self.setText(text);repolish(self,state=state)
        if state=='ok' and motion():self.anim.start()
        else:self.anim.stop();self.pulse=0.0;self.update()
    def paintEvent(self,event):
        super().paintEvent(event);painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);center=QPointF(5,self.height()/2);state=self.property('state')
        if state=='ok':
            if self.pulse:
                halo=QColor(INK);halo.setAlphaF(.22*(1-self.pulse));painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(halo);painter.drawEllipse(center,3.5+3*self.pulse,3.5+3*self.pulse)
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(INK));painter.drawEllipse(center,3.5,3.5)
        else:painter.setPen(QPen(QColor(MUTED if state=='warn' else FAINT),1.4));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawEllipse(center,3.2,3.2)

# Keyframe diamonds for row status.
STATUS_MARK={'勾选':'solid','已应用':'solid','待应用':'hollow','待翻译':'faint','未加载':'dash','未连接':'dash','仅翻译':'dash','已适配':'solid'}
class DiamondDelegate(QStyledItemDelegate):
    """Status as a keyframe: solid = in effect in AE, hollow = ready to apply, faint = needs Chinese.
    Rows being written breathe while the write runs."""
    def __init__(self,parent=None,active=None):
        super().__init__(parent);self.active=active or (lambda index:False);self.phase=0.0
    def paint(self,painter,option,index):
        text=index.data() or '';opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);opt.text=''
        style=opt.widget.style() if opt.widget else QApplication.style();style.drawControl(QStyle.ControlElement.CE_ItemViewItem,opt,painter,opt.widget)
        if not text:return
        mark=STATUS_MARK.get(text,'dash');painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx=opt.rect.x()+18;cy=opt.rect.center().y()+.5;ink=QColor(INK);soft=QColor(FAINT)
        radius=5.0
        if self.active(index):
            wave=.5-.5*math.cos(self.phase*2*math.pi);halo=QColor(INK);halo.setAlphaF(.16*(1-wave));painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(halo);painter.drawPolygon(_diamond(cx,cy,radius+5*wave))
        draw_mark(painter,mark,cx,cy,radius)

        font=QFont(opt.font);font.setPixelSize(12);painter.setFont(font);painter.setPen(QColor(INK if mark in ('solid','hollow') else MUTED))
        painter.drawText(QRectF(cx+12,opt.rect.y(),opt.rect.width()-(cx+12-opt.rect.x()),opt.rect.height()),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,text)
        painter.restore()
    def sizeHint(self,option,index):
        size=super().sizeHint(option,index);return QSize(max(size.width(),92),size.height())

class TrashDelegate(QStyledItemDelegate):
    """A delete button drawn in a cell (only where the item's UserRole+3 is set); the view handles the click."""
    def paint(self,painter,option,index):
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);opt.text=''
        style=opt.widget.style() if opt.widget else QApplication.style();style.drawControl(QStyle.ControlElement.CE_ItemViewItem,opt,painter,opt.widget)
        if not index.data(Qt.ItemDataRole.UserRole+3):return
        hover=bool(option.state&QStyle.StateFlag.State_MouseOver);centre=QRectF(option.rect).center();painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if hover:painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(SELECT));painter.drawEllipse(centre,14,14)
        painter.drawPixmap(QRectF(centre.x()-8,centre.y()-8,16,16).toRect(),pixmap('trash',INK if hover else FAINT,16));painter.restore()

class Breather:
    """Drives a delegate's phase and repaints its view while something is being written."""
    def __init__(self,view,delegate):
        self.view=view;self.delegate=delegate;self.timer=QTimer(view);self.timer.setInterval(40);self.timer.timeout.connect(self.tick)
    def start(self):
        if motion():self.timer.start()
    def stop(self):self.timer.stop();self.delegate.phase=0.0;self.view.viewport().update()
    def tick(self):self.delegate.phase=(self.delegate.phase+.04/1.3)%1.0;self.view.viewport().update()

def fade_in(widget,duration=200):
    """Opacity 0 → 1 on a widget that has just been shown."""
    if not motion():return
    effect=QGraphicsOpacityEffect(widget);widget.setGraphicsEffect(effect);effect.setOpacity(0.0)
    animate(effect,b'opacity',1.0,duration,QEasingCurve.Type.OutCubic,0.0,lambda:widget.setGraphicsEffect(None))

class CheckHeader(QHeaderView):
    """A horizontal header whose first section is a tri-state box for the rows on show.
    Clicking it calls toggle(); that section never sorts."""
    def __init__(self,parent,toggle):
        super().__init__(Qt.Orientation.Horizontal,parent);self.toggle=toggle;self.state=Qt.CheckState.Unchecked;self.setSectionsClickable(True)
    def set_state(self,state):
        if state!=self.state:self.state=state;self.viewport().update()
    def box_rect(self,rect)->QRectF:return QRectF(rect.x()+11,rect.center().y()-7.5,16,16)
    def paintSection(self,painter,rect,logical):
        if logical!=0:super().paintSection(painter,rect,logical);return
        # The box section never shows a sort arrow, so its plain background is drawn here.
        painter.save();painter.fillRect(rect,QColor(PAPER));painter.setPen(QPen(QColor(LINE),1));painter.drawLine(rect.bottomLeft(),rect.bottomRight())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing);box=self.box_rect(rect).adjusted(.5,.5,-.5,-.5)
        if self.state==Qt.CheckState.Unchecked:painter.setPen(QPen(QColor(BOX),1));painter.setBrush(QColor(PAPER));painter.drawRoundedRect(box,5,5)
        else:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(INK));painter.drawRoundedRect(box,5,5)
            if self.state==Qt.CheckState.Checked:painter.drawPixmap(box.adjusted(2,2,-2,-2).toRect(),pixmap('tick',ON_INK,12))
            else:painter.setPen(QPen(QColor(ON_INK),2,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap));painter.drawLine(QPointF(box.x()+4.5,box.center().y()),QPointF(box.right()-4.5,box.center().y()))
        painter.restore()
    def mousePressEvent(self,event):
        if self.logicalIndexAt(event.position().toPoint())==0 and event.button()==Qt.MouseButton.LeftButton:self.toggle();return
        super().mousePressEvent(event)

class CheckDragger(QObject):
    """Press a box and sweep up or down: every visible row passed takes the state the first box switched to.
    Shift+press checks the visible run from the last box; near the top or bottom edge the table scrolls.
    paint(rows, state) updates rows on screen as the pointer moves; finish(rows, state) runs once on release."""
    EDGE=28
    def __init__(self,table,column,is_checked,paint,finish):
        super().__init__(table);self.table=table;self.column=column;self.is_checked=is_checked;self.paint=paint;self.finish=finish
        self.state=None;self.last=None;self.anchor=None;self.touched=[];self.direction=0
        self.scroller=QTimer(self);self.scroller.setInterval(40);self.scroller.timeout.connect(self.scroll)
        table.viewport().installEventFilter(self)
    def visible_between(self,first,last)->list[int]:
        low,high=sorted((first,last));return [row for row in range(low,high+1) if not self.table.isRowHidden(row)]
    def sweep(self,row):
        if row<0 or self.state is None:return
        rows=[r for r in self.visible_between(self.last,row) if r not in self.touched]
        if rows:self.touched+=rows;self.paint(rows,self.state)
        self.last=row
    def eventFilter(self,obj,event):
        kind=event.type()
        if kind in (QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonDblClick) and event.button()==Qt.MouseButton.LeftButton:
            index=self.table.indexAt(event.position().toPoint())
            if not index.isValid() or index.column()!=self.column:return False
            row=index.row();self.state=not self.is_checked(row);self.touched=[];self.last=row
            if event.modifiers()&Qt.KeyboardModifier.ShiftModifier and self.anchor is not None and self.anchor<self.table.rowCount():
                self.last=self.anchor
            self.sweep(row);self.anchor=row;return True
        if self.state is None:return False
        if kind==QEvent.Type.MouseMove:
            point=event.position().toPoint();self.sweep(self.table.rowAt(point.y()))
            height=self.table.viewport().height();self.direction=-1 if point.y()<self.EDGE else 1 if point.y()>height-self.EDGE else 0
            if self.direction and not self.scroller.isActive():self.scroller.start()
            elif not self.direction:self.scroller.stop()
            return True
        if kind==QEvent.Type.MouseButtonRelease:
            self.scroller.stop();rows,state=self.touched,self.state;self.state=None;self.touched=[]
            if rows:self.finish(rows,state)
            return True
        return False
    def scroll(self):
        if self.state is None:self.scroller.stop();return
        bar=self.table.verticalScrollBar();bar.setValue(bar.value()+self.direction*bar.singleStep())
        point=self.table.viewport().mapFromGlobal(QCursor.pos());self.sweep(self.table.rowAt(max(1,min(point.y(),self.table.viewport().height()-2))))

def check_state(flags)->Qt.CheckState:
    """Header state for a list of booleans: none, some or all."""
    flags=list(flags)
    if flags and all(flags):return Qt.CheckState.Checked
    return Qt.CheckState.PartiallyChecked if any(flags) else Qt.CheckState.Unchecked

def table_style(table,row_height=34):
    """The common table look: no grid, comfortable rows, quiet header."""
    table.setShowGrid(False);table.setFrameShape(QFrame.Shape.NoFrame);table.verticalHeader().setVisible(False);table.verticalHeader().setDefaultSectionSize(row_height)
    table.verticalHeader().setMinimumSectionSize(row_height);table.horizontalHeader().setHighlightSections(False);table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
    table.setWordWrap(False);table.setAlternatingRowColors(False);table.setMouseTracking(True)
