"""Quick effect search: a small search box over AE that finds an effect by its Chinese name, English name,
internal name or pinyin initials and adds it to the selected layers.

FX Console reads AE's effect list once when AE starts, so it never sees names written by this program. This box
searches the program's own table instead (plug-in name, Chinese candidate, AE's current name, internal name,
category) and adds the chosen effect through the same hand-over as parameter requests (parameter_names.add_effect).

The key is a real global hotkey, but it is registered only while AE is the foreground window and released as soon
as anything else is, so other programs keep the key."""
from __future__ import annotations
import ctypes,sys
from ctypes import wintypes as W
from pathlib import Path
from typing import NamedTuple
from PySide6.QtCore import QAbstractNativeEventFilter,QEvent,QObject,QPoint,QSize,QTimer,Qt,Signal
from PySide6.QtGui import QColor,QCursor,QGuiApplication,QKeySequence
from PySide6.QtWidgets import QApplication,QLabel,QLayout,QListWidget,QListWidgetItem,QVBoxLayout,QWidget
import ui_kit
from ui_kit import SearchField,Surface,label
from core import readable_category

# Pinyin initials without a dictionary: GB2312's first level (the 3755 most common characters) is ordered by
# pinyin, so the first letter follows from the character's code. Second-level characters (ordered by radical)
# and characters outside GB2312 get no initial; a polyphone gets its first-listed reading (长 -> c).
_BOUNDS=((0xB0A1,'a'),(0xB0C5,'b'),(0xB2C1,'c'),(0xB4EE,'d'),(0xB6EA,'e'),(0xB7A2,'f'),(0xB8C1,'g'),(0xB9FE,'h'),
         (0xBBF7,'j'),(0xBFA6,'k'),(0xC0AC,'l'),(0xC2E8,'m'),(0xC4C3,'n'),(0xC5B6,'o'),(0xC5BE,'p'),(0xC6DA,'q'),
         (0xC8BB,'r'),(0xC8F6,'s'),(0xCBFA,'t'),(0xCDDA,'w'),(0xCEF4,'x'),(0xD1B9,'y'),(0xD4D1,'z'))
_LEVEL1_END=0xD7F9

# Second-level characters seen in effect names, and polyphones whose other reading is common in them (重复 c, 伽马 g).
EXTRA={'渲':'x','阈':'y','伽':'gj','漩':'x','嗖':'s','浏':'l','彗':'h','霭':'a','炫':'x','漪':'y','粝':'l','魅':'m','氛':'f',
       '重':'zc','长':'cz','调':'td','弹':'td','行':'xh','降':'jx','藏':'cz','校':'xj','更':'g','曾':'cz','朝':'cz','着':'zc'}

def initial(char:str)->str:
    """The pinyin initial(s) of a common Chinese character, the lower-case letter or digit itself, else ''.
    A few polyphones give more than one letter (重 -> 'zc')."""
    if char.isascii():return char.lower() if char.isalnum() else ''
    if char in EXTRA:return EXTRA[char]
    try:raw=char.encode('gb2312')
    except UnicodeEncodeError:return ''
    if len(raw)!=2:return ''
    code=raw[0]<<8|raw[1]
    if not _BOUNDS[0][0]<=code<=_LEVEL1_END:return ''
    letter=''
    for start,value in _BOUNDS:
        if code<start:break
        letter=value
    return letter

def letters(text:str)->list[str]:
    """The possible initials of each position: ['g','s','m','h'] for 高斯模糊, ['c','zc','f'] for 长重复... ASCII
    words count by their first letter (Gaussian Blur -> g, b); characters without an initial are skipped."""
    out=[];word=False
    for char in text:
        if char.isascii() and char.isalnum():
            if not word:out.append(char.lower())
            word=True
        else:
            word=False
            if value:=initial(char):out.append(value)
    return out

def initials(text:str)->str:
    """gsmh for 高斯模糊 (a polyphone's first reading)."""
    return ''.join(options[0] for options in letters(text))

