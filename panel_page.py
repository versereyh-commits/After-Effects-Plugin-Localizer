"""面板与脚本: installed CEP extensions and plain ScriptUI scripts on the left, the chosen one's interface text on the right.
Reading and editing the Chinese only touch this tool's own data; installed files change only on Apply / Restore."""
from pathlib import Path
import os,subprocess,threading
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QAbstractItemView,
                               QListWidget,QListWidgetItem,QStackedWidget,QCheckBox,QToolButton,QMenu,QComboBox)
import ui_kit
from ui_kit import (button,label,page_head,table_style,confirm,menu_button,elastic,soft_path,Surface,SearchField,EmptyState,LoaderButton,
                    SchemeDelegate,CheckHeader,CheckDragger,check_state,SHADOW)
import panel_localizer as P
from llm_client import translate_ui_strings,TranslationError
from runtime import data_root,write_json_atomic

CHECK,SOURCE,CHINESE,KIND,PLACE=range(5)
KEY=Qt.ItemDataRole.UserRole+1   # list items carry their target's key

def adobe_own(t)->bool:return t.kind=='cep' and t.identifier.lower().startswith('com.adobe.')

def path_key(path):return os.path.normcase(str(Path(path).resolve()))

def script_owner(target,spaces):
    """Installation identity by path, never by a non-unique AE display label."""
    if Path(target.entry).suffix.lower() not in ('.jsx','.jsxbin'):return None
    path=path_key(Path(target.root)/target.entry);matches=[]
    for space in spaces:
        if not space.exe:continue
        folder=path_key(Path(space.exe).parent/'Scripts')
        if path.startswith(folder+os.sep):matches.append((len(folder),path_key(space.exe)))
    return max(matches)[1] if matches else None

def inset(widget)->QWidget:
    holder=QWidget();row=QHBoxLayout(holder);row.setContentsMargins(SHADOW,0,SHADOW,0);row.setSpacing(0);row.addWidget(widget);return holder

