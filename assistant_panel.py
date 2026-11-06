"""The assistant in the window: the ball, the panel it opens, saved conversations, the option menus, the context gauge
and the state machine behind the ball's face. Work states come from the program, never from the model; the model may
only colour its own replies with a mood. Every movement goes through ui_kit.motion(), so turning motion off stops it."""
from __future__ import annotations
import base64,json,math,re,threading,time
from pathlib import Path
from PySide6.QtCore import QBuffer,QEasingCurve,QEvent,QObject,QPoint,QPointF,QRect,QRectF,Qt,QTimer,QVariantAnimation,Signal,Slot
from PySide6.QtGui import QBrush,QColor,QImage,QLinearGradient,QPainter,QPainterPath,QPen,QPixmap,QRegion
from PySide6.QtWidgets import QApplication,QCheckBox,QFileDialog,QFrame,QGridLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QScrollArea,QSizePolicy,QStackedWidget,QTextEdit,QToolButton,QVBoxLayout,QWidget
import agent_core,patch_loader
from agent_core import Agent,ChatError,DEFAULT_LEVEL,LEVELS,LEVEL_NAMES,LEVEL_NOTES,level_at_least,normalize_options,option_message
from agent_tools import AppTools,help_outline
from assistant_store import ChatStore,new_id,title_of,when
from llm_client import EFFORTS,list_models
from mochi_face import MochiFace
import ui_kit
from ui_kit import animate,button,confirm,fade_in,label

# The ball rests on the panel's send spot, so opening the panel does not move it: the panel grows out from it.
BALL=56;DRAWER=380;DRAWER_TALL=600;MARGIN=8;PADDING=10;INSET=MARGIN+PADDING+2;SLOT=46;CORNER=18
# The wash over the blurred page, from the top of the panel to the bottom: position and how much paper shows.
GLASS_WASH=((0,.3),(.55,.62),(1,.93));REVEAL_PAD=30
# Live blur: sampled at a quarter of the size, with GLASS_BLEED pixels of the page around the panel so what lies beside it
# bleeds in, then halved GLASS_LEVELS times and brought back one step at a time. Milliseconds between frames while something
# is changing the page and at rest; it stays fast until the page has been still for GLASS_SETTLE seconds. A frame may cost at most 1/GLASS_SHARE of the time.
GLASS_SCALE=4;GLASS_LEVELS=2;GLASS_BLEED=48;GLASS_FAST=16;GLASS_IDLE=1000;GLASS_SETTLE=1.0;GLASS_SHARE=3;GLASS_CORNERS=.5
INPUT_MIN=38;INPUT_MAX=120
TOOL_FACE={'read':'search','help':'recall','net':'translating','write':'writing','file':'reading','danger':'panic','patch':'patching'}
TOOL_NOTE={'read':'查询中','help':'查帮助','net':'翻译中','write':'执行中','file':'读文件','danger':'删除中','patch':'修补中'}
# How long a passing state stays before the face returns to rest.
PASSING={'done':4200,'error':5000,'helpless':4000,'stopped':2600,'refuse':2600,'tired':4000,'received':900,'happy':2500,'puzzled':3200,'content':3000,'surprised':2000,'sad':3400,'confused':3000,'shy':2600,'curious':2200,
         'greeting':1800,'goodbye':1000,'recall':1400,'choosing':1500,'wake':1800}
PONDER_AFTER=15000;GAUGE_OPEN=180;GAUGE_LINGER=1250;REVEAL_OPEN=460;REVEAL_CLOSE=300
LEVEL_SHORT={'read':'只读','data':'改数据','ae':'操作 AE','full':'完全访问'}
SUGGESTIONS=('这个软件怎么用？','帮我把勾选的效果按影视后期习惯翻译','现在有哪些效果还没翻译？','为什么我应用后 AE 里没变？')
THANKS=re.compile(r'谢谢|感谢|多谢|辛苦|真棒|厉害|太好了|thank',re.I)
COMMANDS=(('/compact','压缩上下文'),('/压缩','压缩上下文'),('/new','开始新对话'),('/新对话','开始新对话'),('/history','查看历史对话'),('/历史','查看历史对话'),('/model','选择模型'),('/模型','选择模型'),('/think','选择思考强度'),('/思考','选择思考强度'),('/undo','撤销最近一轮改动'),('/撤销','撤销最近一轮改动'))