def pinyin_hit(text:str,query:str)->int:
    """2 when the query spells the initials from the start, 1 when it spells them from elsewhere, else 0."""
    options=letters(text)
    for start in range(len(options)-len(query)+1):
        if all(char in options[start+i] for i,char in enumerate(query)):return 2 if start==0 else 1
    return 0

class Entry(NamedTuple):
    match_name:str
    english:str     # the plug-in's own name
    chinese:str     # AE's current name when it was localized, else the Chinese candidate, else ''
    category:str
    extra:str=''    # the other Chinese name (candidate vs AE's current), searched but not shown
    live:str=''     # the name AE's effects list shows now (only when connected)

    @property
    def title(self)->str:return self.chinese or self.english

def has_chinese(text:str)->bool:return any('一'<=c<='鿿' for c in text)

def build_entries(plugins,candidates:dict[str,str],live_effects=None)->list[Entry]:
    """One entry per effect. With AE connected (live_effects: its catalog rows) only effects AE has loaded are
    listed, since only those can be added; their current AE names count as Chinese names when localized."""
    current={r['matchName']:r.get('displayName','') for r in live_effects or () if r.get('matchName')}
    seen=set();entries=[]
    for plugin in plugins:
        key=plugin.match_name
        if not key or key in seen or (live_effects is not None and key not in current):continue
        seen.add(key)
        english=plugin.display_name or key;candidate=(candidates.get(key) or '').strip();now=current.get(key,'')
        shown=now if now and now!=english and has_chinese(now) else candidate
        extra=candidate if shown!=candidate else (now if now and now!=english else '')
        entries.append(Entry(key,english,shown,readable_category(plugin.category or ''),extra,now))
    # Effects AE has that the plug-in scan missed (it lists only files it could read).
    for key,name in current.items():
        if key not in seen and name:
            seen.add(key);entries.append(Entry(key,name,name if has_chinese(name) else (candidates.get(key) or '').strip(),'','',name))
    return entries

def _subsequence(query:str,text:str)->bool:
    found=0
    for char in text:
        if found<len(query) and char==query[found]:found+=1
    return found==len(query)

def score(entry:Entry,query:str)->int:
    """How well an entry matches; 0 is no match. Chinese names first, then pinyin initials, English, the rest."""
    q=''.join(query.split()).casefold()
    if not q:return 1
    chinese=[''.join(n.split()).casefold() for n in (entry.chinese,entry.extra) if n]
    english=entry.english.casefold();squeezed=''.join(english.split())
    best=0
    def take(value):
        nonlocal best;best=max(best,value)
    for name in chinese:
        if name==q:take(120)
        elif name.startswith(q):take(100)
        elif q in name:take(85)
        hit=pinyin_hit(name,q) if q.isascii() and q.isalnum() else 0
        if hit:take(75 if hit==2 else 62)
    if english==q or squeezed==q:take(110)
    elif english.startswith(q) or squeezed.startswith(q):take(80)
    elif any(word.startswith(q) for word in english.split()):take(70)
    elif q in squeezed:take(55)
    if pinyin_hit(entry.english,q)==2:take(50)
    if q in entry.match_name.casefold():take(35)
    if q in entry.category.casefold():take(25)
    if not best and len(q)>1 and any(_subsequence(q,text) for text in (*chinese,squeezed,*map(initials,chinese))):take(10)
    return best

PREFIX_SCORES={120,110,100,80,75,50}   # score(): the query is the whole name or its beginning

def search(entries:list[Entry],query:str,limit=12)->list[Entry]:
    scored=[(score(e,query),e) for e in entries]
    hits=[(s,e) for s,e in scored if s>0]
    # Best match first. Names that start with the query come in name order ("CC" lists the CC effects A to Z); among
    # names that only contain it the shorter, closer one leads (模糊 -> 高斯模糊 before 快速方框模糊).
    hits.sort(key=lambda pair:(-pair[0],0 if pair[0] in PREFIX_SCORES else len(pair[1].title),pair[1].title.casefold()))
    return [e for _,e in hits[:limit]]

