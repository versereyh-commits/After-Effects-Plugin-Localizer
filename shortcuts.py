"""Keyboard shortcuts: one table of commands, the user's own keys in shortcuts.json and a master switch.

Every QAction / QShortcut is bound to a command id; a change (or the switch) re-keys all of them at once,
so new keys work immediately without restarting."""
from __future__ import annotations
import json
from pathlib import Path
from runtime import write_json_atomic
from typing import NamedTuple
from PySide6.QtCore import Qt,Signal,QKeyCombination,QTimer
from PySide6.QtGui import QAction,QColor,QKeySequence,QShortcut
from PySide6.QtWidgets import QDialog,QHBoxLayout,QHeaderView,QLineEdit,QTableWidget,QTableWidgetItem,QToolButton,QVBoxLayout,QAbstractItemView
import ui_kit
from ui_kit import SHADOW,Banner,SearchField,Surface,button,confirm,icon,label,table_style

PORTABLE=QKeySequence.SequenceFormat.PortableText
NATIVE=QKeySequence.SequenceFormat.NativeText
PAGES=('效果名称','参数名称','翻译方案','AE 版本','设置与日志')

class Command(NamedTuple):
    id:str
    title:str
    group:str
    default:str
    local:bool=False    # Works only while its list or table has focus.

COMMANDS=(
    Command('translate','翻译所选项 / 勾选项','效果名称','Ctrl+T'),
    Command('apply','应用所选项 / 勾选项到 AE','效果名称','Ctrl+Return'),
    Command('restore','恢复所选项 / 勾选项原名','效果名称',''),
    Command('find','搜索效果','效果名称','Ctrl+F'),
    Command('refresh','刷新 AE“效果和预设”列表','效果名称','Ctrl+R'),
    Command('check_filtered','勾选当前筛选结果','效果名称',''),
    Command('uncheck_all','取消全部勾选','效果名称',''),
    Command('connect','连接 / 重新读取 AE','AE','F5'),
    Command('restore_all','一键恢复全部默认（含参数）','AE',''),
    Command('save_scheme','保存更改到当前方案','翻译方案','Ctrl+S'),
    Command('save_as','新建方案 / 另存勾选项为新方案','翻译方案','Ctrl+Shift+S'),
    Command('import','从文件导入方案','翻译方案',''),
    Command('show_all','显示全部效果（使用方案时）','翻译方案',''),
    *(Command(f'page{index}',f'转到“{name}”','页面',f'Ctrl+{index+1}') for index,name in enumerate(PAGES)),
    Command('undo','撤销方案或汉化记录的修改','列表与表格','Ctrl+Z',True),
    Command('delete','删除所选方案 / 从方案中删除','列表与表格','Delete',True),
    Command('rename','重命名所选方案','列表与表格','F2',True),
    Command('load','载入所选方案','列表与表格','Return',True),
)
BY_ID={command.id:command for command in COMMANDS}
FKEYS={getattr(Qt.Key,f'Key_F{n}') for n in range(1,13)}
LOCAL_KEYS=FKEYS|{Qt.Key.Key_Delete,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Backspace,Qt.Key.Key_Insert,Qt.Key.Key_Space}
MODIFIER_KEYS={Qt.Key.Key_Control,Qt.Key.Key_Shift,Qt.Key.Key_Alt,Qt.Key.Key_Meta,Qt.Key.Key_AltGr,Qt.Key.Key_unknown}
HELD=Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.AltModifier|Qt.KeyboardModifier.MetaModifier

def sequence(text:str)->QKeySequence:return QKeySequence.fromString(text or '',PORTABLE)
def portable(seq:QKeySequence)->str:return seq.toString(PORTABLE)
def same_key(text:str)->str:
    """Keypad Enter and Return are one key here, so Ctrl+Enter conflicts with Ctrl+Return."""
    return portable(sequence(text)).replace('Enter','Return')
def aliases(text:str)->list[QKeySequence]:
    """The key, plus keypad Enter wherever Return was chosen."""
    keys=[sequence(text)]
    if 'Return' in text:keys.append(sequence(text.replace('Return','Enter')))
    return keys

def local_shortcut(parent,slot,context=Qt.ShortcutContext.WidgetShortcut)->QShortcut:
    """A key that works only while `parent` (or, with WidgetWithChildrenShortcut, something inside it) has focus; keyed by bind()."""
    shortcut=QShortcut(parent);shortcut.setContext(context);shortcut.activated.connect(slot);return shortcut