MAX_IMAGES=4;IMAGE_SIDE=1568;THUMB_SIDE=96;MAX_IMAGE_FILE=30*1024*1024
IMAGE_TYPES=('.png','.jpg','.jpeg','.bmp','.webp','.gif')
def encode_image(image:QImage,side:int=IMAGE_SIDE,quality:int=85)->dict:
    """A picture ready to send: at most side pixels long, on white where it was transparent, base64. Paths and metadata stay behind."""
    if max(image.width(),image.height())>side:image=image.scaled(side,side,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
    flat=QImage(image.size(),QImage.Format.Format_RGB32);flat.fill(QColor(255,255,255));p=QPainter(flat);p.drawImage(0,0,image);p.end()
    buffer=QBuffer();buffer.open(QBuffer.OpenModeFlag.WriteOnly);media='image/jpeg'
    if not flat.save(buffer,'JPEG',quality):
        buffer=QBuffer();buffer.open(QBuffer.OpenModeFlag.WriteOnly);media='image/png'
        if not flat.save(buffer,'PNG'):raise ValueError('图片无法编码')
    return {'media':media,'data':base64.b64encode(bytes(buffer.data())).decode('ascii')}
def thumb_pixmap(data:str)->QPixmap:
    picture=QPixmap();picture.loadFromData(base64.b64decode(data));return picture

def short_number(value:int)->str:return f'{value/1000:.1f}K' if value>=1000 else str(value)
def tool_button(name,tip,role='MUTED')->QToolButton:
    item=QToolButton();item.setObjectName('aiIcon');item.setToolTip(tip);item.setCursor(Qt.CursorShape.PointingHandCursor);ui_kit.themed_icon(item,name,role,16);return item

class Bridge(QObject):
    """Carries work from the agent thread to the main thread, and events the other way."""
    call=Signal(object);state=Signal(str,str);say=Signal(str);ask=Signal(object,str,object)
    def __init__(self):
        super().__init__();self.call.connect(self.run_call,Qt.ConnectionType.QueuedConnection)
    @Slot(object)
    def run_call(self,fn):fn()

class Host:
    """What the agent needs from the program. Lives on the agent's side of the bridge."""
    def __init__(self,panel):
        self.panel=panel;self.window=panel.window_;self.bridge=panel.bridge;self.saved=None;self.undo_state=None;self.skip_confirm=False;self.tools_=AppTools(self.window,self);self.turn_space=None;self.turn_host=None;self.dispatch_tool=None
    def space_identity(self):
        s=self.window.space;return (s.key,str(s.folder.resolve()),str(s.exe))
    def host_identity(self):
        live=self.window.live or {}
        return (live.get('pid'),live.get('creationTime')) if live.get('pid') else None
    def cancelled(self):return self.panel.agent.stop or self.window._stopping.is_set()
    def begin_turn(self):
        self.turn_space=self.space_identity();self.turn_host=self.host_identity()
    def guard(self,tool=None):
        if self.panel.agent.stop or self.window._stopping.is_set():raise RuntimeError('已停止，未开始下一步操作')
        if self.panel.in_turn and self.turn_space is not None:
            if self.turn_space!=self.space_identity():raise RuntimeError('AE 版本已切换，请重新发送请求')
            actual=self.host_identity()
            if self.turn_host is not None and actual!=self.turn_host:raise RuntimeError('AE 宿主已变化，请重新发送请求')
            if actual is not None:self.turn_host=actual
        if tool is not None and not level_at_least(self.level(),tool.level):raise PermissionError('权限已降低，未执行')
        if tool is not None and tool.changes and self.window.busy():raise RuntimeError('程序正在执行其他操作，请稍后再试')
    def dispatch(self,tool,args):
        self.on_main(lambda:self.guard(tool));self.dispatch_tool=tool
        try:return tool.run(args)
        finally:self.dispatch_tool=None
    def question(self,tool,args):return self.on_main(lambda:tool.confirm(args))
    def level(self)->str:return self.panel.level
    def all_tools(self):return self.tools_.tools
    def tools(self):return [t for t in self.tools_.tools if level_at_least(self.level(),t.level)]
    def config(self)->dict:
        s=self.window.settings;return {'service':s.service,'key':s.key,'base_url':s.base_url,'model':self.panel.chat_model(),'effort':self.panel.effort}
    def usage(self,tokens):self.window.record_usage(tokens)
    def prompt(self)->str:
        status=self.on_main(lambda:self.tools_.app_status({}))
        facts='；'.join(f'{k}={v}' for k,v in status.items() if k in ('appVersion','aeVersion','adapted','connected','page','busy','candidates','checkedEffects','parameterRowsRead','activeScheme','patchesActive'))
        return agent_core.system_prompt(self.level(),self.tools(),help_outline(),facts)
    def on_main(self,fn):
        """Runs fn on the main thread and waits. Direct when already there (tests and the panel itself)."""
        tool=self.dispatch_tool
        if threading.current_thread() is threading.main_thread():self.guard(tool);return fn()
        done=threading.Event();box={};lock=threading.Lock()
        def wrapped():
            with lock:
                if box.get('abandoned'):return
                box['started']=True
            try:self.guard(tool);box['value']=fn()
            except Exception as error:box['error']=error
            done.set()
        self.bridge.call.emit(wrapped);deadline=time.monotonic()+60
        while not done.wait(.1):
            with lock:
                if not box.get('started') and (self.panel.agent.stop or time.monotonic()>deadline):
                    box['abandoned']=True;raise RuntimeError('已取消或界面响应超时，排队操作未执行')
        if 'error' in box:raise box['error']
        return box.get('value')
    def ui_job(self,start)->dict:
        """Starts one of the window's jobs the way its button would, waits for it, and reports what it logged.
        Refuses while the program is busy: the user's own operation is never interrupted."""
        done=threading.Event();box={}
        def begin():
            w=self.window
            if w.busy():raise RuntimeError('程序正在执行其他操作，请稍后再试')
            box['before']=w.log.document().lineCount();old=w.job;start()
            # Connected after the window's own, so its result handling has run by the time this fires.
            if w.job is not old:w.job.finished.connect(lambda:done.set())
            elif w._tasks:w.task_finished.connect(lambda name:done.set())
            else:done.set()
        # Once a real job has begun, cancellation waits for its existing write/rollback guards.
        self.on_main(begin);done.wait()
        def report():
            w=self.window;lines=w.log.toPlainText().splitlines()[box['before']-1:]
            failed=[line for line in lines if line.startswith('操作未完成')]
            if failed:raise RuntimeError(failed[-1].removeprefix('操作未完成：'))
            return {'statusLine':w.status.text(),'log':lines[-12:]}
        return self.on_main(report)
    def confirm(self,tool,question)->bool:
        if self.skip_confirm:return True
        done=threading.Event();box={}
        def answer(ok,always):
            box['ok']=ok
            if always:self.skip_confirm=True
            done.set()
        self.panel.pending_answer=answer;self.bridge.ask.emit(tool,question,answer)
        while not done.wait(.2):
            if self.panel.agent.stop:return False
        return box.get('ok',False)
    # Undo: what the first change of a turn found, put back as a whole.
    def snapshot(self):
        """Called on the main thread before a change. Only the first in each turn counts."""
        if self.saved is not None:return
        w=self.window;page=w.parameter_page;store,checked=page.popup_stores() if page.popup_supported() else (None,None)
        self.saved=dict(space=self.space_identity(),pending=dict(w.pending),enabled=set(w.enabled),parameter_pending=dict(w.parameter_pending),parameter_enabled=set(w.parameter_enabled),category=dict(w.category_pending),ticked=set(w.ticked),
                        folders={k:list(v) for k,v in w.favorites.folders.items()},current=w.favorites.current,choices=dict(store.values) if store else None,choice_checks=(type(checked.values)(checked.values) if checked else None))
        self.panel.bridge.call.emit(lambda:self.panel.undo_btn.setEnabled(True))
    def end_turn(self):
        if self.saved is not None:self.undo_state=self.saved
        self.saved=None
    def undo(self)->dict:
        state=self.saved or self.undo_state
        if state is None:return {'undone':False,'note':'没有可撤销的改动'}
        if state.get('space')!=self.space_identity():return {'undone':False,'note':'这份撤销记录属于其他 AE 版本，请切回原版本后撤销'}
        if self.window.busy():return {'undone':False,'note':'请等待当前操作完成后撤销'}
        w=self.window;page=w.parameter_page;self.saved=None;self.undo_state=None
        for target,value in ((w.pending,state['pending']),(w.parameter_pending,state['parameter_pending']),(w.category_pending,state['category'])):target.clear();target.update(value)
        for target,value in ((w.enabled,state['enabled']),(w.parameter_enabled,state['parameter_enabled']),(w.ticked,state['ticked'])):target.clear();target.update(value)
        w.favorites.folders={k:list(v) for k,v in state['folders'].items()};w.favorites.current=state['current'];w.favorites.save()
        for listener in w.favorites.listeners:listener()
        if state['choices'] is not None:
            store,checked=page.popup_stores();store.values.clear();store.values.update(state['choices']);store.save();checked.values.clear();checked.values.update(state['choice_checks']);checked.save()
        w.sync_rows(set(w.pending)|set(state['enabled'])|{p.match_name for p in w.plugins});w.save_translations();w.save_enabled();w.save_parameters();w.save_categories();w.paint_groups();page.refresh_values();w.selection_changed()
        self.panel.undo_btn.setEnabled(False);return {'undone':True}

class Events:
    def __init__(self,bridge):self.bridge=bridge
    def state(self,name,detail):self.bridge.state.emit(name,detail)
    def say(self,text):self.bridge.say.emit(text)

def paper_wash(top,bottom)->QLinearGradient:
    wash=QLinearGradient(0,top,0,bottom)
    for at,alpha in GLASS_WASH:
        color=QColor(ui_kit.PAPER);color.setAlphaF(alpha);wash.setColorAt(at,color)
    return wash
GLASS_WAKERS=frozenset((QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonRelease,QEvent.Type.MouseButtonDblClick,QEvent.Type.Wheel,QEvent.Type.KeyPress,QEvent.Type.Move,QEvent.Type.Resize))
class GlassDrawer(QFrame):
    """Frosted glass over the page, blurred live: while the panel is open the widgets underneath are drawn again at a
    quarter of their size, blurred and put under a wash of the paper colour that is nearly solid at the bottom and thins
    out towards the top. Qt cannot blur what is behind a child widget, so the panel does the page's drawing itself.
    It paints every pixel of itself, so the page below is not repainted each time something in the panel changes.
    At rest it looks once a second; input to the window, moving or resizing it, and the program's own work speed it up."""
    def __init__(self,parent,skip):
        super().__init__(parent);self.skip=skip;self.bare=False;self.frosted=True;self.small=None;self.backdrop=None;self.pane=None;self.pane_key=None;self.frost_pane=None;self.watched=None;self.corners_stale=True;self.corners=None;self.corners_at=0.0;self.changed_at=0.0;self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,True)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setTimerType(Qt.TimerType.PreciseTimer);self.timer.timeout.connect(self.tick)
    def set_frosted(self,on):
        """Off: a plain paper-coloured panel. Nothing of the page is drawn again, no timer runs and the window is not watched;
        the page behind paints the corners itself, as for any other widget."""
        on=bool(on)
        if on==self.frosted:return
        self.frosted=on;self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,on);self.small=None;self.backdrop=None;self.frost_pane=None;self.pane_key=None;self.corners=None;self.corners_stale=True;self.corners_at=0.0
        if self.isVisible():
            if on:self.changed_at=time.perf_counter();self.refresh();self.timer.start(GLASS_FAST);self.watch(True)
            else:self.timer.stop();self.watch(False)
        self.update()
    def showEvent(self,event):
        if self.frosted:self.changed_at=time.perf_counter();self.corners_stale=True;self.corners_at=0.0;self.refresh();self.timer.start(GLASS_FAST);self.watch(True)
        super().showEvent(event)
    def hideEvent(self,event):
        self.timer.stop();self.watch(False);super().hideEvent(event)
    def resizeEvent(self,event):
        super().resizeEvent(event);self.corners_stale=True;self.corners_at=0.0;self.pane_key=None
        if self.isVisible() and self.frosted:self.refresh()
    def watch(self,on):
        # On the native window, not the application: only this window's input, moves and resizes arrive here, before
        # any widget acts on them, and no timers or paints. Nothing else tells a still page that it is about to change.
        handle=self.window().windowHandle();watched=self.watched
        if watched is not None:
            try:watched.removeEventFilter(self)
            except RuntimeError:pass
        self.watched=handle if on else None
        if on and handle is not None:handle.installEventFilter(self)
    def eventFilter(self,watched,event):
        kind=event.type()
        if kind in GLASS_WAKERS or (kind==QEvent.Type.MouseMove and event.buttons()!=Qt.MouseButton.NoButton):self.wake()
        return False
    def wake(self):
        """Something is about to change the page: full speed from now until it has been still for a moment."""
        self.changed_at=time.perf_counter()
        if self.frosted and self.isVisible() and self.timer.remainingTime()>GLASS_FAST:self.timer.start(0)   # after whatever caused this has been handled
    def next_wait(self,active,minimized,idle_for,cost)->int:
        """Milliseconds to the next frame: full speed while the page moves, gently when it does not, and never so often that heavy drawing takes over the program."""
        wait=GLASS_FAST if active and not minimized and idle_for<=GLASS_SETTLE else GLASS_IDLE
        return max(wait,int(cost*1000*GLASS_SHARE))
    def tick(self):
        window=self.window();start=time.perf_counter();minimized=window.isMinimized()
        if not minimized and self.refresh():self.changed_at=start
        now=time.perf_counter();self.timer.start(self.next_wait(window.isActiveWindow(),minimized,now-self.changed_at,now-start))
    def under(self,area=None):
        """The widgets below this panel with the part of each inside the area, and where that part goes relative to the area."""
        area=area or self.geometry()
        for child in self.parentWidget().children():
            # The animation is never part of the page: it is still up, showing a picture of this panel, at the moment the panel appears.
            if isinstance(child,QWidget) and not isinstance(child,Reveal) and child is not self and child.isVisible() and child not in self.skip():
                part=child.geometry().intersected(area)
                if not part.isEmpty():yield child,part.translated(-child.pos()),part.topLeft()-area.topLeft()
    def reach(self)->QRect:
        """The page the blur is made from: the panel and a margin around it, in the parent's coordinates."""
        return self.geometry().adjusted(-GLASS_BLEED,-GLASS_BLEED,GLASS_BLEED,GLASS_BLEED)
    def sample(self)->QImage:
        area=self.reach();image=QImage(max(1,area.width()//GLASS_SCALE),max(1,area.height()//GLASS_SCALE),QImage.Format.Format_RGB32);image.fill(QColor(ui_kit.CANVAS));p=QPainter(image);p.scale(image.width()/area.width(),image.height()/area.height())
        for child,part,at in self.under(area):child.render(p,at,QRegion(part))
        p.end();return image
    def take_corners(self):
        """The sharp page in the four corners the rounded shape leaves open. They change rarely and are tiny, so they are taken less often."""
        ratio=self.devicePixelRatioF();shot=QPixmap(self.size()*ratio);shot.setDevicePixelRatio(ratio);shot.fill(QColor(ui_kit.CANVAS));p=QPainter(shot);w,h=self.width(),self.height()
        for tile in (QRect(0,0,CORNER,CORNER),QRect(w-CORNER,0,CORNER,CORNER),QRect(0,h-CORNER,CORNER,CORNER),QRect(w-CORNER,h-CORNER,CORNER,CORNER)):
            for child,part,at in self.under():
                piece=QRect(at,part.size()).intersected(tile)
                if not piece.isEmpty():p.drawPixmap(piece.topLeft(),child.grab(piece.translated(part.topLeft()-at)))
        p.end();self.corners=shot;self.corners_at=time.perf_counter()
    def refresh(self)->bool:
        """Draws the page once more. True when it looks different from the last time."""
        if not self.frosted:return False
        sampled=self.sample();mode=Qt.TransformationMode.SmoothTransformation;keep=Qt.AspectRatioMode.IgnoreAspectRatio;steps=[sampled]
        same=sampled==self.small and self.backdrop is not None;self.corners_stale=self.corners_stale or not same
        if self.corners_stale and time.perf_counter()-self.corners_at>GLASS_CORNERS:self.take_corners();self.corners_stale=False;self.update()
        if same:return False
        self.small=sampled
        # Half by half down and back up, a step at a time: one big jump leaves visible squares that make the glass look like a picture.
        for _ in range(GLASS_LEVELS):
            last=steps[-1];steps.append(last.scaled(max(1,last.width()//2),max(1,last.height()//2),keep,mode))
        soft=steps[-1]
        for step in reversed(steps[:-1]):soft=soft.scaled(step.size(),keep,mode)
        self.backdrop=soft;self.frost_pane=None;self.pane_key=None;self.update();return True
    def frost(self)->QPixmap:
        """The blurred page at full size, covering reach(). It stays put on the page while the panel's shape moves over it."""
        if self.frost_pane is None:
            area=self.reach();ratio=self.devicePixelRatioF();pane=QPixmap(area.size()*ratio);pane.setDevicePixelRatio(ratio);pane.fill(QColor(ui_kit.CANVAS if self.frosted else ui_kit.PAPER));p=QPainter(pane);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True)
            if self.frosted and self.backdrop is not None:p.drawImage(QRectF(0,0,area.width(),area.height()),self.backdrop)
            p.end();self.frost_pane=pane
        return self.frost_pane
    def glass(self)->QPixmap:
        key=(ui_kit.PAPER,self.height(),self.width(),self.frosted)
        if key!=self.pane_key:
            ratio=self.devicePixelRatioF();pane=QPixmap(self.size()*ratio);pane.setDevicePixelRatio(ratio);pane.fill(QColor(ui_kit.PAPER))
            if not self.frosted:self.pane=pane;self.pane_key=key;return pane
            p=QPainter(pane);p.drawPixmap(-GLASS_BLEED,-GLASS_BLEED,self.frost());p.fillRect(QRectF(self.rect()),paper_wash(0,self.height()));p.end();self.pane=pane;self.pane_key=key
        return self.pane
    def contents(self)->QPixmap:
        """What is on the glass, on nothing, for the animation."""
        ratio=self.devicePixelRatioF();shot=QPixmap(self.size()*ratio);shot.setDevicePixelRatio(ratio);shot.fill(Qt.GlobalColor.transparent);self.bare=True
        try:self.render(shot,QPoint(),QRegion(),QWidget.RenderFlag.DrawChildren)
        finally:self.bare=False
        return shot
    def paintEvent(self,event):
        if self.bare:return
        p=QPainter(self)
        if not self.frosted:pass    # not opaque: the page behind has drawn the corners
        elif self.corners is not None:p.drawPixmap(0,0,self.corners)
        else:p.fillRect(self.rect(),QColor(ui_kit.CANVAS))
        p.setRenderHint(QPainter.RenderHint.Antialiasing,True);p.setPen(QPen(QColor(ui_kit.LINE),1));p.setBrush(QBrush(self.glass()));p.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),CORNER,CORNER);p.end()

def pixel_aligned(point:QPoint,widget:QWidget)->QPoint:
    """The nearest point at or above-left of this one (else below-right) that falls on a whole device pixel of the window."""
    ratio=widget.devicePixelRatioF();origin=widget.mapTo(widget.window(),QPoint(0,0))
    def whole(v,o):
        for step in range(0,8):
            for c in (v-step,v+step):
                if c>=0 and abs((c+o)*ratio-round((c+o)*ratio))<1e-6:return c
        return v
    return QPoint(whole(point.x(),origin.x()),whole(point.y(),origin.y()))
def smooth(low,high,x):
    u=max(0.0,min(1.0,(x-low)/(high-low)));return u*u*(3-2*u)
def lerp(a,b,u):return a+(b-a)*u
def bezier(x1,y1,x2,y2)->QEasingCurve:
    curve=QEasingCurve(QEasingCurve.Type.BezierSpline);curve.addCubicBezierSegment(QPointF(x1,y1),QPointF(x2,y2),QPointF(1,1));return curve
# Both answer the click at once: a slow first tenth of a second reads as lag. Opening still keeps its contents back, so
# it can be seen (an early version showed the whole panel after 33ms and looked like no animation), and runs slightly past its place.
OPEN_CURVE=bezier(.26,.22,.16,1.14);CLOSE_CURVE=bezier(.3,.12,.35,1)
class Reveal(QWidget):
    """The ball turning into the panel, or the panel back into the ball: one rounded shape that grows out of the ball,
    height a little ahead of width. The blurred page shows through it from the start; the contents fade in once there is room for them."""
    def __init__(self,parent,background,frost,frost_at,picture,geometry,ball,final,opening,done,squash):
        # Opaque, over a picture of what lies behind: the page's table underneath is not redrawn for every frame, which
        # is what used to eat the animation (only a frame or two made it to the screen).
        super().__init__(parent);self.background=background;self.frost=frost;self.frost_at=frost_at;self.picture=picture;self.setGeometry(geometry);self.ball=QRectF(ball).adjusted(4,4,-4,-4);self.final=QRectF(final);self.opening=opening;self.done=done;self.squash=squash;self.x=0.0;self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,True)
        ratio=self.devicePixelRatioF();self.layer=QImage(self.size()*ratio,QImage.Format.Format_ARGB32_Premultiplied);self.layer.setDevicePixelRatio(ratio);self.line=QColor(ui_kit.LINE)
        self.anim=QVariantAnimation(self);self.anim.setDuration(REVEAL_OPEN if opening else REVEAL_CLOSE);self.anim.setStartValue(0.0);self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self.step);self.anim.finished.connect(self.finish);self.show();self.repaint();self.anim.start()   # timed from the first frame on screen
    def step(self,value):
        self.x=value;self.squash(self.ball_squash(value));self.update()
    def ball_squash(self,x)->float:
        """The ball gives as the panel leaves it, and catches it when it comes back."""
        u=min(1.0,x/.62) if self.opening else max(0.0,(x-.5)/.5);return .13*math.sin(u*math.pi*2.5)*(1-u)**2
    def finish(self):
        done=self.done;self.done=None;self.anim.stop();self.squash(0.0)
        if done:done()    # the panel appears first, so nothing shows through in between
        self.hide();self.deleteLater()
    def shape_at(self,x)->float:
        """How far the shape has grown, from 0 at the ball to 1 at the panel's place. Runs a little past 1 on the way."""
        return OPEN_CURVE.valueForProgress(x) if self.opening else 1-CLOSE_CURVE.valueForProgress(x)
    def shape(self):
        x=self.x;tall=self.shape_at(x);wide=self.shape_at(max(0.0,(x-.06)/.94) if self.opening else min(1.0,x/.94));a,b=self.ball,self.final
        rect=QRectF(QPointF(lerp(a.left(),b.left(),wide),lerp(a.top(),b.top(),tall)),QPointF(min(self.width(),lerp(a.right(),b.right(),wide)),min(self.height(),lerp(a.bottom(),b.bottom(),tall))))
        radius=lerp(a.width()/2,CORNER,smooth(0,.5,min(wide,tall)));path=QPainterPath();path.addRoundedRect(rect,radius,radius);return rect,path
    def paintEvent(self,event):
        rect,path=self.shape();x=self.x;shown=smooth(.3,.72,x) if self.opening else 1-smooth(0,.34,x);layer=self.layer;layer.fill(0)
        # The frost stays where it is on the page and the shape uncovers it, like glass sliding over: only the wash moves with the shape.
        q=QPainter(layer);q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,True);q.drawPixmap(self.frost_at,self.frost);q.fillRect(rect,paper_wash(rect.top(),rect.bottom()))
        if shown>0:
            # The contents settle towards the corner the ball sits in.
            corner=self.final.bottomRight();scale=.94+.06*min(1.0,self.shape_at(x));q.save();q.translate(corner);q.scale(scale,scale);q.translate(-corner);q.setOpacity(shown);q.drawPixmap(self.final.topLeft(),self.picture);q.restore()
        # Everything outside the shape is taken away. Filling the shape in DestinationIn would not do: it leaves untouched what the fill does not cover.
        outside=QPainterPath();outside.addRect(QRectF(self.rect()));outside.addPath(path);outside.setFillRule(Qt.FillRule.OddEvenFill);q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut);q.setRenderHint(QPainter.RenderHint.Antialiasing,True);q.fillPath(outside,QColor(0,0,0,255));q.end()
        p=QPainter(self);p.drawPixmap(0,0,self.background);p.drawImage(0,0,layer);p.setRenderHint(QPainter.RenderHint.Antialiasing,True);p.setPen(QPen(self.line,1));p.setBrush(Qt.BrushStyle.NoBrush);p.drawPath(path);p.end()

