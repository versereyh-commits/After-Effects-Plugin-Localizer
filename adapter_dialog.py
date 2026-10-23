"""User-visible AI analysis workflow, sharing the main window's single worker."""
from pathlib import Path
import json,threading
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit,QFileDialog,QCheckBox
from adapter_analysis import run_analysis,save_report,load_report
from adapter_resolver import run_static_analysis,Resolver
from runtime import data_root
from local_adapters import NATIVE
import ui_kit
from ui_kit import SHADOW,LoaderButton,Surface,button,label,pixmap,repolish,soft_path

FAILURE_GUIDE={
    'structure':'这个 AE 构建的内部结构与已支持的版本不同，需要更新识别规则后才能适配，未启用。可导出报告反馈；“AI 诊断原因”只说明原因，不能启用这个版本。',
    'module':'无法读取所选 AE 的核心模块，未启用。请确认选择的是完整安装目录中的 AfterFX.exe，然后重试。',
    'transient':'识别期间 AE 模块发生了变化（可能正在更新），未启用。请等更新完成后点击“重试自动识别”。',
}

class AdapterAnalysisDialog(QDialog):
    """Four steps — identify, self-check in a QA project, confirm in AE, and the recommended background connection —
    with the process log beside them."""
    def __init__(self,owner,space):
        super().__init__(owner);self.owner=owner;self.space=space;self.running=False;self.report=None;self.cancel_event=threading.Event();self.validation=None;self.task_kind='code';self.completed=False;self.offline_failure=None;self.bridge_state='missing'
        self.setWindowTitle('自动识别与适配验证 · '+space.label);self.resize(1120,840)
        outer=QVBoxLayout(self);outer.setContentsMargins(20,16,20,12);outer.setSpacing(6)
        head=QVBoxLayout();head.setContentsMargins(SHADOW,0,SHADOW,0);head.setSpacing(2);head.addWidget(label('自动识别与适配验证','pageTitle'))
        self.target=label(f'{space.label} · {space.version}\n{soft_path(space.exe)}','pageNote',True);head.addWidget(self.target);outer.addLayout(head)
        body=QHBoxLayout();body.setSpacing(0);steps=QVBoxLayout();steps.setSpacing(0)
        self.step_badges=[]
        def step(number,title,text,tag=None):
            card=Surface(padding=(18,9,18,9),spacing=6);top=QHBoxLayout();top.setSpacing(10)
            badge=QLabel(str(number));badge.setObjectName('stepIndex');badge.setFixedSize(22,22);badge.setAlignment(Qt.AlignmentFlag.AlignCenter);self.step_badges.append(badge)
            top.addWidget(badge);top.addWidget(label(title,'cardTitle'))
            if tag:top.addWidget(label(tag,'stepTag'))
            top.addStretch();card.box.addLayout(top)
            card.box.addWidget(label(text,'muted',True));steps.addWidget(card);return card.box
        box=step(1,'识别候选配置','代码先离线核查模块、名称接口和结构，无需密钥。结构不同时可让 AI 诊断原因，但只能生成报告；候选不会解锁实时汉化。')
        row=QHBoxLayout();row.setSpacing(8)
        self.start_btn=LoaderButton('开始自动识别','primary');self.start_btn.clicked.connect(lambda:self.owner.run_with(self.start_btn,'orbit','识别中',self.start_code))
        self.ai_btn=LoaderButton('AI 诊断原因');self.ai_btn.setToolTip('让 AI 解释识别失败的原因并写入报告，便于更新识别规则；AI 结论不能启用这个版本');self.ai_btn.setVisible(False);self.ai_btn.setEnabled(False);self.ai_btn.clicked.connect(lambda:self.owner.run_with(self.ai_btn,'orbit','分析中',self.start))
        self.reuse_btn=button('载入上次分析','',tip='载入上次分析（不联网）');self.reuse_btn.clicked.connect(self.reuse_analysis)
        self.cancel_btn=button('取消','quiet');self.cancel_btn.setEnabled(False);self.cancel_btn.clicked.connect(self.cancel)
        for widget in (self.start_btn,self.ai_btn,self.reuse_btn,self.cancel_btn):row.addWidget(widget)
        row.addStretch();box.addLayout(row)
        self.export_btn=button('导出分析与候选配置…','link');self.export_btn.setEnabled(False);self.export_btn.clicked.connect(self.export);box.addWidget(self.export_btn,0,Qt.AlignmentFlag.AlignLeft)
        box=step(2,'自有 QA 工程自检','先在 AE 中自行保存并关闭当前工程，保持空工程。自检会创建专用 QA 工程，测试中文标签、数值、渲染、恢复、重复操作和工程切换。')
        row=QHBoxLayout();row.setSpacing(8)
        self.validate_btn=LoaderButton('执行自检');self.validate_btn.setEnabled(False);self.validate_btn.clicked.connect(lambda:self.owner.run_with(self.validate_btn,'scan','自检中',self.validate))
        self.recover_btn=button('恢复未完成验证…','quiet');self.recover_btn.clicked.connect(self.recover_validation)
        row.addWidget(self.validate_btn);row.addWidget(self.recover_btn);row.addStretch();box.addLayout(row)
        box=step(3,'在 AE 中核对并启用','刷新 AE 列表后在 AE 中核对下列四项，全部确认才能完成。完成后恢复全部原名并保存本机适配；普通应用仍不自动刷新列表。')
        self.refresh_native_btn=button('刷新 AE 列表（手动）','',icon_name='refresh');self.refresh_native_btn.setEnabled(False);self.refresh_native_btn.clicked.connect(self.refresh_native);box.addWidget(self.refresh_native_btn,0,Qt.AlignmentFlag.AlignLeft)
        # One confirmation for the whole AE review; the certificate still records each of NATIVE.
        box.addWidget(label('在 AE 中确认：效果列表显示“适配验证效果”；用中文搜索并双击添加该效果成功；效果控件显示中文参数标签；用 AE 原有控件调节参数正常。','muted',True))
        self.native_check=QCheckBox('以上四项都已在 AE 中确认');self.native_check.setEnabled(False);box.addWidget(self.native_check)
        row=QHBoxLayout();row.setSpacing(8)
        self.finish_btn=LoaderButton('完成核对、恢复并启用','primary');self.finish_btn.setEnabled(False);self.finish_btn.clicked.connect(lambda:self.owner.run_with(self.finish_btn,'fill','启用中',self.finish_validation))
        self.restore_qa_btn=LoaderButton('取消验证并恢复原名','danger');self.restore_qa_btn.setEnabled(False);self.restore_qa_btn.clicked.connect(lambda:self.owner.run_with(self.restore_qa_btn,'rewind','恢复中',self.restore_validation))
        row.addWidget(self.finish_btn);row.addWidget(self.restore_qa_btn);row.addStretch();box.addLayout(row)
        # The same background extension as the settings page offers; installing it here does not depend on the steps above.
        box=step(4,'后台连接','装进 AE 的看不见的扩展，读写参数时直接在 AE 后台运行：不再另起一个 AE 进程，AE 窗口和焦点保持不变，'
                 '软件放在带空格的文件夹里也能正常读写。安装后重启一次 AE 生效；设置页也可安装或移除。',tag='推荐安装')
        row=QHBoxLayout();row.setSpacing(8)
        self.bridge_install=button('安装后台连接');self.bridge_install.clicked.connect(lambda:self.owner.install_bridge(self) and self.update_bridge())
        self.bridge_remove=button('移除','quiet');self.bridge_remove.clicked.connect(lambda:(self.owner.remove_bridge(self),self.update_bridge()))
        self.bridge_note=label('','hint')
        row.addWidget(self.bridge_install);row.addWidget(self.bridge_remove);row.addWidget(self.bridge_note);row.addStretch();box.addLayout(row)
        steps.addStretch();left=QVBoxLayout();left.addLayout(steps);body.addLayout(left,5)
        log=Surface(padding=(18,9,18,9),spacing=6);log.box.addWidget(label('过程记录','cardTitle'))
        self.output=QPlainTextEdit();self.output.setReadOnly(True);self.output.setPlaceholderText('点击“开始自动识别”。离线识别成功后，再由你启动自检。');log.box.addWidget(self.output,1)
        self.status=label('就绪；识别阶段只读；自检由你手动启动，验证通过才启用。','muted',True);log.box.addWidget(self.status);body.addWidget(log,6)
        outer.addLayout(body,1)
        foot=QHBoxLayout();foot.setContentsMargins(SHADOW,4,SHADOW,0);foot.addStretch()
        close=button('关闭');close.clicked.connect(self.reject);foot.addWidget(close);outer.addLayout(foot)
        self.update_bridge()
    BRIDGE_STATE={'unavailable':'这个版本没有附带后台连接扩展','missing':'未安装','outdated':'与本软件版本不一致，请重新安装',
                  'installed':'已安装，重启 AE 后生效','connected':'已连接'}
    def update_bridge(self):
        """Step 4 shows the extension's state in a few words (the description above says what it does) and offers
        install or remove, the same actions as the settings page."""
        import cep_bridge
        try:self.bridge_state=cep_bridge.status()
        except OSError:self.bridge_state='missing'
        self.bridge_note.setText(self.BRIDGE_STATE[self.bridge_state])
        self.bridge_install.setVisible(self.bridge_state in ('missing','outdated'));self.bridge_install.setText('重新安装' if self.bridge_state=='outdated' else '安装后台连接')
        self.bridge_remove.setVisible(self.bridge_state in ('outdated','installed','connected'))
        self.sync_steps()
    def sync_steps(self):
        """Badges show which step is done, current or still ahead."""
        identified=bool(self.report and self.report.get('state')=='candidate')
        reviewing=self.finish_btn.isEnabled() or self.finish_btn.loading()
        states=('done' if identified or self.completed else 'current',
                'done' if reviewing or self.completed else 'current' if identified else 'todo',
                'done' if self.completed else 'current' if reviewing else 'todo',
                'done' if self.bridge_state in ('installed','connected') else 'current' if self.completed else 'todo')
        for number,(badge,state) in enumerate(zip(self.step_badges,states),1):
            if state=='done':ui_kit.themed_pixmap(badge,'check','ON_INK',12)
            else:badge.setProperty('themeIcon',None);badge.setText(str(number))
            repolish(badge,state=state)
    def reuse_analysis(self):
        if not self.can_analyze():return
        self.begin_analysis('reuse')
        self.owner.work(lambda progress:load_report(self.space.exe,data_root()/'adapter-analysis'),self.reused,failure=self.failed)
    def reused(self,report):
        if self.cancel_event.is_set():self.stopped();self.status.setText('载入已取消');return
        if report is None:self.stopped();self.status.setText('没有找到可复用的候选，请先开始自动识别');return
        self.analysis_done(report)
    def begin_analysis(self,kind):
        self.cancel_event.clear();self.running=True;self.task_kind=kind;self.report=None;self.completed=False
        self.native_check.setEnabled(False);self.native_check.setChecked(False)
        self.finish_btn.setEnabled(False);self.refresh_native_btn.setEnabled(False)
        for widget in (self.start_btn,self.ai_btn,self.reuse_btn,self.export_btn,self.validate_btn):widget.setEnabled(False)
        self.cancel_btn.setEnabled(True);self.output.clear()
    def can_analyze(self):
        if self.running or self.owner.busy():return False
        if self.validation and self.validation.held:self.status.setText('请先恢复当前验证');return False
        from adapter_validation import recover_pending
        try:pending=recover_pending(self.space.exe)
        except Exception as error:self.task_kind='recovery';self.failed('请先处理未完成验证：'+str(error));return False
        if pending:
            self.recovery_loaded(pending);return False
        return True
    def start_code(self):
        if not self.can_analyze():return
        self.offline_failure=None;self.ai_btn.setVisible(False);self.begin_analysis('code')
        self.owner.work(lambda progress:run_static_analysis(self.space.exe,progress,self.cancel_event),self.analysis_done,failure=self.failed)
        self.owner.job.progress.connect(self.status.setText)
    def ai_work(self,progress,config):
        context={key:self.offline_failure.get(key) for key in ('failureStage','evidence')}
        context['summary']='离线识别未完成；仅分析已有证据，未知结构不得推测。'
        report=run_analysis(self.space.exe,config,progress,self.cancel_event,failure_context=context)
        if self.cancel_event.is_set():return report
        if report.get('state')=='candidate':
            resolver=None
            try:resolver=Resolver(self.space.exe);resolver.complete_static('');resolver.verify_files()
            except (ValueError,RuntimeError,OSError,KeyError,TypeError):
                # Keep the model's explanation: it is the useful part of a diagnosis.
                report.update(state='blocked',candidateReadConfig={},fieldEvidence={},summary='AI 分析未能通过确定性补全，需要更新识别规则；以下 AI 说明仅供参考：\n'+str(report.get('summary','')))
            finally:
                if resolver:resolver.close()
        return report
    def start(self):
        if not self.offline_failure or not self.can_analyze():return
        if not self.owner.settings.ready() and not self.owner.configure_api():return
        s=self.owner.settings;config={'service':s.service,'base_url':s.base_url,'model':s.model,'key':s.key}
        self.begin_analysis('ai')
        self.status.setText('正在分析；最多10轮，取消会在当前请求结束后停止。')
        self.owner.work(lambda progress:self.ai_work(progress,config),self.analysis_done,failure=self.failed)
        self.owner.job.progress.connect(self.status.setText)
    def stopped(self):
        self.running=False;self.start_btn.setEnabled(True);self.reuse_btn.setEnabled(True);self.ai_btn.setEnabled(bool(self.offline_failure) and self.task_kind!='validation');self.cancel_btn.setEnabled(False);QTimer.singleShot(0,self,self.sync_steps)
    def analysis_done(self,report):
        if self.cancel_event.is_set():self.stopped();self.status.setText('识别已取消；未接收候选');return
        source=report.get('analysisSource','ai');guide=''
        if source=='code':
            self.offline_failure=report if report['state']=='blocked' else None
            self.start_btn.set_label('重试自动识别' if self.offline_failure else '开始自动识别')
            if self.offline_failure:
                kind=report.get('failureKind','structure');guide=FAILURE_GUIDE.get(kind,FAILURE_GUIDE['structure'])
                # AI reads the same modules and must pass the same deterministic
                # completion, so it is offered only to explain a structure gap.
                if kind!='structure':self.offline_failure=None
        self.ai_btn.setVisible(bool(self.offline_failure));self.stopped();self.report=report
        if source=='ai':self.ai_btn.setEnabled(False)
        try:
            path=save_report(report,data_root()/'adapter-analysis');saved='\n本机报告：'+str(path)
        except OSError:saved='\n报告未能保存，请使用“导出”按钮。'
        fields=report['candidateReadConfig'];steps=' → '.join(entry['tool'] for entry in report['trace']) or ('本机确定性解析' if source=='code' else '未完成工具调用')
        self.output.setPlainText(('代码识别结果：\n' if source=='code' else 'AI 分析说明（可能有误，以本机证据为准）：\n')+report['summary']+('\n\n下一步：'+guide if guide else '')+'\n\n工具流程：'+steps+'\n\n候选读取配置（由本机证据生成）：\n'+json.dumps(fields,ensure_ascii=False,indent=2)+'\n\n仍需验证：\n'+'\n'.join(report['missing'])+saved)
        self.status.setText(guide or f"{'已生成候选' if report['state']=='candidate' else '识别未完成'}；{len(fields)} 个字段有本机证据。尚未验证实时写入，未启用新构建。")
        self.owner.status.setText(self.status.text())
        self.export_btn.setEnabled(True);self.owner.log.appendPlainText(f'适配分析：{self.space.label} · {report["state"]} · {len(fields)} 个候选字段；未启用新构建')
        self.validate_btn.setEnabled(report['state']=='candidate')
    def failed(self,message):
        self.stopped();self.status.setText(message);self.owner.error(message)
        if self.task_kind=='code' and not self.cancel_event.is_set():
            self.offline_failure={'failureStage':'读取模块','summary':message,'evidence':[]};self.ai_btn.setVisible(True);self.ai_btn.setEnabled(True);self.start_btn.set_label('重试自动识别')
    def cancel(self):
        if self.task_kind=='validation' and self.validation:self.validation.cancel_requested.set()
        else:self.cancel_event.set()
        self.cancel_btn.setEnabled(False);self.status.setText('已请求取消，等待当前安全阶段结束；验证阶段会恢复本次修改。')
    def validation_work(self,function,callback):
        if self.running or self.owner.busy():return
        if self.validation:self.validation.cancel_requested.clear()
        self.running=True;self.task_kind='validation';self.ai_btn.setEnabled(False);self.reuse_btn.setEnabled(False);self.start_btn.setEnabled(False);self.validate_btn.setEnabled(False);self.finish_btn.setEnabled(False);self.refresh_native_btn.setEnabled(False);self.restore_qa_btn.setEnabled(False);self.cancel_btn.setEnabled(True)
        self.owner.work(function,callback,failure=self.validation_failed);self.owner.job.progress.connect(self.status.setText);QTimer.singleShot(0,self,self.sync_steps)
    def validate(self):
        if self.running or self.owner.busy() or self.report is None or self.report['state']!='candidate':return
        if self.validation and self.validation.held:self.status.setText('请先恢复当前验证会话');return
        from adapter_validation import ValidationRun
        try:self.validation=ValidationRun(self.space.exe,self.report)
        except Exception as error:self.task_kind='validation';self.failed('自检未启动：'+str(error));return
        self.validation_work(self.validation.run,self.validation_ready)
    def validation_ready(self,result):
        self.stopped();self.status.setText('自动自检通过。请手动刷新 AE 列表，在 AE 中核对四项后勾选确认，然后完成并恢复。');self.owner.status.setText(self.status.text())
        self.output.appendPlainText('\n自动自检：\n'+json.dumps(result,ensure_ascii=False,indent=2))
        self.start_btn.setEnabled(False);self.finish_btn.setEnabled(True);self.refresh_native_btn.setEnabled(True);self.restore_qa_btn.setEnabled(True)
        self.native_check.setEnabled(True)
    def validation_failed(self,message):
        self.failed('自检失败：'+message);self.restore_qa_btn.setEnabled(bool(self.validation));self.validate_btn.setEnabled(bool(self.report and self.report.get('state')=='candidate' and self.validation and not self.validation.held))
        if self.validation and self.validation.review_ready:self.finish_btn.setEnabled(True)
    def refresh_native(self):
        if self.validation:self.validation_work(lambda progress:self.validation.refresh(),lambda result:self.validation_ready({'nativeListRefreshed':True}))
    def finish_validation(self):
        native={key:self.native_check.isChecked() for key in NATIVE}
        if not all(native.values()):self.status.setText('请在 AE 中核对全部四项后勾选确认，不能跳过原生验证。');return
        self.validation_work(lambda progress:self.validation.finish(native,progress),self.validation_finished)
    def validation_finished(self,result):
        self.completed=True;self.stopped();self.finish_btn.setEnabled(False);self.refresh_native_btn.setEnabled(False);self.restore_qa_btn.setEnabled(False);self.validate_btn.setEnabled(True)
        self.native_check.setEnabled(False)
        self.status.setText('验证通过，已恢复全部原名并保存本机构建适配。再次连接时会复核模块。');self.owner.status.setText(self.status.text())
        self.output.appendPlainText('\n验证完成：\n'+json.dumps(result,ensure_ascii=False,indent=2));self.owner.reload_installations(self.space.exe)
    def restore_validation(self):
        if self.validation:self.validation_work(lambda progress:self.validation.cancel(),self.validation_cancelled)
    def validation_cancelled(self,result):
        self.stopped();self.restore_qa_btn.setEnabled(False);self.finish_btn.setEnabled(False);self.refresh_native_btn.setEnabled(False);self.validate_btn.setEnabled(True)
        self.native_check.setEnabled(False);self.native_check.setChecked(False)
        self.status.setText('验证已取消，本次修改已恢复，未保存新适配。')
    def recover_validation(self):
        if self.running or self.owner.busy():return
        if self.validation and self.validation.held:self.status.setText('当前已有待恢复会话，请点击“取消验证并恢复原名”');return
        from adapter_validation import recover_pending
        self.validation_work(lambda progress:recover_pending(self.space.exe),self.recovery_loaded)
    def recovery_loaded(self,run):
        self.stopped()
        if run is None:self.status.setText('没有找到此安装的未恢复验证');return
        self.validation=run;self.start_btn.setEnabled(False);self.validate_btn.setEnabled(False);self.restore_qa_btn.setEnabled(True)
        self.status.setText('已找到未完成验证。请点击“取消验证并恢复原名”；原 AE 仍运行时需保持对应软件 QA 工程。')
    def export(self):
        if self.report is None:return
        path,_=QFileDialog.getSaveFileName(self,'导出适配分析',str(Path.home()/'AE适配分析.json'),'JSON (*.json)')
        if path:
            try:Path(path).write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding='utf8')
            except OSError:self.status.setText('导出失败，请检查保存目录是否可写。')
    def reject(self):
        if self.running:self.status.setText('请先取消并等待当前分析结束后关闭。');return
        if self.validation and self.validation.held:self.restore_validation();return
        super().reject()
    def closeEvent(self,event):
        if self.running:event.ignore();self.status.setText('请先取消并等待当前分析结束后关闭。');return
        if self.validation and self.validation.held:event.ignore();self.restore_validation();return
        super().closeEvent(event)
