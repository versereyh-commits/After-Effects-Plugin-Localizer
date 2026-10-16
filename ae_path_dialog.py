from pathlib import Path
import json
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog
from app import Job
from ae_paths import locate,remember,compatibility
from runtime import data_root,file_version
from ui_kit import SHADOW,DiamondDelegate,Surface,button,label,table_style

class AEPathDialog(QDialog):
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.job=None;self.reports=[];self.setWindowTitle('AE 路径与兼容性');self.resize(1080,560)
        layout=QVBoxLayout(self);layout.setContentsMargins(20,22,20,18);layout.setSpacing(8)
        head=QVBoxLayout();head.setContentsMargins(SHADOW,0,SHADOW,0);head.setSpacing(2);head.addWidget(label('AE 路径与兼容性','pageTitle'))
        head.addWidget(label('自动查找注册表、默认目录和运行中的 AE，也可选择其他磁盘上的安装目录或 AfterFX.exe。路径定位不等于版本适配；仅已验证构建可实时写入，检测不会修改 AE。','pageNote',True));layout.addLayout(head)
        card=Surface(padding=(14,10,14,10))
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['AE / 文件版本','安装路径','发现方式','实时汉化兼容性']);table_style(self.table,40)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.table.setItemDelegateForColumn(3,DiamondDelegate(self.table));card.box.addWidget(self.table)
        layout.addWidget(card,1)
        self.status=label('','muted',True);self.status.setContentsMargins(SHADOW,0,SHADOW,0);layout.addWidget(self.status)
        tools=QHBoxLayout();tools.setContentsMargins(SHADOW,4,SHADOW,0);tools.setSpacing(8)
        self.folder=button('选择 AE 安装目录…','',icon_name='folder');self.folder.clicked.connect(self.choose_folder)
        self.exe=button('选择 AfterFX.exe…');self.exe.clicked.connect(self.choose_exe)
        self.export=button('导出兼容性报告…','quiet');self.export.clicked.connect(self.export_report)
        self.refresh=button('重新检测','',icon_name='refresh');self.refresh.clicked.connect(self.detect)
        self.close_btn=button('关闭');self.close_btn.clicked.connect(self.accept)
        self.use=button('使用选中 AE','primary');self.use.clicked.connect(self.use_selected)
        self.buttons=(self.refresh,self.folder,self.exe,self.export,self.use,self.close_btn)
        for widget in (self.folder,self.exe,self.export):tools.addWidget(widget)
        tools.addStretch()
        for widget in (self.refresh,self.close_btn,self.use):tools.addWidget(widget)
        layout.addLayout(tools);self.detect()
    def busy(self):return self.job is not None and self.job.isRunning()
    def detect(self):
        if self.busy():return
        for widget in self.buttons:widget.setEnabled(False)
        self.status.setText('正在检测 AE 路径与模块构建……')
        from core import installations
        self.job=Job(lambda progress:[compatibility(item,file_version) for item in installations()])
        self.job.result.connect(self.loaded);self.job.failure.connect(self.status.setText);self.job.finished.connect(self.finish_job);self.job.start()
    def loaded(self,reports):
        self.reports=reports;self.table.setRowCount(len(reports))
        for row,item in enumerate(reports):
            for column,text in enumerate((item['name']+' · '+item['version'],item['exe'],'、'.join(item['sources']),item['status'])):
                cell=QTableWidgetItem(text);cell.setToolTip(text);self.table.setItem(row,column,cell)
        if reports:self.table.selectRow(0)
        self.status.setText(f'找到 {len(reports)} 个 AE 安装。报告仅含路径、版本和模块哈希，不读取工程或 API Key。' if reports else '未找到 AE，请手动选择安装目录或 AfterFX.exe。')
    def finish_job(self):
        for widget in self.buttons:widget.setEnabled(True)
        self.export.setEnabled(bool(self.reports));self.use.setEnabled(bool(self.reports))
    def add(self,path):
        if not path:return
        try:
            item=locate(path);version=file_version(Path(item['exe']))
            if not version or version=='unknown':raise ValueError('无法识别 AE 文件版本，请选择完整安装')
            remember(data_root()/'ae-installations.json',item['exe'])
        except (OSError,ValueError,RuntimeError) as error:self.status.setText(str(error));return
        self.detect()
    def choose_folder(self):self.add(QFileDialog.getExistingDirectory(self,'选择 AE 安装目录'))
    def choose_exe(self):self.add(QFileDialog.getOpenFileName(self,'选择 AfterFX.exe','','After Effects (AfterFX.exe)')[0])
    def use_selected(self):
        row=self.table.currentRow()
        if row<0:return
        self.owner.reload_installations(self.reports[row]['exe']);self.accept()
    def export_report(self):
        path,_=QFileDialog.getSaveFileName(self,'导出兼容性报告',str(Path.home()/'AE兼容性报告.json'),'JSON (*.json)')
        if path:
            try:Path(path).write_text(json.dumps({'schemaVersion':1,'mode':'read_only','hostVerified':False,'installations':self.reports},ensure_ascii=False,indent=2),encoding='utf8');self.status.setText('兼容性报告已导出')
            except OSError as error:self.status.setText('导出失败：'+str(error))
    def reject(self):
        if self.busy():self.status.setText('请等待路径检测完成');return
        super().reject()
    def closeEvent(self,event):
        if self.busy():event.ignore();self.status.setText('请等待路径检测完成')
        else:event.accept()