class Popover(QFrame):
    """A small floating card above whatever opened it: fades and rises in, sinks and fades out. Takes no keyboard focus."""
    hovered=Signal(bool)
    def enterEvent(self,event):self.hovered.emit(True);super().enterEvent(event)
    def leaveEvent(self,event):self.hovered.emit(False);super().leaveEvent(event)
    def __init__(self,panel,width=260):
        super().__init__(panel.drawer);self.panel=panel;self.setObjectName('aiPop');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);self.setFixedWidth(width)
        self.box=QVBoxLayout(self);self.box.setContentsMargins(4,4,4,4);self.box.setSpacing(1);self.hide();self.closing=False
    def show_above(self,anchor):
        self.anchor=anchor;self.fit_height();x,y=self.spot_above();self.move(x,y+8);self.show();self.raise_();self.panel.ball.raise_();fade_in(self,150);animate(self,b'pos',QPoint(x,y),170,QEasingCurve.Type.OutCubic)
    def fit_height(self):
        """Height for the fixed width, so wrapped notes show in full. Styles first: they set the font sizes."""
        for child in self.findChildren(QWidget):child.ensurePolished()
        self.ensurePolished();layout=self.layout();layout.invalidate();layout.activate();self.resize(self.width(),layout.totalHeightForWidth(self.width()) if layout.hasHeightForWidth() else layout.totalSizeHint().height())
    def spot_above(self):
        drawer=self.parentWidget();spot=self.anchor.mapTo(drawer,self.anchor.rect().topLeft());return max(8,min(spot.x(),drawer.width()-self.width()-8)),max(8,spot.y()-self.height()-6)
    def fit_above(self):
        """Its content changed (the model list arrived): same bottom edge, new height."""
        self.fit_height();self.move(*self.spot_above())
    def dismiss(self):
        if self.closing:return
        self.closing=True
        def gone():self.hide();self.deleteLater()
        if ui_kit.motion() and self.isVisible():animate(self,b'pos',self.pos()+QPoint(0,6),120,QEasingCurve.Type.InCubic,None,gone)
        else:gone()

class PopRow(QFrame):
    """One line of a small menu: a title, an optional note (below it, or beside it when inline), a tick on the chosen one."""
    clicked=Signal()
    def __init__(self,title,note='',current=False,inline=False):
        super().__init__();self.title_text=title;self.setObjectName('aiPopRow');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);self.setAttribute(Qt.WidgetAttribute.WA_Hover,True);self.setCursor(Qt.CursorShape.PointingHandCursor)
        outer=QHBoxLayout(self);outer.setContentsMargins(8,5,8,5);outer.setSpacing(6);words=QHBoxLayout() if inline else QVBoxLayout();words.setSpacing(6 if inline else 1)
        name=QLabel(title);name.setObjectName('aiPopTitle');name.setTextFormat(Qt.TextFormat.PlainText);words.addWidget(name)
        if note:
            self.note=QLabel(note);self.note.setObjectName('aiPopNote');self.note.setTextFormat(Qt.TextFormat.PlainText);self.note.setWordWrap(not inline);words.addWidget(self.note)
        if inline:words.addStretch()
        outer.addLayout(words,1)
        if current:
            tick=QLabel();ui_kit.themed_pixmap(tick,'check','INK',12);outer.addWidget(tick,0,Qt.AlignmentFlag.AlignVCenter)
    def text(self)->str:return self.title_text
    def mouseReleaseEvent(self,event):
        if self.isEnabled() and self.rect().contains(event.position().toPoint()):self.clicked.emit()