def record_timing(path:Path,seconds:float,route:str,changed:int,ok:bool):
    """quick-search-timing.jsonl: numbers only (no effect, layer or project names), at most 200 lines kept."""
    import json,time
    try:
        lines=path.read_text(encoding='utf8').splitlines()[-199:] if path.exists() else []
        lines.append(json.dumps({'time':int(time.time()),'ms':round(seconds*1000),'route':route,'layers':changed,'ok':ok}))
        path.write_text(''.join(line+'\n' for line in lines),encoding='utf8')
    except OSError:pass

# --- The hotkey (Windows): registered only while AE is in front. ---
WM_HOTKEY=0x0312
MOD_ALT,MOD_CONTROL,MOD_SHIFT,MOD_WIN,MOD_NOREPEAT=0x1,0x2,0x4,0x8,0x4000
EVENT_SYSTEM_FOREGROUND=3;WINEVENT_OUTOFCONTEXT=0
HOTKEY_ID=0x5A51   # quick search; every AeHotkey has its own id (layer parameters: HOTKEY_ID+1)

def virtual_key(seq:QKeySequence)->tuple[int,int]|None:
    """(modifiers, virtual-key code) for RegisterHotKey, or None for a key it cannot express."""
    if seq.isEmpty():return None
    combo=seq[0];key=combo.key();mods=combo.keyboardModifiers();flags=MOD_NOREPEAT
    for qt,win in ((Qt.KeyboardModifier.ControlModifier,MOD_CONTROL),(Qt.KeyboardModifier.ShiftModifier,MOD_SHIFT),(Qt.KeyboardModifier.AltModifier,MOD_ALT),(Qt.KeyboardModifier.MetaModifier,MOD_WIN)):
        if mods&qt:flags|=win
    value=int(key.value) if hasattr(key,'value') else int(key)
    if Qt.Key.Key_A.value<=value<=Qt.Key.Key_Z.value or Qt.Key.Key_0.value<=value<=Qt.Key.Key_9.value:return flags,value
    if Qt.Key.Key_F1.value<=value<=Qt.Key.Key_F12.value:return flags,0x70+value-Qt.Key.Key_F1.value
    special={Qt.Key.Key_Space:0x20,Qt.Key.Key_Return:0x0D,Qt.Key.Key_Enter:0x0D,Qt.Key.Key_Insert:0x2D,Qt.Key.Key_Delete:0x2E,
             Qt.Key.Key_Home:0x24,Qt.Key.Key_End:0x23,Qt.Key.Key_PageUp:0x21,Qt.Key.Key_PageDown:0x22,
             Qt.Key.Key_QuoteLeft:0xC0,Qt.Key.Key_Minus:0xBD,Qt.Key.Key_Equal:0xBB,Qt.Key.Key_Slash:0xBF,Qt.Key.Key_Period:0xBE,Qt.Key.Key_Comma:0xBC,Qt.Key.Key_Semicolon:0xBA}
    code=special.get(key)
    return (flags,code) if code else None

AE_IMAGE='afterfx.exe'
def window_image(hwnd)->str:
    """The executable name (lower case) of the process owning a window, or ''."""
    from win_process import window_process
    found=window_process(hwnd)
    return Path(found[1]).name.lower() if found else ''

def foreground()->int:return int(ctypes.windll.user32.GetForegroundWindow() or 0)

def bring_forward(hwnd:int)->bool:
    """Gives the foreground back to a window (AE) once the box closes; allowed because this program has it."""
    return bool(hwnd) and bool(ctypes.windll.user32.SetForegroundWindow(W.HWND(hwnd)))

WINEVENTPROC=ctypes.WINFUNCTYPE(None,W.HANDLE,W.DWORD,W.HWND,W.LONG,W.LONG,W.DWORD,W.DWORD)

class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self,owner):super().__init__();self.owner=owner
    def nativeEventFilter(self,event_type,message):
        if event_type in (b'windows_generic_MSG','windows_generic_MSG'):
            msg=W.MSG.from_address(int(message))
            if msg.message==WM_HOTKEY and msg.wParam==self.owner.id:
                self.owner.fire();return True,0
        return False,0