def problem(command_id:str,seq:QKeySequence)->str:
    """Why a key cannot be used for this command; empty when it can."""
    if seq.isEmpty():return ''
    if seq.count()!=1:return '只能设置一个组合键'
    combo=seq[0];key=combo.key();mods=combo.keyboardModifiers()
    if key in MODIFIER_KEYS:return '请在修饰键之外再按一个键'
    if key==Qt.Key.Key_Escape:return 'Esc 用于关闭窗口和取消录制，不能设置为快捷键'
    if key==Qt.Key.Key_Tab or key==Qt.Key.Key_Backtab:return 'Tab 用于切换输入焦点，不能设置为快捷键'
    if mods&HELD or key in FKEYS:return ''
    if BY_ID[command_id].local and key in LOCAL_KEYS:return ''
    return '需要搭配 Ctrl 或 Alt（F1–F12 除外），以免打字时误触'+('；列表类操作也可以用 Delete、Enter、Space' if BY_ID[command_id].local else '')

class Keymap:
    """The keys in force. `enabled` is the master switch: off clears every bound key but keeps the choices."""
    def __init__(self,path):
        self.path=Path(path);self.enabled=True;self.keys={c.id:c.default for c in COMMANDS};self.targets={};self.listeners=[];self.load()
    def load(self):
        try:data=json.loads(self.path.read_text(encoding='utf8'))
        except (OSError,ValueError):return
        if not isinstance(data,dict):return
        self.enabled=data.get('enabled',True) is not False
        keys=data.get('keys')
        if not isinstance(keys,dict):return
        for command_id,text in keys.items():
            if command_id not in BY_ID or not isinstance(text,str):continue
            seq=sequence(text)
            if text and (seq.isEmpty() or problem(command_id,seq)):continue
            other=self.conflict(command_id,seq)
            if other:self.keys[other]=''
            self.keys[command_id]=portable(seq)
    def save(self)->str:
        data={'enabled':self.enabled,'keys':{k:v for k,v in self.keys.items() if v!=BY_ID[k].default}}
        try:
            write_json_atomic(self.path,data);return ''
        except OSError as error:return '快捷键设置未能保存：'+str(error)
    # Reading.
    def text(self,command_id)->str:
        """The key as shown on screen, whether or not shortcuts are switched on."""
        return sequence(self.keys[command_id]).toString(NATIVE)
    def hint(self,command_id)->str:
        """The key that works right now; empty when unset or switched off."""
        return self.text(command_id) if self.enabled else ''
    def tip(self,command_id,text)->str:
        key=self.hint(command_id);return f'{text}（{key}）' if key else text
    def conflict(self,command_id,seq)->str|None:
        if seq.isEmpty():return None
        wanted=same_key(portable(seq))
        return next((c.id for c in COMMANDS if c.id!=command_id and self.keys[c.id] and same_key(self.keys[c.id])==wanted),None)
    def customized(self)->int:return sum(1 for c in COMMANDS if self.keys[c.id]!=c.default)
    # Changing; every change re-keys the bound actions and tells the listeners.
    def bind(self,command_id,target):
        self.targets.setdefault(command_id,[]).append(target);self._key(target,command_id);return target
    def _key(self,target,command_id):
        keys=aliases(self.keys[command_id]) if self.enabled and self.keys[command_id] else []
        if isinstance(target,QAction):target.setShortcuts(keys)
        else:target.setKeys(keys)
    def apply(self):
        for command_id,targets in self.targets.items():
            alive=[]
            for target in targets:
                try:self._key(target,command_id);alive.append(target)
                except RuntimeError:pass    # Its widget was destroyed.
            self.targets[command_id]=alive
        for listener in list(self.listeners):listener()
    def assign(self,command_id,seq:QKeySequence)->str:
        """Sets a key (empty clears it), taking it from any command that had it. Returns a save error, if any."""
        other=self.conflict(command_id,seq)
        if other:self.keys[other]=''
        self.keys[command_id]=portable(seq);self.apply();return self.save()
    def set_enabled(self,on)->str:
        self.enabled=bool(on);self.apply();return self.save()
    def reset(self,command_id=None)->str:
        for c in COMMANDS if command_id is None else (BY_ID[command_id],):
            other=self.conflict(c.id,sequence(c.default))
            if other:self.keys[other]=''
            self.keys[c.id]=c.default
        self.apply();return self.save()