class StackBar(QWidget):
    """The gauge's bar: one thin line split by what fills the context."""
    def __init__(self,parts,window):
        super().__init__();self.parts=parts;self.window_size=window;self.setFixedHeight(6)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing,True);p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(ui_kit.LINE));p.drawRoundedRect(QRectF(self.rect()),3,3);x=0.0
        for value,role in self.parts:
            width=self.width()*value/max(1,self.window_size)
            if width>.5:p.setBrush(QColor(ui_kit.tone(role)));p.drawRoundedRect(QRectF(x,0,max(2.0,width-1),6),3,3);x+=width
        p.end()

class ContextRing(QWidget):
    """A small ring and the percentage of the context in use; opens the gauge."""
    clicked=Signal();hovered=Signal(bool)
    def __init__(self):
        super().__init__();self.setFixedSize(46,24);self.setCursor(Qt.CursorShape.PointingHandCursor);self.value=0.0;self.anim=QVariantAnimation(self);self.anim.setDuration(420);self.anim.setEasingCurve(QEasingCurve.Type.OutCubic);self.anim.valueChanged.connect(self.show_value)
    def set_share(self,share):
        if not ui_kit.motion():self.value=share;self.update();return
        self.anim.stop();self.anim.setStartValue(self.value);self.anim.setEndValue(float(share));self.anim.start()
    def show_value(self,value):self.value=value;self.update()
    def enterEvent(self,event):self.hovered.emit(True);super().enterEvent(event)
    def leaveEvent(self,event):self.hovered.emit(False);super().leaveEvent(event)
    def mousePressEvent(self,event):self.clicked.emit();event.accept()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing,True);alarm=self.value>=.95;tone=QColor(ui_kit.DANGER if alarm else ui_kit.INK_2 if self.value>=.8 else ui_kit.MUTED)
        rect=QRectF(3,6,12,12);p.setPen(QPen(QColor(ui_kit.LINE),2));p.drawEllipse(rect);pen=QPen(tone,2);pen.setCapStyle(Qt.PenCapStyle.RoundCap);p.setPen(pen);p.drawArc(rect,90*16,int(-360*16*max(.02,self.value)))
        font=p.font();font.setPixelSize(11);p.setFont(font);p.setPen(tone);p.drawText(QRectF(19,0,31,24),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,f'{round(self.value*100)}%');p.end()

class Dots(QWidget):
    """Three dots waving while the model thinks."""
    def __init__(self):
        super().__init__();self.setFixedSize(46,20);self.phase=0.0;self.timer=QTimer(self);self.timer.setInterval(50);self.timer.timeout.connect(self.tick)
        if ui_kit.motion():self.timer.start()
    def tick(self):self.phase+=.3;self.update()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing,True);p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            lift=max(0,math.sin(self.phase-i*.7));p.setOpacity(.35+.65*lift);p.setBrush(QColor(ui_kit.MUTED));p.drawEllipse(QPointF(8+i*12,12-4*lift),3,3)
        p.end()

class Choice(QFrame):
    """One thing the user may have meant, as a card: a click sends exactly what it says."""
    clicked=Signal()
    def __init__(self,title,detail=''):
        super().__init__();self.setObjectName('aiChoice');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);self.setAttribute(Qt.WidgetAttribute.WA_Hover,True);self.setCursor(Qt.CursorShape.PointingHandCursor);box=QVBoxLayout(self);box.setContentsMargins(12,8,12,8);box.setSpacing(2)
        name=QLabel(title);name.setObjectName('aiChoiceTitle');name.setTextFormat(Qt.TextFormat.PlainText);name.setWordWrap(True);box.addWidget(name)
        if detail:
            note=QLabel(detail);note.setObjectName('aiChoiceNote');note.setTextFormat(Qt.TextFormat.PlainText);note.setWordWrap(True);box.addWidget(note)
    def mouseReleaseEvent(self,event):
        if self.rect().contains(event.position().toPoint()):self.clicked.emit()

class Steps(QWidget):
    """One group of tool calls: a line that says what is happening; click to see each step."""
    def __init__(self):
        super().__init__();box=QVBoxLayout(self);box.setContentsMargins(0,0,0,0);box.setSpacing(2);self.head=QPushButton();self.head.setObjectName('aiSteps');self.head.setCursor(Qt.CursorShape.PointingHandCursor);self.head.clicked.connect(self.toggle);box.addWidget(self.head)
        self.detail=label('','muted',True);self.detail.setMaximumHeight(0);self.detail.setContentsMargins(14,0,0,4);box.addWidget(self.detail);self.names=[];self.failed=0;self.running=False;self.expanded=False;self.current=''
    def begin(self,name):self.names.append(name);self.running=True;self.current=name;self.paint()
    def finish(self,ok):
        self.running=False;self.failed+=0 if ok else 1
        if self.names and not ok:self.names[-1]+='（失败）'
        self.paint()
    def text(self)->str:
        count=len(self.names);return f'共 {count} 步'+(f'，{self.failed} 步失败' if self.failed else '')
    def paint(self):
        arrow='▾' if self.expanded else '▸';self.head.setText(f'{arrow} 正在{self.current}…' if self.running else f'{arrow} {self.text()}');self.detail.setText('\n'.join('· '+n for n in self.names))
    def toggle(self):
        self.expanded=not self.expanded;self.paint();target=self.detail.sizeHint().height() if self.expanded else 0
        if not ui_kit.motion():self.detail.setMaximumHeight(target);return
        anim=QVariantAnimation(self);anim.setDuration(180);anim.setEasingCurve(QEasingCurve.Type.OutCubic);anim.setStartValue(self.detail.maximumHeight());anim.setEndValue(target);anim.valueChanged.connect(self.detail.setMaximumHeight);anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

