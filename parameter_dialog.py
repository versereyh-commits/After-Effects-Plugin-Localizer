"""Parameter-label page: per-effect, per-parameter candidates applied to AE's native effect controls."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QColor,QFont,QKeySequence,QShortcut,QActionGroup
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QTableWidget,QTableWidgetItem,QComboBox,QHeaderView,QToolButton,QMenu,QStackedWidget,QButtonGroup,QPushButton,QAbstractItemView
from parameter_names import read_parameters,validate_label,record_read_timing
import time
from llm_client import translate_names,TranslationError,PARAMETER_PROMPT
import ui_kit
from ui_kit import CheckDragger,CheckHeader,check_state,SHADOW,Breather,DiamondDelegate,EmptyState,LoaderButton,MetricTile,SearchField,Surface,TrashDelegate,button,icon,label,confirm
H_CHECK,H_EFFECT,H_NAME,H_CHINESE,H_TRASH=range(5)  # columns of the record of translated parameters

STATUS=5
FILTERS=(('all','全部参数',None),('checked','已勾选',None),('todo','待翻译','hollow'),('applied','已应用','solid'))

def menu_tool(text,entries,parent)->QToolButton:
    widget=QToolButton(parent);widget.setText(text);menu=QMenu(widget)
    for title,callback in entries:menu.addAction(title,callback)
    widget.setMenu(menu);widget.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);widget.setCursor(Qt.CursorShape.PointingHandCursor);return widget

class ParameterPanel(QWidget):
    """Host work runs on the owner window's single worker, so only one job touches AE at a time."""
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.rows=[];self.filling=False;self.wanted=None;self.writing=set();self.read_scope='selected';self.empty_reason=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        tiles=QWidget();tile_row=QHBoxLayout(tiles);tile_row.setContentsMargins(SHADOW,0,SHADOW,0);tile_row.setSpacing(12)
        self.chip_group=QButtonGroup(self);self.chips={}
        for index,(key,text,mark) in enumerate(FILTERS):
            tile=MetricTile(text,mark);self.chip_group.addButton(tile,index);self.chips[key]=tile;tile_row.addWidget(tile)
        self.chips['all'].setChecked(True);self.chip_group.idClicked.connect(lambda _:self.filter_rows());layout.addWidget(tiles)
        card=Surface(padding=(14,12,14,8),spacing=8)
        bar=QHBoxLayout();bar.setSpacing(8)
        self.view_group=QButtonGroup(self);self.view_buttons=[]
        for index,(text,tip) in enumerate((('当前工程','从 AE 读取的、当前工程中已加载效果的参数'),('汉化记录','本机为此 AE 版本翻译过的全部参数，不需要连接 AE；可删除记录或加入任意方案'))):
            view=QPushButton(text);view.setObjectName('segment');view.setCheckable(True);view.setToolTip(tip);view.setCursor(Qt.CursorShape.PointingHandCursor);self.view_group.addButton(view,index);self.view_buttons.append(view);bar.addWidget(view)
        self.view_buttons[0].setChecked(True);self.view_group.idClicked.connect(lambda _:self.show_view());bar.addSpacing(6)
        self.refresh=LoaderButton('读取 AE 参数');ui_kit.themed_icon(self.refresh,'refresh','MUTED',16);self.refresh.setToolTip('按右侧范围读取效果参数；首次结构扫描可能较慢，重复读取优先复用缓存')
        self.refresh.clicked.connect(lambda:self.owner.run_with(self.refresh,'scan','读取中',self.load));bar.addWidget(self.refresh)
        self.scan_menu=menu_tool('选中图层',[],self);self.scan_menu.setToolTip('参数读取范围；选择后点击“读取 AE 参数”执行');self.scope_group=QActionGroup(self);self.scope_group.setExclusive(True);self.scope_actions={}
        for value,title in (('selected','选中图层'),('project','整个工程')):
            action=self.scan_menu.menu().addAction(title);action.setCheckable(True);action.setChecked(value==self.read_scope);self.scope_group.addAction(action);self.scope_actions[value]=action
            action.triggered.connect(lambda checked=False,v=value:self.set_read_scope(v))
        bar.addWidget(self.scan_menu)
        self.effects=QComboBox();self.effects.setMinimumWidth(220);self.effects.addItem('所有已加载效果','');self.effects.currentIndexChanged.connect(self.filter_rows);bar.addWidget(self.effects)
        field=SearchField('搜索参数名称或内部标识');self.search=field.edit;self.search.textChanged.connect(self.filter_rows);self.search.textChanged.connect(self.filter_history);bar.addWidget(field,1)
        self.history_add=QToolButton();self.history_add.setText('把勾选记录加入方案');self.history_add.setMenu(QMenu(self.history_add));self.history_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);self.history_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.history_add.menu().aboutToShow.connect(lambda:self.owner.fill_add_menu(self.history_add.menu(),self.history_checked(),'parameters'));bar.addWidget(self.history_add)
        self.history_delete=button('删除勾选记录');self.history_delete.clicked.connect(self.delete_checked_records);bar.addWidget(self.history_delete)
        card.box.addLayout(bar)
        # The page header shows these; the panel owns their behaviour.
        self.check_all_btn=button('勾选全部参数',tip='勾选所有已读取效果的全部参数；不受效果筛选和搜索影响');self.check_all_btn.clicked.connect(lambda:self.check_all(True));self.check_all_btn.setEnabled(False)
        self.check_tool=menu_tool('勾选',[('勾选当前结果',lambda:self.check(True)),('取消当前结果',lambda:self.check(False)),('取消全部参数',lambda:self.check_all(False))],self)
        self.translate_btn=LoaderButton('翻译勾选参数');self.translate_btn.setToolTip('用所选翻译模型翻译勾选的参数；每个标签最多 31 个 UTF-8 字节')
        self.translate_btn.clicked.connect(lambda:self.owner.run_with(self.translate_btn,'breathe','翻译中',self.translate))
        self.apply_btn=LoaderButton('应用勾选参数','primary');self.apply_btn.clicked.connect(lambda:self.owner.run_with(self.apply_btn,'fill','写入中',self.apply))
        self.restore_tool=menu_tool('恢复',[('恢复勾选参数',self.restore),('恢复全部参数',self.restore_all)],self);ui_kit.themed_icon(self.restore_tool,'restore','MUTED',16)
        self.restore_tool.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.tools=[self.check_all_btn,self.check_tool,self.restore_tool,self.translate_btn,self.apply_btn]
        self.stack=QStackedWidget()
        self.empty=EmptyState('params')
        self.empty_read=self.empty.add_button(LoaderButton('读取 AE 参数','primary'));self.empty_read.clicked.connect(lambda:self.owner.run_with(self.empty_read,'scan','读取中',self.load))
        self.empty_versions=self.empty.add_button(button('查看 AE 版本'));self.empty_versions.clicked.connect(lambda:self.owner.go(3))
        self.empty_clear=self.empty.add_button(button('清除筛选'));self.empty_clear.clicked.connect(self.clear_filters)
        self.empty_history=self.empty.add_button(button('查看汉化记录'));self.empty_history.clicked.connect(lambda:(self.view_buttons[1].setChecked(True),self.show_view()))
        self.stack.addWidget(self.empty)
        self.table=QTableWidget(0,6);self.check_header=CheckHeader(self.table,self.toggle_visible);self.table.setHorizontalHeader(self.check_header);self.anchor=None
        self.table.setHorizontalHeaderLabels(['','AE 当前参数名','中文候选（可编辑）','效果标识','参数标识','状态'])
        self.table.horizontalHeaderItem(0).setToolTip('勾选当前结果中的全部参数或取消；按住行内复选框上下拖动可快速勾选，Shift+点击可连续勾选')
        from ui_kit import table_style
        table_style(self.table);self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection);header=self.table.horizontalHeader();header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive);header.setStretchLastSection(True);header.moveSection(STATUS,1)
        for c,width in ((0,52),(STATUS,104),(1,190),(2,200),(3,190),(4,260)):self.table.setColumnWidth(c,width)
        self.delegate=DiamondDelegate(self.table,lambda index:bool(self.writing) and 0<=index.row()<len(self.rows) and self.rows[index.row()]['id'] in self.writing)
        self.table.setItemDelegateForColumn(STATUS,self.delegate);self.breather=Breather(self.table,self.delegate)
        self.dragger=CheckDragger(self.table,0,lambda row:self.rows[row]['id'] in self.owner.parameter_enabled,self.paint_checks,self.finish_checks)
        self.table.itemChanged.connect(self.changed);self.stack.addWidget(self.table);self.build_history();card.box.addWidget(self.stack,1)
        self.status=label('在 AE 图层中添加效果后点击“读取 AE 参数”。当前适配标准参数标签和折叠标题，暂不翻译下拉菜单内部条目或插件自绘界面。','muted',True);card.box.addWidget(self.status)
        self.write_scope_note=label('读取范围仅限制列表；应用和恢复仍影响整个工程中同类效果的已加载实例。','muted',True);card.box.addWidget(self.write_scope_note)
        # The layer-parameters key (app_live.Window.layer_parameters); the window sets the text whenever keys change.
        self.key_note=label('','muted',True);card.box.addWidget(self.key_note)
        layout.addWidget(card,1)
        self.controls=[self.refresh,self.scan_menu,self.effects,self.search,self.table,self.empty_read,*self.tools]
        self.show_view()
    def history_mode(self)->bool:return self.view_group.checkedId()==1
    def update_empty(self):
        live=self.owner.space.live if hasattr(self.owner,'space') else True
        if not hasattr(self,'history'):return
        self.empty_history.hide()
        if self.history_mode():
            if not self.history_keys:
                self.empty.set_icon('params');self.empty.show_message('还没有汉化记录','在“当前工程”里读取参数并翻译或填写中文后，它们会记录在这里；不需要连接 AE 也能查看、删除或加入方案。')
                self.empty_read.hide();self.empty_versions.hide();self.empty_clear.hide();self.stack.setCurrentIndex(0)
            elif all(self.history.isRowHidden(i) for i in range(len(self.history_keys))):
                self.empty.set_icon('search');self.empty.show_message('没有匹配的记录','换个关键词试试。')
                self.empty_read.hide();self.empty_versions.hide();self.empty_clear.show();self.stack.setCurrentIndex(0)
            else:self.stack.setCurrentIndex(2)
            return
        if not live:
            self.empty.set_icon('versions');self.empty.show_message('此 AE 版本尚未适配参数实时汉化','参数标签只能在已验证的构建上写入。可在“AE 版本”页切换版本，或用“自动识别与适配”为这个版本完成本机验收。')
            self.empty_read.hide();self.empty_versions.show();self.empty_clear.hide();self.stack.setCurrentIndex(0)
        elif not self.rows:
            messages={'noActiveComp':('没有活动合成','请在 AE 中打开合成并选中图层，再点击读取。'),'noSelectedLayers':('没有选中的图层','请在 AE 当前合成中选中一个或多个图层，再点击读取。'),'noSupportedParameters':('选中图层没有受支持参数','请在选中图层添加效果，或改选“整个工程”后读取。')}
            title,description=messages.get(self.empty_reason,('读取 AE 中的效果参数','默认读取当前活动合成的选中图层；也可切换为“整个工程”。首次结构扫描可能较慢。'+(f'也可以先看看 {len(self.history_keys)} 条汉化记录。' if self.history_keys else '')))
            self.empty.set_icon('params');self.empty.show_message(title,description)
            self.empty_read.show();self.empty_versions.hide();self.empty_clear.hide();self.empty_history.setVisible(bool(self.history_keys));self.stack.setCurrentIndex(0)
        elif all(self.table.isRowHidden(i) for i in range(len(self.rows))):
            self.empty.set_icon('search');self.empty.show_message('没有匹配的参数','换个关键词或效果，或切换上方的统计卡查看其他状态。')
            self.empty_read.hide();self.empty_versions.hide();self.empty_clear.show();self.stack.setCurrentIndex(0)
        else:self.stack.setCurrentIndex(1)
    def clear_filters(self):
        self.search.clear();self.effects.setCurrentIndex(0);self.chips['all'].setChecked(True);self.filter_rows();self.filter_history()
    def clear(self):
        """Another AE version was chosen: rows read from the previous one no longer apply."""
        self.wanted=None;self.loaded({'parameters':[]});self.status.setText('已切换 AE 版本，请重新读取参数。')
    def set_read_scope(self,scope):
        if self.owner.busy() or not self.owner.space.live:
            self.scope_actions[self.read_scope].setChecked(True);return
        if scope not in self.scope_actions or scope==self.read_scope:return
        self.read_scope=scope;self.scope_actions[scope].setChecked(True);self.scan_menu.setText(self.scope_actions[scope].text());self.wanted=None
        self.loaded({'parameters':[]});self.status.setText('已切换为“'+self.scan_menu.text()+'”，点击“读取 AE 参数”执行。')
    def focus_effect(self,effect:str|None):
        if not effect:return
        if not self.rows:self.wanted=effect;self.load();return
        index=self.effects.findData(effect)
        if index<0:self.status.setText(f'当前工程中没有已加载的 {effect}；请先在 AE 图层中添加该效果后重新读取。');return
        self.effects.setCurrentIndex(index)
    def load(self,rescan=False):
        if self.owner.busy() or not self.owner.space.live:return
        scope=self.read_scope
        self.owner.work(lambda progress:read_parameters(rescan=rescan,progress=progress,expected_image=self.owner.space.exe,scope=scope),self.loaded)
    def row_status(self,i)->str:
        candidate=self.owner.parameter_pending.get(self.rows[i]['id'],'').strip()
        if not candidate:return '待翻译'
        return '已应用' if self.rows[i]['name']==candidate else '待应用'
    def loaded(self,report):
        started=time.perf_counter();self.table.setUpdatesEnabled(False)
        try:
            self.rows=report['parameters'];self.empty_reason=report.get('emptyReason');self.filling=True;self.table.setRowCount(len(self.rows))
            wanted=self.wanted or self.effects.currentData();self.wanted=None
            self.effects.blockSignals(True);self.effects.clear();self.effects.addItem('所有已加载效果','')
            for effect in sorted({r['effectMatchName'] for r in self.rows}):self.effects.addItem(effect,effect)
            found=self.effects.findData(wanted);self.effects.setCurrentIndex(max(found,0));self.effects.blockSignals(False)
            mono=QFont('Cascadia Mono');mono.setStyleHint(QFont.StyleHint.Monospace);mono.setPixelSize(12)
            for i,row in enumerate(self.rows):
                check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(Qt.CheckState.Checked if row['id'] in self.owner.parameter_enabled else Qt.CheckState.Unchecked);self.table.setItem(i,0,check)
                for col,value in ((1,row['name']),(2,self.owner.parameter_pending.get(row['id'],'')),(3,row['effectMatchName']),(4,row['matchName']),(STATUS,'')):
                    item=QTableWidgetItem(value)
                    if col!=2:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
                    if col in (3,4):item.setForeground(QColor(ui_kit.MUTED))
                    if col==4:item.setFont(mono)
                    self.table.setItem(i,col,item)
                self.table.item(i,STATUS).setText(self.row_status(i))
            self.filling=False;self.remember_labels();self.fill_history();self.filter_rows();self.update_counts();self.owner.update_nav();self.update_check_all();self.update_empty()
            scope_text='选中图层' if self.read_scope=='selected' else '整个工程'
            if not self.rows:self.status.setText({'noActiveComp':'没有活动合成，请在 AE 中打开合成并选择图层。','noSelectedLayers':'没有选中图层，请在 AE 中选择图层后读取。','noSupportedParameters':'选中图层没有可读取的受支持参数。'}.get(self.empty_reason,scope_text+'没有可读取的受支持参数。'))
            if self.rows:self.status.setText(f'{scope_text}：读取 {len(self.rows)} 个参数标签、{sum(r.get("instances",1) for r in self.rows)} 个参数实例；图层或效果变化后请重新读取。')
            if wanted and found<0 and self.rows:self.status.setText(f'{scope_text}没有已加载的 {wanted}；显示范围内全部 {len(self.rows)} 个参数。')
        finally:
            self.filling=False;self.table.setUpdatesEnabled(True)
        if 'timings' in report:
            metrics=dict(report['timings'],event='table',tableMs=(time.perf_counter()-started)*1000);record_read_timing(metrics)
            self.status.setText(self.status.text()+f" 读取 {metrics['totalMs']/1000:.2f} 秒，表格 {metrics['tableMs']/1000:.2f} 秒。")
    def refresh_values(self):
        """A scheme replaced the candidates and checks: redraw the rows already read, keeping the status line."""
        if not self.rows:self.fill_history();self.update_empty();return
        status=self.status.text();self.loaded({'parameters':self.rows});self.status.setText(status)
    def row_matches(self,i,mode)->bool:
        if mode=='all':return True
        key=self.rows[i]['id']
        return {'checked':key in self.owner.parameter_enabled,'todo':self.row_status(i)=='待翻译','applied':self.row_status(i)=='已应用'}[mode]
    def filter_rows(self):
        effect=self.effects.currentData();query=self.search.text().casefold();mode=FILTERS[max(self.chip_group.checkedId(),0)][0]
        for i,row in enumerate(self.rows):self.table.setRowHidden(i,bool(effect and row['effectMatchName']!=effect) or query not in ' '.join(self.table.item(i,c).text() for c in range(1,5)).casefold() or not self.row_matches(i,mode))
        if hasattr(self,'empty_clear'):self.update_empty()
        if hasattr(self,'check_header'):self.update_check_header()
    def visible(self):return [i for i in range(len(self.rows)) if not self.table.isRowHidden(i)]
    def update_check_header(self):self.check_header.set_state(check_state(self.rows[i]['id'] in self.owner.parameter_enabled for i in self.visible()))
    def toggle_visible(self):
        rows=self.visible()
        if rows and not self.owner.busy() and self.owner.space.live:self.check(not all(self.rows[i]['id'] in self.owner.parameter_enabled for i in rows))
    def update_counts(self):
        for key,_,_ in FILTERS:self.chips[key].set_value(len(self.rows) if key=='all' else sum(1 for i in range(len(self.rows)) if self.row_matches(i,key)))
        self.update_check_header()
    def changed(self,item):
        if self.filling or item.column() not in (0,2):return
        key=self.rows[item.row()]['id']
        if item.column()==0:
            checked=item.checkState()==Qt.CheckState.Checked;anchor=self.anchor;self.anchor=item.row();visible=self.visible()
            if anchor is not None and anchor!=item.row() and anchor in visible and item.row() in visible and QApplication.keyboardModifiers()&Qt.KeyboardModifier.ShiftModifier:
                first,last=sorted((visible.index(anchor),visible.index(item.row())));self.filling=True
                for i in visible[first:last+1]:
                    self.table.item(i,0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                    (self.owner.parameter_enabled.add if checked else self.owner.parameter_enabled.discard)(self.rows[i]['id'])
                self.filling=False
            elif checked:self.owner.parameter_enabled.add(key)
            else:self.owner.parameter_enabled.discard(key)
        else:
            self.owner.parameter_pending[key]=item.text()
            self.filling=True;self.table.item(item.row(),STATUS).setText(self.row_status(item.row()));self.filling=False
        self.owner.save_parameters();self.update_counts()
        if item.column()==2:self.fill_history()
    def update_check_all(self):self.check_all_btn.setEnabled(bool(self.rows) and self.owner.space.live and not self.owner.busy())
    def check_all(self,value):
        if not self.rows or self.owner.busy() or not self.owner.space.live:return
        self.check(value,all_rows=True)
        self.status.setText(f'已勾选全部 {len(self.rows)} 个参数；不受筛选影响。' if value else f'已取消全部 {len(self.rows)} 个参数的勾选。')
    def check(self,value,all_rows=False):
        self.filling=True
        for i in range(len(self.rows)):
            if all_rows or not self.table.isRowHidden(i):
                self.table.item(i,0).setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
                key=self.rows[i]['id']
                if value:self.owner.parameter_enabled.add(key)
                else:self.owner.parameter_enabled.discard(key)
        self.filling=False;self.owner.save_parameters();self.update_counts()
    def paint_checks(self,rows,state):
        self.filling=True
        for row in rows:
            self.table.item(row,0).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
            (self.owner.parameter_enabled.add if state else self.owner.parameter_enabled.discard)(self.rows[row]['id'])
        self.filling=False
    def finish_checks(self,rows,state):
        self.anchor=rows[-1];self.owner.save_parameters();self.update_counts()
        self.status.setText(f"已{'勾选' if state else '取消勾选'} {len(rows)} 个参数")
    def selected(self):return [r for r in self.rows if r['id'] in self.owner.parameter_enabled]
    def translate(self):
        selected=self.selected()
        if not selected:self.status.setText('请先勾选参数');return
        if not self.owner.settings.ready() and not self.owner.configure_api():return
        settings=self.owner.settings
        def run(progress):
            # The parameter prompt carries the 31-byte limit and AE's usual wording; category names the effect for context.
            items=[{'id':r['id'],'sourceName':r['name'],'category':'所属效果 '+r['effectMatchName']} for r in selected]
            try:return translate_names(items,settings.key,settings.base_url,settings.model,progress=lambda n,total:progress(f'参数翻译 {n}/{total}'),service=settings.service,usage=self.owner.record_usage,system_prompt=PARAMETER_PROMPT),''
            except TranslationError as e:return e.partial,str(e)
        def done(result):
            names,error=result;valid={};failures=[]
            for key,name in names.items():
                try:valid[key]=validate_label(name)
                except ValueError:failures.append(key)
            self.owner.parameter_pending.update(valid);self.owner.save_parameters();self.loaded({'parameters':self.rows});self.status.setText(f'已翻译 {len(valid)} 项；查看候选后应用。'+(f'{len(failures)} 项名称过长，请手动缩短。' if failures else '')+error)
        self.owner.work(run,done)
    def apply(self):
        names={r['id']:self.owner.parameter_pending.get(r['id'],'') for r in self.selected() if self.owner.parameter_pending.get(r['id'],'').strip()}
        skipped=len(self.selected())-len(names)
        if not names:self.status.setText('勾选的参数还没有中文候选，请先翻译或双击“中文候选”填写');return
        scope=self.read_scope
        # Written from the heap-only structure when that is what is cached (strict guards unchanged); a background
        # full scan then covers anything outside the default heap.
        def work(progress):return self.owner.parameter_session.apply(names,progress,full=False),read_parameters(expected_image=self.owner.space.exe,scope=scope)
        def done(result):
            operation,report=result;self.loaded(report);same=operation.get('unchanged',0);written=operation['parameters']-same
            if operation.get('heapOnly'):self.owner.schedule_completion(names)
            self.status.setText(f"已应用 {written} 个参数名称，{same} 项已为目标名称，{skipped} 项无候选已跳过；请在 AE 原有控件中调节。")
            self.owner.toast.notify('参数处理完成',f"已应用 {written} 项，{same} 项无需修改")
        self.writing=set(names);self.owner.work(work,done)
        if self.owner.busy():self.breather.start()
        else:self.writing=set()
    def stop_writing(self):
        if self.writing:self.writing=set();self.breather.stop()
    def restore(self):self.restore_keys([r['id'] for r in self.selected()])
    def restore_all(self):self.restore_keys(None)
    def restore_keys(self,keys):
        scope=self.read_scope
        def work(progress):return self.owner.parameter_session.restore(keys,progress),read_parameters(expected_image=self.owner.space.exe,scope=scope)
        def done(result):
            operation,report=result;self.loaded(report);self.status.setText(f"已恢复 {operation.get('restored',0)} 个参数；{operation.get('pending',0)} 个未加载参数保留恢复记录。")
            self.owner.toast.notify('已恢复参数原名',f"已恢复 {operation.get('restored',0)} 个参数标签")
        self.owner.work(work,done)

    # The record: every parameter of this AE version that has a Chinese candidate, readable without AE.
    def build_history(self):
        self.history_keys=[];self.history_undo=[];self.history_filling=False
        table=self.history=QTableWidget(0,5);self.history_header=CheckHeader(table,self.toggle_history);table.setHorizontalHeader(self.history_header)
        table.setHorizontalHeaderLabels(['','效果','参数','中文名称（可编辑）','']);from ui_kit import table_style;table_style(table)
        for column in range(5):table.horizontalHeader().setSectionResizeMode(column,QHeaderView.ResizeMode.Fixed if column in (H_CHECK,H_TRASH) else QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(H_CHECK,52);table.setColumnWidth(H_TRASH,52);table.setItemDelegateForColumn(H_TRASH,TrashDelegate(table))
        table.horizontalHeaderItem(H_CHECK).setToolTip('勾选的参数会一并应用、翻译，也可以加入方案。点这里勾选或取消当前列出的记录；按住复选框上下拖动可快速勾选')
        table.horizontalHeaderItem(H_CHINESE).setToolTip('双击修改中文候选；清空即删除这条记录')
        table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked|QTableWidget.EditTrigger.EditKeyPressed);table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.itemChanged.connect(self.history_changed);table.cellClicked.connect(self.history_clicked)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);table.customContextMenuRequested.connect(lambda pos:self.history_menu(table.rowAt(pos.y())).exec(table.viewport().mapToGlobal(pos)) if table.rowAt(pos.y())>=0 else None)
        self.history_dragger=CheckDragger(table,H_CHECK,lambda row:self.history_keys[row] in self.owner.parameter_enabled,self.paint_history,self.finish_history)
        from shortcuts import local_shortcut;self.owner.keymap.bind('undo',local_shortcut(self,self.undo_record,Qt.ShortcutContext.WidgetWithChildrenShortcut))
        self.pending_delete=[];self.history_timer=QTimer(self);self.history_timer.setSingleShot(True);self.history_timer.setInterval(0);self.history_timer.timeout.connect(self.history_tick)
        self.stack.addWidget(table)
    def show_view(self):
        history=self.history_mode()
        for widget in (self.refresh,self.scan_menu,self.effects):widget.setVisible(not history)
        for widget in (self.history_add,self.history_delete):widget.setVisible(history)
        self.search.setPlaceholderText('搜索效果、参数或中文' if history else '搜索参数名称或内部标识')
        self.fill_history();self.update_empty()
    def remember_labels(self):
        """Keeps the label AE showed for each parameter before it was renamed, so the record stays readable."""
        labels=getattr(self.owner,'parameter_labels',None)
        if labels is None:return
        before=dict(labels)
        for row in self.rows:
            if row['name'] and row['name']!=self.owner.parameter_pending.get(row['id']):labels.setdefault(row['id'],row['name'])
        if labels!=before:
            message=self.owner.parameter_labels_store.save()
            if message:self.owner.log.appendPlainText(message)
    def fill_history(self):
        if not hasattr(self,'history'):return
        pending=self.owner.parameter_pending;labels={key:self.owner.parameter_label(key) for key,value in pending.items() if value.strip()}
        # Parameters of one effect keep their order in AE (by internal identity), whatever their label.
        self.history_keys=sorted(labels,key=lambda k:(labels[k][0].casefold(),k));table=self.history
        self.history_filling=True;table.setRowCount(len(self.history_keys))
        for row,key in enumerate(self.history_keys):
            effect,name=labels[key];tip=f'效果：{effect}\n参数：{name}\n内部标识：{key}'
            check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(Qt.CheckState.Checked if key in self.owner.parameter_enabled else Qt.CheckState.Unchecked);check.setToolTip(tip);table.setItem(row,H_CHECK,check)
            for column,text in ((H_EFFECT,effect),(H_NAME,name),(H_CHINESE,pending[key]),(H_TRASH,'')):
                item=QTableWidgetItem(text);item.setToolTip('删除这条记录'+self.owner.undo_note('（{key} 可撤销）') if column==H_TRASH else tip)
                if column!=H_CHINESE:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
                if column==H_EFFECT:item.setForeground(QColor(ui_kit.MUTED))
                if column==H_TRASH:item.setData(Qt.ItemDataRole.UserRole+3,True)
                table.setItem(row,column,item)
        self.history_filling=False;self.view_buttons[1].setText(f'汉化记录 {len(self.history_keys)}' if self.history_keys else '汉化记录');self.filter_history()
    def filter_history(self,*_):
        if not hasattr(self,'history'):return
        query=self.search.text().casefold().strip()
        for row in range(len(self.history_keys)):
            text=' '.join(self.history.item(row,c).text()+' '+self.history.item(row,c).toolTip() for c in (H_EFFECT,H_NAME,H_CHINESE)).casefold()
            self.history.setRowHidden(row,bool(query) and query not in text)
        self.update_history_checks()
        if self.history_mode():self.update_empty()
    def history_visible(self)->list[int]:return [r for r in range(len(self.history_keys)) if not self.history.isRowHidden(r)]
    def history_checked(self)->list[str]:return [k for k in self.history_keys if k in self.owner.parameter_enabled]
    def update_history_checks(self):
        self.history_header.set_state(check_state(self.history_keys[r] in self.owner.parameter_enabled for r in self.history_visible()))
        checked=bool(self.history_checked());self.history_delete.setEnabled(checked and not self.owner.busy())
        self.history_add.setText(f'把勾选记录加入方案（{len(self.history_checked())}）' if checked else '把勾选记录加入方案')
    def set_history_checks(self,rows,state):
        self.history_filling=True
        for row in rows:
            self.history.item(row,H_CHECK).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
            (self.owner.parameter_enabled.add if state else self.owner.parameter_enabled.discard)(self.history_keys[row])
        self.history_filling=False
    def paint_history(self,rows,state):self.set_history_checks(rows,state)
    def finish_history(self,rows,state):
        self.owner.save_parameters();self.update_history_checks();self.refresh_project_checks()
        self.status.setText(f"已{'勾选' if state else '取消勾选'} {len(rows)} 条记录")
    def toggle_history(self):
        rows=self.history_visible()
        if rows:self.set_history_checks(rows,not all(self.history_keys[r] in self.owner.parameter_enabled for r in rows));self.finish_history(rows,self.history_keys[rows[0]] in self.owner.parameter_enabled)
    def refresh_project_checks(self):
        """The project view shows the same checks; redraw them without touching its filters."""
        self.filling=True
        for i,row in enumerate(self.rows):self.table.item(i,0).setCheckState(Qt.CheckState.Checked if row['id'] in self.owner.parameter_enabled else Qt.CheckState.Unchecked)
        self.filling=False;self.update_counts()
    def history_changed(self,item):
        if self.history_filling or item.column() not in (H_CHECK,H_CHINESE):return
        key=self.history_keys[item.row()]
        if item.column()==H_CHECK:
            (self.owner.parameter_enabled.add if item.checkState()==Qt.CheckState.Checked else self.owner.parameter_enabled.discard)(key)
            self.owner.save_parameters();self.update_history_checks();self.refresh_project_checks();return
        name=' '.join(item.text().split())
        if name:
            try:name=validate_label(name)
            except ValueError as error:self.status.setText(f'参数名称无效：{error}');self.history_timer.start();return
            self.owner.parameter_pending[key]=name;self.owner.save_parameters();self.status.setText(f'已修改记录：{name}')
        else:self.pending_delete=[key]
        # Redrawing waits for the editor to close: this runs inside the table's own change signal.
        self.history_timer.start()
    def history_tick(self):
        keys,self.pending_delete=self.pending_delete,[]
        if keys:self.delete_records(keys,ask=False)
        else:self.sync_views()
    def sync_views(self):
        if self.rows:self.refresh_values()
        else:self.fill_history();self.update_empty()
    def history_clicked(self,row,column):
        if column==H_TRASH and 0<=row<len(self.history_keys):self.delete_records([self.history_keys[row]],ask=False)
    def delete_checked_records(self):self.delete_records(self.history_checked())
    def delete_records(self,keys,ask=True):
        """Forgets the Chinese candidates and checks of these parameters; names already applied in AE are untouched."""
        if not keys or self.owner.busy():return
        pending=self.owner.parameter_pending;shown='、'.join(' · '.join(self.owner.parameter_label(k)) for k in keys[:4])+(f' 等 {len(keys)} 条' if len(keys)>4 else '')
        if ask and not confirm(self,f'删除 {len(keys)} 条汉化记录？',f'{shown}\n删除中文候选和勾选；不改 AE 中已应用的名称（恢复请用“恢复”菜单），方案文件也不受影响。'+self.owner.undo_note('可按 {key} 撤销。'),'删除',danger=True):return
        self.history_undo=[*self.history_undo[-29:],[(k,pending.get(k,''),k in self.owner.parameter_enabled) for k in keys]]
        for key in keys:pending.pop(key,None);self.owner.parameter_enabled.discard(key)
        self.owner.save_parameters();self.status.setText(f'已删除 {len(keys)} 条汉化记录'+self.owner.undo_note('；{key} 撤销'));self.owner.toast.notify('已删除汉化记录',shown+self.owner.undo_note(' · {key} 撤销'))
        self.sync_views()
    def undo_record(self):
        if not self.history_undo:self.status.setText('没有可以撤销的记录删除');return
        for key,name,checked in self.history_undo.pop():
            if name:self.owner.parameter_pending[key]=name
            if checked:self.owner.parameter_enabled.add(key)
        self.owner.save_parameters();self.sync_views();self.status.setText('已撤销删除')
    def history_menu(self,row)->QMenu:
        menu=QMenu(self)
        if row<0 or row>=len(self.history_keys):return menu
        key=self.history_keys[row];checked=self.history_checked()
        self.owner.fill_add_menu(menu.addMenu('把这条加入方案'),[key],'parameters')
        if key in checked and len(checked)>1:self.owner.fill_add_menu(menu.addMenu(f'把勾选的 {len(checked)} 条加入方案'),checked,'parameters')
        menu.addAction('修改中文名称',lambda:self.history.editItem(self.history.item(row,H_CHINESE)))
        menu.addSeparator();menu.addAction('删除这条记录',lambda:self.delete_records([key],ask=False))
        return menu