class PanelPage(QWidget):
    def __init__(self,owner,root=None):
        super().__init__();self.owner=owner;self.root=Path(root) if root else data_root();self.targets=[];self.status={};self.report=None;self.words={}
        self.cancelled=threading.Event();self.filling=False;self.reread=False;self.extra=[];self.locations_error='';self.read_error=''
        try:
            saved=P.document(self.root/'panels'/'locations.json',[])
            if not isinstance(saved,list) or not all(isinstance(x,str) for x in saved):raise ValueError('面板定位记录格式无效，已保留')
        except (ValueError,OSError) as error:saved=[];self.locations_error=str(error)
        self.extra=saved
        col=QVBoxLayout(self);col.setContentsMargins(0,0,0,0);col.setSpacing(12)
        head,row,self.summary=page_head('面板与脚本','汉化 CEP 扩展和明文 JSX 脚本的界面文字；改文件前先备份，可随时恢复原文')
        self.scan=button('重新查找',icon_name='refresh',tip='查找已安装的 CEP 扩展和 AE Scripts 文件夹里的脚本，只读取、不修改');self.scan.clicked.connect(self.discover);row.addWidget(self.scan)
        self.add_cep_action=QAction('CEP 扩展文件夹…',self);self.add_cep_action.triggered.connect(self.locate_cep)
        self.add_jsx_action=QAction('JSX 脚本文件…',self);self.add_jsx_action.triggered.connect(self.locate_jsx)
        self.add_tool=menu_button('手动添加',[self.add_cep_action,self.add_jsx_action]);self.add_tool.setToolTip('添加不在默认位置的扩展（含 CSXS/manifest.xml 的文件夹）或脚本');row.addWidget(self.add_tool)
        self.history_btn=button('汉化记录');self.history_btn.clicked.connect(self.show_history);row.addWidget(self.history_btn)
        col.addWidget(inset(head))
        split=QHBoxLayout();split.setSpacing(0)
        split.addWidget(self.build_list());split.addWidget(self.build_detail(),1);col.addLayout(split,1)
        self.controls=(self.scan,self.add_tool,self.find_btn,self.target_list,self.extract_btn,self.translate_btn,self.table,self.apply_btn,self.restore_btn,self.more_btn,self.history_btn,self.type_box,self.target_search,self.adobe_box,self.term_search,self.untranslated)
        self.fill_targets();self.show_detail();self.update_controls()
    def build_list(self)->Surface:
        side=Surface(padding=(10,10,10,10),spacing=8);side.setFixedWidth(320+2*SHADOW)
        search=SearchField('搜索名称或标识');self.target_search=search.edit;self.target_search.textChanged.connect(self.filter_targets);side.box.addWidget(search)
        self.type_box=QComboBox()
        for text,key in (('全部类型（当前 AE 范围）','all'),('当前 AE 脚本','current'),('共享 CEP 扩展','cep'),('手动添加','manual'),('已汉化','applied')):self.type_box.addItem(text,key)
        self.type_box.currentIndexChanged.connect(self.filter_targets);side.box.addWidget(self.type_box)
        self.target_list=QListWidget();self.target_list.setObjectName('schemes');self.target_list.setItemDelegate(SchemeDelegate(self.target_list));self.target_list.setMouseTracking(True)
        self.target_list.currentItemChanged.connect(lambda *_:self.selected())
        self.list_empty=EmptyState('inbox');self.list_empty.text.setFixedWidth(240)
        self.find_btn=self.list_empty.add_button(button('查找面板与脚本','primary'));self.find_btn.clicked.connect(self.discover)
        self.list_stack=QStackedWidget();self.list_stack.addWidget(self.list_empty);self.list_stack.addWidget(self.target_list);side.box.addWidget(self.list_stack,1)
        self.adobe_box=QCheckBox('显示 Adobe 自带扩展');self.adobe_box.setObjectName('check');self.adobe_box.setToolTip('Adobe 自己的 CEP 扩展（标识以 com.adobe. 开头）默认不列出')
        self.adobe_box.toggled.connect(self.filter_targets);side.box.addWidget(self.adobe_box)
        self.list_hint=label('','hint',True);side.box.addWidget(self.list_hint)
        return side
    def build_detail(self)->Surface:
        frame=Surface(padding=(22,18,22,16),spacing=10)
        self.head_box=QWidget();head=QVBoxLayout(self.head_box);head.setContentsMargins(0,0,0,0);head.setSpacing(6)
        top=QHBoxLayout();top.setSpacing(8);self.title=elastic(label('','detailName'));top.addWidget(self.title,1)
        self.state_tag=label('已汉化','tag');self.sign_tag=label('带签名','tag');self.sign_tag.setProperty('kind','outline')
        self.sign_tag.setToolTip('扩展带 Adobe 签名；修改会使签名失效，应用时需要开启 Adobe 扩展调试模式')
        for tag in (self.state_tag,self.sign_tag):tag.setFixedHeight(20);top.addWidget(tag,0,Qt.AlignmentFlag.AlignVCenter)
        self.restore_btn=LoaderButton('恢复原文');ui_kit.themed_icon(self.restore_btn,'restore','INK_2',16);self.restore_btn.setToolTip('用备份恢复本工具改过的文件；文件被其他程序改过时停止，不会覆盖')
        self.restore_btn.clicked.connect(lambda:self.owner.run_with(self.restore_btn,'rewind','恢复中',self.restore));top.addWidget(self.restore_btn)
        self.apply_btn=LoaderButton('应用汉化','primary');self.apply_btn.setToolTip('把勾选且有中文的文字写入文件；写入前列出要改的文件并备份')
        self.apply_btn.clicked.connect(lambda:self.owner.run_with(self.apply_btn,'fill','写入中',self.apply));top.addWidget(self.apply_btn)
        self.reread_action=QAction('重新读取界面文字',self);self.reread_action.triggered.connect(self.extract)
        self.force_read_action=QAction('忽略缓存，重新读取',self);self.force_read_action.triggered.connect(lambda:self.extract(force=True))
        self.reveal_action=QAction('打开所在文件夹',self);self.reveal_action.triggered.connect(self.reveal)
        self.forget_action=QAction('从列表移除',self);self.forget_action.triggered.connect(self.forget)
        self.more_btn=QToolButton();self.more_btn.setObjectName('icon');ui_kit.themed_icon(self.more_btn,'more','INK_2',16);self.more_btn.setToolTip('更多操作')
        menu=QMenu(self.more_btn)
        for entry in (self.reread_action,self.force_read_action,self.reveal_action,None,self.forget_action):menu.addSeparator() if entry is None else menu.addAction(entry)
        self.more_btn.setMenu(menu);self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.setStyleSheet('QToolButton::menu-indicator { image:none; width:0; }');top.addWidget(self.more_btn)
        head.addLayout(top);self.meta=label('','muted',True);self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);head.addWidget(self.meta)
        frame.box.addWidget(self.head_box)
        self.terms=QWidget();terms=QVBoxLayout(self.terms);terms.setContentsMargins(0,4,0,0);terms.setSpacing(10)
        tools=QHBoxLayout();tools.setSpacing(12)
        field=SearchField('搜索原文或中文');self.term_search=field.edit;self.term_search.textChanged.connect(self.filter_terms);tools.addWidget(field,1)
        self.counts=label('','muted');tools.addWidget(self.counts)
        self.untranslated=QCheckBox('只看没有中文的');self.untranslated.setObjectName('check');self.untranslated.toggled.connect(self.filter_terms);tools.addWidget(self.untranslated)
        self.translate_btn=LoaderButton('翻译勾选项');ui_kit.themed_icon(self.translate_btn,'sparkle','INK_2',16)
        self.translate_btn.setToolTip('用设置里的翻译服务翻译还没有中文的勾选项；已有的中文（包括手动填写的）不变')
        self.translate_btn.clicked.connect(lambda:self.owner.run_with(self.translate_btn,'breathe','翻译中',self.translate));tools.addWidget(self.translate_btn)
        terms.addLayout(tools)
        self.table=QTableWidget(0,5);self.check_header=CheckHeader(self.table,self.toggle_visible);self.table.setHorizontalHeader(self.check_header)
        self.table.setHorizontalHeaderLabels(['','原文','中文（双击修改，自动保存）','类型','位置']);table_style(self.table)
        self.table.horizontalHeaderItem(CHECK).setToolTip('勾选的文字参与翻译和应用。\n点这里勾选或取消当前列出的文字；按住复选框上下拖动可快速勾选')
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked|QAbstractItemView.EditTrigger.EditKeyPressed|QAbstractItemView.EditTrigger.SelectedClicked)
        header=self.table.horizontalHeader();header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column in (SOURCE,CHINESE):header.setSectionResizeMode(column,QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(CHECK,QHeaderView.ResizeMode.Fixed)
        for column,width in ((CHECK,44),(KIND,84),(PLACE,170)):self.table.setColumnWidth(column,width)
        self.table.itemChanged.connect(self.edited)
        self.dragger=CheckDragger(self.table,CHECK,self.is_checked,self.paint_checks,lambda rows,state:self.update_counts())
        terms.addWidget(self.table,1)
        self.empty=EmptyState('inbox');self.extract_btn=self.empty.add_button(button('读取界面文字','primary'))
        self.extract_btn.clicked.connect(lambda:self.owner.run_with(self.extract_btn,'breathe','读取中',self.extract))
        self.stack=QStackedWidget();self.stack.addWidget(self.empty);self.stack.addWidget(self.terms);frame.box.addWidget(self.stack,1)
        foot=QHBoxLayout();foot.setSpacing(8);self.note=elastic(label('','hint'));foot.addWidget(self.note,1)
        self.diagnostics_btn=button('读取诊断');self.diagnostics_btn.clicked.connect(self.show_diagnostics);foot.addWidget(self.diagnostics_btn)
        self.cancel_btn=button('取消');self.cancel_btn.clicked.connect(self.cancelled.set);self.cancel_btn.hide();foot.addWidget(self.cancel_btn)
        frame.box.addLayout(foot);return frame
    # Targets.
    def target(self):
        item=self.target_list.currentItem();key=item.data(KEY) if item else None
        return next((t for t in self.targets if t.key==key),None)
    def localizer(self):return P.Localizer(self.target(),self.root/'panels')
    def stopped(self):return self.cancelled.is_set() or self.owner._stopping.is_set()
    def visible_count(self):return sum(not self.target_list.item(i).isHidden() for i in range(self.target_list.count()))
    def show_history(self):
        from panel_details import History
        History(self,self.root/'panels').exec()
    def show_diagnostics(self):
        from panel_details import Diagnostics
        t=self.target();Diagnostics(self,self.report,self.read_error or (t.reason if t else '')).exec()
    def record(self,t):
        """The target's file transaction as recorded by this tool (display only; Restore re-checks everything)."""
        try:r=P.document(self.root/'panels'/t.key/'recovery'/'transaction.json')
        except (ValueError,OSError):return 'broken'
        return r.get('status') if isinstance(r,dict) else None
    def restorable(self,t)->bool:return bool(t) and self.status.get(t.key) in ('applied','prepared','broken')
    def manual(self,t)->bool:
        own={os.path.normcase(str(Path(t.root))),os.path.normcase(str(Path(t.root)/t.entry))}
        return any(os.path.normcase(str(Path(p).absolute())) in own for p in self.extra)
    def item_data(self,t)->dict:
        state=self.status.get(t.key)
        if t.kind=='cep':lines=['共享 CEP 扩展'+(' · '+t.version if t.version else ''),t.identifier]
        elif t.kind=='jsx':lines=[('当前 AE 脚本' if script_owner(t,self.owner.spaces) else '共享 / 手动脚本')+' · '+Path(t.root).name,t.entry]
        else:lines=['无法汉化',Path(t.root,t.entry).name if t.entry else Path(t.root).name]
        tags=[('不支持',False)] if t.reason else [('已汉化',True)] if state=='applied' else [('未完成',False)] if state in ('prepared','broken') else []
        return dict(title=t.name,lines=lines,tags=tags,tip=str(Path(t.root,t.entry) if t.kind=='jsx' else Path(t.root)))
    def order(self,t):return (not self.restorable(t),adobe_own(t),bool(t.reason),t.kind!='cep',t.name.casefold(),t.root)
    def fill_targets(self,select=None):
        current=select or (self.target().key if self.target() else None)
        self.target_list.blockSignals(True);self.target_list.clear()
        for t in sorted(self.targets,key=self.order):
            data=self.item_data(t);item=QListWidgetItem(t.name);item.setData(Qt.ItemDataRole.UserRole,data);item.setData(KEY,t.key);item.setToolTip(data['tip'])
            self.target_list.addItem(item)
            if t.key==current:self.target_list.setCurrentItem(item)
        self.target_list.blockSignals(False);self.filter_targets(keep=True)
        if not self.targets:self.list_empty.show_message('还没有查找','列出已安装的 CEP 扩展和 AE Scripts 文件夹里的脚本。只读取文件，不做修改。')
        self.list_stack.setCurrentWidget(self.target_list if self.targets else self.list_empty)
    def filter_targets(self,*_,keep=False):
        query=self.target_search.text().strip().casefold();adobe=self.adobe_box.isChecked();shown=hidden=0
        current=path_key(self.owner.space.exe) if self.owner.space.exe else None
        mode=self.type_box.currentData()
        for i in range(self.target_list.count()):
            item=self.target_list.item(i);t=next(x for x in self.targets if x.key==item.data(KEY))
            off=(not adobe and adobe_own(t) and not self.restorable(t))
            belongs=script_owner(t,self.owner.spaces)
            match=mode=='all' or (mode=='current' and belongs==current and belongs is not None) or (mode=='cep' and t.kind=='cep') or (mode=='manual' and self.manual(t)) or (mode=='applied' and self.status.get(t.key)=='applied')
            hide=not match or (belongs is not None and belongs!=current) or off or (bool(query) and query not in (t.name+' '+t.identifier+' '+t.entry).casefold())
            item.setHidden(hide);shown+=not hide;hidden+=off
        self.list_hint.setText(self.owner.space.label+f' · 当前显示 {shown} / 共 {len(self.targets)} 个'+(f'，隐藏了 {hidden} 个 Adobe 自带扩展' if hidden else '')+'\n脚本按安装目录区分；共享扩展及外部手动脚本单独标注。' if self.targets else '')
        if getattr(self.owner,'panel_page',None) is self:self.owner.update_nav()
        item=self.target_list.currentItem()
        if item is None or item.isHidden():
            first=next((self.target_list.item(i) for i in range(self.target_list.count()) if not self.target_list.item(i).isHidden()),None)
            self.target_list.setCurrentItem(first)
            if first is None:self.selected()
        elif keep:self.selected()
    def version_changed(self):
        self.reread=False
        self.fill_targets()
        self.update_controls()
    def refresh_status(self,t):
        self.status[t.key]=self.record(t)
        for i in range(self.target_list.count()):
            item=self.target_list.item(i)
            if item.data(KEY)==t.key:item.setData(Qt.ItemDataRole.UserRole,self.item_data(t))
        self.filter_targets()
    # Detail.
    def show_detail(self):
        t=self.target();self.head_box.setVisible(bool(t));self.extract_btn.setVisible(bool(t and not t.reason));self.empty.set_icon('inbox')
        if t:
            self.title.setText(t.name);self.state_tag.setVisible(self.status.get(t.key)=='applied');self.sign_tag.setVisible(t.signed)
            kind={'cep':'CEP 扩展','jsx':'JSX 脚本'}.get(t.kind,'不支持的格式')
            parts=[kind]+([t.identifier] if t.kind=='cep' else [])+(['版本 '+t.version] if t.version else [])
            self.meta.setText(' · '.join(parts)+'\n'+soft_path(Path(t.root,t.entry) if t.kind=='jsx' else t.root))
        if self.report and self.report['terms']:self.stack.setCurrentWidget(self.terms);return
        if not t:self.empty.show_message('选择一个面板或脚本','在左侧列表里选择要汉化的扩展或脚本。' if self.targets else '先点左侧的“查找面板与脚本”，或用右上角“手动添加”。')
        elif t.reason:self.empty.set_icon('warning');self.empty.show_message('无法汉化这个目标',t.reason)
        elif self.report:self.empty.show_message('没有找到可以汉化的文字','；'.join(self.report['warnings']) or '这个目标里没有可以静态确认的界面文字。')
        else:self.empty.show_message('读取界面文字','列出这个'+('扩展' if t.kind=='cep' else '脚本')+'里可以汉化的按钮、标题和提示文字，只读取，不修改。'+('\n\n已有 '+str(len(self.words))+' 条中文保存在本机，读取后自动填入。' if self.words else ''))
        self.stack.setCurrentWidget(self.empty)
    def update_controls(self):
        idle=not self.owner.busy();t=self.target();ok=bool(t and not t.reason);terms=bool(self.report and self.report['terms'])
        for widget in (self.scan,self.add_tool,self.find_btn,self.target_list,self.type_box,self.target_search,self.adobe_box,self.term_search,self.untranslated,self.history_btn):widget.setEnabled(idle)
        self.extract_btn.setEnabled(idle and ok);self.reread_action.setEnabled(idle and ok and self.report is not None)
        self.force_read_action.setEnabled(idle and ok)
        checked=self.checked();valid=[]
        for source in checked:
            try:
                if self.words.get(source):P.validate_text(source,self.words[source]);valid.append(source)
            except ValueError:pass
        self.translate_btn.setEnabled(idle and terms and bool(checked));self.table.setEnabled(idle)
        self.translate_btn.setToolTip('请先勾选词条' if not checked else f'翻译 {len(checked)} 条勾选项；已有中文（含手动译文）会被成功的新译文替换，失败保留原文')
        bad=[s for s in checked if self.words.get(s) and s not in valid]
        self.apply_btn.setEnabled(idle and ok and terms and bool(valid) and not bad);self.restore_btn.setEnabled(idle and ok and self.restorable(t))
        self.apply_btn.setToolTip('请先勾选词条' if not checked else '存在占位符或格式无效的中文，请先修改' if bad else '勾选项没有有效中文，请先翻译或修改' if not valid else f'备份并应用 {len(valid)} 条有效中文；写入前仍会核验文件')
        self.diagnostics_btn.setEnabled(idle and bool(self.report or self.read_error or (t and t.reason)))
        self.more_btn.setEnabled(bool(t));self.reveal_action.setEnabled(bool(t));self.forget_action.setEnabled(idle and bool(t) and self.manual(t))
    def finished(self):
        self.cancel_btn.hide();self.update_controls()
        if self.reread and not self.owner._stopping.is_set():self.reread=False;QTimer.singleShot(0,self.extract)
    def run(self,work,done,on_failure=None):
        if self.owner.busy() or self.owner._stopping.is_set():return
        self.cancelled.clear();self.cancel_btn.show()
        def result(value):
            if self.stopped():self.note.setText('已取消后续步骤；已经开始的文件写入已安全收尾。');return
            done(value)
        def failure(message):
            if self.stopped():self.note.setText('已取消。')
            else:
                if on_failure:on_failure(str(message))
                self.owner.error(str(message))
        self.owner.work(work,result,failure)
    def discover(self):
        executables=[s.exe for s in self.owner.spaces if s.exe];extra=list(self.extra)
        self.note.setText('正在查找已安装的面板与脚本……')
        self.run(lambda progress:P.discover(executables,extra,self.stopped),self.found)
    def found(self,result,select=None):
        targets,warnings=result;self.targets=targets;self.status={t.key:self.record(t) for t in targets}
        self.fill_targets(select)
        self.note.setText(f'找到 {len(targets)} 个面板和脚本。'+('有 '+str(len(warnings))+' 个无法读取：'+'；'.join(warnings[:3]) if warnings else ''))
    def locate_cep(self):
        p=QFileDialog.getExistingDirectory(self,'选择扩展文件夹（含 CSXS/manifest.xml）')
        if p:self.locate(p)
    def locate_jsx(self):
        p,_=QFileDialog.getOpenFileName(self,'选择 JSX 脚本','','AE 脚本 (*.jsx *.jsxbin)')
        if p:self.locate(p)
    def locate(self,path):
        try:
            t=P.inspect(path)
            if self.locations_error:raise ValueError(self.locations_error+'；添加记录未覆盖，请先备份并修复该记录')
            if path not in self.extra:self.extra.append(path);write_json_atomic(self.root/'panels'/'locations.json',self.extra)
            self.found(([t]+[x for x in self.targets if x.key!=t.key],[]),select=t.key)
        except Exception as error:self.owner.error(str(error))
    def forget(self):
        t=self.target()
        if not t or not self.manual(t):return
        own={os.path.normcase(str(Path(t.root))),os.path.normcase(str(Path(t.root)/t.entry))}
        try:
            self.extra=[p for p in self.extra if os.path.normcase(str(Path(p).absolute())) not in own];write_json_atomic(self.root/'panels'/'locations.json',self.extra)
            self.targets=[x for x in self.targets if x.key!=t.key];self.fill_targets();self.note.setText(f'已从列表移除“{t.name}”；本机保存的中文和备份保留。')
        except (ValueError,OSError) as error:self.owner.error(str(error))
    def reveal(self):
        t=self.target()
        if not t:return
        path=Path(t.root,t.entry) if t.kind=='jsx' else Path(t.root)
        if not path.exists():self.owner.error('文件夹不存在：'+str(path));return
        subprocess.Popen(['explorer','/select,',os.path.normpath(path)] if path.is_file() else ['explorer',os.path.normpath(path)])
    def selected(self):
        self.report=None;self.read_error='';self.words={};self.filling=True;self.table.setRowCount(0);self.filling=False;self.term_search.clear();self.note.clear();t=self.target()
        if t and not t.reason:
            try:
                localizer=self.localizer();self.words=localizer.dictionary()
                cached=localizer.cached_report()
                if cached is not None:self.extracted(cached)
            except (ValueError,OSError) as error:self.owner.error(str(error))
        self.show_detail();self.update_controls()
    # Interface text.
    def extract(self,force=False):
        t=self.target()
        if not t or t.reason:return
        l=self.localizer();self.note.setText('正在读取界面文字……')
        def failed(message):self.report=None;self.read_error=message;self.fill();self.show_detail();self.update_controls();self.note.setText('读取未完成，可打开“读取诊断”查看原因。')
        self.run(lambda progress:l.extract(self.stopped,force=force),self.extracted,failed)
    def extracted(self,report):
        self.report=report;self.read_error='';self.fill();self.show_detail()
        mode='复用缓存' if report.get('readMode')=='cache' else '重新解析'
        self.note.setText(f"{mode} · 找到 {len(report['terms'])} 条。"+('存在未覆盖内容，详见读取诊断。' if report['warnings'] else '静态词条不代表整个面板的覆盖率。'))
    def fill(self):
        checked={self.table.item(i,SOURCE).text():self.table.item(i,CHECK).checkState() for i in range(self.table.rowCount())}
        self.filling=True;self.table.setRowCount(0)
        for row in self.report['terms'] if self.report else []:
            n=self.table.rowCount();self.table.insertRow(n);sources=row['sources']
            place=sources[0]+(f' 等 {len(sources)} 处' if len(sources)>1 else '')
            for c,text in enumerate(('',row['text'],self.words.get(row['text'],''),'悬停提示' if row['kind']=='tip' else '界面文字',place)):
                item=QTableWidgetItem(text)
                if c==CHECK:item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(checked.get(row['text'],Qt.CheckState.Checked))
                elif c!=CHINESE:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
                if c==PLACE:item.setToolTip('\n'.join(sources))
                elif c!=CHECK:item.setToolTip(text)
                self.table.setItem(n,c,item)
        self.filling=False;self.filter_terms();self.update_controls()
    def filter_terms(self,*_):
        query=self.term_search.text().strip().casefold();bare=self.untranslated.isChecked()
        for i in range(self.table.rowCount()):
            source,chinese=self.table.item(i,SOURCE).text(),self.table.item(i,CHINESE).text()
            self.table.setRowHidden(i,(bool(query) and query not in (source+'\n'+chinese).casefold()) or (bare and bool(chinese)))
        self.update_counts()
    def visible(self)->list[int]:return [i for i in range(self.table.rowCount()) if not self.table.isRowHidden(i)]
    def is_checked(self,row)->bool:return self.table.item(row,CHECK).checkState()==Qt.CheckState.Checked
    def paint_checks(self,rows,state):
        self.table.blockSignals(True)
        try:
            for row in rows:self.table.item(row,CHECK).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        finally:self.table.blockSignals(False)
        self.update_counts()
    def toggle_visible(self):
        rows=self.visible();self.paint_checks(rows,not all(self.is_checked(r) for r in rows))
    def update_counts(self):
        rows=range(self.table.rowCount());self.check_header.set_state(check_state(self.is_checked(r) for r in self.visible()))
        done=sum(bool(self.table.item(r,CHINESE).text()) for r in rows)
        self.counts.setText(f'{self.table.rowCount()} 条 · 已有中文 {done} · 勾选 {len(self.checked())}' if self.table.rowCount() else '')
        self.update_controls()
    def checked(self):return [self.table.item(i,SOURCE).text() for i in range(self.table.rowCount()) if self.is_checked(i)]
    def edited(self,item):
        if self.filling:return
        if item.column()==CHECK:self.update_counts();return
        if item.column()!=CHINESE:return
        source=self.table.item(item.row(),SOURCE).text();text=item.text();values=dict(self.words)
        if text:values[source]=text
        else:values.pop(source,None)
        try:self.localizer().save(values);self.words=values;self.update_counts()
        except (ValueError,OSError) as error:
            self.filling=True;item.setText(self.words.get(source,''));self.filling=False;self.owner.error(str(error))
    def translate(self):
        if not self.owner.settings.ready():self.owner.configure_api();return
        todo=self.checked()
        if not todo:self.note.setText('请先勾选需要翻译或重新翻译的文字。');return
        settings=self.owner.settings;config=(settings.key,settings.base_url,settings.model,settings.service);name=self.target().name
        def work(progress):
            # translate_names reports (done, total); the job's progress channel takes one line of text.
            report=lambda done,total:progress(f'正在翻译界面文字……已完成 {done}/{total} 条')
            try:return translate_ui_strings([dict(id=str(i),sourceName=s,category=name) for i,s in enumerate(todo)],*config[:3],service=config[3],progress=report,cancelled=self.stopped,usage=self.owner.record_usage),''
            except TranslationError as error:return error.partial,str(error)
        def done(value):
            result,error=value;values=dict(self.words)
            try:
                for key,text in result.items():values[todo[int(key)]]=P.validate_text(todo[int(key)],text)
                self.localizer().save(values);self.words=values;self.fill();self.note.setText(error or f'已翻译 {len(result)} 条；核对后点“应用汉化”。')
            except (ValueError,OSError) as e:self.owner.error(str(e))
        self.run(work,done)
    def apply(self):
        if not self.report:return
        l=self.localizer();translations={s:self.words[s] for s in self.checked() if self.words.get(s)};signed=l.target.signed
        try:changes=l.preview(self.report,translations,allow_signed=signed)
        except (ValueError,OSError) as error:self.owner.error(str(error));return
        text=f'将用 {len(translations)} 条中文修改下面 {len(changes)} 个文件，修改前先备份。重新打开面板后生效。\n\n'+'\n'.join(changes)
        if signed:text+='\n\n此扩展带签名，修改会使签名失效。继续将开启 Adobe 扩展调试模式。\n'+self.owner.bridge_install_text()
        if not confirm(self,'应用汉化？',text,'备份并应用'):return
        report=self.report;t=self.target()
        def work(progress):
            if signed:
                import cep_bridge
                if self.stopped():raise RuntimeError('已取消')
                cep_bridge.enable_debug_mode()
            return l.apply(report,translations,signed,self.stopped)
        def done(count):
            self.report=None;self.refresh_status(t);self.reread=bool(self.target() and self.target().key==t.key);self.show_detail();self.update_controls()
            self.note.setText(f'已修改 {count} 个文件。关闭并重新打开这个面板后显示中文。')
        self.run(work,done)
    def restore(self):
        t=self.target()
        if not t:return
        if not confirm(self,'恢复原文？','用备份恢复本工具改过的文件。文件被其他程序改过（例如扩展已升级）时会停止，不覆盖。本机保存的中文保留。','恢复'):return
        l=self.localizer()
        def work(progress):
            if self.stopped():raise RuntimeError('已取消')
            return l.restore()
        def done(n):
            self.report=None;self.refresh_status(t);self.reread=bool(self.target() and self.target().key==t.key);self.show_detail();self.update_controls()
            self.note.setText(f'已恢复 {n} 个文件。关闭并重新打开这个面板后显示原文。')
        self.run(work,done)