class KeyCapture(QLineEdit):
    """Records one key combination: Esc cancels, Backspace clears. Receivers must not delete it from the signal
    (it is still inside its own key or focus handler); ShortcutDialog acts on the next turn of the event loop."""
    captured=Signal(QKeySequence);cancelled=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self.setReadOnly(True);self.setObjectName('field');self.setPlaceholderText('按下新的组合键…');self.done=False
    def keyPressEvent(self,event):
        key=Qt.Key(event.key());mods=event.modifiers()&(Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier|Qt.KeyboardModifier.AltModifier|Qt.KeyboardModifier.MetaModifier)
        if key in MODIFIER_KEYS:self.setText(QKeySequence(mods.value).toString(NATIVE));return
        if key==Qt.Key.Key_Escape and not mods:self.finish(None);return
        if key==Qt.Key.Key_Backspace and not mods:self.finish(QKeySequence());return
        if key==Qt.Key.Key_Enter:key=Qt.Key.Key_Return
        self.finish(QKeySequence(QKeyCombination(mods,key)))
    def keyReleaseEvent(self,event):
        if not self.done and not event.modifiers():self.setText('')
    def focusOutEvent(self,event):
        super().focusOutEvent(event)
        if not self.done:self.finish(None)
    def finish(self,seq):
        if self.done:return
        self.done=True
        if seq is None:self.cancelled.emit()
        else:self.captured.emit(seq)

K_TITLE,K_KEY,K_DEFAULT,K_RESET=range(4)