class AeHotkey(QObject):
    """A global key that exists only while AE is the foreground window. Bound through shortcuts.Keymap like any
    other command (setKeys); the master shortcut switch and an empty key turn it off."""
    triggered=Signal(int)   # the AE window that was in front
    unavailable=Signal(str) # the key, when another program already holds it (said once per key)
    def __init__(self,parent=None,active=None,hotkey_id=HOTKEY_ID):
        super().__init__(parent);self.id=hotkey_id;self.key=None;self.registered=False;self.ae_window=0;self.failed=None;self.key_text=''
        # Offscreen (tests) or not Windows: nothing global is ever registered.
        self.active=(sys.platform=='win32' and QGuiApplication.platformName()!='offscreen') if active is None else active
        self.filter=None;self.hook=None;self.proc=None;self.timer=None
        if self.active:
            self.filter=_HotkeyFilter(self);QApplication.instance().installNativeEventFilter(self.filter)
            self.proc=WINEVENTPROC(lambda *args:self.follow())
            self.hook=ctypes.windll.user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND,EVENT_SYSTEM_FOREGROUND,None,self.proc,0,0,WINEVENT_OUTOFCONTEXT)
            # A slow look as well, for foreground changes Windows does not report (such as a restore from minimized).
            self.timer=QTimer(self);self.timer.setInterval(500);self.timer.timeout.connect(self.follow);self.timer.start()
    def setKeys(self,keys:list[QKeySequence]):
        """Keymap's interface: the first key is used; [] switches the hotkey off."""
        key=virtual_key(keys[0]) if keys else None
        if key==self.key:return
        self.release();self.key=key;self.key_text=keys[0].toString(QKeySequence.SequenceFormat.NativeText) if keys else '';self.failed=None;self.follow()
    def follow(self):
        """Registers the key while AE is in front and releases it otherwise."""
        if not self.active or self.key is None:return
        hwnd=foreground();ae=window_image(hwnd)==AE_IMAGE
        if ae:self.ae_window=hwnd
        if ae and not self.registered:
            modifiers,code=self.key
            self.registered=bool(ctypes.windll.user32.RegisterHotKey(None,self.id,modifiers,code))
            if not self.registered and self.failed is None:
                self.failed=hwnd   # another program owns the key (an input method, FX Console...)
                self.unavailable.emit(self.key_text)
        elif not ae and self.registered:self.release()
    def release(self):
        if self.registered:ctypes.windll.user32.UnregisterHotKey(None,self.id);self.registered=False
    def fire(self):
        hwnd=foreground()
        if window_image(hwnd)!=AE_IMAGE:self.release();return
        self.ae_window=hwnd;self.triggered.emit(hwnd)
    def close(self):
        self.release()
        if self.timer:self.timer.stop()
        if self.hook:ctypes.windll.user32.UnhookWinEvent(self.hook);self.hook=None
        if self.filter:QApplication.instance().removeNativeEventFilter(self.filter);self.filter=None