class Reply(QFrame):
    """The model's words: plain on the page with a copy button on hover, appearing a little at a time."""
    def __init__(self,text,animated=True,finished=None):
        super().__init__();self.setObjectName('aiReply');self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);self.full=text;self.finished=finished;box=QVBoxLayout(self);box.setContentsMargins(2,0,2,0);box.setSpacing(0)
        self.words=QLabel();self.words.setObjectName('aiReplyText');self.words.setWordWrap(True);self.words.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);self.words.setOpenExternalLinks(False);self.words.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);box.addWidget(self.words)
        self.copy=tool_button('copy','复制');self.copy.clicked.connect(self.copy_text);row=QHBoxLayout();row.setContentsMargins(0,0,0,0);row.addWidget(self.copy);row.addStretch();self.copy.setFixedSize(26,24);holder=QWidget();holder.setFixedHeight(24);holder.setLayout(row);self.copy.hide();box.addWidget(holder)
        if animated and ui_kit.motion() and len(text)>2:
            self.shown=0;self.words.setTextFormat(Qt.TextFormat.PlainText);self.timer=QTimer(self);self.timer.setInterval(16);self.step_by=max(1,len(text)//40);self.timer.timeout.connect(self.tick);self.timer.start()
        else:self.settle()
    def tick(self):
        self.shown+=self.step_by;self.words.setText(self.full[:self.shown])
        if self.shown>=len(self.full):self.timer.stop();self.settle()
    def settle(self):
        self.words.setTextFormat(Qt.TextFormat.MarkdownText);self.words.setText(self.full)
        if self.finished:self.finished();self.finished=None
    def copy_text(self):QApplication.clipboard().setText(self.full)
    def enterEvent(self,event):self.copy.show();super().enterEvent(event)
    def leaveEvent(self,event):self.copy.hide();super().leaveEvent(event)

class ChatInput(QTextEdit):
    """The message box. Pictures pasted or dropped become attachments; text stays plain."""
    def __init__(self,panel):
        super().__init__();self.panel=panel;self.setAcceptRichText(False)
    def picture_files(self,source)->list[str]:
        return [u.toLocalFile() for u in source.urls() if u.isLocalFile() and u.toLocalFile().casefold().endswith(IMAGE_TYPES)] if source.hasUrls() else []
    def canInsertFromMimeData(self,source):return source.hasImage() or bool(self.picture_files(source)) or super().canInsertFromMimeData(source)
    def insertFromMimeData(self,source):
        files=self.picture_files(source)
        if files:self.panel.attach_files(files)
        elif source.hasImage():self.panel.attach_image(QImage(source.imageData()))
        else:super().insertFromMimeData(source)

class AssistantPanel(QObject):
    def __init__(self,window):
        super().__init__(window);self.window_=window;self.central=window.centralWidget();self.bridge=Bridge();self.level=self.saved_level();self.effort=self.saved_effort();self.thread=None;self.open=False;self.running=False;self.in_turn=False;self.had_text=False;self.job_active=False;self.pending_answer=None;self.card=None
        self.pop=None;self.reveal=None;self.choices=None;self.trial=None;self.trial_card=None;self.items=[];self.steps=None;self.dots=None;self.chat_id=None;self.chat_title='';self.last_text='';self.failures=0;self.models={};self.attachments=[]
        self.store=ChatStore(window.assistant_env['root']);patch_loader.chat_source=lambda:self.chat_id
        self.rest_timer=QTimer(self);self.rest_timer.setSingleShot(True);self.rest_timer.timeout.connect(self.rest);self.ponder=QTimer(self);self.ponder.setSingleShot(True);self.ponder.setInterval(PONDER_AFTER);self.ponder.timeout.connect(self.pondering);self.gauge_timer=QTimer(self);self.gauge_timer.setSingleShot(True);self.gauge_timer.setInterval(GAUGE_OPEN);self.gauge_timer.timeout.connect(self.gauge_hover_settled);self.gauge_leave=QTimer(self);self.gauge_leave.setSingleShot(True);self.gauge_leave.setInterval(GAUGE_LINGER);self.gauge_leave.timeout.connect(self.gauge_left)
        self.build();self.host=Host(self);self.agent=Agent(self.host,Events(self.bridge))
        self.bridge.state.connect(self.agent_state);self.bridge.say.connect(self.show_reply);self.bridge.ask.connect(self.show_confirm)
        self.central.installEventFilter(self);self.place();self.ball.set_emotion('boot');self.boot_timer=QTimer(self);self.boot_timer.setSingleShot(True);self.boot_timer.timeout.connect(self.rest);self.boot_timer.start(3000);self.show_greeting()
    def saved_level(self)->str:
        # The highest level is never remembered: each run of the program starts below it.
        value=self.window_.ui_preferences().get('assistantLevel');return value if value in LEVELS and value!='full' else DEFAULT_LEVEL
    def saved_effort(self)->str:
        value=self.window_.ui_preferences().get('assistantEffort');return value if value in EFFORTS else 'default'
    def chat_model(self)->str:
        s=self.window_.settings;chosen=self.window_.ui_preferences().get('assistantModels');value=chosen.get(s.service) if isinstance(chosen,dict) else None
        return value if isinstance(value,str) and value.strip() else s.model
    def build(self):
        self.drawer=GlassDrawer(self.central,lambda:(self.ball,));self.drawer.setObjectName('aiDrawer');self.drawer.hide()
        col=QVBoxLayout(self.drawer);col.setContentsMargins(PADDING+2,10,PADDING,PADDING);col.setSpacing(8)
        head=QHBoxLayout();head.setSpacing(2);self.history_btn=tool_button('history','历史对话');self.history_btn.clicked.connect(self.toggle_history);head.addWidget(self.history_btn);self.mood_note=label('','muted');self.mood_note.setContentsMargins(6,0,0,0);head.addWidget(self.mood_note);head.addStretch()
        self.undo_btn=tool_button('restore','撤销最近一轮对话对候选、勾选和收藏的改动；已写入 AE 的内容要用恢复');self.undo_btn.setEnabled(False);self.undo_btn.clicked.connect(self.undo);head.addWidget(self.undo_btn)
        self.new_btn=tool_button('plus','新对话');self.new_btn.clicked.connect(self.new_chat);head.addWidget(self.new_btn);self.close_btn=tool_button('close','收起');self.close_btn.clicked.connect(self.toggle);head.addWidget(self.close_btn);col.addLayout(head)
        self.pages=ui_kit.FadeStack();col.addWidget(self.pages,1);self.pages.addWidget(self.build_chat());self.pages.addWidget(self.build_history())
        self.ball=MochiFace(self.central,idle=(30000,90000));self.ball.poked.connect(self.ball_clicked);self.ball.hovered.connect(self.ball_hover);self.ball.resize(BALL,BALL);self.ball.raise_();self.update_hint()
    def build_chat(self)->QWidget:
        page=QWidget();col=QVBoxLayout(page);col.setContentsMargins(0,0,0,0);col.setSpacing(6)
        self.scroll=QScrollArea();self.scroll.setObjectName('aiScroll');self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame);self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.feed=QWidget();self.feed.setObjectName('aiFeed');self.feed_box=QVBoxLayout(self.feed);self.feed_box.setContentsMargins(0,4,6,4);self.feed_box.setSpacing(10);self.feed_box.addStretch();self.scroll.setWidget(self.feed);col.addWidget(self.scroll,1)
        self.hint=label('','aiDivider');self.hint.setAlignment(Qt.AlignmentFlag.AlignRight);col.addWidget(self.hint)
        card=self.composer=QFrame();card.setObjectName('aiComposer');card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);grid=QGridLayout(card);grid.setContentsMargins(10,8,4,4);grid.setSpacing(2)
        self.strip=QWidget();self.strip_box=QHBoxLayout(self.strip);self.strip_box.setContentsMargins(0,0,0,4);self.strip_box.setSpacing(6);self.strip_box.addStretch();self.strip.hide();grid.addWidget(self.strip,0,0)
        self.input=ChatInput(self);self.input.setObjectName('aiInput');self.input.setPlaceholderText('问我怎么用，告诉我要做什么，也可以粘贴截图');self.input.setFixedHeight(INPUT_MIN);self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded);self.input.installEventFilter(self);self.input.textChanged.connect(self.typing);self.input.document().documentLayout().documentSizeChanged.connect(self.fit_input)
        grid.addWidget(self.input,1,0)
        chips=QHBoxLayout();chips.setSpacing(1);self.image_btn=tool_button('image','添加图片（也可以直接粘贴截图或拖进输入框）');self.image_btn.clicked.connect(self.pick_images);chips.addWidget(self.image_btn);self.level_chip=self.chip('权限，决定 AI 能做到哪一步',self.open_levels);self.model_chip=self.chip('这次对话用的模型，不影响翻译设置',self.open_models);self.effort_chip=self.chip('思考强度：越高越慢、越费额度',self.open_efforts)
        for item in (self.level_chip,self.model_chip,self.effort_chip):chips.addWidget(item)
        chips.addStretch();self.ring=ContextRing();self.ring.clicked.connect(self.open_gauge);self.ring.hovered.connect(self.gauge_hover);chips.addWidget(self.ring);grid.addLayout(chips,2,0)
        spacer=QWidget();spacer.setFixedWidth(SLOT);grid.addWidget(spacer,0,1,3,1);col.addWidget(card);self.update_chips()
        return page
    def chip(self,tip,action)->QPushButton:
        item=QPushButton();item.setObjectName('aiChip');item.setToolTip(tip);item.setCursor(Qt.CursorShape.PointingHandCursor);item.clicked.connect(action);return item
    def build_history(self)->QWidget:
        page=QWidget();col=QVBoxLayout(page);col.setContentsMargins(0,0,0,0);col.setSpacing(8);field=ui_kit.SearchField('搜索历史对话');self.history_search=field.edit;self.history_search.textChanged.connect(self.fill_history);col.addWidget(field)
        self.history_scroll=QScrollArea();self.history_scroll.setObjectName('aiScroll');self.history_scroll.setWidgetResizable(True);self.history_scroll.setFrameShape(QFrame.Shape.NoFrame);self.history_list=QWidget();self.history_list.setObjectName('aiFeed');self.history_box=QVBoxLayout(self.history_list);self.history_box.setContentsMargins(0,0,4,0);self.history_box.setSpacing(2);self.history_box.addStretch();self.history_scroll.setWidget(self.history_list);col.addWidget(self.history_scroll,1);return page
    # Geometry and opening
    def eventFilter(self,watched,event):
        kind=event.type()
        if watched is self.central and kind==QEvent.Type.Resize:self.place()
        elif watched is self.input and kind==QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter) and not event.modifiers()&Qt.KeyboardModifier.ShiftModifier:self.send_or_stop();return True
            if event.key()==Qt.Key.Key_Escape:
                if self.pop:self.close_pop()
                else:self.toggle()
                return True
        elif watched is self.input and kind in (QEvent.Type.FocusIn,QEvent.Type.FocusOut):ui_kit.repolish(self.composer,focused='true' if kind==QEvent.Type.FocusIn else 'false')
        elif kind==QEvent.Type.MouseButtonPress and self.pop is not None and isinstance(watched,QWidget) and watched.window() is self.window_ and not self.pop.isAncestorOf(watched) and watched is not self.pop and watched is not self.ring:self.close_pop()
        return False
    def ball_rect(self)->QRect:
        area=self.central.rect();return QRect(area.width()-BALL-INSET,area.height()-BALL-INSET,BALL,BALL)
    def place(self):
        # On a whole screen pixel, so the animation's picture of the panel lands exactly where the panel then appears.
        area=self.central.rect();tall=min(DRAWER_TALL,area.height()-MARGIN*2);at=pixel_aligned(QPoint(area.width()-DRAWER-MARGIN,area.height()-MARGIN-tall),self.central);self.drawer.setGeometry(at.x(),at.y(),DRAWER,tall);self.ball.setGeometry(self.ball_rect());self.ball.raise_()
    def toggle(self):
        self.close_pop()
        if self.reveal is not None:self.reveal.finish()
        self.open=not self.open;self.update_hint();animated=ui_kit.motion() and self.ball.isVisible()
        if self.open:
            self.update_context()
            if not self.busy_face():self.show_mood('wake' if self.ball.emotion in ('sleep','dim','daze') else 'greeting')
            if animated:self.play_reveal(True)
            else:self.drawer.show();self.drawer.raise_()
            self.input.setFocus()
        else:
            if not self.busy_face():self.show_mood('goodbye')
            if animated:self.play_reveal(False)
            else:self.drawer.hide()
        self.ball.raise_()
    def play_reveal(self,opening):
        drawer=self.drawer;drawer.layout().activate();drawer.refresh();frost=drawer.frost();reach=drawer.reach();picture=drawer.contents();final=drawer.geometry()
        # Room above and to the left, because the panel runs a little past its place before settling.
        geometry=final.adjusted(-REVEAL_PAD,-REVEAL_PAD,0,0).intersected(self.central.rect())
        # On whole screen pixels: at 125% a corner between two of them puts the page's picture half a pixel off the page,
        # and everything behind the panel jumps when the animation starts and again when it ends.
        geometry.setTopLeft(pixel_aligned(geometry.topLeft(),self.central));corner=geometry.topLeft()
        def done():
            self.reveal=None
            if opening:self.drawer.show();self.drawer.raise_();self.input.setFocus()
            self.ball.raise_()
        # All in one go with no paint between: hiding the panel and covering the spot look like one step on screen.
        self.drawer.hide();self.ball.blank=True;background=self.central.grab(geometry);self.ball.blank=False
        self.reveal=Reveal(self.central,background,frost,reach.topLeft()-corner,picture,geometry,self.ball.geometry().translated(-corner),final.translated(-corner),opening,done,self.ball.set_squash);self.ball.raise_()
    def ball_clicked(self):
        """Closed, the ball opens the panel; open, it is the send button (stop while working)."""
        if not self.open:self.toggle()
        elif self.pages.currentIndex()==1:self.show_chat()
        elif self.in_turn:self.stop()
        elif self.input.toPlainText().strip() or self.attachments:self.send()
        else:self.input.setFocus();self.update_hint('先在输入框写点什么，再点小球发送')
    def ball_hover(self,inside):
        if inside and not self.busy_face() and self.ball.emotion in ('idle','dim','daze','listening','alert'):self.ball.set_emotion('hover')
        elif not inside and self.ball.emotion=='hover':self.rest()
    def update_hint(self,text=None):
        typed=bool(self.input.toPlainText().strip() or self.attachments);value=text or ('点小球停止' if self.in_turn else '点小球发送，或按 Enter' if typed else '写好后点小球发送 · 输入 / 查看命令')
        if value!=self.hint.text():self.hint.setText(value);fade_in(self.hint,160) if self.hint.isVisible() else None
        self.ball.setToolTip('停止' if self.open and self.in_turn else '发送' if self.open else '打开 AI 助手')
    def fit_input(self,*_):
        target=max(INPUT_MIN,min(INPUT_MAX,int(self.input.document().size().height())+6))
        if target==self.input.height():return
        if not (ui_kit.motion() and self.input.isVisible()):self.input.setFixedHeight(target);return
        anim=QVariantAnimation(self.input);anim.setDuration(110);anim.setStartValue(self.input.height());anim.setEndValue(target);anim.valueChanged.connect(self.input.setFixedHeight);anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    # The option menus
    def update_chips(self):
        self.level_chip.setText(LEVEL_SHORT[self.level]+' ▾');ui_kit.repolish(self.level_chip,alarm='true' if self.level=='full' else 'false')
        model=self.chat_model();self.model_chip.setText((model if len(model)<=12 else model[:11]+'…')+' ▾');self.effort_chip.setText('思考'+EFFORTS[self.effort]+' ▾')
    def close_pop(self):
        if self.pop is not None:
            self.pop.dismiss();self.pop=None;self.gauge_leave.stop();QApplication.instance().removeEventFilter(self)
    def new_pop(self,width=260)->Popover:
        # Watches every click only while a card is open: a filter on the whole application is not free.
        self.close_pop();self.pop=Popover(self,width);QApplication.instance().installEventFilter(self);return self.pop
    def add_row(self,pop,title,action,current=False,box=None,note='',inline=False)->PopRow:
        row=PopRow(title,note,current,inline);row.clicked.connect(action);(box or pop.box).addWidget(row);return row
    def open_levels(self):
        pop=self.new_pop()
        for key in LEVELS:
            self.add_row(pop,LEVEL_NAMES[key],lambda k=key:self.choose_level(LEVELS.index(k)),key==self.level,note=LEVEL_NOTES[key])
        pop.show_above(self.level_chip)
    def choose_level(self,index):
        self.close_pop();chosen=LEVELS[index]
        if chosen=='full' and self.level!='full' and not confirm(self.window_,'开启完全访问权限？','AI 将可以直接读取和修改这台电脑上的任何文件，并修补本程序，不再逐项询问。读取到的文件内容会发给你选择的翻译服务。\n\n'
                                                               '工具会先备份被改动或删除的文件（数据文件夹的 assistant-backups）并记录，但 AI 也可能判断失误。只在需要它修复问题时开启；程序下次启动时会自动退回。','开启'):return
        self.level=chosen;self.update_chips();self.update_context()
        if chosen!='full':self.window_.save_ui_preference('assistantLevel',chosen)
        if not self.busy_face():self.ball.set_emotion('alert') if chosen=='full' else self.rest()
    def open_efforts(self):
        pop=self.new_pop(216);notes={'default':'中等推理，一般够用','low':'更快、更省','high':'更慢，用于难题','max':'最慢、最费额度'}
        for key,name in EFFORTS.items():self.add_row(pop,name,lambda k=key:self.choose_effort(k),key==self.effort,note=notes[key],inline=True)
        pop.show_above(self.effort_chip)
    def choose_effort(self,key):
        self.close_pop();self.effort=key;self.window_.save_ui_preference('assistantEffort',key);self.update_chips()
        if not self.busy_face():self.show_mood('choosing')
    def open_models(self):
        pop=self.new_pop(250);search=QLineEdit();search.setObjectName('aiPopSearch');search.setPlaceholderText('搜索或输入模型名称');pop.box.addWidget(search);scroll=QScrollArea();scroll.setObjectName('aiScroll');scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);holder=QWidget();holder.setObjectName('aiFeed');box=QVBoxLayout(holder);box.setContentsMargins(0,0,0,0);box.setSpacing(2);box.addStretch();scroll.setWidget(holder);pop.box.addWidget(scroll);note=QLabel('正在获取模型列表…');note.setObjectName('aiPopNote');note.setWordWrap(True);note.setContentsMargins(8,2,8,3);pop.box.addWidget(note)
        s=self.window_.settings;service=s.service
        def fill():
            if pop is not self.pop:return
            while box.count()>1:
                item=box.takeAt(0)
                if item.widget():item.widget().deleteLater()
            query=search.text().strip();wanted=query.casefold();current=self.chat_model();rows=[]
            if not wanted:rows.append(('跟随翻译设置','',current==s.model and not self.has_own_model()))
            known=self.models.get(service) or list(dict.fromkeys([*s.provider.models,s.model]))
            rows+=[(m,m,m==current and self.has_own_model()) for m in known if wanted in m.casefold()]
            if query and query not in known:rows.append((f'使用“{query}”',query,False))
            for text,value,now in rows[:300]:
                row=self.add_row(pop,text,lambda v=value:self.choose_model(v),now,box);box.removeWidget(row);box.insertWidget(box.count()-1,row)
            scroll.setFixedHeight(min(210,len(rows)*29+4))
            if pop.isVisible():pop.fit_above()
        search.textChanged.connect(lambda _:fill());fill();pop.show_above(self.model_chip);search.setFocus()
        if service in self.models:note.setText(f'共 {len(self.models[service])} 个模型');return
        key=s.key;base=s.base_url
        def fetch():
            try:rows=list_models(service,base,key);error=''
            except Exception as failure:rows=None;error=str(failure) or type(failure).__name__
            def done():
                if rows:self.models[service]=rows
                if pop is self.pop:
                    note.setText(f'共 {len(rows)} 个模型' if rows else f'取不到模型列表，可手动输入：{error}');fill()
            self.bridge.call.emit(done)
        threading.Thread(target=fetch,daemon=True,name='assistant-models').start()
    def has_own_model(self)->bool:
        chosen=self.window_.ui_preferences().get('assistantModels');return isinstance(chosen,dict) and bool(chosen.get(self.window_.settings.service))
    def choose_model(self,value):
        self.close_pop();prefs=self.window_.ui_preferences().get('assistantModels');chosen=dict(prefs) if isinstance(prefs,dict) else {};service=self.window_.settings.service
        if value:chosen[service]=value
        else:chosen.pop(service,None)
        self.window_.save_ui_preference('assistantModels',chosen);self.update_chips();self.update_context()
        if not self.busy_face():self.show_mood('choosing')
    # The context gauge
    def update_context(self):
        try:self.context=self.agent.context()
        except Exception:return
        self.ring.set_share(self.context['share'])   # no tooltip: hovering opens the card, and a tip would cover it
    def gauge_hover(self,inside):
        """Pointer on the ring or on the card. It opens after a short pause and goes GAUGE_LINGER after the pointer leaves both."""
        if inside:
            self.gauge_leave.stop()
            if self.pop is None:self.gauge_timer.start()
        else:
            self.gauge_timer.stop()
            if getattr(self.pop,'is_gauge',False):self.gauge_leave.start()
    def gauge_hover_settled(self):
        if self.pop is None and self.open and self.ring.underMouse():self.open_gauge()
    def gauge_left(self):
        if getattr(self.pop,'is_gauge',False) and not self.ring.underMouse() and not self.pop.underMouse():self.close_pop()
    def open_gauge(self):
        if isinstance(self.pop,Popover) and getattr(self.pop,'is_gauge',False):self.close_pop();return
        self.update_context();pop=self.new_pop(250);pop.is_gauge=True;pop.hovered.connect(self.gauge_hover);c=getattr(self,'context',{'system':0,'tools':0,'messages':0,'used':0,'window':1,'share':0})
        top=QHBoxLayout();top.setContentsMargins(8,4,8,3);top.addWidget(label(f"上下文已用 {round(c['share']*100)}%",'aiPopTitle'));top.addStretch();top.addWidget(label(f"~{short_number(c['used'])} / {short_number(c['window'])}",'aiPopNote'));pop.box.addLayout(top)
        parts=((c['system'],'FAINT'),(c['tools'],'MUTED'),(c['messages'],'INK'));bar=StackBar(parts,c['window']);holder=QVBoxLayout();holder.setContentsMargins(8,0,8,4);holder.addWidget(bar);pop.box.addLayout(holder)
        for (value,role),name in zip(parts,('系统提示词','工具定义','对话消息')):
            row=QHBoxLayout();row.setContentsMargins(8,1,8,1);swatch=QFrame();swatch.setFixedSize(7,7);swatch.setStyleSheet(f'background:{ui_kit.tone(role)};border-radius:2px;');row.addWidget(swatch);row.addSpacing(2);row.addWidget(label(name,'aiPopNote'));row.addStretch();row.addWidget(label('~'+short_number(value),'aiPopNote'));pop.box.addLayout(row)
        pack=self.add_row(pop,'压缩上下文',self.compact);pack.setEnabled(not self.in_turn and len(self.agent.messages)>=2);pack.setToolTip('把这段对话总结成摘要，腾出空间；也可以输入 /compact 或 /压缩。数字是估算，快满时会自动压缩');pop.show_above(self.ring)
    def compact(self):
        self.close_pop()
        if self.in_turn:return
        if len(self.agent.messages)<2:self.add_note('还没有可压缩的对话');return
        self.agent.stop=False;self.host.begin_turn()
        self.in_turn=True;self.running=True;self.update_hint();self.agent_state('compressing','')
        def work():
            try:
                result=self.agent.compact();self.bridge.state.emit('compressed',f"{result['before']}|{result['after']}")
            except ChatError as error:
                self.bridge.state.emit('stopped' if error.kind=='stopped' else 'failed',error.kind)
                if error.kind!='stopped':self.bridge.say.emit('压缩没有完成，对话保持原样：'+str(error))
            except Exception as error:
                self.bridge.state.emit('failed','service');self.bridge.say.emit('压缩没有完成，对话保持原样：'+(str(error) or type(error).__name__))
            self.bridge.call.emit(self.turn_over)
        self.thread=threading.Thread(target=work,daemon=False,name='assistant-compact');self.thread.start()
    # Feed
    def add(self,widget,record=None,animated=True):
        self.feed_box.addWidget(widget)
        if record:self.items.append(record)
        if animated:fade_in(widget,200)
        self.scroll_down()
    def scroll_down(self):
        bar=self.scroll.verticalScrollBar()
        def go():animate(bar,b'value',bar.maximum(),180,QEasingCurve.Type.OutCubic)
        QTimer.singleShot(0,go)
    def clear_feed(self):
        self.steps=None;self.dots=None;self.card=None;self.choices=None;self.trial_card=None
        while self.feed_box.count()>1:
            item=self.feed_box.takeAt(1)
            if item.widget():item.widget().hide();item.widget().deleteLater()
    def show_greeting(self):
        block=self.greeting=QWidget();col=QVBoxLayout(block);col.setContentsMargins(4,20,4,4);col.setSpacing(6);col.addWidget(label('想让我做点什么？','aiGreeting'));col.addWidget(label('可以问功能，也可以直接让我翻译和操作。','muted',True));col.addSpacing(8)
        for i,text in enumerate(SUGGESTIONS):
            item=button(text,'aiSuggest');item.setCursor(Qt.CursorShape.PointingHandCursor);item.clicked.connect(lambda checked=False,t=text:self.send(t));col.addWidget(item);QTimer.singleShot(60*i,lambda w=item:fade_in(w,220) if w.isVisible() else None)
        self.feed_box.addWidget(block)
    def add_user(self,text,animated=True,thumbs=()):
        row=QWidget();box=QHBoxLayout(row);box.setContentsMargins(40,0,0,0);box.addStretch();frame=QFrame();frame.setObjectName('aiMine');frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);inner=QVBoxLayout(frame);inner.setContentsMargins(12,8,12,8)
        if thumbs:
            pictures=QHBoxLayout();pictures.setSpacing(4)
            for data in thumbs:
                shot=QLabel();shot.setPixmap(thumb_pixmap(data));pictures.addWidget(shot)
            inner.addLayout(pictures)
        words=QLabel(text);words.setObjectName('aiMineText');words.setTextFormat(Qt.TextFormat.PlainText);words.setWordWrap(True);words.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);inner.addWidget(words);box.addWidget(frame)
        self.add(row,{'kind':'user','text':text,**({'thumbs':list(thumbs)} if thumbs else {})},animated)
    def add_note(self,text,animated=True):
        self.add(label(text,'muted',True),{'kind':'note','text':text},animated)
    def add_divider(self,text,animated=True):
        note=label(text,'aiDivider');note.setAlignment(Qt.AlignmentFlag.AlignCenter);self.add(note,{'kind':'divider','text':text},animated)
    def show_reply(self,text):
        self.drop_dots();self.close_steps();self.add(Reply(text,True,self.scroll_down),{'kind':'reply','text':text})
    def show_note(self,text):self.add_note(text)
    def show_understood(self,text):
        """What the assistant took the request to mean, quietly above what it does about it, so a wrong reading shows at once."""
        self.drop_dots();self.add_note('理解：'+text)
    def show_choices(self,question,options,animated=True,live=True):
        """The readings of an unclear request, as cards. Typing something else works just as well; sending anything clears them."""
        self.drop_dots();self.close_steps()
        if question:self.add(Reply(question,animated,self.scroll_down),None,animated)
        if not live:
            # An old conversation has moved on: what was offered stays readable but can no longer be sent.
            if options:self.add(label('当时的选项：'+' / '.join(o['title'] for o in options),'muted',True),None,False)
            return
        block=self.choices=QWidget();col=QVBoxLayout(block);col.setContentsMargins(0,0,0,0);col.setSpacing(6)
        for i,option in enumerate(options):
            card=Choice(option['title'],option.get('detail',''));card.clicked.connect(lambda o=option:self.pick_choice(o));col.addWidget(card)
            if animated:QTimer.singleShot(70*i,lambda w=card:fade_in(w,200) if w.isVisible() else None)
        other=button('都不是，我自己说','aiSteps');other.setCursor(Qt.CursorShape.PointingHandCursor);other.clicked.connect(self.input.setFocus);col.addWidget(other);self.add(block,None,False);self.update_hint('点一个选项，或者直接告诉我')
    def pick_choice(self,option):
        if not self.in_turn:self.send(option_message(option))
    def close_choices(self):
        if self.choices is not None:self.choices.hide();self.choices.deleteLater();self.choices=None
    def show_dots(self):
        if self.dots is None:self.dots=Dots();self.add(self.dots)
    def drop_dots(self):
        if self.dots is not None:self.dots.hide();self.dots.deleteLater();self.dots=None
    def close_steps(self):
        if self.steps is not None:self.items.append({'kind':'steps','names':self.steps.names,'failed':self.steps.failed});self.steps=None
    def show_confirm(self,tool,question,answer):
        if not self.open:self.toggle()
        self.drop_dots();card=self.card=QFrame();card.setObjectName('aiCard');card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);box=QVBoxLayout(card);box.setContentsMargins(12,10,12,10);box.setSpacing(8)
        words=QLabel(question);words.setTextFormat(Qt.TextFormat.PlainText);words.setWordWrap(True);box.addWidget(words);always=QCheckBox('这次对话不再询问');box.addWidget(always)
        row=QHBoxLayout();ok=button('确认','primary');no=button('取消');row.addStretch();row.addWidget(no);row.addWidget(ok);box.addLayout(row);self.add(card)
        def finish(value):
            self.pending_answer=None;self.close_card();self.add_note(('已确认：' if value else '已取消：')+question);answer(value,value and always.isChecked())
        ok.clicked.connect(lambda:finish(True));no.clicked.connect(lambda:finish(False));ok.setFocus()
    def close_card(self):
        if self.card is not None:self.card.hide();self.card.deleteLater();self.card=None
    # A change to the program, waiting for the user's word after the restart
    def show_trial(self,state):
        self.trial=state;chat=state.get('chat')
        if chat and chat!=self.chat_id and not self.in_turn:
            try:self.open_chat(chat)
            except Exception:pass
        modules='、'.join(state.get('modules',[]))
        if self.agent.messages:
            self.agent.messages+=[{'role':'user','content':f'[系统] 程序已重启，刚才改的模块（{modules}）已加载，助手自检通过，正在等用户确认是否保留。是否解决了原来的问题要问用户或自己查，不要只凭这条就说已解决。'},{'role':'assistant','content':json.dumps({'say':''})}]
        self.show_trial_card()
        if self.window_.isVisible():
            if not self.open:self.toggle()
            self.ball.engine.bounce()
    def show_trial_card(self):
        if self.trial is None or self.trial_card is not None:return
        modules='、'.join(self.trial.get('modules',[]));card=self.trial_card=QFrame();card.setObjectName('aiCard');card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);box=QVBoxLayout(card);box.setContentsMargins(12,10,12,10);box.setSpacing(8)
        words=QLabel(f'AI 修改了程序（{modules}），已经生效，助手自检也通过了。问题解决了吗？');words.setTextFormat(Qt.TextFormat.PlainText);words.setWordWrap(True);box.addWidget(words);box.addWidget(label('保留后才算数。不确认的话，下次启动会自动撤销。','muted',True))
        row=QHBoxLayout();keep=button('保留修改','primary');undo=button('撤销修改');row.addStretch();row.addWidget(undo);row.addWidget(keep);box.addLayout(row);keep.clicked.connect(lambda:self.settle_trial(True));undo.clicked.connect(lambda:self.settle_trial(False));self.add(card)
    def settle_trial(self,kept):
        version=self.window_.assistant_env['version']
        try:
            if kept:patch_loader.confirm_trial(version);text='已保留 AI 对程序的修改。'
            else:patch_loader.rollback_trial(version);text='已撤销 AI 对程序的修改，重启程序后恢复原样。'
        except OSError as error:text='这次操作没有成功：'+str(error)
        else:self.trial=None
        if self.trial_card is not None:self.trial_card.hide();self.trial_card.deleteLater();self.trial_card=None
        self.add_note(text)
        if self.agent.messages:self.agent.messages+=[{'role':'user','content':'[系统] '+text},{'role':'assistant','content':json.dumps({'say':''})}]
        self.save_chat()
    # Sending
    def typing(self):
        text=self.input.toPlainText();typed=bool(text.strip())
        # The first word typed: the ball hops once, to show where sending happens.
        if typed and not self.had_text and not self.in_turn:self.ball.engine.bounce()
        self.had_text=typed;self.update_hint();self.slash_menu(text)
        if not self.busy_face():
            if typed and self.ball.emotion!='listening':self.show_mood('listening')
            elif not typed and self.ball.emotion=='listening':self.rest()
    def slash_menu(self,text):
        """Typing a slash lists the commands; a click or Enter runs them."""
        showing=getattr(self.pop,'is_slash',False)
        if not text.startswith('/') or ' ' in text.strip() or '\n' in text:
            if showing:self.close_pop()
            return
        rows=[(c,n) for c,n in COMMANDS if c.startswith(text.strip().casefold())]
        if not rows:
            if showing:self.close_pop()
            return
        pop=self.new_pop(216);pop.is_slash=True
        for command,name in rows:self.add_row(pop,command,lambda c=command:self.run_command(c),note=name,inline=True)
        pop.show_above(self.composer)
    def run_command(self,command)->bool:
        action={'/compact':self.compact,'/压缩':self.compact,'/new':self.new_chat,'/新对话':self.new_chat,'/history':self.toggle_history,'/历史':self.toggle_history,'/model':self.open_models,'/模型':self.open_models,'/think':self.open_efforts,'/思考':self.open_efforts,'/undo':self.undo,'/撤销':self.undo}.get(command)
        if action is None:return False
        self.close_pop();self.input.clear();action();return True
    def send_or_stop(self):
        if self.in_turn:self.stop()
        else:self.send()
    def send(self,text=None):
        text=(text if text is not None else self.input.toPlainText()).strip();pictures=list(self.attachments)
        if not (text or pictures) or self.in_turn or self.window_._stopping.is_set():return
        if not text:text='看看这张图' if len(pictures)==1 else '看看这些图'
        if text.startswith('/') and self.run_command(text.split()[0].casefold()):return
        w=self.window_
        if not w.settings.ready():
            self.add_note('还没有设置翻译服务。AI 助手用的是你在设置里选的同一个服务和密钥。')
            if not w.configure_api():return
        self.close_pop();self.close_choices();self.input.clear();self.clear_attachments();self.remove_greeting();self.add_user(text,True,[p['thumb'] for p in pictures]);self.last_text=text;self.running=True;self.in_turn=True;self.update_hint();self.show_mood('received');self.update_context()
        if self.chat_id is None:self.chat_id=new_id();self.chat_title=title_of(text)
        self.host.begin_turn();images=[{'media':p['media'],'data':p['data']} for p in pictures];self.thread=threading.Thread(target=self.run_agent,args=(text,images),daemon=False,name='assistant');self.thread.start()
    # Pictures waiting to be sent
    def pick_images(self):
        files,_=QFileDialog.getOpenFileNames(self.window_,'选择图片','','图片 (*.png *.jpg *.jpeg *.bmp *.webp *.gif)')
        if files:self.attach_files(files)
    def attach_files(self,files):
        for name in files:
            try:
                if Path(name).stat().st_size>MAX_IMAGE_FILE:self.update_hint(f'{Path(name).name} 太大，没有添加');continue
            except OSError:self.update_hint(f'{Path(name).name} 读取不了');continue
            image=QImage(name)
            if image.isNull():self.update_hint(f'{Path(name).name} 打不开，没有添加');continue
            self.attach_image(image)
    def attach_image(self,image):
        if image.isNull():return
        if len(self.attachments)>=MAX_IMAGES:self.update_hint(f'一次最多 {MAX_IMAGES} 张图片');return
        try:picture=encode_image(image);picture['thumb']=encode_image(image,THUMB_SIDE,75)['data']
        except ValueError as error:self.update_hint(str(error));return
        self.attachments.append(picture);self.fill_strip();self.update_hint();self.input.setFocus();self.update_context()
        if not self.busy_face():self.show_mood('curious');self.ball.engine.bounce()
    def remove_attachment(self,picture):
        self.attachments=[p for p in self.attachments if p is not picture];self.fill_strip();self.update_hint();self.input.setFocus()
    def clear_attachments(self):self.attachments=[];self.fill_strip()
    def fill_strip(self):
        while self.strip_box.count()>1:
            item=self.strip_box.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        for i,picture in enumerate(self.attachments):
            holder=QWidget();holder.setFixedSize(56,44);shot=QLabel(holder);shot.setObjectName('aiThumb');shot.setGeometry(0,0,56,44);shot.setScaledContents(True);shot.setPixmap(thumb_pixmap(picture['thumb']))
            remove=tool_button('close','移除这张图片','INK');remove.setParent(holder);remove.setGeometry(36,0,20,20);remove.clicked.connect(lambda checked=False,p=picture:self.remove_attachment(p));self.strip_box.insertWidget(i,holder);fade_in(holder,160)
        self.strip.setVisible(bool(self.attachments))
    def remove_greeting(self):
        if getattr(self,'greeting',None) is not None:self.greeting.hide();self.feed_box.removeWidget(self.greeting);self.greeting.deleteLater();self.greeting=None
    def run_agent(self,text,images=None):
        try:self.agent.run(text,images)
        except Exception as error:
            self.bridge.state.emit('failed','service');self.bridge.say.emit('助手出错了：'+(str(error) or type(error).__name__))
        self.bridge.call.emit(self.turn_over)
    def turn_over(self):
        self.running=False;self.in_turn=False;self.drop_dots();self.close_steps();self.ponder.stop();self.update_hint('点一个选项，或者直接告诉我' if self.choices is not None else None);self.host.end_turn();self.close_card();self.mood_note.setText('');self.update_context();self.save_chat()
        if not self.rest_timer.isActive() and self.ball.emotion in ('thinking','pondering','responding','compressing','waiting','translating','writing','search','recall','reading','patching','panic'):self.rest()
        if self.window_._stopping.is_set():QTimer.singleShot(0,self.window_.close)
    def stop(self):
        self.agent.stop=True;self.close_card();self.drop_dots();self.mood_note.setText('正在停止，当前操作完成后结束')
        if self.pending_answer:self.pending_answer(False,False);self.pending_answer=None
    def undo(self):
        if self.in_turn:self.add_note('请等待当前对话操作完成后撤销');return
        result=self.host.undo();self.add_note('已撤销最近一轮对话的改动' if result['undone'] else result['note'])
    # Saved conversations
    def save_chat(self):
        if self.chat_id is None or not self.agent.messages:return
        try:self.store.save({'id':self.chat_id,'title':self.chat_title or '未命名对话','model':self.chat_model(),'messages':self.agent.messages,'items':self.items})
        except (OSError,ValueError) as error:self.window_.log.appendPlainText('AI 对话记录未能保存：'+str(error))
    def new_chat(self):
        if self.in_turn:return
        self.save_chat();self.agent.reset();self.items=[];self.chat_id=None;self.chat_title='';self.failures=0;self.host.skip_confirm=False;self.host.undo_state=None;self.undo_btn.setEnabled(False);self.clear_feed();self.remove_greeting();self.show_greeting();self.show_trial_card();self.show_chat();self.input.setFocus();self.update_context()
    def show_chat(self):
        self.pages.setCurrentIndex(0);ui_kit.themed_icon(self.history_btn,'history','MUTED',16);self.history_btn.setToolTip('历史对话');self.update_hint()
    def toggle_history(self):
        if self.pages.currentIndex()==1:self.show_chat();return
        self.close_pop();self.save_chat();self.history_search.clear();self.fill_history();self.pages.setCurrentIndex(1);ui_kit.themed_icon(self.history_btn,'back','MUTED',16);self.history_btn.setToolTip('返回对话');self.history_search.setFocus()
    def fill_history(self):
        while self.history_box.count()>1:
            item=self.history_box.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        query=self.history_search.text().strip().casefold();rows=[r for r in self.store.list() if query in r['title'].casefold()]
        if not rows:
            self.history_box.insertWidget(0,label('没有找到对话' if query else '还没有历史对话。说点什么就会自动保存。','muted',True));return
        for i,row in enumerate(rows):self.history_box.insertWidget(i,self.history_row(row))
    def history_row(self,row)->QWidget:
        frame=QFrame();frame.setObjectName('aiRow');frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);frame.setCursor(Qt.CursorShape.PointingHandCursor);ui_kit.repolish(frame,current='true' if row['id']==self.chat_id else 'false');box=QHBoxLayout(frame);box.setContentsMargins(10,7,4,7)
        texts=QVBoxLayout();texts.setSpacing(1);title=QLabel(row['title']);title.setObjectName('aiRowTitle');title.setTextFormat(Qt.TextFormat.PlainText);texts.addWidget(title);texts.addWidget(label(f"{when(row['updated'])} · {row['count']} 条消息",'aiDivider'));box.addLayout(texts,1)
        remove=tool_button('trash','删除这段对话');box.addWidget(remove);armed={'on':False}
        def delete():
            # Two clicks: the first arms it for a moment, so a stray click loses nothing.
            if not armed['on']:
                armed['on']=True;remove.setToolTip('再点一次删除');ui_kit.themed_icon(remove,'trash','DANGER',16);QTimer.singleShot(2500,lambda:(armed.update(on=False),ui_kit.themed_icon(remove,'trash','MUTED',16)) if frame.isVisible() else None);return
            self.store.delete(row['id'])
            if row['id']==self.chat_id:self.chat_id=None;self.new_chat();self.toggle_history()
            else:self.fill_history()
        remove.clicked.connect(delete);frame.mousePressEvent=lambda event:self.open_chat(row['id']);return frame
    def open_chat(self,chat_id):
        if self.in_turn:self.add_note('这一轮结束后才能切换对话');self.show_chat();return
        try:chat=self.store.load(chat_id)
        except (OSError,ValueError) as error:self.add_note('这段对话打不开：'+str(error));self.show_chat();return
        self.save_chat();self.agent.reset();self.agent.messages=chat['messages'];self.items=[];self.chat_id=chat['id'];self.chat_title=chat.get('title','');self.clear_feed();self.remove_greeting();self.undo_btn.setEnabled(False);self.host.undo_state=None;self.host.skip_confirm=False
        for item in chat['items']:self.render_item(item,item is chat['items'][-1])
        self.show_trial_card();self.show_chat();self.update_context();self.show_mood('recall');self.input.setFocus()
    def render_item(self,item,last=False):
        kind=item.get('kind');text=str(item.get('text',''))
        if kind=='user':self.add_user(text,False,[str(x) for x in item.get('thumbs',[])])
        elif kind=='reply':self.add(Reply(text,False),item,False)
        elif kind=='note':self.add_note(text,False)
        elif kind=='divider':self.add_divider(text,False)
        elif kind=='options':self.items.append(item);self.show_choices(str(item.get('question','')),normalize_options(item.get('options')),False,last)
        elif kind=='steps':
            names=[str(n) for n in item.get('names',[])];steps=Steps();steps.names=names;steps.failed=int(item.get('failed',0));steps.paint();self.add(steps,item,False)
    # The face
    def busy_face(self)->bool:return self.running or self.job_active
    def show_mood(self,key):
        self.ball.set_emotion(key);self.rest_timer.stop()
        if key in PASSING:self.rest_timer.start(PASSING[key])
    def rest(self):
        """Back to whatever is true now, once a passing state ends."""
        if self.running:self.ball.set_emotion('thinking');return
        if self.job_active:self.ball.set_emotion('busy');return
        w=self.window_
        if self.open and self.input.toPlainText().strip():self.ball.set_emotion('listening');return
        self.ball.set_emotion('alert' if self.level=='full' else 'idle' if self.open or w.live or not w.space.live else 'dim')
    def pondering(self):
        if self.running and self.ball.emotion=='thinking':self.ball.set_emotion('pondering');self.mood_note.setText('模型想得比较久，再等等')
    def agent_state(self,name,detail):
        self.drawer.wake()
        self.ponder.stop()
        if name=='thinking':
            self.mood_note.setText('思考中');self.ball.set_emotion('pondering' if self.effort in ('high','max') else 'thinking');self.rest_timer.stop();self.ponder.start();self.show_dots()
        elif name=='responding':self.mood_note.setText('回复中');self.ball.set_emotion('responding');self.rest_timer.stop();self.drop_dots()
        elif name=='waiting':self.mood_note.setText('等你确认');self.ball.set_emotion('waiting');self.rest_timer.stop()
        elif name=='tool':
            kind,_,description=detail.partition('|');self.drop_dots();self.ball.set_emotion(TOOL_FACE.get(kind,'busy'));self.mood_note.setText(TOOL_NOTE.get(kind,'执行中'));self.rest_timer.stop()
            if self.steps is None:self.steps=Steps();self.add(self.steps)
            self.steps.begin(re.split(r'[。：（，]',description or TOOL_NOTE.get(kind,'执行'))[0]);self.scroll_down()
        elif name=='tool_done':
            if self.steps is not None:self.steps.finish(detail=='ok')
        elif name=='note':self.add_note(detail)
        elif name=='understood':self.show_understood(detail)
        elif name=='options':
            value=json.loads(detail);self.items.append({'kind':'options','question':value['question'],'options':value['options']});self.show_choices(value['question'],value['options'])
        elif name=='compressing':self.drop_dots();self.ball.set_emotion('compressing');self.mood_note.setText('压缩上下文中');self.rest_timer.stop();self.show_dots()
        elif name=='compressed':
            self.drop_dots();before,_,after=detail.partition('|');self.add_divider(f'上下文已压缩：约 {short_number(int(before))} → {short_number(int(after))}');self.update_context();self.show_mood('content')
        elif name=='stopped':self.drop_dots();self.show_mood('stopped');self.add_note('已停止')
        elif name=='refused':self.show_mood('refuse')
        elif name=='failed':
            self.drop_dots();self.failures+=1;self.show_mood('tired' if detail=='limit' else 'refuse' if detail=='vision' else 'sad' if self.failures>=2 else 'error')
        elif name=='finished':self.finish_face(detail)
    def finish_face(self,detail):
        self.running=False;self.drop_dots()   # the turn_over call follows; rest must not fall back to thinking
        self.close_steps();self.failures=self.failures+1 if detail in ('partial','failed-tool') else 0
        if detail=='changed':self.show_mood('done')
        elif detail in ('partial','failed-tool'):self.show_mood('sad' if self.failures>=3 else 'helpless')
        elif THANKS.search(self.last_text):self.show_mood('shy')
        elif detail in PASSING:self.show_mood(detail)
        else:self.show_mood('content')
    # The program's own work drives the face too.
    def job_started(self):
        self.drawer.wake()
        self.job_active=True
        if not self.running:self.ball.set_emotion('busy');self.rest_timer.stop()
    def job_finished(self):
        self.drawer.wake()
        self.job_active=False
        if not self.running and not self.rest_timer.isActive():self.rest()
    def job_failed(self):
        self.drawer.wake()
        if not self.running:self.show_mood('error')
    def job_note(self,key):
        if not self.running:self.show_mood(key)
    def shutdown(self):self.agent.stop=True;self.boot_timer.stop();self.rest_timer.stop();self.save_chat()
