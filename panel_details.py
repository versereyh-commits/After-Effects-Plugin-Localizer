"""Read-only diagnostic and recovery-record windows, using the existing desktop theme."""
from pathlib import Path
import json
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QTableWidget,QTableWidgetItem,QHeaderView,QPlainTextEdit
from ui_kit import button,label,table_style
import panel_records

class Diagnostics(QDialog):
    def __init__(self,parent,report=None,error=''):
        super().__init__(parent);self.setWindowTitle('读取诊断');self.resize(760,510)
        box=QVBoxLayout(self);box.setContentsMargins(24,20,24,20)
        info=(report or {}).get('diagnostics',{})
        box.addWidget(label('读取诊断','pageTitle'))
        text=[]
        if error:text.append('本次未完成读取：'+error)
        if report:
            text.append(f"已扫描 {info.get('scannedFiles',len(report.get('hashes',{})))} 个文件，找到 {len(report.get('terms',[]))} 条词条。")
            text.append('读取方式：'+('复用内存缓存（已核对文件哈希）' if report.get('readMode')=='cache' else '重新解析文件'))
            for row in info.get('skippedFiles',[]):text.append('跳过文件：'+row['file']+' · '+row['reason'])
            if info.get('truncated'):text.append('词条已达上限，结果不完整。')
            text.extend(report.get('warnings',[]))
        text.append('词条数不能代表整个面板的翻译覆盖率。签名、CSP、未知语法与动态界面均可能限制可处理范围。')
        view=QPlainTextEdit();view.setReadOnly(True);view.setPlainText('\n\n'.join(text));box.addWidget(view,1)
        done=button('关闭');done.clicked.connect(self.accept);box.addWidget(done,0,Qt.AlignmentFlag.AlignRight)

class History(QDialog):
    def __init__(self,parent,folder):
        super().__init__(parent);self.folder=Path(folder);self.rows=[];self.setWindowTitle('汉化记录');self.resize(940,620)
        box=QVBoxLayout(self);box.setContentsMargins(24,20,24,20)
        head=QHBoxLayout();head.addWidget(label('汉化记录','pageTitle'),1);refresh=button('刷新记录');refresh.clicked.connect(self.reload);head.addWidget(refresh);box.addLayout(head)
        box.addWidget(label('查看修改时间、文件、备份与冲突。恢复请返回目标详情，仍会重新核验文件。','hint',True))
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['目标','状态','记录时间（UTC）','文件数']);table_style(self.table)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.table.setColumnWidth(2,210)
        self.table.itemSelectionChanged.connect(self.selected);box.addWidget(self.table,1)
        self.detail=QPlainTextEdit();self.detail.setReadOnly(True);self.detail.setMaximumHeight(160);box.addWidget(self.detail)
        foot=QHBoxLayout();self.open_btn=button('打开备份目录');self.open_btn.clicked.connect(self.open_backup);foot.addWidget(self.open_btn);foot.addStretch();close=button('关闭');close.clicked.connect(self.accept);foot.addWidget(close);box.addLayout(foot)
        self.reload()
    def reload(self):
        self.rows=panel_records.entries(self.folder);self.table.setRowCount(0)
        for r in self.rows:
            n=self.table.rowCount();self.table.insertRow(n)
            for c,text in enumerate((r['name'],r['state'],r['at'] or '旧记录未记录时间',str(len(r['files'])))):
                item=QTableWidgetItem(text);item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable);item.setToolTip(text);self.table.setItem(n,c,item)
        if self.rows:self.table.selectRow(0)
        else:self.detail.setPlainText('暂无汉化记录。');self.open_btn.setEnabled(False)
    def selected(self):
        row=self.table.currentRow();valid=0<=row<len(self.rows);self.open_btn.setEnabled(valid)
        if valid:
            r=self.rows[row];self.detail.setPlainText('文件：\n'+'\n'.join(r['files'])+'\n\n核验：\n'+('\n'.join(r['issues']) or ('历史快照，仅供查看；当前目标状态请看最新记录。' if not r['current'] else '未发现冲突；恢复时仍会再次核验。'))+'\n\n备份目录：'+r['backup'])
    def open_backup(self):
        row=self.table.currentRow()
        if 0<=row<len(self.rows):
            from panel_files import inside
            p=Path(self.rows[row]['backup'])
            try:
                p=inside(self.folder,p.relative_to(self.folder))
                if p.is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
            except (ValueError,OSError):self.detail.setPlainText('备份目录已变化，未打开。请刷新记录。')