# --- The box. ---
ROW=32
HEAD=24            # a section caption (收藏 / 最近使用) shown before anything is typed
MAX_RESULTS=5000   # in effect every match: one row each, the list scrolls
VISIBLE=8          # effect rows shown at once; the rest scroll
class QuickSearch(QWidget):
    """A frameless search box near the pointer: type, ↑↓ to choose, Enter to add, Esc (or clicking elsewhere) to close."""
    chosen=Signal(object)   # Entry
    def __init__(self,entries=None):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        self.entries=list(entries or ());self.busy=False;self.return_to=0;self.pinned=[];self.recent=[]
        self.pinned_caption='收藏'   # the window sets the current folder's name
        # upcoming() -> (favourites, recent) for the next opening; the closed box is left showing them (see setVisible).
        self.upcoming=None
        # The window is always exactly as big as what it shows: compact with no results, taller with more (up to 8 rows),
        # never left at the size of an earlier search.
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        surface=self.surface=Surface(radius=14,padding=(10,10,10,8),spacing=6);surface.setFixedWidth(520);outer.addWidget(surface)
        field=SearchField('搜索效果：中文、英文或拼音首字母');field.setFixedHeight(40);self.edit=field.edit;surface.box.addWidget(field)
        self.list=QListWidget();self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus);self.list.setUniformItemSizes(True)
        self.list.itemClicked.connect(lambda item:self.choose(self.list.row(item)));surface.box.addWidget(self.list)
        self.note=label('','muted');surface.box.addWidget(self.note)
        self.edit.textChanged.connect(self.refresh);self.edit.installEventFilter(self)
    def open(self,entries:list,return_to:int=0,at:QPoint|None=None,pinned=(),recent=()):
        """pinned: favourite entries in order, recent: recently added ones; both listed before anything is typed."""
        self.entries=list(entries);self.pinned=list(pinned);self.recent=list(recent)
        self.return_to=return_to;self.busy=False;self.adding=None;self.edit.setReadOnly(False)
        self.edit.blockSignals(True);self.edit.clear();self.edit.blockSignals(False);self.refresh();self.place(at or QCursor.pos())
        # Shown fully transparent and painted first: Windows otherwise puts up the window before its first paint
        # (one blank white frame), then the real box -- a flash. Visible only once a whole frame is ready.
        self.setWindowOpacity(0.0);self.show();self.raise_();self.activateWindow();self.edit.setFocus();self.repaint()
        QTimer.singleShot(0,self.reveal)
        if sys.platform=='win32' and QGuiApplication.platformName()!='offscreen':bring_forward(int(self.winId()))
    def reveal(self):
        # Always back to opaque: a box hidden meanwhile and shown again later (an error message) must not stay invisible.
        if self.isVisible():self.repaint()
        self.setWindowOpacity(1.0)
    def showEvent(self,event):
        super().showEvent(event)
        if not getattr(self,'calm',False) and sys.platform=='win32' and QGuiApplication.platformName()!='offscreen':
            # No Windows show/hide animation for this small box (DWMWA_TRANSITIONS_FORCEDISABLED).
            self.calm=True;value=ctypes.c_int(1)
            try:ctypes.windll.dwmapi.DwmSetWindowAttribute(W.HWND(int(self.winId())),3,ctypes.byref(value),4)
            except OSError:pass
    def place(self,point:QPoint):
        self.fit()
        screen=QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen();room=screen.availableGeometry()
        x=min(max(point.x()-40,room.left()+8),room.right()-self.width()-8)
        y=point.y()+16
        if y+self.height()>room.bottom()-8:y=max(room.top()+8,point.y()-self.height()-16)
        self.move(x,y)
    def sections(self)->list[tuple[str,list[Entry]]]:
        """What shows before anything is typed: the current folder's favourites, then the recent effects."""
        return [(caption,entries) for caption,entries in ((self.pinned_caption,self.pinned),('最近使用',self.recent)) if entries]
    def refresh(self,*_):
        # Every match, best first ("CC" alone matches about a hundred effects); 8 rows show, the rest scroll.
        query=self.edit.text();typed=bool(query.strip())
        groups=[('',search(self.entries,query,limit=MAX_RESULTS))] if typed else self.sections()
        # Uniform rows keep a long result list fast; the captions are shorter, so not with them.
        self.list.clear();self.list.setUniformItemSizes(typed);height=0;shown=0;first=-1
        for caption,entries in groups:
            if caption:
                # A caption row: not selectable, the arrow keys step over it.
                head=QListWidgetItem('  '+caption);head.setFlags(Qt.ItemFlag.NoItemFlags);head.setSizeHint(QSize(0,HEAD));head.setForeground(QColor(ui_kit.MUTED))
                font=head.font();font.setPixelSize(11);head.setFont(font);self.list.addItem(head)
                if shown<VISIBLE:height+=HEAD
            for entry in entries:
                # Only the name: the English name, internal name, category and pinyin still match, but are not shown.
                item=QListWidgetItem('  '+entry.title);item.setSizeHint(QSize(0,ROW));item.setData(Qt.ItemDataRole.UserRole,entry);item.setToolTip(entry.match_name);self.list.addItem(item)
                if first<0:first=self.list.count()-1
                if shown<VISIBLE:height+=ROW
                shown+=1
        if first>=0:self.list.setCurrentRow(first)
        self.list.setVisible(bool(shown));self.list.setFixedHeight(height+4 if shown else 0)
        if not self.busy:
            self.note.setText('没有匹配的效果' if typed and not shown else '' if typed else f'共 {len(self.entries)} 个效果 · ↑↓ 选择，Enter 添加到所选图层，Esc 关闭')
        self.fit()
    def step(self,down:bool):
        """Moves to the next (or previous) effect row, round the end, over the captions."""
        count=self.list.count();row=self.list.currentRow()
        for _ in range(count):
            row=(row+(1 if down else -1))%count
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) is not None:self.list.setCurrentRow(row);return
    def fit(self):
        # The list comes and goes: settle the layout first so the window shrinks as well as grows.
        # Settled at once, even while hidden: otherwise Qt shows the window at its old size for a frame (a flash).
        self.note.setVisible(bool(self.note.text()))
        self.surface.layout().invalidate();self.surface.updateGeometry();self.layout().invalidate();self.layout().activate()
    def eventFilter(self,watched,event):
        if watched is self.edit and event.type()==QEvent.Type.KeyPress:
            key=event.key()
            if key==Qt.Key.Key_Escape:self.dismiss();return True
            if key in (Qt.Key.Key_Down,Qt.Key.Key_Up) and self.list.count():self.step(key==Qt.Key.Key_Down);return True
            if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter):self.choose(self.list.currentRow());return True
        return False
    def choose(self,row:int):
        if self.busy or row<0 or row>=self.list.count():return
        entry=self.list.item(row).data(Qt.ItemDataRole.UserRole)
        if entry is None:return    # a caption
        # Nothing on screen changes for a normal add (about 0.1 s): no greyed field, no extra line that would make
        # the box jump just before it closes. Typing is ignored meanwhile; a slow add says so after 0.4 s.
        self.busy=True;self.edit.setReadOnly(True);self.adding=entry
        QTimer.singleShot(400,lambda:self.show_note(f'正在添加“{entry.title}”……') if self.busy and self.adding is entry else None)
        self.chosen.emit(entry)
    def show_note(self,text:str,error=False):
        self.note.setText(text);self.note.setStyleSheet(f'color:{ui_kit.DANGER};' if error else '');self.fit()
    def finished(self,error:str=''):
        """The add came back: close and give AE the keyboard again, or keep the box open with the reason."""
        self.busy=False;self.adding=None;self.edit.setReadOnly(False)
        if not error:self.dismiss();return
        self.show_note(error,True)
        if not self.isVisible():self.show()
        self.raise_();self.activateWindow();self.edit.setFocus();self.edit.selectAll()
    def dismiss(self):
        self.hide()
        if self.return_to and sys.platform=='win32' and QGuiApplication.platformName()!='offscreen':bring_forward(self.return_to)
    def setVisible(self,visible):
        # Every way of closing (add, Esc, clicking elsewhere) ends here. Windows keeps the last picture of a hidden
        # see-through window and may put it up again for the first frame of the next opening: so the box is set to
        # what that opening will show (rest), made transparent and drawn once more before it hides.
        if not visible and self.isVisible() and not getattr(self,'vanishing',False):
            self.vanishing=True
            try:
                self.setWindowOpacity(0.0);self.repaint()     # transparent first, so the reset below is never seen
                if not self.busy:self.rest()
                self.repaint()
            finally:self.vanishing=False
        super().setVisible(visible)
    def hideEvent(self,event):
        # Leave it as the next opening starts, so that opening paints nothing from this one.
        super().hideEvent(event)
        if not self.busy:self.rest()
    def rest(self):
        """The state the next opening starts in: nothing typed, and the favourites and recent effects it will list
        (measured at a dark box: resting compact while the next opening lists favourites put up one frame of the
        compact box first)."""
        if self.upcoming:self.pinned,self.recent=map(list,self.upcoming())
        self.edit.blockSignals(True);self.edit.clear();self.edit.blockSignals(False);self.note.setStyleSheet('');self.refresh()
    def changeEvent(self,event):
        # Clicking anywhere else closes it, except while an add is running (AE may take the focus meanwhile).
        if event.type()==QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible() and not self.busy:
            QTimer.singleShot(0,lambda:None if self.isActiveWindow() or self.busy else self.hide())
        super().changeEvent(event)