class ShortcutDialog(QDialog):
    """Lists every command; click a key to record a new one. Each change takes effect and is saved at once."""
    def __init__(self,keymap:Keymap,parent=None):
        super().__init__(parent);self.keymap=keymap;self.setWindowTitle('快捷键');self.setMinimumSize(640,560);self.rows=[];self.capture=None
        # A recording's result is handled on the next turn of the event loop, never inside the recorder's own handler.
        self.pending=None;self.later=QTimer(self);self.later.setSingleShot(True);self.later.setInterval(0);self.later.timeout.connect(self.handle_pending)
        outer=QVBoxLayout(self);outer.setContentsMargins(20,22,20,18);outer.setSpacing(8)
        head=QVBoxLayout();head.setContentsMargins(SHADOW,0,SHADOW,0);head.setSpacing(2)
        head.addWidget(label('快捷键','pageTitle'));head.addWidget(label('点击“快捷键”一栏，直接按下新的组合键，立即生效。Esc 取消录制，Backspace 清除。','pageNote',True));outer.addLayout(head)
        self.off=Banner('warn');self.off.text.setText('快捷键已全部停用（设置页的总开关）。这里的修改会保存，重新启用后生效。');outer.addWidget(self.off)
        surface=Surface(padding=(14,12,14,8),spacing=8)
        search=SearchField('搜索功能或按键');self.search=search.edit;self.search.textChanged.connect(self.filter);surface.box.addWidget(search)
        table=self.table=QTableWidget(0,4);table.setHorizontalHeaderLabels(['功能','快捷键','默认','']);table_style(table,36)
        header=table.horizontalHeader();header.setSectionResizeMode(K_TITLE,QHeaderView.ResizeMode.Stretch)
        for column,width in ((K_KEY,170),(K_DEFAULT,120),(K_RESET,44)):header.setSectionResizeMode(column,QHeaderView.ResizeMode.Fixed);table.setColumnWidth(column,width)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.cellClicked.connect(self.clicked);surface.box.addWidget(table,1)
        self.message=label('','muted',True);self.message.setContentsMargins(2,0,2,0);surface.box.addWidget(self.message);outer.addWidget(surface,1)
        foot=QHBoxLayout();foot.setContentsMargins(SHADOW,6,SHADOW,0)
        self.reset_all_btn=button('全部恢复默认');self.reset_all_btn.clicked.connect(self.reset_all);foot.addWidget(self.reset_all_btn);foot.addStretch()
        close=button('关闭','primary');close.clicked.connect(self.accept);close.setDefault(True);foot.addWidget(close);outer.addLayout(foot)
        self.fill()
    def fill(self):
        """Group captions, then one row per command."""
        self.capture=None;table=self.table;table.clearSpans();table.setRowCount(0);self.rows=[]
        group=None
        for command in COMMANDS:
            if command.group!=group:
                group=command.group;row=table.rowCount();table.insertRow(row);self.rows.append(None)
                item=QTableWidgetItem(group+('（只在对应列表或表格中生效）' if command.local else ''));font=item.font();font.setBold(True);item.setFont(font);item.setForeground(QColor(ui_kit.MUTED))
                table.setItem(row,0,item);table.setSpan(row,0,1,4)
            row=table.rowCount();table.insertRow(row);self.rows.append(command.id)
            key=self.keymap.text(command.id);default=sequence(command.default).toString(NATIVE)
            title=QTableWidgetItem(command.title);title.setToolTip(command.title)
            shown=QTableWidgetItem(key or '未设置');shown.setToolTip('点击后按下新的组合键');shown.setForeground(QColor(ui_kit.INK if key else ui_kit.FAINT))
            font=shown.font();font.setBold(bool(key) and key!=default);shown.setFont(font)
            plain=QTableWidgetItem(default or '无');plain.setForeground(QColor(ui_kit.MUTED))
            for column,item in ((K_TITLE,title),(K_KEY,shown),(K_DEFAULT,plain)):table.setItem(row,column,item)
            if self.keymap.keys[command.id]!=command.default:
                reset=QToolButton();reset.setObjectName('icon');ui_kit.themed_icon(reset,'restore','INK_2',16);reset.setToolTip(f'恢复默认（{default or "无"}）')
                reset.setCursor(Qt.CursorShape.PointingHandCursor);reset.clicked.connect(lambda _=False,c=command.id:self.reset(c));table.setCellWidget(row,K_RESET,reset)
        self.off.setVisible(not self.keymap.enabled);self.reset_all_btn.setEnabled(self.keymap.customized()>0);self.filter()
    def filter(self,*_):
        text=self.search.text().strip().casefold();shown=set()
        for row,command_id in enumerate(self.rows):
            if command_id is None:continue
            command=BY_ID[command_id];hit=not text or text in f'{command.title} {command.group} {self.keymap.text(command_id)}'.casefold()
            self.table.setRowHidden(row,not hit)
            if hit:shown.add(command.group)
        for row,command_id in enumerate(self.rows):
            if command_id is None:self.table.setRowHidden(row,self.table.item(row,0).text().split('（')[0] not in shown)
    def row_of(self,command_id)->int:return self.rows.index(command_id)
    def clicked(self,row,column):
        if 0<=row<len(self.rows) and self.rows[row] and column in (K_TITLE,K_KEY,K_DEFAULT):self.record(self.rows[row])
    def record(self,command_id):
        """Puts a recorder in the key cell; the next combination pressed is the new key."""
        if self.capture is not None:self.stop_recording();self.fill()
        row=self.row_of(command_id);capture=self.capture=KeyCapture(self.table)
        capture.captured.connect(lambda seq,c=capture:self.queue(c,command_id,seq));capture.cancelled.connect(lambda c=capture:self.queue(c,command_id,None))
        self.table.setCellWidget(row,K_KEY,capture);capture.setFocus();self.message.setText(f'正在为“{BY_ID[command_id].title}”录制……')
    def queue(self,capture,command_id,seq):
        if capture is not self.capture:return
        self.pending=(command_id,seq);self.later.start()
    def handle_pending(self):
        pending,self.pending=self.pending,None
        if pending is None or not self.isVisible():return
        command_id,seq=pending
        if seq is None:self.fill();self.message.setText('')
        else:self.chosen(command_id,seq)
    def stop_recording(self):
        """Ends a recording without acting on it (another row was clicked, or the dialog is closing)."""
        if self.capture is not None:self.capture.done=True
        self.later.stop();self.pending=None
    def chosen(self,command_id,seq:QKeySequence):
        command=BY_ID[command_id];why=problem(command_id,seq);key=seq.toString(NATIVE)
        if why:self.fill();self.message.setText(f'{key} 不能用于“{command.title}”：{why}');return
        other=self.keymap.conflict(command_id,seq)
        if other and not confirm(self,f'{key} 已分配给“{BY_ID[other].title}”',f'改为分配给“{command.title}”，并清除“{BY_ID[other].title}”的快捷键？','改为分配'):
            self.fill();self.message.setText('未更改');return
        error=self.keymap.assign(command_id,seq);self.fill()
        self.message.setText(error or (f'“{command.title}”已清除快捷键' if seq.isEmpty() else f'“{command.title}”改为 {key}'+(f'，“{BY_ID[other].title}”已清除' if other else '')+('；快捷键启用后生效' if not self.keymap.enabled else '')))
    def reset(self,command_id):
        default=BY_ID[command_id].default;other=self.keymap.conflict(command_id,sequence(default))
        error=self.keymap.reset(command_id);self.fill()
        self.message.setText(error or f'“{BY_ID[command_id].title}”已恢复默认'+(f'，“{BY_ID[other].title}”已清除' if other else ''))
    def done(self,result):
        """Closing (the title-bar ×, Esc outside recording or 关闭) ends any recording without changing a key."""
        self.stop_recording();super().done(result)
    def reset_all(self):
        if not confirm(self,'全部快捷键恢复默认？',f'已自定义的 {self.keymap.customized()} 项会改回默认组合键。','恢复默认'):return
        error=self.keymap.reset();self.fill();self.message.setText(error or '已全部恢复默认')
