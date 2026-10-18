from __future__ import annotations
from pathlib import Path
from dataclasses import asdict
from datetime import datetime
from typing import NamedTuple
import json,os,re,shutil,subprocess,sys,threading
from PySide6.QtCore import Qt,QEvent,QEventLoop,QObject,QTimer,QItemSelection,QItemSelectionModel,QPoint,QRect,QRectF,QEasingCurve,QUrl,Signal
from PySide6.QtGui import QAction,QColor,QDesktopServices,QFont,QKeySequence,QPainter,QShortcut
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QLineEdit,QTableWidget,QTableWidgetItem,QHeaderView,QPlainTextEdit,QFileDialog,QCheckBox,QDialog,QFormLayout,QMessageBox,
                               QComboBox,QInputDialog,QMenu,QFrame,QToolButton,QButtonGroup,QListWidget,QListWidgetItem,QSizePolicy,QGridLayout,QGraphicsOpacityEffect,QAbstractItemView,QWidgetAction,QScrollArea)
from app import Job,apply_theme
from core import scan,installations,shared_roots
from live_names import NameSession,read_live,UNSUPPORTED
from ae_ui import refresh_effects
from runtime import data_root,file_version,write_json_atomic
from settings_store import Settings
from llm_client import translate_names,TranslationError,PROVIDERS,list_models
from parameter_names import ParameterSession,identity,split_identity,validate_label
from effect_instances import EffectInstanceSession
from ae_profiles import is_supported,supported_text
import cep_bridge,release_notes,ui_kit,token_usage,updater
from PySide6.QtWidgets import QStackedWidget
from shortcuts import Keymap,ShortcutDialog,PAGES,local_shortcut
from ui_kit import confirm,choose,inform,ask_text,SchemeDelegate,SchemeRow,SchemeSwitcher,CheckHeader,CheckDragger,TrashDelegate,check_state
from ui_kit import (SHADOW,STATUS_MARK,Banner,DiamondDelegate,Breather,EmptyState,FadeStack,Legend,LoaderButton,MetricTile,UsageTile,NavRail,Pill,SearchField,Spinner,StatusTag,Surface,
                    button,divider,elastic,fade_in,icon,label,logo,menu_button,page_head,repolish,soft_path,table_style)

APP_VERSION='1.1.5'
APP_NAME='AE汉化工具'
# Installed copies: the current folder name, and the folder older versions used (an upgrade keeps it).
INSTALL_FOLDERS=('AE汉化工具','AE插件名称汉化')
# Shown on the settings page; wording chosen by the user.
THANKS='AE 效果名称实时汉化的可行性最早由「帧绘剪工作室」验证跑通。本软件在此基础上独立实现，谨此致谢。'
# Shown under the acknowledgement: what users should not expect.
LIMITS=('· 中文只写在正在运行的 AE 里，不改插件文件：AE 重启后恢复原名，需打开本软件连接 AE 重新应用（效果名称可自动）。\n'
        '· 参数名称每次都要在参数页读取后应用，不会自动重新应用。\n'
        '· 只有已验证的 AE 构建能实时汉化；其他版本需先在“AE 版本”页自动识别与适配。\n'
        '· 下拉菜单选项和插件自绘的界面无法汉化；参数名称最多约 10 个汉字，效果名称约 15 个汉字。\n'
        '· “效果和预设”列表要手动刷新才显示新名称；图层上的效果标题随工程保存。')
CHECK,ORIGINAL,CANDIDATE,CURRENT,KEY,CATEGORY,STATUS=range(7)
SUPPORTED_BUILD='26.0.0.67'

def _under(path,root)->bool:
    path,root=(os.path.normcase(os.path.abspath(x)) for x in (path,root))
    return path==root or path.startswith(root.rstrip(os.sep)+os.sep)

class VersionSpace:
    """One installed AE version: its plugin folder and its own candidates, checks and schemes."""
    def __init__(self,key:str,label:str,version:str,plugins:Path|None,live:bool,folder:Path,exe:Path|None=None):
        self.key=key;self.label=label;self.version=version;self.plugins=plugins;self.live=live;self.folder=folder;self.extra:list[Path]=[]
        self.exe=exe
        self.map_path=folder/'translations.json';self.enabled_path=folder/'scheme-enabled.json'
        self.scheme_folder=folder/'schemes';self.active_path=folder/'scheme-active.json'
    def roots(self)->list[Path]:return [r for r in (self.plugins,*self.extra) if r]
    def owns(self,path)->bool:return any(_under(path,root) for root in self.roots())
    def item_text(self,count:int|None=None)->str:
        return f"{self.label} · {self.version}"+(f' · {count} 个' if count is not None else '')+('' if self.live else '（仅翻译）')

def discover_spaces(installs:list[dict],version_of,root:Path)->list[VersionSpace]:
    """The supported build keeps the original data files; other versions get versions/<name>/."""
    spaces=[]
    for item in installs:
        try:version=str(version_of(Path(item['exe'])))
        except Exception:version='版本未知'
        live=is_supported(version)
        key=item.get('key',item['name'])
        folder=root if version==SUPPORTED_BUILD and not any(s.version==SUPPORTED_BUILD for s in spaces) else root/'versions'/(re.sub(r'[^0-9A-Za-z]+','_',key).strip('_') or 'AE')
        spaces.append(VersionSpace(key,item['name'].replace('Adobe After Effects','AE').strip() or item['name'],version,Path(item['plugins']),live,folder,Path(item['exe'])))
    return spaces or [VersionSpace('default','AE','未检测到安装',None,False,root)]
SCHEME_FORMAT='ae-localizer-scheme'

class CandidateStore:
    """Candidate names (or, with keys=True, the set of identities to localize) on disk.
    A malformed file is copied aside before anything may overwrite it."""
    def __init__(self,path:Path,keys=False):
        self.path=path;self.keys=keys;self.values=set() if keys else {};self.unsafe=False
        try:value=json.loads(path.read_text(encoding='utf8'))
        except FileNotFoundError:return
        except (OSError,ValueError):value=None
        if keys:
            if isinstance(value,list):self.values={k for k in value if isinstance(k,str)}
            self.unsafe=not isinstance(value,list) or len(self.values)!=len(value)
            return
        if isinstance(value,dict):self.values={k:v for k,v in value.items() if isinstance(k,str) and isinstance(v,str)}
        self.unsafe=not isinstance(value,dict) or len(self.values)!=len(value)
    def secure(self)->str:
        if not self.unsafe:return ''
        if not self.path.exists():self.unsafe=False;return ''
        backup=self.path.with_name(self.path.name+'.invalid-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
        try:shutil.copy2(self.path,backup)
        except OSError:return '中文候选记录格式异常，且暂时无法备份原文件；备份成功前不会覆盖它'
        self.unsafe=False;return '中文候选记录格式异常，已忽略无效内容；原文件备份为 '+backup.name
    def save(self)->str:
        message=self.secure()
        if self.unsafe:return message
        write_json_atomic(self.path,sorted(self.values) if self.keys else self.values)
        return message

def entries(names:dict[str,str],enabled:set[str])->dict:
    return {k:{'name':names.get(k,''),'enabled':k in enabled} for k in sorted(set(names)|enabled)}

def dump_scheme(names:dict[str,str],enabled:set[str],title:str|None=None,saved:datetime|None=None,parameters:tuple[dict[str,str],set[str]]|None=None)->dict:
    """Effects always; parameters only when given. Older versions ignore the extra key."""
    value={'format':SCHEME_FORMAT,'schemaVersion':1}
    if title is not None:value.update(name=title,savedAt=(saved or datetime.now()).isoformat(timespec='seconds'))
    value['effects']=entries(names,enabled)
    if parameters is not None:value['parameters']=entries(*parameters)
    return value

class SavedScheme(NamedTuple):
    path:Path;title:str;names:dict[str,str];enabled:set[str];saved:datetime
    # None: saved before schemes carried parameter labels; loading it leaves them alone.
    parameter_names:dict[str,str]|None=None;parameter_enabled:set[str]|None=None
    @property
    def parameters(self)->tuple[dict[str,str],set[str]]|None:
        return None if self.parameter_names is None else (self.parameter_names,self.parameter_enabled or set())

def read_entries(section,what)->tuple[dict[str,str],set[str]]:
    names={};enabled=set()
    for key,entry in section.items():
        if not isinstance(entry,dict) or not isinstance(entry.get('name',''),str) or not isinstance(entry.get('enabled',False),bool):
            raise ValueError(f'翻译方案中的{what}条目格式错误：'+str(key))
        if entry.get('name'):names[key]=entry['name']
        if entry.get('enabled'):enabled.add(key)
    return names,enabled

def open_scheme(path:Path)->SavedScheme:
    try:value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as error:raise ValueError('无法读取翻译方案文件') from error
    if not isinstance(value,dict) or value.get('format')!=SCHEME_FORMAT or value.get('schemaVersion')!=1 or not isinstance(value.get('effects'),dict):
        raise ValueError('这不是本软件保存的翻译方案文件')
    names,enabled=read_entries(value['effects'],'')
    parameters=value.get('parameters')
    if parameters is not None and not isinstance(parameters,dict):raise ValueError('翻译方案中的参数部分格式错误')
    parameter_names,parameter_enabled=read_entries(parameters,'参数') if parameters is not None else (None,None)
    # Files exported before schemes had titles are named after the file.
    title=value.get('name') if isinstance(value.get('name'),str) and value['name'].strip() else Path(path).stem
    try:saved=datetime.fromisoformat(value['savedAt'])
    except (KeyError,TypeError,ValueError):saved=datetime.fromtimestamp(Path(path).stat().st_mtime)
    return SavedScheme(Path(path),title.strip(),names,enabled,saved.replace(tzinfo=None),parameter_names,parameter_enabled)

def read_scheme(path:Path)->tuple[dict[str,str],set[str]]:
    scheme=open_scheme(path);return scheme.names,scheme.enabled

def list_schemes(folder:Path)->tuple[list[SavedScheme],int]:
    """Saved schemes, newest first, and how many files in the folder were not schemes."""
    found=[];skipped=0
    for path in sorted(folder.glob('*.json')):
        try:found.append(open_scheme(path))
        except ValueError:skipped+=1
    return sorted(found,key=lambda s:s.saved,reverse=True),skipped

def clean_title(title:str)->str:
    title=' '.join(title.split())
    if not title:raise ValueError('方案名称不能为空')
    if len(title)>60:raise ValueError('方案名称最多 60 个字符')
    return title

def scheme_file(folder:Path,title:str)->Path:
    stem=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',title).rstrip('. ') or '方案'
    if stem.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:stem='_'+stem
    return folder/(stem+'.json')

def ago(moment:datetime,now:datetime|None=None)->str:
    """Short relative time for lists: 刚刚, 5 分钟前, 今天 / 昨天 14:20, then dates."""
    now=now or datetime.now();seconds=(now-moment).total_seconds()
    if 0<=seconds<60:return '刚刚'
    if 0<=seconds<3600:return f'{int(seconds//60)} 分钟前'
    days=(now.date()-moment.date()).days
    if days==0:return f'今天 {moment:%H:%M}'
    if days==1:return f'昨天 {moment:%H:%M}'
    return f'{moment:%m-%d %H:%M}' if moment.year==now.year else f'{moment:%Y-%m-%d}'

def scheme_counts(scheme:SavedScheme)->str:
    effects=len(set(scheme.names)|scheme.enabled)
    return f'{effects} 个效果'+('' if scheme.parameter_names is None else f' · {len(set(scheme.parameter_names)|(scheme.parameter_enabled or set()))} 个参数')

def changed_keys(names:dict[str,str],enabled:set[str],new_names:dict[str,str],new_enabled:set[str])->tuple[set[str],set[str]]:
    """Keys whose Chinese name, and keys whose check, differ between two states."""
    return {k for k in set(names)|set(new_names) if names.get(k,'')!=new_names.get(k,'')},enabled^new_enabled

def scheme_changes(effects:tuple[dict,set],parameters:tuple[dict,set],scheme:SavedScheme)->dict[str,int]:
    """How many entries loading the scheme would change; parameters only when the scheme has them."""
    names,checks=changed_keys(*effects,scheme.names,scheme.enabled);result={'效果中文':len(names),'效果勾选':len(checks)}
    if scheme.parameters is not None:
        names,checks=changed_keys(*parameters,*scheme.parameters);result.update({'参数中文':len(names),'参数勾选':len(checks)})
    return result

def describe_changes(changes:dict[str,int])->str:
    parts=[f'{name} {count} 项' for name,count in changes.items() if count]
    return '将替换：'+'、'.join(parts) if parts else '与当前表格相同'

def duplicate_names(names:dict[str,str],originals:dict[str,str]|None=None)->dict[str,list[str]]:
    """Chinese names used by more than one effect; AE would list them identically. Effects that already
    share one original name (BCC's effect and Premiere copy of a transition) may share a translation too."""
    users={}
    for key,value in names.items():
        if value.strip():users.setdefault(value.strip(),[]).append(key)
    originals=originals or {}
    def same(keys):return all(key in originals for key in keys) and len({originals[key].casefold() for key in keys})==1
    return {value:sorted(keys,key=str.casefold) for value,keys in users.items() if len(keys)>1 and not same(keys)}

def next_title(schemes:list[SavedScheme])->str:
    taken={s.title.casefold() for s in schemes};number=1
    while f'方案 {number}'.casefold() in taken:number+=1
    return f'方案 {number}'

def record_usage(tokens):
    """Adds a translation reply's tokens to the running totals (called from worker threads)."""
    token_usage.record(tokens,data_root())

def bilingual_name(english:str,chinese:str)->str:
    """'English (中文)' when it fits AE's 47-byte name field; the Chinese alone otherwise, or when it already carries the English."""
    english=english.strip();chinese=chinese.strip()
    if not english or not chinese or chinese.casefold().startswith(english.casefold()):return chinese
    both=f'{english} ({chinese})'
    return both if len(both.encode('utf8'))<=47 else chinese

write_json=write_json_atomic  # settings, schemes and the remembered version

def brief(mapping:dict[str,str],limit=10)->str:
    items=list(mapping.items())
    return '、'.join(f'{k} → {v}' for k,v in items[:limit])+(f' 等共 {len(items)} 项' if len(items)>limit else '')

def with_live(operation,expected_image=None):
    try:return operation,(read_live(expected_image=expected_image) if expected_image else read_live()),''
    except Exception as error:return operation,None,str(error)

class ModelBox(QComboBox):
    """A model drop-down that says when its list is about to open, so the dialog can fetch the service's list first."""
    opening=Signal()
    def showPopup(self):self.opening.emit();super().showPopup()

class ApiDialog(QDialog):
    """Chooses the translation service; each service keeps its own address, model and key."""
    def __init__(self,settings,parent):
        super().__init__(parent);self.settings=settings;self.job=None;self.setWindowTitle('翻译模型设置');self.setMinimumWidth(600)
        self.profiles={key:dict(value) for key,value in settings.profiles.items()};self.service=settings.service
        outer=QVBoxLayout(self);outer.setContentsMargins(20,22,20,18);outer.setSpacing(6)
        head=QVBoxLayout();head.setContentsMargins(SHADOW,0,SHADOW,0);head.setSpacing(2)
        head.addWidget(label('翻译模型','pageTitle'));head.addWidget(label('名称翻译和可选的 AI 诊断使用这里选择的服务。每个服务分别记住自己的地址、模型和密钥。','pageNote',True));outer.addLayout(head)
        surface=Surface(padding=(20,18,20,16));layout=QFormLayout();layout.setSpacing(12);layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter);surface.box.addLayout(layout)
        self.services=QComboBox()
        for key,service in PROVIDERS.items():self.services.addItem(service.name,key)
        self.base=QLineEdit();self.model=ModelBox();self.model.setEditable(True);self.model.setToolTip('可从列表选择，也可直接输入服务商提供的模型名称；第一次展开时会自动向服务商获取可用模型')
        self.model.opening.connect(self.auto_fetch);self.fetch_job=None;self.fetched=getattr(settings,'fetched_models',{});settings.fetched_models=self.fetched
        self.fetch_btn=LoaderButton('');self.fetch_btn.setObjectName('icon');ui_kit.themed_icon(self.fetch_btn,'refresh','INK_2',16);self.fetch_btn.setToolTip('从服务商获取可用模型列表（只发送接口地址和 API Key）');self.fetch_btn.clicked.connect(lambda:self.fetch_models())
        model_row=QHBoxLayout();model_row.setSpacing(8);model_row.addWidget(self.model,1);model_row.addWidget(self.fetch_btn)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.link=QLabel();self.link.setOpenExternalLinks(True);self.hint=label('','muted',True)
        self.remember=QCheckBox('在此 Windows 用户下加密保存密钥');self.remember.setChecked(settings.remember)
        layout.addRow(label('服务商','field'),self.services);layout.addRow('',self.hint);layout.addRow(label('接口地址','field'),self.base);layout.addRow(label('模型','field'),model_row)
        layout.addRow(label('API Key','field'),self.key);layout.addRow('',self.link);layout.addRow('',self.remember);outer.addWidget(surface)
        note=label('默认只在本次软件会话中使用密钥。名称翻译发送所选名称、内部标识和分类；“AI 诊断原因”另外发送模块符号及限定反汇编证据。两者均不发送工程、画面或参数值。','muted',True)
        note.setContentsMargins(SHADOW,0,SHADOW,0);outer.addWidget(note)
        self.error=label('','field',True);self.error.setContentsMargins(SHADOW,4,SHADOW,4);outer.addWidget(self.error)
        foot=QHBoxLayout();foot.setContentsMargins(SHADOW,6,SHADOW,0)
        self.test_btn=LoaderButton('测试连接');self.test_btn.setToolTip('用一个示例名称（Gaussian Blur）实际请求一次所选服务');self.test_btn.clicked.connect(self.test)
        cancel=button('取消');cancel.clicked.connect(self.reject);save=button('保存','primary');save.clicked.connect(self.save);save.setDefault(True)
        foot.addWidget(self.test_btn);foot.addStretch();foot.addWidget(cancel);foot.addWidget(save);outer.addLayout(foot)
        self.services.currentIndexChanged.connect(self.switch);self.show_service()
    def store(self):
        self.profiles[self.service].update(base_url=self.base.text().strip(),model=self.model.currentText().strip(),key=self.key.text().strip())
    def switch(self,index):
        self.store();self.service=self.services.itemData(index);self.show_service()
    def show_service(self):
        service=PROVIDERS[self.service];profile=self.profiles[self.service]
        self.services.blockSignals(True);self.services.setCurrentIndex(self.services.findData(self.service));self.services.blockSignals(False)
        self.base.setText(profile['base_url']);self.base.setPlaceholderText(service.base_url or 'https://服务商地址/v1')
        self.fill_models()
        self.key.setText(profile['key']);self.key.setEnabled(service.needs_key);self.key.setPlaceholderText('API Key' if service.needs_key else '本机服务不需要密钥')
        self.link.setText(f'<a style="color:{ui_kit.INK}" href="{service.key_page}">获取 {service.name} API Key</a>' if service.key_page else '')
        self.hint.setText(service.note);self.hint.setVisible(bool(service.note));self.error.clear()
    def fill_models(self):
        """The service's preset models, or the list it returned when asked; the typed model is always kept."""
        service=PROVIDERS[self.service];models=self.fetched.get(self.service) or list(service.models);current=self.profiles[self.service]['model']
        self.model.blockSignals(True);self.model.clear();self.model.addItems(models);self.model.setCurrentText(current);self.model.blockSignals(False)
    def auto_fetch(self):
        """First time the list opens for a service: ask the service, if it can be asked without a missing key."""
        service=PROVIDERS[self.service]
        if self.service in self.fetched or self.fetching():return
        if service.needs_key and not self.key.text().strip():return
        if not self.base.text().strip():return
        self.fetch_models(auto=True)
    def fetching(self):return self.fetch_job is not None and self.fetch_job.isRunning()
    def fetch_models(self,auto=False):
        if self.fetching():return
        self.store();service=self.service;profile=dict(self.profiles[service])
        self.error.setText(f'正在从 {PROVIDERS[service].name} 获取模型列表……');self.fetch_btn.start('orbit','')
        self.fetch_job=Job(lambda progress:list_models(service,profile['base_url'],profile['key']))
        self.fetch_job.result.connect(lambda models:self.fetched_models(service,models,auto))
        self.fetch_job.failure.connect(lambda message:self.error.setText('获取模型列表失败：'+message+'；仍可直接输入模型名称'))
        self.fetch_job.finished.connect(self.fetch_btn.stop);self.fetch_job.start()
    def fetched_models(self,service,models,auto):
        if not models:self.error.setText('服务商没有返回可用模型；仍可直接输入模型名称');return
        self.fetched[service]=models
        if service!=self.service:return
        self.store();was_open=self.model.view().isVisible();self.fill_models()
        self.error.setText(f'已获取 {len(models)} 个可用模型'+('' if self.model.currentText() in models else f'；当前填写的 {self.model.currentText()} 不在列表中，请确认'))
        if was_open:self.model.hidePopup();self.model.showPopup()
    def busy(self):return (self.job is not None and self.job.isRunning()) or self.fetching()
    def save(self):
        if self.busy():self.error.setText('请等待连接测试或模型列表获取完成');return
        self.store()
        try:
            self.settings.profiles={key:dict(value) for key,value in self.profiles.items()};self.settings.service=self.service;self.settings.remember=self.remember.isChecked()
            self.settings.save();self.accept()
        except Exception as error:self.error.setText(str(error))
    def test(self):
        if self.busy():return
        self.store();service=self.service;profile=dict(self.profiles[service])
        sample=[{'id':'test','sourceName':'Gaussian Blur','category':'Blur & Sharpen'}]
        self.error.setText('正在测试连接……');self.test_btn.start('breathe','测试中')
        self.job=Job(lambda progress:translate_names(sample,profile['key'],profile['base_url'],profile['model'],service=service,usage=record_usage))
        self.job.result.connect(lambda names:self.error.setText(f"连接成功：Gaussian Blur → {names['test']}"))
        self.job.failure.connect(lambda message:self.error.setText('测试失败：'+message));self.job.finished.connect(self.test_btn.stop);self.job.start()
    def reject(self):
        # The test worker must finish before the dialog (its parent) is destroyed.
        if self.busy():self.error.setText('请等待连接测试或模型列表获取完成');return
        super().reject()

EFFECTS,PARAMETERS,SCHEMES,VERSIONS,SETTINGS=range(5)
MISSING='当前 AE 未加载'
ROW_FILTERS=(('all','全部'),('checked','已勾选'),('applied','已应用'),('missing','未加载'))
TILE_MARKS={'applied':'solid','missing':'dash'}

class BackgroundCheck(QObject):
    """A short read-only task (the update check) on a daemon thread: closing the window never waits for it, and
    the thread simply ends with the program. Results come back to the window's thread as signals."""
    result=Signal(object);failure=Signal(str);finished=Signal()
    def __init__(self,work,parent=None):
        super().__init__(parent);self.work=work;self.thread=threading.Thread(target=self._run,daemon=True)
    def start(self):self.thread.start()
    def isRunning(self)->bool:return self.thread.is_alive()
    def wait(self,ms)->bool:self.thread.join(ms/1000);return not self.thread.is_alive()
    def _run(self):
        try:value=self.work()
        except Exception as error:self._emit(self.failure,str(error))
        else:self._emit(self.result,value)
        self._emit(self.finished)
    @staticmethod
    def _emit(signal,*args):
        # The window may be gone by the time a check ends; there is then no one to tell.
        try:signal.emit(*args)
        except RuntimeError:pass

class Toast(QLabel):
    """A one-line notice floating over the status line, where it covers no control.
    It rises in, hides itself or on click; the full text stays in its tooltip, the status line and the log."""
    def __init__(self,parent,status:QLabel):
        super().__init__(parent);self.setObjectName('toast');self.setWordWrap(False);self.full='';self.status=status;self.hide();self.motion=None
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.dismiss)
    def notify(self,title:str,detail:str='',error=False):
        self.setProperty('error',error);self.style().unpolish(self);self.style().polish(self)
        self.full=title+('：'+detail if detail else '');self.setToolTip(self.full+'\n（点击关闭）')
        self.place();self.show();self.raise_();self.cover(True)
        if ui_kit.motion():
            effect=QGraphicsOpacityEffect(self);self.setGraphicsEffect(effect);end=self.pos()
            ui_kit.animate(effect,b'opacity',1.0,180,QEasingCurve.Type.OutCubic,0.0)
            self.motion=ui_kit.animate(self,b'pos',end,320,QEasingCurve.Type.OutBack,end+QPoint(0,14))
        self.timer.start(7000 if error else 3500)
    def place(self):
        area=self.parentWidget().toast_area()
        self.setText(self.full);self.adjustSize();room=self.width()-self.fontMetrics().horizontalAdvance(self.full)
        if self.width()>area.width():
            self.setText(self.fontMetrics().elidedText(self.full,Qt.TextElideMode.ElideRight,area.width()-room));self.adjustSize()
        self.move(area.center().x()-self.width()//2,area.bottom()-self.height())
    def dismiss(self):
        self.timer.stop()
        if ui_kit.motion() and self.isVisible():
            effect=QGraphicsOpacityEffect(self);self.setGraphicsEffect(effect)
            ui_kit.animate(effect,b'opacity',0.0,180,QEasingCurve.Type.InCubic,1.0,self._hidden)
        else:self._hidden()
    def _hidden(self):
        if self.timer.isActive():return
        self.hide();self.setGraphicsEffect(None);self.cover(False)
    def mousePressEvent(self,event):self.dismiss()
    def paintEvent(self,event):
        # A painted pill: stylesheet radii are not applied to label backgrounds on every platform.
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ui_kit.DANGER if self.property('error') else ui_kit.INK));painter.drawRoundedRect(QRectF(self.rect()),self.height()/2,self.height()/2);painter.end()
        super().paintEvent(event)
    def cover(self,covered):
        # The status line repeats the notice underneath; it stays blank while the notice covers it.
        self.status.setStyleSheet('color:transparent;' if covered else '')

def inset(widget)->QWidget:
    """Aligns a widget without a shadow to the paper edges of neighbouring surfaces."""
    holder=QWidget();row=QHBoxLayout(holder);row.setContentsMargins(SHADOW,0,SHADOW,0);row.setSpacing(0);row.addWidget(widget);return holder

P_CHECK,P_NAME,P_CHINESE,P_TABLE,P_TRASH=range(5)  # columns of the scheme page's effects preview
Q_CHECK,Q_EFFECT,Q_NAME,Q_CHINESE,Q_TABLE,Q_TRASH=range(6)  # and of its parameters preview
PART_WORD={'effects':'效果','parameters':'参数'}

class EffectPicker(QDialog):
    """Chooses effects to add to a scheme. Effects that already have a Chinese name (in the table,
    another scheme or AE itself) show it with its source and bring it along when added."""
    def __init__(self,parent,rows,what='效果'):
        super().__init__(parent);self.setObjectName('prompt');self.setWindowTitle(f'添加{what}到方案');self.resize(720,640)
        col=QVBoxLayout(self);col.setContentsMargins(28,24,28,22);col.setSpacing(10)
        col.addWidget(label(f'添加{what}到方案','promptTitle'))
        intro=('列出此 AE 版本中、方案里还没有的效果。' if what=='效果' else '列出本机记录过的参数（读取过或翻译过的）中、方案里还没有的。')
        col.addWidget(label(intro+f'已有中文名称的{what}显示名称和来源，加入时一并带上；加入后默认勾选，之后可在方案页修改。','promptText',True))
        tools=QHBoxLayout();tools.setSpacing(14);field=SearchField('搜索名称、中文或内部标识');self.search=field.edit;self.search.textChanged.connect(self.filter);tools.addWidget(field,1)
        self.translated_only=QCheckBox('只看已有中文的');self.translated_only.setObjectName('check');self.translated_only.toggled.connect(self.filter);tools.addWidget(self.translated_only);col.addLayout(tools)
        self.table=QTableWidget(0,4);self.header=CheckHeader(self.table,self.toggle_visible);self.table.setHorizontalHeader(self.header)
        self.table.setHorizontalHeaderLabels(['',what,'中文名称','来源']);table_style(self.table,34);self.what=what
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for column,mode in enumerate((QHeaderView.ResizeMode.Fixed,QHeaderView.ResizeMode.Stretch,QHeaderView.ResizeMode.Stretch,QHeaderView.ResizeMode.Fixed)):self.table.horizontalHeader().setSectionResizeMode(column,mode)
        self.table.setColumnWidth(0,44);self.table.setColumnWidth(3,150);self.table.horizontalHeaderItem(0).setToolTip(f'勾选或取消当前列出的{what}；按住复选框上下拖动可快速勾选')
        self.filling=True;self.table.setRowCount(len(rows))
        for row,(key,display,name,source) in enumerate(rows):
            check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(Qt.CheckState.Unchecked);check.setData(Qt.ItemDataRole.UserRole,key);self.table.setItem(row,0,check)
            for column,text in ((1,display),(2,name),(3,source)):
                item=QTableWidgetItem(text);item.setFlags(Qt.ItemFlag.ItemIsEnabled);item.setToolTip(f'内部标识：{key}')
                if column==3:item.setForeground(QColor(ui_kit.MUTED))
                self.table.setItem(row,column,item)
        self.filling=False;self.translated=sum(1 for row in rows if row[2])
        self.table.itemChanged.connect(lambda item:item.column()==0 and not self.filling and self.update_count())
        self.table.cellClicked.connect(lambda row,column:column and self.set_rows([row],not self.is_checked(row)))
        self.dragger=CheckDragger(self.table,0,self.is_checked,lambda rows,state:self.set_rows(rows,state,False),lambda rows,state:self.update_count())
        col.addWidget(self.table,1);self.none_note=label(f'没有已有中文的{what}：本机各 AE 版本的表格和方案里都没有它们的中文。取消“只看已有中文的”即可选择全部，加入后在方案页填写中文。','muted',True);self.none_note.hide();col.addWidget(self.none_note)
        bottom=QHBoxLayout();bottom.setSpacing(8);self.count=label('','muted');bottom.addWidget(self.count);bottom.addStretch()
        cancel=button('取消');cancel.clicked.connect(self.reject);bottom.addWidget(cancel)
        self.ok=button('加入','primary');self.ok.clicked.connect(self.accept);bottom.addWidget(self.ok);col.addLayout(bottom)
        self.translated_only.setChecked(bool(self.translated));self.translated_only.setToolTip('来源：当前表格、本版本及其他 AE 版本的方案和中文候选、连接时 AE 显示的名称');self.filter();self.search.setFocus()
    def is_checked(self,row)->bool:return self.table.item(row,0).checkState()==Qt.CheckState.Checked
    def set_rows(self,rows,state,count=True):
        self.filling=True
        for row in rows:self.table.item(row,0).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        self.filling=False
        if count:self.update_count()
    def visible(self)->list[int]:return [r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]
    def toggle_visible(self):
        rows=self.visible()
        if rows:self.set_rows(rows,not all(self.is_checked(r) for r in rows))
    def filter(self,*_):
        query=self.search.text().casefold().strip();only=self.translated_only.isChecked()
        for row in range(self.table.rowCount()):
            text=' '.join(self.table.item(row,c).text()+' '+self.table.item(row,c).toolTip() for c in range(1,4)).casefold()
            self.table.setRowHidden(row,(bool(query) and query not in text) or (only and not self.table.item(row,2).text()))
        self.update_count()
    def chosen(self)->list[str]:
        return [self.table.item(r,0).data(Qt.ItemDataRole.UserRole) for r in range(self.table.rowCount()) if self.is_checked(r)]
    def update_count(self):
        count=len(self.chosen());self.ok.setText(f'加入 {count} 项' if count else '加入');self.ok.setEnabled(bool(count))
        self.header.set_state(check_state(self.is_checked(r) for r in self.visible()))
        self.none_note.setVisible(self.translated_only.isChecked() and not self.translated)
        total=self.table.rowCount();self.count.setText(f'可加入 {total} 个{self.what}，其中 {self.translated} 个已有中文' if total else f'没有可加入的{self.what}')

def pick_effects(parent,rows,what='效果')->list[str]:
    """rows: (key, display name, Chinese name or '', where the name comes from)."""
    dialog=EffectPicker(parent,rows,what)
    return dialog.chosen() if dialog.exec()==QDialog.DialogCode.Accepted else []

def pick_parameters(parent,rows)->list[str]:return pick_effects(parent,rows,'参数')

class Window(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle(f'{APP_NAME} {APP_VERSION}');self.resize(1400,880);self.setMinimumSize(1200,720)
        # Someone who used an earlier version (before anything below writes a file): only they get the update notice.
        self.returning=(data_root()/'ui.json').is_file() or (data_root()/'versions').is_dir()
        self.session=NameSession();self.settings=Settings();self.live=None;self.job=None;self.plugins=[];self.pending={};self._fill=False;self.close_after=False
        self.schemes=[];self.active=None;self.preview_path=None;self.skipped_schemes=0;self.all_plugins=[];self.scan_errors=[];self.unread=0
        self.needs_refresh=False;self.writing=set();self.loading_button=None;self.next_loader=None
        # With a scheme in use the effects table shows only that scheme's effects unless show_all is on.
        self.show_all=False;self.scope_key=None
        self.load_ui_preferences()
        # Every keyboard shortcut is bound through the keymap so it can be changed or switched off while running.
        self.keymap=Keymap(data_root()/'shortcuts.json')
        installs=installations()
        from local_adapters import load_installations
        local_warnings=load_installations(installs,data_root()/'adapters')
        self.spaces=discover_spaces(installs,file_version,data_root());self.shared=shared_roots();self.version_path=data_root()/'ae-version.json'
        try:remembered_version=json.loads(self.version_path.read_text(encoding='utf8')).get('key')
        except (OSError,ValueError,AttributeError):remembered_version=None
        space=next((s for s in self.spaces if s.key==remembered_version),None) or next((s for s in self.spaces if s.live),self.spaces[0])
        load_warning=self.bind_space(space)
        self.parameter_session=ParameterSession()
        self.instance_session=EffectInstanceSession()
        self.bind_targets()
        load_warning='\n'.join(x for x in (load_warning,self.bind_parameter_stores(space)) if x)
        self.make_actions()
        body=QWidget();self.setCentralWidget(body);outer=QHBoxLayout(body);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        outer.addWidget(self.build_nav())
        main=QVBoxLayout();main.setContentsMargins(24-SHADOW,22,24-SHADOW,10);main.setSpacing(14)
        self.banner=Banner('warn','自动识别与适配…',lambda:self.analyze_adapter(self.space));self.note=self.banner.text;main.addWidget(inset(self.banner))
        self.pages=FadeStack()
        for build in (self.build_effects_page,self.build_parameters_page,self.build_schemes_page,self.build_versions_page,self.build_settings_page):self.pages.addWidget(build())
        main.addWidget(self.pages,1)
        foot=QHBoxLayout();foot.setContentsMargins(SHADOW,0,SHADOW,0);foot.setSpacing(8)
        self.progress=Spinner('arc');self.progress.hide();foot.addWidget(self.progress)
        self.status=elastic(label('就绪','statusLine'));foot.addWidget(self.status,1)
        main.addLayout(foot);outer.addLayout(main,1)
        self.toast=Toast(self,self.status)
        self.buttons=(self.version_box,self.connect_btn,self.scheme_switcher,self.check_tool,self.more_tool,self.restore_tool,self.refresh_btn,
                      self.apply_btn,self.restore_btn,self.translate_btn,self.parameters_btn,self.reveal_btn,*self.all_actions,
                      self.page_save_as_btn,self.page_import_btn,self.load_scheme_btn,self.scheme_apply_btn,self.scheme_restore_btn,self.add_effects_btn,self.delete_checked_btn,self.discard_btn,self.scheme_more,self.empty_save_btn,
                      self.paths_btn,self.scan_btn,self.use_version_btn,self.dir_btn,self.analyze_btn,self.api_btn,*self.parameter_page.controls)
        if load_warning:self.log.appendPlainText(load_warning)
        for warning in local_warnings:self.log.appendPlainText(warning)
        from ae_paths import read_saved
        _,path_warning=read_saved(data_root()/'ae-installations.json')
        if path_warning:self.log.appendPlainText(path_warning)
        self.refresh_schemes(self.remembered_scheme());self.update_version_bar();self.update_api_info()
        self.refresh_bridge()
        self.keymap.listeners.append(self.refresh_shortcut_hints);self.refresh_shortcut_hints()
        self.go(EFFECTS);self.update_buttons();QTimer.singleShot(100,self.start_scan)
        self.update_job=None;self.update_offer=None;self.pending_installer=None;self.reapplied_for=None;self.show_update_info()
        QTimer.singleShot(2500,self.auto_check_update);QTimer.singleShot(600,self.show_release_notice)
    # What is new: shown once, the first time a new version opens; the settings page offers it again.
    def show_release_notice(self):
        seen=self.ui_preferences().get('seenVersion')
        if seen==APP_VERSION:return
        entries=release_notes.newer(seen,APP_VERSION) if seen or self.returning else []
        # Remembered before showing, so the notice never comes back for this version whatever happens next.
        self.save_ui_preference('seenVersion',APP_VERSION)
        if entries:inform(self,f'{APP_NAME} {APP_VERSION} 更新内容',release_notes.html(entries))
    def show_current_notes(self):
        inform(self,f'{APP_NAME} {APP_VERSION} 更新内容',release_notes.html(release_notes.newer(None,APP_VERSION)))
    # Preferences that only concern the interface.
    def ui_preferences(self)->dict:return ui_prefs()
    def save_ui_preference(self,key,value):
        try:write_json(data_root()/'ui.json',{**self.ui_preferences(),key:value})
        except OSError:pass
    def load_ui_preferences(self):ui_kit.set_motion(self.ui_preferences().get('motion',True) is not False)
    def save_motion(self,enabled):
        ui_kit.set_motion(enabled);self.save_ui_preference('motion',bool(enabled))
    # Dark mode: off unless chosen; switches the whole interface at once, nothing is reloaded and AE is not touched.
    def set_dark_mode(self,on):
        on=bool(on);self.save_ui_preference('darkMode',on)
        if on==ui_kit.is_dark():return
        app=QApplication.instance();fading=getattr(self,'theme_fade',None)
        if fading is not None:fading.finish()
        # With motion on, a snapshot of the old look covers the window while Qt restyles every widget (~0.2 s),
        # then fades into a snapshot of the new look; the title bar changes at the midpoint.
        central=self.centralWidget();fade=ui_kit.motion() and central is not None and central.isVisible()
        cover=self.theme_fade=ui_kit.Crossfade(central,central.grab()) if fade else None
        previous=dict(ui_kit.DARK if ui_kit.is_dark() else ui_kit.LIGHT);ui_kit.set_theme(on)
        # Recolour first, before the new stylesheet lets the window repaint and refill cells with the new colours.
        ui_kit.retheme(app,previous,tint=not fade);apply_theme(app,ui_kit.stylesheet(),on)
        self.status.setText('已切换为深色模式' if on else '已切换为浅色模式')
        if cover is not None:
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)    # settle layouts under the cover
            if not cover.done:cover.start(300,lambda:ui_kit.tint_windows(app))
    # Bilingual writing: AE gets "English (中文)" instead of the Chinese alone; off unless the user turns it on.
    @property
    def bilingual(self)->bool:
        if not hasattr(self,'_bilingual'):self._bilingual=self.ui_preferences().get('bilingualNames') is True
        return self._bilingual
    def set_bilingual(self,on):
        self._bilingual=bool(on);self.save_ui_preference('bilingualNames',self._bilingual)
        # What counts as "applied" follows the chosen form, so every row is re-judged.
        self._fill=True;self.table.setSortingEnabled(False)
        for row in range(self.table.rowCount()):self.color_current(row)
        self.table.setSortingEnabled(True);self._fill=False;self.filter_rows();self.show_detail(self.selected_rows())
        self.status.setText('双语写入已开启：之后应用的效果名称为“英文原名 (中文)”' if self._bilingual else '双语写入已关闭：之后应用的效果名称只写中文')
    def target_name(self,english:str,chinese:str)->str:
        """The name a write puts into AE for this candidate."""
        return bilingual_name(english,chinese) if self.bilingual else chinese.strip()
    # Updates: the manifest comes from the public release repository; installing needs the user's yes twice
    # (download, then close and install) and an installer whose size and SHA256 match the manifest.
    def show_update_info(self,note=''):
        if not hasattr(self,'update_info'):return
        if not updater.configured():
            # No release repository yet: nothing goes online and the controls say why.
            for widget in (self.update_box,self.update_btn):widget.setEnabled(False);widget.setToolTip('更新发布地址尚未设置，发布仓库建好后启用')
            self.update_info.setText(f'当前版本 {APP_VERSION}\n更新发布地址尚未设置，暂不检查更新');return
        prefs=self.ui_preferences();checked=prefs.get('updateCheckedOn')
        text=f'当前版本 {APP_VERSION}'+(f' · 上次检查 {checked}' if isinstance(checked,str) else '')
        if self.update_offer:text+=f"\n可更新到 {self.update_offer['version']}"
        self.update_info.setText(text+(f'\n{note}' if note else ''))
    def auto_check_update(self):
        if not updater.configured() or self.ui_preferences().get('updateCheck') is False:return
        prefs=self.ui_preferences()
        if prefs.get('updateCheckedOn')==datetime.now().date().isoformat():
            # Already asked today: reuse what was found rather than going online again.
            try:offer=updater.validate(prefs['updateOffer']) if prefs.get('updateOffer') else None
            except updater.UpdateError:offer=None
            self.found_update(offer,manual=False);return
        self.check_update(manual=False)
    def check_update(self,manual=True):
        if not updater.configured() or (self.update_job is not None and self.update_job.isRunning()):return
        if manual:self.update_btn.start('orbit','检查中')
        self.update_job=job=BackgroundCheck(lambda:updater.check(APP_VERSION),self)
        job.result.connect(lambda offer:self.found_update(offer,manual,save=True))
        job.failure.connect(lambda message:self.update_failed(message,manual))
        job.finished.connect(self.update_btn.stop);job.start()
    def update_failed(self,message,manual):
        self.log.appendPlainText('检查更新未完成：'+message);self.show_update_info('检查失败：'+message)
        if manual:self.toast.notify('检查更新未完成',message,error=True)
    def found_update(self,offer,manual,save=False):
        if save:
            prefs=self.ui_preferences();prefs.update(updateCheckedOn=datetime.now().date().isoformat(),updateOffer=offer)
            try:write_json(data_root()/'ui.json',prefs)
            except OSError:pass
        if offer and not updater.is_newer(offer['version'],APP_VERSION):offer=None
        self.update_offer=offer;skipped=self.ui_preferences().get('skippedVersion')
        wanted=bool(offer) and (manual or offer['version']!=skipped)
        if wanted:
            first=offer['notes'].splitlines()[0] if offer['notes'] else ''
            self.update_notice.setText(f"有新版本 {offer['version']}")
            self.update_notice.setToolTip(f"当前 {APP_VERSION}，可更新到 {offer['version']}"+(f'：{first}' if first else '')+'\n点击查看更新说明并下载安装')
        self.update_notice.setVisible(wanted)
        if wanted and save and not manual:self.toast.notify('发现新版本',f"{offer['version']} 可以更新，点左侧“有新版本”查看");self.show_update_info()
        if manual and offer:self.offer_update(offer)
        elif manual:self.toast.notify('已是最新版本',f'当前版本 {APP_VERSION}')
    def offer_update(self,offer):
        if not offer:return
        size=offer['installer']['bytes']/1024/1024
        text=(f"新版本 {offer['version']}"+(f"（{offer['published']} 发布）" if offer['published'] else '')+f"，安装包约 {size:.1f} MB。\n\n"
              +(offer['notes'] or '这个版本没有附更新说明。')+'\n\n下载后会核对文件大小和 SHA256，核对通过才能安装。')
        picked=choose(self,f"更新到 {offer['version']}？",text,['跳过此版本','下载并安装'])
        if picked is None:return
        if picked==0:
            self.save_ui_preference('skippedVersion',offer['version']);self.update_notice.hide();self.status.setText(f"已跳过版本 {offer['version']}，可在设置页手动检查更新");return
        folder=data_root()/'updates';name=offer['installer']['name']
        def fetch(progress):
            # Older downloads are of no use once a newer installer is wanted; a part of this one is continued.
            if folder.is_dir():
                for old in folder.iterdir():
                    if old.is_file() and old.name not in (name,name+'.part',name+'.part.json'):old.unlink(missing_ok=True)
            return updater.download(offer,folder,progress=lambda done,total:progress(f'正在下载更新 {done/1048576:.1f} / {total/1048576:.1f} MB'))
        self.status.setText(f"正在下载 {offer['version']}……");self.work(fetch,lambda path:self.downloaded(offer,path),failure=lambda message:self.download_failed(offer,message))
    def download_failed(self,offer,message):
        """A failed download offers the release page, so the installer can still be fetched in a browser."""
        page=updater.release_page(offer['version'])
        self.status.setText(message);self.status.setToolTip(message);self.log.appendPlainText(f'下载更新未完成：{message}（发布页面：{page}）')
        picked=choose(self,'下载更新未完成',f"{message}\n\n也可以打开发布页面，用浏览器或下载工具下载 {offer['installer']['name']}，下载后双击运行即可安装。",['关闭','打开下载页面'])
        if picked==1:QDesktopServices.openUrl(QUrl(page))
    def downloaded(self,offer,path):
        self.log.appendPlainText(f"已下载并核对 {offer['version']} 安装包：{path.name}")
        exe=Path(sys.executable);programs=Path(os.environ.get('LOCALAPPDATA',''))/'Programs';home=programs/INSTALL_FOLDERS[0]
        portable=getattr(sys,'frozen',False) and all(exe.parent.resolve()!=(programs/name).resolve() for name in INSTALL_FOLDERS)
        note=('\n\n当前运行的不是安装版（例如便携版），安装包会装到“'+str(home)+'”，当前这份不会被替换。' if portable else '')
        if not confirm(self,f"关闭软件并安装 {offer['version']}？",'安装包已下载并核对通过。点“安装”后本软件会关闭（开启“临时翻译”时先恢复原名），然后打开安装向导。\nAE 正在运行时，安装向导会提示先关闭 AE。'+note,'安装'):
            self.status.setText(f"安装包已保存，可稍后在“查看更新”中安装：{path}");return
        self.pending_installer=path;self.close()
    def launch_installer(self):
        path,self.pending_installer=self.pending_installer,None
        try:os.startfile(str(path))
        except OSError as error:self.log.appendPlainText('无法打开安装包：'+str(error))
    # Translation token totals, as the services reported them.
    def record_usage(self,tokens):record_usage(tokens)
    def update_usage(self):
        if not hasattr(self,'usage_tile'):return
        totals=token_usage.summary(data_root());short=token_usage.compact
        self.usage_tile.set_figures(short(totals['all']),[('近 30 天',short(totals['30d'])),('近 7 天',short(totals['7d']))])
        self.usage_tile.setToolTip(f"翻译累计 {totals['all']:,} token · 近 30 天 {totals['30d']:,} · 近 7 天 {totals['7d']:,}\n"
                                   '按服务商返回的输入 + 输出 token 统计效果、参数翻译和连接测试，只记在本机；不含 AI 诊断。服务未返回用量时不计入。')
    # Layout: navigation rail (with the AE connection), one page per area, and a status line.
    def action(self,text,slot,command=None,tip=None)->QAction:
        """A window action; with a command id its key comes from the keymap (and follows later changes)."""
        action=QAction(text,self);action.triggered.connect(lambda checked=False:slot())
        if command:self.keymap.bind(command,action);self.addAction(action)
        if tip:action.setToolTip(tip);action.setStatusTip(tip)
        return action
    def press(self,widget):
        """Keyboard route to a panel button, so the key acts exactly like a click (same target, same loader)."""
        if widget.isEnabled() and widget.isVisibleTo(self) and not self.busy():widget.click()
    def make_actions(self):
        A=self.action
        # The row menu acts on its own row; the keys drive the detail panel's buttons (selected row, or the checked effects).
        self.translate_action=A('翻译此效果',self.translate_selected)
        self.translate_checked_action=A('翻译勾选项中缺少中文的效果',self.translate_checked)
        self.retranslate_checked_action=A('重新翻译全部勾选项',lambda:self.translate_checked(overwrite=True),tip='已有中文候选的勾选项也会重新翻译并覆盖')
        self.api_action=A('翻译模型设置…',self.configure_api)
        self.apply_selected_action=A('应用此效果的名称',self.apply_selected)
        self.restore_selected_action=A('恢复此效果原名',self.restore_selected)
        self.restore_checked_action=A('恢复勾选项原名',self.restore_checked)
        self.restore_all_action=A('一键恢复全部默认（含参数）',self.restore_all,'restore_all',tip='把本软件改过的所有效果和参数名称恢复为 AE 原名；方案和勾选保留')
        self.refresh_action=A('刷新 AE“效果和预设”列表',self.refresh_native,'refresh')
        self.check_filtered_action=A('勾选当前筛选结果',lambda:self.check_visible(True),'check_filtered')
        self.uncheck_filtered_action=A('取消勾选当前筛选结果',lambda:self.check_visible(False))
        self.uncheck_all_action=A('取消全部勾选',lambda:self.check_keys(set(self.enabled),False),'uncheck_all',tip='包括被筛选隐藏的效果')
        self.save_scheme_action=A('保存更改到当前方案',self.save_scheme,'save_scheme')
        self.save_as_action=A('新建方案…',self.new_scheme_from_checks,'save_as')
        self.import_action=A('从文件导入方案…',self.import_scheme,'import')
        self.manage_action=A('管理全部方案…',lambda:self.go(SCHEMES))
        self.no_scheme_action=A('不使用方案',lambda:self.choose_scheme(None),tip='保留当前表格，只是不再与任何方案关联')
        self.show_all_action=A('显示全部效果',lambda:self.set_show_all(self.show_all_action.isChecked()),'show_all',tip='关闭时“效果名称”页只列出当前方案中的效果；打开后列出全部效果，未加入方案的变灰，仍可勾选和编辑')
        self.show_all_action.setCheckable(True)
        self.find_action=A('搜索效果',lambda:(self.go(EFFECTS),self.filter.setFocus(),self.filter.selectAll()),'find')
        A('连接 / 重读 AE',lambda:self.press(self.connect_btn),'connect')
        A('翻译所选项 / 勾选项',lambda:self.press(self.translate_btn),'translate')
        A('应用所选项 / 勾选项',lambda:self.press(self.apply_btn),'apply')
        A('恢复所选项 / 勾选项原名',lambda:self.press(self.restore_btn),'restore')
        for index in range(len(PAGES)):A(PAGES[index],lambda i=index:self.go(i),f'page{index}')
        self.all_actions=(self.translate_action,self.translate_checked_action,self.retranslate_checked_action,self.api_action,self.apply_selected_action,
                          self.restore_selected_action,self.restore_checked_action,self.restore_all_action,self.refresh_action,self.check_filtered_action,self.uncheck_filtered_action,self.uncheck_all_action,
                          self.save_scheme_action,self.save_as_action,self.import_action,self.no_scheme_action)
    def build_nav(self):
        rail=NavRail();rail.setFixedWidth(232);self.nav=rail;col=rail.col
        brand=QHBoxLayout();brand.setContentsMargins(8,0,0,14);brand.setSpacing(10);mark=ui_kit.themed_logo(QLabel(),30);brand.addWidget(mark)
        names=QVBoxLayout();names.setSpacing(0);names.addWidget(label('AE插件汉化','brand'));names.addWidget(label(f'After Effects · {APP_VERSION}','brandSub'));brand.addLayout(names);brand.addStretch();col.addLayout(brand)
        self.nav_group=QButtonGroup(self);self.nav_buttons=[]
        for section,items in (('汉化',(('效果名称','effects',EFFECTS),('参数名称','params',PARAMETERS),('翻译方案','schemes',SCHEMES))),('环境',(('AE 版本','versions',VERSIONS),('设置与日志','settings',SETTINGS)))):
            rail.add_section(section)
            for text,glyph,index in items:
                item=rail.add_item(text,glyph);self.nav_group.addButton(item,index);self.nav_buttons.append(item)
        col.addStretch()
        # A found update waits here, where it takes no room from the pages; clicking shows the details.
        self.update_notice=button('','',icon_name='info');self.update_notice.clicked.connect(lambda:self.offer_update(self.update_offer));self.update_notice.hide()
        col.addWidget(self.update_notice);col.addSpacing(8)
        host=QFrame();host.setObjectName('hostCard');host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True);box=QVBoxLayout(host);box.setContentsMargins(12,12,12,12);box.setSpacing(8)
        top=QHBoxLayout();top.addWidget(label('After Effects','hostTitle'));top.addStretch();self.pill=Pill('未连接');top.addWidget(self.pill);box.addLayout(top)
        self.version_box=QComboBox();self.version_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon);self.version_box.setMinimumContentsLength(8)
        self.version_box.view().setMinimumWidth(340);self.version_box.setToolTip('只显示和更改所选 AE 版本的插件；中文候选、勾选和方案按版本分开保存');self.version_box.activated.connect(self.choose_version);box.addWidget(self.version_box)
        self.connect_btn=LoaderButton('连接 AE','primary');self.connect_btn.clicked.connect(lambda:self.run_with(self.connect_btn,'orbit','连接中',self.connect_live));box.addWidget(self.connect_btn)
        col.addWidget(host);col.addSpacing(8)
        self.nav_foot=label('','navFoot',True);self.nav_foot.setContentsMargins(8,0,8,0);col.addWidget(self.nav_foot)
        self.nav_group.idClicked.connect(self.go)
        return rail
    def build_effects_page(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        head,row,self.summary=page_head('效果名称','正在发现插件目录……')
        self.scheme_switcher=SchemeSwitcher();self.scheme_switcher.menu().aboutToShow.connect(self.scheme_menu)
        # Kept across rebuilds of the switcher menu; filled each time it opens.
        self.add_checked_menu=QMenu('把勾选项加入方案',self.scheme_switcher.menu());self.add_checked_menu.aboutToShow.connect(lambda:self.fill_add_menu(self.add_checked_menu,sorted(self.enabled,key=str.casefold)));row.addWidget(self.scheme_switcher);row.addSpacing(8)
        self.restore_tool=menu_button('恢复',[self.restore_checked_action,None,self.restore_all_action],icon_name='restore');row.addWidget(self.restore_tool)
        self.more_tool=menu_button('更多',[('翻译',[self.translate_checked_action,self.retranslate_checked_action,None,self.api_action]),None,self.refresh_action]);row.addWidget(self.more_tool)
        self.refresh_btn=button('','icon');ui_kit.themed_icon(self.refresh_btn,'refresh','INK_2',16);self.refresh_btn.clicked.connect(self.refresh_native);row.addWidget(self.refresh_btn)
        layout.addWidget(inset(head))
        tiles=QWidget();tile_row=QHBoxLayout(tiles);tile_row.setContentsMargins(0,0,0,0);tile_row.setSpacing(12)
        self.chip_group=QButtonGroup(self);self.chips={}
        for index,(key,text) in enumerate(ROW_FILTERS):
            tile=MetricTile(text,TILE_MARKS.get(key));tile.setToolTip(f'只看“{text}”的效果');self.chip_group.addButton(tile,index);self.chips[key]=tile;tile_row.addWidget(tile,2)
        # The last tile only reports what translation has cost so far; it filters nothing, and is wider so its three figures fit at 1200 px.
        self.usage_tile=UsageTile('翻译 token 用量');tile_row.addWidget(self.usage_tile,3);self.update_usage()
        self.chips['all'].setChecked(True);self.chip_group.idClicked.connect(lambda _:self.filter_rows());layout.addWidget(inset(tiles))
        split=QHBoxLayout();split.setSpacing(0)
        table_card=Surface(padding=(14,12,14,6),spacing=8)
        bar=QHBoxLayout();bar.setSpacing(8)
        search=SearchField('搜索名称、内部标识或分类');self.filter=search.edit;self.filter.textChanged.connect(self.filter_rows);bar.addWidget(search,1)
        self.check_tool=menu_button('勾选',[self.check_filtered_action,self.uncheck_filtered_action,None,self.uncheck_all_action]);self.check_tool.setObjectName('quiet');bar.addWidget(self.check_tool)
        table_card.box.addLayout(bar)
        self.table=QTableWidget(0,7);self.check_header=CheckHeader(self.table,self.toggle_visible);self.table.setHorizontalHeader(self.check_header)
        self.table.setHorizontalHeaderLabels(['','原始名称','中文候选（可编辑）','AE 当前名称','内部标识','分类','状态']);table_style(self.table);self.check_anchor=None
        self.table.horizontalHeaderItem(CHECK).setToolTip('勾选的效果组成当前方案；批量翻译、应用、恢复只作用于勾选项。\n点这里勾选或取消当前筛选结果；按住行内复选框上下拖动可快速勾选，Shift+点击可连续勾选')
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows);self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked|QAbstractItemView.EditTrigger.EditKeyPressed|QAbstractItemView.EditTrigger.SelectedClicked)
        header=self.table.horizontalHeader();header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive);header.setStretchLastSection(True);header.moveSection(STATUS,1)
        for column,width in ((CHECK,52),(STATUS,100),(ORIGINAL,150),(CANDIDATE,150),(CURRENT,140),(KEY,170),(CATEGORY,120)):self.table.setColumnWidth(column,width)
        self.status_delegate=DiamondDelegate(self.table,lambda index:bool(self.writing) and self.table.item(index.row(),KEY) is not None and self.table.item(index.row(),KEY).text() in self.writing)
        self.table.setItemDelegateForColumn(STATUS,self.status_delegate);self.breather=Breather(self.table,self.status_delegate)
        self.table.itemSelectionChanged.connect(self.selection_changed);self.table.itemChanged.connect(self.item_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.table.customContextMenuRequested.connect(self.table_menu)
        self.dragger=CheckDragger(self.table,CHECK,lambda row:self.table.item(row,KEY).text() in self.enabled,self.paint_checks,self.finish_checks)
        self.table_stack=QStackedWidget();self.table_empty=EmptyState('effects');self.table_stack.addWidget(self.table_empty);self.table_stack.addWidget(self.table)
        self.empty_paths_btn=self.table_empty.add_button(button('检测 / 定位 AE…'));self.empty_paths_btn.clicked.connect(self.open_ae_paths)
        self.empty_clear_btn=self.table_empty.add_button(button('清除筛选'));self.empty_clear_btn.clicked.connect(self.clear_filters);self.empty_clear_btn.hide()
        self.empty_all_btn=self.table_empty.add_button(button('显示全部效果','primary'));self.empty_all_btn.clicked.connect(lambda:self.set_show_all(True));self.empty_all_btn.hide()
        self.table_empty.show_message('正在读取插件名称','首次启动会扫描各 AE 版本的插件目录，通常几秒内完成。')
        table_card.box.addWidget(self.table_stack,1)
        split.addWidget(table_card,1);split.addWidget(self.build_detail())
        layout.addLayout(split,1);return page
    def build_detail(self):
        frame=Surface(padding=(20,18,20,16),spacing=8);frame.setFixedWidth(330+2*SHADOW);col=frame.box
        self.detail_eyebrow=label('效果详情','eyebrow');col.addWidget(self.detail_eyebrow)
        self.selected_label=label('在左侧表格中选择效果','detailName',True);col.addWidget(self.selected_label)
        self.detail_state=StatusTag();col.addWidget(self.detail_state);col.addSpacing(6)
        field_row=QHBoxLayout();field_row.setSpacing(8);field_row.addWidget(label('中文名称','field'));field_row.addStretch()
        # Applies to every effect-name write (effects page, schemes page); the table keeps only the Chinese.
        self.bilingual_box=QCheckBox('双语写入');self.bilingual_box.setChecked(self.bilingual)
        self.bilingual_box.setToolTip('写入 AE 时使用“英文原名 (中文)”，例如 Gaussian Blur (高斯模糊)，在 AE 里用英文或中文都能搜到。\n'
                                      '超过 AE 名称长度（47 字节）的只写中文；表格里的中文候选不变。只影响效果名称和图层效果标题，不影响参数名称。\n'
                                      '切换后已应用的效果会显示为“待应用”，再次应用即可按新方式写入。')
        self.bilingual_box.toggled.connect(self.set_bilingual);field_row.addWidget(self.bilingual_box);col.addLayout(field_row)
        # The field edits the selected row; Enter applies just that row.
        self.name_edit=QLineEdit();self.name_edit.setObjectName('field');self.name_edit.setPlaceholderText('输入中文名称，回车应用此效果')
        self.name_edit.returnPressed.connect(lambda:self.run_with(self.apply_btn,'fill','写入中',self.apply_selected));self.name_edit.editingFinished.connect(self.commit_name);col.addWidget(self.name_edit)
        col.addSpacing(4)
        # The three buttons act on the checked effects when there are any (the selected row then takes no part), otherwise on the selected row.
        self.target_note=label('','muted',True);col.addWidget(self.target_note)
        self.translate_btn=LoaderButton('翻译所选项');ui_kit.themed_icon(self.translate_btn,'sparkle','INK_2',16);self.translate_btn.clicked.connect(lambda:self.run_with(self.translate_btn,'breathe','翻译中',self.translate_target));col.addWidget(self.translate_btn)
        self.apply_btn=LoaderButton('应用所选项','primary');self.apply_btn.clicked.connect(lambda:self.run_with(self.apply_btn,'fill','写入中',self.apply_target));col.addWidget(self.apply_btn)
        pair=QHBoxLayout();pair.setSpacing(8)
        self.restore_btn=LoaderButton('恢复所选项');self.restore_btn.clicked.connect(lambda:self.run_with(self.restore_btn,'rewind','恢复中',self.restore_target));pair.addWidget(self.restore_btn,1)
        self.parameters_btn=button('参数名称','',icon_name='params',tip='转到“参数名称”页并筛选这个效果');self.parameters_btn.clicked.connect(lambda:self.open_parameters());pair.addWidget(self.parameters_btn,1)
        col.addLayout(pair);col.addSpacing(10);col.addWidget(divider());col.addSpacing(6)
        self.detail_info=label('','field',True);self.detail_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);col.addWidget(self.detail_info)
        self.path_label=label('','muted',True);col.addWidget(self.path_label)
        self.reveal_btn=button('打开插件所在文件夹','link');self.reveal_btn.clicked.connect(lambda:self.reveal_selected());col.addWidget(self.reveal_btn,0,Qt.AlignmentFlag.AlignLeft)
        col.addStretch()
        self.detail_legend=Legend((('solid','已在 AE 生效'),('hollow','待应用'),('faint','待翻译')));col.addWidget(self.detail_legend)
        self.detail_hint=label('按住复选框上下拖动可快速勾选；右键查看此效果的更多操作','muted',True);col.addWidget(self.detail_hint)
        self.detail_frame=frame;frame.installEventFilter(self)
        return frame
    def fit_detail(self):
        """On short windows the drag tip, the legend and then the file path (still opened by the link) give way so the rest stays readable."""
        frame=getattr(self,'detail_frame',None)
        if frame is None:return
        layout=frame.layout();optional=(self.detail_hint,self.detail_legend,self.path_label)
        for widget in optional:widget.setVisible(True)
        for widget in optional:
            layout.invalidate()
            if layout.totalHeightForWidth(frame.width())<=frame.height():break
            widget.setVisible(False)
    def eventFilter(self,obj,event):
        if event.type()==QEvent.Type.Resize:
            if obj is getattr(self,'detail_frame',None):self.fit_detail()
            elif obj in getattr(self,'settings_frames',()):
                need=obj.layout().totalHeightForWidth(obj.width())
                if need>0 and obj.minimumHeight()!=need:obj.setMinimumHeight(need)
        return super().eventFilter(obj,event)
    def build_parameters_page(self):
        from parameter_dialog import ParameterPanel
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        head,row,note=page_head('参数名称','汉化 AE 原生“效果控件”里的参数标签；数值仍用 AE 原有控件调节')
        self.parameter_page=ParameterPanel(self);self.parameter_note=note
        for widget in self.parameter_page.tools:row.addWidget(widget)
        layout.addWidget(inset(head));layout.addWidget(self.parameter_page,1);return page
    def build_schemes_page(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        head,row,_=page_head('翻译方案','一个方案保存一整套汉化：效果名称和参数名称的中文候选与勾选。切换只替换表格，不改动 AE')
        self.page_import_btn=button('导入…',tip='从 .json 文件导入方案到此 AE 版本的方案库');self.page_import_btn.clicked.connect(self.import_scheme)
        self.page_save_as_btn=button('另存当前为新方案','primary',tip='把当前的效果与参数表格保存为新方案');self.page_save_as_btn.clicked.connect(self.save_scheme_as)
        for widget in (self.page_import_btn,self.page_save_as_btn):row.addWidget(widget)
        layout.addWidget(inset(head))
        split=QHBoxLayout();split.setSpacing(0)
        list_card=Surface(padding=(10,10,10,10),spacing=8);list_card.setFixedWidth(320+2*SHADOW)
        search=SearchField('搜索方案');self.scheme_search=search.edit;self.scheme_search.textChanged.connect(self.filter_scheme_list);list_card.box.addWidget(search)
        self.scheme_list=QListWidget();self.scheme_list.setObjectName('schemes');self.scheme_list.setItemDelegate(SchemeDelegate(self.scheme_list));self.scheme_list.setMouseTracking(True);self.scheme_list.currentRowChanged.connect(self.preview_row)
        self.scheme_list.itemDoubleClicked.connect(lambda _:self.load_selected_scheme());self.scheme_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scheme_list.customContextMenuRequested.connect(self.scheme_list_menu)
        for command,slot in (('load',self.load_selected_scheme),('rename',lambda:self.rename_scheme(self.selected_scheme())),('delete',lambda:self.delete_scheme(self.selected_scheme()))):
            self.keymap.bind(command,local_shortcut(self.scheme_list,slot))
        self.scheme_search_field=search;list_card.box.addWidget(self.scheme_list,1)
        self.list_hint=label('','hint');list_card.box.addWidget(self.list_hint);split.addWidget(list_card)
        frame=Surface(padding=(22,18,22,16),spacing=10);self.detail_stack=QStackedWidget();frame.box.addWidget(self.detail_stack)
        self.scheme_empty=EmptyState('schemes');self.scheme_empty.show_message('还没有保存的方案','在“效果名称”和“参数名称”页填好中文候选、勾选要汉化的项目，再保存为方案；之后可以随时切换，也可以导出给别人。')
        self.empty_save_btn=self.scheme_empty.add_button(button('另存当前为新方案','primary'));self.empty_save_btn.clicked.connect(self.save_scheme_as);self.detail_stack.addWidget(self.scheme_empty)
        detail=QWidget();col=QVBoxLayout(detail);col.setContentsMargins(0,0,0,0);col.setSpacing(10)
        top=QHBoxLayout();top.setSpacing(8);self.preview_title=label('','detailName');top.addWidget(self.preview_title)
        self.current_tag=label('当前','tag');self.dirty_tag=label('未保存','tag');self.dirty_tag.setProperty('kind','outline')
        for tag in (self.current_tag,self.dirty_tag):tag.setFixedHeight(20);top.addWidget(tag,0,Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        self.discard_btn=button('放弃更改',tip='丢弃当前表格的未保存更改，恢复此方案保存的内容');self.discard_btn.clicked.connect(self.discard_changes);top.addWidget(self.discard_btn)
        self.scheme_apply_btn=LoaderButton('汉化到 AE');self.scheme_apply_btn.clicked.connect(lambda:self.run_with(self.scheme_apply_btn,'fill','汉化中',self.apply_saved_scheme))
        self.scheme_restore_btn=LoaderButton('还原原名');ui_kit.themed_icon(self.scheme_restore_btn,'restore','INK_2',16);self.scheme_restore_btn.clicked.connect(lambda:self.run_with(self.scheme_restore_btn,'rewind','还原中',self.restore_saved_scheme))
        self.load_scheme_btn=button('载入此方案','primary');self.load_scheme_btn.clicked.connect(self.primary_scheme_action);top.addWidget(self.load_scheme_btn)
        self.scheme_more=QToolButton();self.scheme_more.setObjectName('icon');ui_kit.themed_icon(self.scheme_more,'more','INK_2',16);self.scheme_more.setToolTip('更多操作：覆盖、重命名、创建副本、导出、删除')
        self.scheme_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);self.scheme_more.setMenu(QMenu(self.scheme_more));self.scheme_more.menu().aboutToShow.connect(lambda:self.list_menu(self.scheme_more.menu(),False))
        self.scheme_more.setCursor(Qt.CursorShape.PointingHandCursor);self.scheme_more.setStyleSheet('QToolButton::menu-indicator { image:none; width:0; }');top.addWidget(self.scheme_more)
        col.addLayout(top);self.preview_meta=elastic(label('','muted'))
        meta=QHBoxLayout();meta.setSpacing(8);meta.addWidget(self.preview_meta,1);meta.addWidget(self.scheme_apply_btn);meta.addWidget(self.scheme_restore_btn);col.addLayout(meta)
        tiles=QHBoxLayout();tiles.setSpacing(12);self.preview_group=QButtonGroup(self)
        self.effect_tile=MetricTile('效果名称');self.param_tile=MetricTile('参数名称')
        for index,(tile,tip) in enumerate(((self.effect_tile,'查看此方案保存的效果名称'),(self.param_tile,'查看此方案保存的参数名称'))):tile.setToolTip(tip);self.preview_group.addButton(tile,index);tiles.addWidget(tile)
        self.effect_tile.setChecked(True);self.preview_group.idClicked.connect(lambda _:self.show_preview_tab());col.addLayout(tiles)
        tools=QHBoxLayout();tools.setSpacing(14);field=SearchField('搜索效果、参数或中文');self.preview_search=field.edit;self.preview_search.textChanged.connect(self.filter_preview);tools.addWidget(field,1)
        self.add_effects_btn=button('添加效果…',tip='挑选效果或参数加入所选方案；已有中文的会一并带上');self.add_effects_btn.clicked.connect(lambda:self.add_to_scheme());tools.addWidget(self.add_effects_btn)
        self.delete_checked_btn=button('删除勾选项');self.delete_checked_btn.clicked.connect(self.delete_checked);tools.addWidget(self.delete_checked_btn)
        self.diff_only=QCheckBox('只看不同');self.diff_only.setObjectName('check');self.diff_only.setToolTip('只列出与当前表格不同、载入此方案后会改变的项目');self.diff_only.toggled.connect(self.filter_preview);tools.addWidget(self.diff_only);col.addLayout(tools)
        self.preview_table=self.effect_preview_view()
        self.param_preview_table=self.parameter_preview_view()
        self.preview_note=EmptyState('params');self.preview_stack=QStackedWidget()
        for widget in (self.preview_table,self.param_preview_table,self.preview_note):self.preview_stack.addWidget(widget)
        col.addWidget(self.preview_stack,1);self.edit_hint=label('','hint');col.addWidget(self.edit_hint)
        self.dup_banner=Banner('warn');self.dup_banner.hide();col.addWidget(self.dup_banner)
        self.keymap.bind('undo',local_shortcut(detail,self.undo_scheme_edit,Qt.ShortcutContext.WidgetWithChildrenShortcut))
        self.detail_stack.addWidget(detail)
        split.addWidget(frame,1);layout.addLayout(split,1);return page
    def effect_preview_view(self)->QTableWidget:
        table=self.editable_preview(['','效果','中文名称','当前表格',''],{'check':P_CHECK,'chinese':P_CHINESE,'table':P_TABLE,'trash':P_TRASH},'effects')
        self.preview_header=table.check_header;return table
    def parameter_preview_view(self)->QTableWidget:
        return self.editable_preview(['','效果','参数','中文名称','当前表格',''],{'check':Q_CHECK,'chinese':Q_CHINESE,'table':Q_TABLE,'trash':Q_TRASH},'parameters')
    def editable_preview(self,headers,cols,part)->QTableWidget:
        """A scheme preview that edits the highlighted scheme itself (see edit_scheme): box, names, current table, delete."""
        table=QTableWidget(0,len(headers));table.cols=cols;table.part=part;word=PART_WORD[part]
        table.check_header=CheckHeader(table,lambda:self.toggle_preview_checks(table));table.setHorizontalHeader(table.check_header)
        table.setHorizontalHeaderLabels(headers);table_style(table,34)
        for column in range(len(headers)):table.horizontalHeader().setSectionResizeMode(column,QHeaderView.ResizeMode.Fixed if column in (cols['check'],cols['trash']) else QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(cols['check'],44);table.setColumnWidth(cols['trash'],52);table.setItemDelegateForColumn(cols['trash'],TrashDelegate(table))
        table.horizontalHeaderItem(cols['check']).setToolTip(f'方案中勾选的{word}，载入后会被勾选。\n点这里勾选或取消当前列出的{word}；按住行内复选框上下拖动可快速勾选')
        table.horizontalHeaderItem(cols['chinese']).setToolTip(f'双击修改方案里的中文名称；清空即删除该{word}的中文')
        table.horizontalHeaderItem(cols['table']).setToolTip('当前表格里的中文：“相同”表示与方案一致，否则显示表格里现在的中文（不比较勾选）')
        self.make_editable(table);return table
    def make_editable(self,table):
        table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked|QTableWidget.EditTrigger.EditKeyPressed)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        table.itemChanged.connect(lambda item:self.preview_edited(item,table));table.cellClicked.connect(lambda row,column:self.preview_clicked(row,column,table))
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);table.customContextMenuRequested.connect(lambda pos:self.preview_menu(pos,table))
        self.keymap.bind('delete',local_shortcut(table,lambda:self.delete_from_scheme(table=table)))
        table.dragger=CheckDragger(table,table.cols['check'],lambda row:self.preview_checked(row,table),lambda rows,state:self.paint_preview_checks(rows,state,table),lambda rows,state:self.finish_preview_checks(rows,state,table))
        if not hasattr(self,'edit_timer'):
            self.scheme_undo=[];self.preview_filling=False;self.queued_edit=None
            self.edit_timer=QTimer(self);self.edit_timer.setSingleShot(True);self.edit_timer.setInterval(0);self.edit_timer.timeout.connect(self.run_queued_edit)
    def scheme_list_menu(self,pos):
        if self.scheme_list.itemAt(pos) is None:return
        self.scheme_list.setCurrentItem(self.scheme_list.itemAt(pos));self.list_menu().exec(self.scheme_list.viewport().mapToGlobal(pos))
    def build_versions_page(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        head,row,_=page_head('AE 版本','每个版本的插件、中文候选、勾选和方案分开保存；只有已验证的构建可以实时写入')
        self.paths_btn=button('检测 / 定位 AE…',tip='查看安装路径、文件版本和兼容性，或手动选择其他磁盘上的 AE');self.paths_btn.clicked.connect(self.open_ae_paths)
        self.scan_btn=button('重新扫描插件','',icon_name='refresh');self.scan_btn.clicked.connect(self.start_scan)
        row.addWidget(self.paths_btn);row.addWidget(self.scan_btn);layout.addWidget(inset(head))
        split=QHBoxLayout();split.setSpacing(0)
        table_card=Surface(padding=(14,10,14,10))
        self.version_table=QTableWidget(0,4);self.version_table.setHorizontalHeaderLabels(['AE','文件版本','插件','实时汉化']);table_style(self.version_table,44)
        self.version_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.version_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.version_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection);self.version_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header=self.version_table.horizontalHeader();header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch);header.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        self.version_table.setItemDelegateForColumn(3,DiamondDelegate(self.version_table));self.version_table.doubleClicked.connect(lambda index:self.choose_version(index.row()));self.version_table.itemSelectionChanged.connect(self.show_version_detail)
        table_card.box.addWidget(self.version_table,1);split.addWidget(table_card,1)
        frame=Surface(padding=(22,18,22,16),spacing=8);frame.setFixedWidth(360+2*SHADOW);col=frame.box
        self.version_eyebrow=label('','eyebrow');col.addWidget(self.version_eyebrow)
        self.version_title=label('','detailName',True);col.addWidget(self.version_title)
        self.version_state=Pill('');col.addWidget(self.version_state)
        self.version_desc=label('','field',True);col.addWidget(self.version_desc);col.addSpacing(6);col.addWidget(divider());col.addSpacing(4)
        col.addWidget(label('AE 路径','eyebrow'));self.version_exe=label('','mono',True);self.version_exe.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);col.addWidget(self.version_exe)
        col.addWidget(label('插件目录','eyebrow'));self.version_dirs=label('','mono',True);self.version_dirs.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);col.addWidget(self.version_dirs)
        col.addStretch()
        self.use_version_btn=button('切换到此版本','primary');self.use_version_btn.clicked.connect(lambda:self.choose_version(self.version_table.currentRow()));col.addWidget(self.use_version_btn)
        self.dir_btn=button('为此版本添加插件目录…','',icon_name='folder');self.dir_btn.clicked.connect(self.add_directory);col.addWidget(self.dir_btn)
        self.analyze_btn=button('自动识别与适配…','',icon_name='sparkle',tip='优先离线代码识别，结构不同时可让 AI 诊断原因（不能启用）；候选必须完成验证才能启用');self.analyze_btn.clicked.connect(self.analyze_adapter);col.addWidget(self.analyze_btn)
        split.addWidget(frame);layout.addLayout(split,1)
        self.versions_note=label('','muted',True);layout.addWidget(inset(self.versions_note))
        return page
    def build_settings_page(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        head,_,_=page_head('设置与日志','翻译服务、退出行为、界面外观和运行记录');layout.addWidget(inset(head))
        grid=QGridLayout();grid.setSpacing(0)
        frame,col=self.settings_card('翻译模型');self.api_info=label('','field',True);col.addWidget(self.api_info)
        col.addWidget(label('翻译只发送所选效果 / 参数的名称、内部标识和分类，不发送工程、画面或参数值。','muted',True));col.addStretch()
        self.api_btn=button('修改翻译模型设置…');self.api_btn.clicked.connect(self.configure_api);col.addWidget(self.api_btn,0,Qt.AlignmentFlag.AlignLeft);grid.addWidget(frame,0,0)
        frame,col=self.settings_card('退出与恢复')
        # Off by default: names stay in AE after the localizer quits. Remembered in ui.json.
        self.exit_restore=QCheckBox('临时翻译');self.exit_restore.setChecked(self.ui_preferences().get('temporaryTranslation') is True)
        self.exit_restore.toggled.connect(lambda on:self.save_ui_preference('temporaryTranslation',bool(on)));col.addWidget(self.exit_restore)
        col.addWidget(label('开启后，退出本软件时把写过的名称（含图层效果标题和参数标签）恢复为原名。关闭时中文留在 AE 中，直到 AE 退出。','muted',True))
        # On by default: AE shows the original names again after every restart.
        self.reapply_box=QCheckBox('AE 重启后自动重新应用效果名称');self.reapply_box.setChecked(self.ui_preferences().get('reapplyNames') is not False)
        self.reapply_box.toggled.connect(lambda on:self.save_ui_preference('reapplyNames',bool(on)));col.addWidget(self.reapply_box)
        col.addWidget(label('AE 每次启动都会恢复原名；本软件连接 AE 时把上次应用的效果名称写回。不改图层效果标题，参数名称需在参数页重新应用。','muted',True));col.addStretch()
        folder_btn=button('打开本地记录','',icon_name='folder');folder_btn.clicked.connect(lambda:os.startfile(str(data_root())));col.addWidget(folder_btn,0,Qt.AlignmentFlag.AlignLeft);grid.addWidget(frame,0,1)
        frame,col=self.settings_card('界面')
        self.motion_box=QCheckBox('界面动效');self.motion_box.setChecked(ui_kit.motion());self.motion_box.toggled.connect(self.save_motion);col.addWidget(self.motion_box)
        col.addWidget(label('加载动画、页面淡入与数字滚动。Windows 关闭“显示动画”时自动停用。','muted',True))
        self.dark_box=QCheckBox('深色模式');self.dark_box.setChecked(ui_kit.is_dark());self.dark_box.toggled.connect(self.set_dark_mode);col.addWidget(self.dark_box)
        col.addWidget(label('深色背景、浅色文字，立即生效。','muted',True));col.addStretch();grid.addWidget(frame,0,2)
        frame,col=self.settings_card('适配范围')
        self.scope_info=label('','field',True);col.addWidget(self.scope_info)
        col.addWidget(label(f'候选、勾选、方案和恢复记录保存在：\n{data_root()}','muted',True));col.addSpacing(6)
        # The background extension (cep_bridge): parameter reads and writes without a second AfterFX.exe.
        col.addWidget(label('无感读写','cardTitle'));self.bridge_info=label('','muted',True);col.addWidget(self.bridge_info);col.addStretch()
        row=QHBoxLayout();row.setSpacing(8)
        self.bridge_btn=button('安装后台连接');self.bridge_btn.clicked.connect(self.install_bridge);row.addWidget(self.bridge_btn)
        self.bridge_remove=button('移除','quiet');self.bridge_remove.clicked.connect(self.remove_bridge);row.addWidget(self.bridge_remove);row.addStretch();col.addLayout(row)
        grid.addWidget(frame,1,0)
        frame,col=self.settings_card('软件更新')
        # On by default; at most one network check a day, the result is kept for the rest of that day.
        self.update_box=QCheckBox('启动时自动检查更新');self.update_box.setToolTip('每天最多联网检查一次。只从 GitHub 公开发布页读取版本信息，不发送本机或工程信息；发现新版只提示，下载和安装都要你确认');self.update_box.setChecked(self.ui_preferences().get('updateCheck') is not False)
        self.update_box.toggled.connect(lambda on:self.save_ui_preference('updateCheck',bool(on)));col.addWidget(self.update_box)
        self.update_info=label('','muted',True);col.addWidget(self.update_info);col.addStretch()
        row=QHBoxLayout();row.setSpacing(8)
        self.update_btn=LoaderButton('检查更新');self.update_btn.clicked.connect(lambda:self.check_update(manual=True));row.addWidget(self.update_btn)
        self.notes_btn=button('本版更新内容','quiet');self.notes_btn.clicked.connect(self.show_current_notes);self.notes_btn.setVisible(bool(release_notes.newer(None,APP_VERSION)))
        row.addWidget(self.notes_btn);row.addStretch();col.addLayout(row)
        grid.addWidget(frame,1,1)
        frame,col=self.settings_card('快捷键')
        # The master switch: off pauses every shortcut, keeping the chosen keys for when it is switched back on.
        self.shortcut_box=QCheckBox('启用键盘快捷键');self.shortcut_box.setChecked(self.keymap.enabled);self.shortcut_box.toggled.connect(self.set_shortcuts_enabled);col.addWidget(self.shortcut_box)
        self.shortcut_info=label('','muted',True);col.addWidget(self.shortcut_info);col.addStretch()
        keys=QHBoxLayout();keys.setSpacing(8)
        self.shortcut_edit_btn=button('编辑快捷键…');self.shortcut_edit_btn.clicked.connect(self.edit_shortcuts);keys.addWidget(self.shortcut_edit_btn)
        self.shortcut_reset_btn=button('恢复默认','quiet');self.shortcut_reset_btn.clicked.connect(self.reset_shortcuts);keys.addWidget(self.shortcut_reset_btn);keys.addStretch();col.addLayout(keys)
        grid.addWidget(frame,1,2)
        # Bottom row: the log over two columns, the acknowledgement beside it, so it costs no extra height.
        frame,col=self.settings_card('致谢')
        self.thanks=label(THANKS,'muted',True);self.thanks.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);col.addWidget(self.thanks);col.addSpacing(6)
        self.limits_title=label('局限','cardTitle');col.addWidget(self.limits_title)
        self.limits=label(LIMITS,'muted',True);self.limits.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);col.addWidget(self.limits);col.addStretch()
        grid.addWidget(frame,2,2)
        layout.addLayout(grid,1)
        frame=Surface(padding=(20,16,20,16));col=frame.box;row=QHBoxLayout();row.addWidget(label('运行日志','cardTitle'));row.addStretch()
        copy=button('复制','quiet');copy.clicked.connect(lambda:QApplication.clipboard().setText(self.log.toPlainText()));row.addWidget(copy)
        clear=button('清空','quiet');clear.clicked.connect(lambda:self.log.clear());row.addWidget(clear);col.addLayout(row)
        # The log grows with the window; on a short window the whole page scrolls rather than squeezing any card.
        self.log=QPlainTextEdit();self.log.setReadOnly(True);self.log.setMinimumHeight(120);col.addWidget(self.log,1);grid.addWidget(frame,2,0,1,2);grid.setRowStretch(2,1)
        return self.scrolling(page)
    def scrolling(self,page):
        """Scrolls a page vertically when the window is too short for it (cards keep their full text)."""
        area=QScrollArea();area.setObjectName('pageScroll');area.setWidgetResizable(True);area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);page.setObjectName('scrollPage');area.setWidget(page);return area
    def settings_card(self,title):
        frame=Surface(padding=(20,16,20,16),spacing=8);frame.box.addWidget(label(title,'cardTitle'))
        # Grid rows do not ask wrapped text how tall it gets; each card claims it, and the log below gives way.
        frame.installEventFilter(self);self.settings_frames=getattr(self,'settings_frames',[])+[frame];return frame,frame.box
    # Shortcuts: changes take effect at once; texts that name a key are rewritten from the keymap.
    def set_shortcuts_enabled(self,on):
        if bool(on)==self.keymap.enabled:return
        error=self.keymap.set_enabled(on)
        if error:self.log.appendPlainText(error)
        self.status.setText('键盘快捷键已启用' if on else '键盘快捷键已全部停用；自定义的组合键会保留')
    def edit_shortcuts(self):
        ShortcutDialog(self.keymap,self).exec()
    def reset_shortcuts(self):
        if not confirm(self,'全部快捷键恢复默认？',f'已自定义的 {self.keymap.customized()} 项会改回默认组合键。','恢复默认'):return
        error=self.keymap.reset()
        if error:self.log.appendPlainText(error)
        self.status.setText('快捷键已恢复默认')
    def undo_note(self,form='{key} 撤销')->str:
        """Text that mentions the undo key, or nothing while it has none."""
        key=self.keymap.hint('undo');return form.format(key=key) if key else ''
    def refresh_shortcut_hints(self):
        k=self.keymap
        if hasattr(self,'shortcut_box'):
            self.shortcut_box.blockSignals(True);self.shortcut_box.setChecked(k.enabled);self.shortcut_box.blockSignals(False)
            count=k.customized();self.shortcut_reset_btn.setEnabled(count>0)
            self.shortcut_info.setText(('已自定义 '+str(count)+' 项' if count else '使用默认快捷键')+('' if k.enabled else '；当前已全部停用')+'。点“编辑快捷键…”可随意搭配组合键，修改立即生效。')
        for index,item in enumerate(self.nav_buttons):item.setToolTip(k.tip(f'page{index}',item.name.text()))
        self.connect_btn.setToolTip(k.tip('connect','核对 AE 路径、构建版本并读取实际效果目录'))
        self.update_refresh_hint()
        self.filter.setPlaceholderText(k.tip('find','搜索名称、内部标识或分类'))
        self.page_save_as_btn.setToolTip(k.tip('save_as','把当前的效果与参数表格保存为新方案'))
        load,rename=k.hint('load'),k.hint('rename')
        self.list_hint.setText(' · '.join(x for x in ('双击'+(f'或 {load} ' if load else '')+'载入',f'{rename} 重命名' if rename else '','右键更多') if x))
        self.delete_checked_btn.setToolTip('把当前列出的勾选项从方案中删除（会确认'+self.undo_note('，可 {key} 撤销')+'）')
        self.parameter_page.history_delete.setToolTip('删除勾选的汉化记录（中文候选和勾选）；不改 AE 中已应用的名称'+self.undo_note('，可 {key} 撤销'))
        self.update_scheme_bar()
        if hasattr(self,'preview_table'):self.edit_hint.setText(' · '.join(x for x in ('复选框可按住上下拖动','双击中文名称修改','垃圾桶删除单个'+('参数' if self.preview_group.checkedId()==1 else '效果'),self.undo_note(),'右键更多') if x))
        self.update_detail_actions()
    def go(self,index:int):
        self.pages.setCurrentIndex(index);self.nav_buttons[index].setChecked(True);self.nav.select(index)
        if index==SETTINGS:self.unread=0;self.update_bridge_info()
        self.update_nav()
    # Background extension: installed on request into the user's CEP folder, active after AE restarts.
    BRIDGE_TEXT={'unavailable':'这个版本没有附带后台连接扩展。读写参数时用命令行把脚本交给 AE，AE 窗口可能被还原（软件会立即恢复）。',
                 'missing':'未安装。安装后重启一次 AE，读写参数和同步图层效果标题时不再启动第二个 AE 进程，AE 窗口和焦点保持不变。',
                 'outdated':'已安装的扩展与本软件版本不一致，请重新安装，然后重启 AE。',
                 'installed':'已安装，重启 AE 后生效。生效前仍用命令行转交。',
                 'connected':'已连接：读写在 AE 后台进行，AE 窗口和焦点保持不变。'}
    def refresh_bridge(self):
        """An extension installed earlier but from another build of this program is brought up to date (the user
        chose to install it); AE loads the new files at its next start."""
        try:
            if cep_bridge.status()=='outdated':cep_bridge.install();self.log.appendPlainText('后台连接扩展已更新为本版本，重启 AE 后生效')
        except OSError as error:self.log.appendPlainText('后台连接扩展未能更新：'+str(error))
        self.update_bridge_info()
    def update_bridge_info(self):
        if not hasattr(self,'bridge_info'):return
        try:state=cep_bridge.status()
        except OSError:state='missing'
        self.bridge_info.setText(self.BRIDGE_TEXT[state])
        self.bridge_btn.setVisible(state in ('missing','outdated'));self.bridge_btn.setText('重新安装' if state=='outdated' else '安装后台连接')
        self.bridge_remove.setVisible(state in ('outdated','installed','connected'))
    def install_bridge(self):
        if not confirm(self,'安装后台连接？',f'把 AE 后台扩展（看不见的 CEP 扩展，不需要管理员权限）复制到：\n{cep_bridge.installed()}\n\n'
                       '同时开启 Adobe 扩展调试模式（当前用户注册表 Software\\Adobe\\CSXS.10～13 的 PlayerDebugMode），让 AE 一定加载这个扩展。'
                       '调试模式下 AE 不再检查扩展签名，对你所有 Adobe 扩展都生效；移除后台连接时，本软件改过的值会恢复原样。\n\n'
                       '安装后需要重启一次 AE 才会生效；之后读写参数不再启动第二个 AE 进程，AE 窗口和焦点保持不变。没有加载时软件自动改用原来的方式。','安装'):return
        try:cep_bridge.install(debug_mode=True)
        except Exception as error:self.error('安装后台连接失败：'+str(error));return
        self.log.appendPlainText(f'已安装后台连接扩展：{cep_bridge.installed()}；Adobe 扩展调试模式已开启');self.update_bridge_info()
        self.toast.notify('后台连接已安装','重启 AE 后生效')
    def remove_bridge(self):
        if not confirm(self,'移除后台连接？','删除 AE 后台扩展，并把本软件开启的 Adobe 扩展调试模式恢复原样（原来就开着的不动）。重启 AE 后读写参数改回命令行转交（AE 窗口可能被还原，软件会立即恢复）。','移除',danger=True):return
        try:cep_bridge.remove()
        except Exception as error:self.error('移除后台连接失败：'+str(error));return
        self.log.appendPlainText('已移除后台连接扩展');self.update_bridge_info()
    def update_nav(self):
        counts=(len(self.plugins),len(self.parameter_page.rows) or '',len(self.schemes),len(self.spaces),self.unread or '')
        for button_,count in zip(self.nav_buttons,counts):button_.set_count(count,button_ is self.nav_buttons[SETTINGS] and bool(self.unread))
    def update_pill(self):
        if not self.space.live:state,text='warn','仅翻译'
        elif self.live:state,text='ok',f"已连接 · {len(self.live.get('effects',[]))}"
        else:state,text='idle','未连接'
        self.pill.set_state(state,text);self.pill.setToolTip(self.note.text() if not self.space.live else f'{self.space.label} {self.space.version}')
        connect_text='自动识别与适配…' if not self.space.live else '重新读取' if self.live else '连接 AE'
        self.connect_btn.set_label(connect_text);primary='primary' if self.space.live and not self.live else ''
        if self.connect_btn.objectName()!=primary:self.connect_btn.setObjectName(primary);repolish(self.connect_btn)
    def update_api_info(self):
        s=self.settings
        key='不需要' if not s.provider.needs_key else ('已设置'+('（加密保存在本机）' if s.remember else '（仅本次会话）') if s.key else '未设置')
        self.api_info.setText(f"服务商：{s.provider.name}\n模型：{s.model or '未填写'}\n接口：{s.base_url or '未填写'}\nAPI Key：{key}")
    # Busy state: the button that started a job shows a loader until the job ends.
    def run_with(self,widget,kind,verb,action):
        self.next_loader=(widget,kind,verb)
        try:action()
        finally:self.next_loader=None
    # AE versions: each installed version is a separate space; only verified builds can be renamed live.
    def bind_space(self,space:VersionSpace)->str:
        self.space=space;space.folder.mkdir(parents=True,exist_ok=True)
        self.map_path=space.map_path;self.store=CandidateStore(self.map_path);self.pending=self.store.values
        self.enabled_store=CandidateStore(space.enabled_path,keys=True);self.enabled=self.enabled_store.values
        if space.live:self.pending.setdefault('S_Glow','辉光')
        self.scheme_folder=space.scheme_folder;self.active_path=space.active_path;self.needs_refresh=False
        warnings=[self.store.secure(),self.enabled_store.secure()]
        if hasattr(self,'parameter_session'):warnings.append(self.bind_parameter_stores(space))
        return '\n'.join(x for x in warnings if x)
    def bind_parameter_stores(self,space):
        self.parameter_store=CandidateStore(space.folder/'parameter-translations.json');self.parameter_pending=self.parameter_store.values
        self.parameter_enabled_store=CandidateStore(space.folder/'parameter-enabled.json',keys=True);self.parameter_enabled=self.parameter_enabled_store.values
        # Labels AE showed for parameters before they were renamed, so records stay readable without reading AE again.
        self.parameter_labels_store=CandidateStore(space.folder/'parameter-labels.json');self.parameter_labels=self.parameter_labels_store.values
        for key,value in {'0001':'滴落率','0002':'存续时间','0003':'涟漪','0004':'位移','0005':'波纹高度','0006':'扩散','0007':'光照','0015':'明暗'}.items():
            self.parameter_pending.setdefault(identity('CC Drizzle','CC Drizzle-'+key),value)
        return '\n'.join(x for x in (self.parameter_store.secure(),self.parameter_enabled_store.secure()) if x)
    def remembered_scheme(self):
        try:return json.loads(self.active_path.read_text(encoding='utf8')).get('name')
        except (OSError,ValueError,AttributeError):return None
    @property
    def roots(self)->list[Path]:
        return list(dict.fromkeys(r for r in [*(root for s in self.spaces for root in s.roots()),*self.shared] if r.is_dir()))
    def space_plugins(self,space:VersionSpace)->list:
        # Plugins in no version's folder (Adobe's common folder) load in every version.
        return [p for p in self.all_plugins if space.owns(p.path) or not any(s.owns(p.path) for s in self.spaces)]
    def update_version_bar(self):
        self.nav_foot.setText(f'实时写入已适配：{supported_text()}')
        box=self.version_box;box.blockSignals(True);box.clear()
        for index,space in enumerate(self.spaces):
            box.addItem(space.item_text(len(self.space_plugins(space)) if self.all_plugins else None))
            box.setItemData(index,f"{space.key}\nAE 路径：{space.exe or '无'}\n插件目录：{space.plugins or '无'}\n"+('已适配，连接时继续核验模块和结构' if space.live else f'只能扫描、翻译和保存方案；已验证构建：{supported_text()}'),Qt.ItemDataRole.ToolTipRole)
        box.setCurrentIndex(self.spaces.index(self.space));box.blockSignals(False)
        self.note.setText(f'{self.space.label}（{self.space.version}）：支持实时改名。更名在当前 AE 会话内生效，插件文件保持原样。' if self.space.live else
                          f'{self.space.label}（{self.space.version}）：只能扫描、翻译和保存方案，尚未适配效果名和参数标签的实时改名。已验证构建：{supported_text()}。中文候选、勾选和方案与其他版本分开保存。')
        self.banner.setVisible(not self.space.live)
        self.scope_info.setText(f'效果名和参数标签实时写入：{supported_text()}。\n支持标准第一层参数标签和折叠标题；下拉菜单内部条目和插件自绘界面暂不支持。')
        self.versions_note.setText(f'实时写入只对已验证构建开放：{supported_text()}。其他版本可扫描、翻译和保存方案；检测到版本不代表已适配。')
        self.fill_versions();self.update_pill();self.update_nav()
    def fill_versions(self):
        table=self.version_table;table.setRowCount(len(self.spaces))
        for row,space in enumerate(self.spaces):
            count=len(self.space_plugins(space)) if self.all_plugins else None
            values=(space.label+('（当前）' if space is self.space else ''),space.version,'' if count is None else str(count),'已适配' if space.live else '仅翻译')
            for column,text in enumerate(values):
                item=QTableWidgetItem(text);item.setToolTip(str(space.exe or text))
                if space is self.space:font=item.font();font.setBold(True);item.setFont(font)
                if column==3:item.setForeground(QColor(ui_kit.INK if space.live else ui_kit.MUTED))
                table.setItem(row,column,item)
        table.selectRow(self.spaces.index(self.space));self.show_version_detail()
    def show_version_detail(self):
        row=self.version_table.currentRow();space=self.spaces[row] if 0<=row<len(self.spaces) else self.space
        count=len(self.space_plugins(space)) if self.all_plugins else None
        self.version_eyebrow.setText('当前使用' if space is self.space else '已安装')
        self.version_title.setText(space.label);self.version_state.set_state('ok' if space.live else 'warn',f'{space.version} · '+('可实时汉化' if space.live else '仅翻译'))
        self.version_desc.setText(('效果名称和参数标签可在 AE 中实时汉化；连接时继续核验模块和结构。' if space.live else
                                   '可扫描、翻译和保存方案。实时写入需要先通过“自动识别与适配”完成本机验收。')+(f'\n{count} 条名称资源。' if count is not None else ''))
        self.version_exe.setText(soft_path(space.exe or '无'));self.version_dirs.setText('\n'.join(soft_path(r) for r in space.roots()) or '无')
        self.use_version_btn.setText('当前使用中' if space is self.space else '切换到此版本')
    def choose_version(self,index):
        if not 0<=index<len(self.spaces):return
        space=self.spaces[index]
        if space is self.space or self.busy():self.update_version_bar();return
        warning=self.bind_space(space)
        self.bind_targets()
        try:write_json(self.version_path,{'key':space.key})
        except OSError:pass
        if warning:self.log.appendPlainText(warning)
        self.parameter_page.clear();self.refresh_schemes(self.remembered_scheme());self.show_space();self.update_version_bar();self.update_buttons();self.selection_changed()
        self.status.setText(f'已切换到 {space.label}'+('' if space.live else '：只能扫描、翻译和保存方案'))
    def bind_targets(self):
        if self.space.live:
            self.session.expected_image=self.space.exe
            if hasattr(self,'parameter_session'):self.parameter_session.expected_image=self.space.exe
            if hasattr(self,'instance_session'):self.instance_session.expected_image=self.space.exe
        if self.live and self.space.live and self.space.exe and self.live.get('image'):
            from ae_paths import path_key
            if path_key(self.live.get('image',''))!=path_key(self.space.exe):self.live=None
    def open_ae_paths(self):
        from ae_path_dialog import AEPathDialog
        AEPathDialog(self).exec()
    def analyze_adapter(self,space=None):
        if self.busy():return
        if not isinstance(space,VersionSpace):
            row=self.version_table.currentRow();space=self.spaces[row] if 0<=row<len(self.spaces) else self.space
        if not space.exe:self.error('请先定位有效的 AE 安装');return
        from adapter_dialog import AdapterAnalysisDialog
        self.adapter_dialog=AdapterAnalysisDialog(self,space)
        try:self.adapter_dialog.exec()
        finally:self.adapter_dialog=None
    def reload_installations(self,selected_exe):
        if self.busy():QTimer.singleShot(50,lambda:self.reload_installations(selected_exe));return
        from ae_paths import path_key
        extras={s.key:s.extra for s in self.spaces}
        self.spaces=discover_spaces(installations(),file_version,data_root());self.shared=shared_roots()
        for space in self.spaces:space.extra=extras.get(space.key,[])
        chosen=next((s for s in self.spaces if s.exe and path_key(s.exe)==path_key(selected_exe)),self.spaces[0])
        warning=self.bind_space(chosen);self.bind_targets()
        if warning:self.log.appendPlainText(warning)
        try:write_json(self.version_path,{'key':chosen.key})
        except OSError as error:self.log.appendPlainText('所选 AE 未能保存：'+str(error))
        self.parameter_page.clear();self.refresh_schemes(self.remembered_scheme());self.all_plugins=[];self.show_space();self.update_version_bar();self.update_buttons();self.start_scan()
    def busy(self):return self.job is not None and self.job.isRunning()
    def work(self,work,callback,failure=None):
        if self.busy():return
        for widget in self.buttons:widget.setEnabled(False)
        self.progress.show()
        self.job=Job(work);self.job.result.connect(callback);self.job.failure.connect(failure or self.error);self.job.progress.connect(self.status.setText);self.job.finished.connect(lambda job=self.job:self.finished(job));self.job.start()
        if self.next_loader:
            widget,kind,verb=self.next_loader;widget.setEnabled(True);widget.start(kind,verb);self.loading_button=widget
        if self.writing:self.breather.start()
    def finished(self,job=None):
        if job is not None and job is not self.job:return
        if self.loading_button:self.loading_button.stop();self.loading_button=None
        if self.writing:self.writing=set();self.breather.stop()
        self.parameter_page.stop_writing();self.progress.hide();self.update_buttons();self.update_usage()
        if self.close_after:QTimer.singleShot(0,self.close)
    def update_buttons(self):
        for widget in self.buttons:widget.setEnabled(True)
        live=self.live is not None and self.space.live
        for widget in (self.apply_selected_action,self.restore_selected_action,self.restore_all_action,self.refresh_btn,self.refresh_action):widget.setEnabled(live)
        for widget in self.parameter_page.controls:widget.setEnabled(self.space.live)
        self.parameter_page.update_check_all();self.parameter_page.update_empty()
        self.update_detail_actions();self.update_scheme_bar();self.update_pill();self.update_refresh_hint()
    # The detail panel's buttons act on the checked effects when there are any, otherwise on the selected row.
    def target_mode(self)->str:return 'checked' if self.enabled else 'selected'
    def update_detail_actions(self):
        if not hasattr(self,'target_note'):return
        rows=self.selected_rows();live=self.live is not None and self.space.live;checked=self.target_mode()=='checked';count=len(self.enabled)
        word='勾选项' if checked else '所选项'
        for widget,verb in ((self.translate_btn,'翻译'),(self.apply_btn,'应用'),(self.restore_btn,'恢复')):widget.set_label(verb+word)
        if checked:note=f'作用于 {count} 个勾选项'+('，所选行不参与' if rows and self.table.item(rows[0],KEY).text() not in self.enabled else '')
        else:note='作用于所选效果；勾选效果后改为作用于勾选项' if rows else '选择一行，或勾选多个效果'
        self.target_note.setText(note)
        why='' if live else ('先连接 AE' if self.space.live else f'{self.space.label} 不支持实时改名')
        target=f'{count} 个勾选项' if checked else '所选效果'
        k=self.keymap
        self.translate_btn.setToolTip(k.tip('translate',f'AI 翻译{target}的名称'+('；已有中文的会先询问是否重新翻译' if checked else '，覆盖现有中文候选')))
        self.apply_btn.setToolTip(k.tip('apply',f'把{target}的中文名称写入 AE'+('（没有中文的跳过）' if checked else ''))+(f'\n{why}' if why else ''))
        self.restore_btn.setToolTip(k.tip('restore',f'把{target}在 AE 中恢复为原名（只恢复本软件改过的）')+(f'\n{why}' if why else ''))
        if not self.busy():
            named=any(self.pending.get(key,'').strip() for key in self.enabled)
            self.translate_btn.setEnabled(bool(count if checked else rows))
            self.apply_btn.setEnabled(live and (named if checked else bool(rows)))
            self.restore_btn.setEnabled(live and bool(count if checked else rows))
            self.translate_action.setEnabled(bool(rows))
        self.parameters_btn.setEnabled(self.space.live and len(rows)==1 and not self.busy());self.name_edit.setEnabled(len(rows)==1);self.reveal_btn.setVisible(len(rows)==1)
    def update_refresh_hint(self):
        """After a write the refresh button stands out until clicked; it never refreshes by itself."""
        on=bool(self.needs_refresh and self.live)
        if self.refresh_btn.property('attention')!=on:repolish(self.refresh_btn,attention=on);ui_kit.themed_icon(self.refresh_btn,'refresh','ON_INK' if on else 'INK_2',16)
        self.refresh_btn.setToolTip(self.keymap.tip('refresh','刷新 AE“效果和预设”列表')+('\n名称已写入，点此刷新后在 AE 中可见' if on else '\n应用与恢复不会自动刷新'))
    def commit_name(self):
        """Keeps what was typed in the detail field as the selected row's candidate."""
        rows=self.selected_rows()
        if len(rows)!=1 or not self.name_edit.isEnabled():return
        item=self.table.item(rows[0],CANDIDATE)
        if item.text()!=self.name_edit.text():item.setText(self.name_edit.text())
    def checked_rows(self)->list[int]:
        """One table row per checked effect, hidden rows included, in table order."""
        rows=[];seen=set()
        for row in range(self.table.rowCount()):
            key=self.table.item(row,KEY).text()
            if key in self.enabled and key not in seen:rows.append(row);seen.add(key)
        return rows
    def translate_target(self):
        if self.target_mode()=='selected':self.translate_selected();return
        self.commit_name();rows=self.checked_rows()
        if not rows:self.toast.notify('无需翻译','勾选的效果都不在当前版本的表格里');return
        missing=[row for row in rows if not self.table.item(row,CANDIDATE).text().strip()];named=len(rows)-len(missing)
        if named:
            choices=[f'全部重新翻译（{len(rows)} 项）']+([f'只翻译缺少的 {len(missing)} 项'] if missing else [])
            text=f'勾选的 {len(rows)} 项中有 {named} 项已有中文候选。'+('重新翻译会覆盖这些中文；只翻译缺少的不会改动它们。' if missing else '重新翻译会覆盖现有的中文候选。')
            picked=choose(self,f'{named} 个勾选项已有中文',text,choices)
            if picked is None:return
            if missing and picked==1:rows=missing
        self.translate_rows(rows)
    def apply_target(self):
        if self.target_mode()=='selected':self.apply_selected()
        else:self.commit_name();self.apply_scheme()
    def restore_target(self):
        if self.target_mode()=='selected':self.restore_selected()
        else:self.restore_checked()
    def error(self,message):
        self.toast.notify('此 AE 版本不支持' if UNSUPPORTED in message else '操作未完成',message,error=True);self.status.setText(message);self.status.setToolTip(message)
        # A failure while closing cancels a pending install too, so a later close never starts it unasked.
        self.log.appendPlainText('操作未完成：'+message);self.close_after=False;self.pending_installer=None
        if self.pages.currentIndex()!=SETTINGS:self.unread+=1;self.update_nav()
    def start_scan(self):
        self.status.setText('正在读取插件名称资源……');self.work(lambda progress:scan(self.roots,lambda n,name:progress(f'已检查 {n} 个文件 · {name}')),self.show_scan)
    def show_scan(self,result):
        self.all_plugins,self.scan_errors=result;self.show_space();self.update_version_bar()
        self.status.setText('扫描完成，正在读取当前 AE……' if self.space.live and not self.live else '扫描完成')
        if self.live:self.update_names()
        elif self.space.live:QTimer.singleShot(150,self.connect_live)
    def show_space(self):
        self.plugins=self.space_plugins(self.space);self.table.clearSelection();self.show_detail([])
        self._fill=True;self.table.setSortingEnabled(False);self.table.setRowCount(len(self.plugins))
        mono=QFont('Cascadia Mono');mono.setStyleHint(QFont.StyleHint.Monospace);mono.setPixelSize(12)
        for row,p in enumerate(self.plugins):
            check=QTableWidgetItem();check.setFlags(Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if p.match_name in self.enabled else Qt.CheckState.Unchecked);self.table.setItem(row,CHECK,check)
            for col,value in ((ORIGINAL,p.display_name),(CANDIDATE,self.pending.get(p.match_name,'')),(CURRENT,'尚未连接'),(KEY,p.match_name),(CATEGORY,p.category),(STATUS,'')):
                item=QTableWidgetItem(value);item.setToolTip(p.path if col==ORIGINAL else value)
                if col!=CANDIDATE:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEditable)
                if col==KEY:item.setFont(mono)
                if col in (KEY,CATEGORY):item.setForeground(QColor(ui_kit.MUTED))
                self.table.setItem(row,col,item)
        self.table.setSortingEnabled(True);self._fill=False
        common=sum(1 for p in self.plugins if not self.space.owns(p.path)) if self.space.plugins else 0
        self.summary.setText(f'{self.space.label}：{len(self.plugins)} 条名称资源'+(f'（含公共目录 {common} 条）' if common else '')+f' · 共 {len(self.spaces)} 个 AE 版本 · {len(self.scan_errors)} 个文件未解析')
        self.update_names();self.filter_rows();self.update_nav()
    def connect_live(self):
        if not self.space.live:self.analyze_adapter(self.space);return
        self.status.setText('正在核对 AE 路径、构建版本和实际效果目录……');self.work(lambda progress:read_live(expected_image=self.space.exe),self.show_live)
    def show_live(self,report):
        self.live=report;self.toast.notify(f'{self.space.label} 已连接');self.update_names();self.update_pill();self.status.setText(f"已连接 AE，读取 {len(report['effects'])} 个实际效果")
        QTimer.singleShot(0,self.auto_reapply)
    # AE reads effect names from the plug-in files every time it starts, so the palette is English again after a
    # restart. The names last applied here (per AE version, minus later restores) are written back once per AE process.
    def kept_path(self)->Path:return self.space.folder/'kept-names.json'
    def kept_names(self)->dict[str,str]:
        try:value=json.loads(self.kept_path().read_text(encoding='utf8'))
        except (OSError,ValueError):return {}
        return {k:v for k,v in value.items() if isinstance(k,str) and isinstance(v,str) and v.strip()} if isinstance(value,dict) else {}
    def update_kept(self,add=None,remove=()):
        """add: {key: Chinese}; remove: keys, or None for all. Runs inside jobs (one at a time), touches no widget."""
        kept={} if remove is None else {k:v for k,v in self.kept_names().items() if k not in set(remove)}
        kept.update(add or {})
        try:write_json(self.kept_path(),kept)
        except OSError:pass  # Only the automatic re-apply depends on it; the write itself already happened.
    def auto_reapply(self):
        if not (self.live and self.space.live) or self.ui_preferences().get('reapplyNames') is False:return
        process=(self.live.get('pid'),self.live.get('creationTime'))
        if process==self.reapplied_for:return
        if self.busy():QTimer.singleShot(500,self.auto_reapply);return
        self.reapplied_for=process
        current={r['matchName']:r['displayName'] for r in self.live['effects']};english={p.match_name:p.display_name for p in self.plugins}
        due={k:v for k,v in self.kept_names().items() if k in current and current[k]!=self.target_name(english.get(k,''),v)}
        if not due:return
        self.status.setText(f'AE 重启后效果名称已变回原名，正在重新应用 {len(due)} 个……');self.writing=set(due)
        self.work(lambda progress:self.apply_names(due,instances=False),self.reapplied,lambda message:self.error('自动重新应用效果名称未完成：'+message))
    def reapplied(self,result):
        operation,live,read_error=result;written=[k for k in operation.get('applied',{}) if k not in set(operation.get('unchanged',[]))]
        if written:
            self.toast.notify('已重新应用效果名称',f'{len(written)} 项（AE 重启后会恢复原名）');self.log.appendPlainText(f'AE 重启后自动重新应用 {len(written)} 个效果名称：'+brief({k:operation["applied"][k] for k in written}))
            self.needs_refresh=True;self.status.setText('已自动重新应用效果名称；点击“刷新 AE 列表”后在 AE 中可见')
        self.update_refresh_hint();self.show_reread(live,read_error)
    def update_names(self):
        if not self.space.live:return self.paint_current(lambda key:'仅翻译 · 不支持实时改名')
        if not self.live:return self.paint_current(lambda key:'尚未连接')
        names={r['matchName']:r['displayName'] for r in self.live['effects']};self.paint_current(lambda key:names.get(key,MISSING))
    def paint_current(self,text):
        self._fill=True;self.table.setSortingEnabled(False)
        for row in range(self.table.rowCount()):
            item=self.table.item(row,CURRENT);item.setText(text(self.table.item(row,KEY).text()));self.color_current(row)
        self.table.setSortingEnabled(True);self._fill=False;self.update_counts()
    def row_status(self,row)->str:
        current=self.table.item(row,CURRENT).text();candidate=self.table.item(row,CANDIDATE).text().strip()
        if current==MISSING:return '未加载'
        if not candidate:return '待翻译'
        if not self.space.live:return '仅翻译'
        if not self.live:return '未连接'
        return '已应用' if current==self.target_name(self.table.item(row,ORIGINAL).text(),candidate) else '待应用'
    def color_current(self,row):
        item=self.table.item(row,CURRENT);candidate=self.table.item(row,CANDIDATE).text().strip()
        color=ui_kit.INK if self.live and candidate and item.text()==self.target_name(self.table.item(row,ORIGINAL).text(),candidate) else ui_kit.FAINT if item.text() in (MISSING,'尚未连接','仅翻译 · 不支持实时改名') else ui_kit.INK_2
        item.setForeground(QColor(color))
        status=self.table.item(row,STATUS)
        if status is not None:status.setText(self.row_status(row))
    # Scope: with a scheme in use, its effects (saved ones plus whatever the table now checks or names).
    def scheme_members(self)->set[str]|None:
        if self.active is None:return None
        return set(self.active.names)|self.active.enabled|set(self.current_names())|self.enabled
    def scoped(self)->bool:return self.active is not None and not self.show_all
    def set_show_all(self,on):
        self.show_all=bool(on);self.show_all_action.setChecked(self.show_all);self.update_scheme_bar()
        if self.active:self.status.setText('显示全部效果，未加入方案的变灰' if self.show_all else f'只显示方案“{self.active.title}”中的效果')
    def paint_membership(self,keys=None):
        """Greys rows outside the scheme in use (only seen when all effects are shown); they stay editable."""
        members=self.scheme_members();self._fill=True;sorting=self.table.isSortingEnabled();self.table.setSortingEnabled(False)
        for row in range(self.table.rowCount()):
            key=self.table.item(row,KEY).text()
            if keys is not None and key not in keys:continue
            outside=members is not None and key not in members
            for column,normal in ((ORIGINAL,ui_kit.INK),(CANDIDATE,ui_kit.INK),(KEY,ui_kit.MUTED),(CATEGORY,ui_kit.MUTED)):self.table.item(row,column).setForeground(QColor(ui_kit.FAINT if outside else normal))
            self.color_current(row)
            if outside:self.table.item(row,CURRENT).setForeground(QColor(ui_kit.FAINT))
            self.table.item(row,ORIGINAL).setData(Qt.ItemDataRole.UserRole,outside)
        self.table.setSortingEnabled(sorting);self._fill=False
    def in_scope(self,row,members=None)->bool:
        if not self.scoped():return True
        return self.table.item(row,KEY).text() in (members if members is not None else self.scheme_members())
    # Rows: search text and the metric tiles both filter; counts follow every change.
    def row_matches(self,row,mode)->bool:
        if mode=='all':return True
        key=self.table.item(row,KEY).text();candidate=self.table.item(row,CANDIDATE).text().strip();current=self.table.item(row,CURRENT).text()
        applied=bool(self.live and candidate and current==self.target_name(self.table.item(row,ORIGINAL).text(),candidate))
        return {'checked':key in self.enabled,'todo':not candidate,'applied':applied,'missing':current==MISSING}[mode]
    def filter_rows(self,*_):
        text=self.filter.text().casefold();mode=ROW_FILTERS[max(self.chip_group.checkedId(),0)][0];members=self.scheme_members()
        for row in range(self.table.rowCount()):
            hit=self.in_scope(row,members) and text in ' '.join(self.table.item(row,c).text() for c in range(ORIGINAL,CATEGORY+1)).casefold() and self.row_matches(row,mode)
            self.table.setRowHidden(row,not hit)
        self.paint_membership();self.update_counts();self.show_table_state();self.update_check_header()
    def visible_keys(self)->set[str]:return {self.table.item(row,KEY).text() for row in self.visible_rows()}
    def update_check_header(self):self.check_header.set_state(check_state(key in self.enabled for key in self.visible_keys()))
    def toggle_visible(self):
        keys=self.visible_keys()
        if keys:self.check_visible(not keys<=self.enabled)
    def show_table_state(self):
        if not self.plugins:
            self.table_empty.set_icon('effects');self.table_empty.show_message('这个版本没有找到插件名称','插件目录中没有可识别的效果资源。可以重新检测 AE 安装，或在“AE 版本”页为此版本添加插件目录。')
            self.empty_paths_btn.show();self.empty_clear_btn.hide();self.empty_all_btn.hide();self.table_stack.setCurrentIndex(0)
        elif not self.visible_rows() and self.scoped() and not any(self.in_scope(r,self.scheme_members()) for r in range(self.table.rowCount())):
            self.table_empty.set_icon('schemes');self.table_empty.show_message(f'方案“{self.active.title}”中还没有效果','显示全部效果后勾选或填写中文，它们就会加入这个方案；保存方案即可记住。')
            self.empty_paths_btn.hide();self.empty_clear_btn.hide();self.empty_all_btn.show();self.table_stack.setCurrentIndex(0)
        elif not self.visible_rows():
            self.table_empty.set_icon('search');self.table_empty.show_message('没有匹配的效果','换个关键词，或切换上方的统计卡查看其他状态。'+('当前只显示方案中的效果。' if self.scoped() else ''))
            self.empty_paths_btn.hide();self.empty_clear_btn.show();self.empty_all_btn.setVisible(self.scoped());self.table_stack.setCurrentIndex(0)
        else:self.table_stack.setCurrentIndex(1)
    def clear_filters(self):
        self.filter.clear();self.chips['all'].setChecked(True);self.filter_rows()
    def update_counts(self):
        members=self.scheme_members();rows=[row for row in range(self.table.rowCount()) if self.in_scope(row,members)]
        for key,text in ROW_FILTERS:
            count=len(rows) if key=='all' else sum(1 for row in rows if self.row_matches(row,key))
            self.chips[key].set_value(count)
        tile=self.chips['all'];caption='方案中' if self.scoped() else '全部'
        if tile.caption!=caption:tile.caption=caption;tile.setToolTip('只看当前方案中的效果；方案菜单里可选择“显示全部效果”' if self.scoped() else '只看“全部”的效果');tile.update()
        self.update_check_header();self.update_detail_actions()
    def visible_rows(self):return [row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)]
    def check_keys(self,keys,checked):
        if checked:self.enabled.update(keys)
        else:self.enabled.difference_update(keys)
        self.sync_rows(keys);self.save_enabled();self.status.setText(f"已{'勾选' if checked else '取消勾选'} {len(keys)} 个效果")
    def paint_checks(self,rows,state):
        """Live feedback while sweeping over boxes; saving waits for the release."""
        keys={self.table.item(row,KEY).text() for row in rows};(self.enabled.update if state else self.enabled.difference_update)(keys)
        self._fill=True;self.table.setSortingEnabled(False)
        for row in rows:self.table.item(row,CHECK).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        self.table.setSortingEnabled(True);self._fill=False
    def finish_checks(self,rows,state):
        keys={self.table.item(row,KEY).text() for row in rows if self.table.item(row,KEY)}
        self.check_anchor=self.table.item(rows[-1],KEY).text() if rows else self.check_anchor
        self.sync_rows(keys);self.save_enabled();self.status.setText(f"已{'勾选' if state else '取消勾选'} {len(keys)} 个效果")
    def check_visible(self,checked):self.check_keys({self.table.item(row,KEY).text() for row in self.visible_rows()},checked)
    def sync_rows(self,keys):
        # Duplicate resource rows (same effect in several AE versions) always show one shared state.
        self._fill=True;self.table.setSortingEnabled(False)
        for row in range(self.table.rowCount()):
            key=self.table.item(row,KEY).text()
            if key not in keys:continue
            self.table.item(row,CHECK).setCheckState(Qt.CheckState.Checked if key in self.enabled else Qt.CheckState.Unchecked)
            self.table.item(row,CANDIDATE).setText(self.pending.get(key,''));self.color_current(row)
        self.table.setSortingEnabled(True);self._fill=False;self.paint_membership(set(keys));self.update_counts()
    def selected_rows(self):return sorted({x.row() for x in self.table.selectionModel().selectedRows()})
    def selection_changed(self):
        self.show_detail(self.selected_rows())
        self.update_detail_actions();self.fit_detail()
    def show_detail(self,rows):
        if not rows:
            self.detail_eyebrow.setText('效果详情');self.selected_label.setText('在左侧表格中选择效果');self.detail_state.set(None,'')
            self.detail_info.setText('点击一行查看详情并输入中文名称；批量操作请勾选左侧复选框。');self.path_label.clear();self.name_edit.clear();return
        row=rows[0];item=lambda col:self.table.item(row,col).text()
        self.detail_eyebrow.setText(item(CATEGORY) or '效果');self.selected_label.setText(item(ORIGINAL))
        state=item(STATUS);self.detail_state.set(STATUS_MARK.get(state),{'已应用':'已在 AE 生效','待应用':'有中文，待应用','待翻译':'还没有中文名称'}.get(state,state))
        self.detail_info.setText(f'内部标识：{item(KEY)}\n分类：{item(CATEGORY)}\nAE 当前名称：{item(CURRENT)}')
        self.path_label.setText(re.sub(r'([\/])',lambda m:m.group(1)+'​',self.table.item(row,ORIGINAL).toolTip()));self.name_edit.setText(item(CANDIDATE))
    def table_menu(self,pos):
        index=self.table.indexAt(pos)
        if not index.isValid():return
        menu=self.row_menu(index.row());menu.exec(self.table.viewport().mapToGlobal(pos))
    def row_menu(self,row)->QMenu:
        """The context menu for one row; that row becomes the current one."""
        self.table.selectRow(row);key=self.table.item(row,KEY).text();checked=key in self.enabled
        menu=QMenu(self);menu.addAction(self.apply_selected_action);menu.addAction(self.restore_selected_action);menu.addAction(self.translate_action);menu.addSeparator()
        menu.addAction('取消勾选此效果' if checked else '勾选此效果',lambda:self.check_keys({key},not checked))
        edit=menu.addAction('编辑此效果的参数名称…',lambda:self.open_parameters(key));edit.setEnabled(self.space.live and not self.busy())
        self.fill_add_menu(menu.addMenu('加入方案'),[key])
        menu.addSeparator();copy=menu.addMenu('复制')
        for title,column in (('内部标识',KEY),('原始名称',ORIGINAL),('中文候选',CANDIDATE)):copy.addAction(title,lambda c=column:self.copy_column(c))
        menu.addAction('打开插件所在文件夹',lambda:self.reveal(self.table.item(row,ORIGINAL).toolTip()))
        return menu
    def fill_add_menu(self,menu:QMenu,keys:list[str],part='effects')->QMenu:
        """Lists every scheme of this version, plus a new one, as places to put keys."""
        menu.clear();word=PART_WORD[part]
        if not keys:menu.addAction(f'先勾选{word}').setEnabled(False);return menu
        if self.busy():menu.addAction('请等待当前操作完成').setEnabled(False);return menu
        for scheme in self.schemes:
            menu.addAction(scheme.title+('（当前方案）' if self.is_active(scheme) else ''),lambda s=scheme:self.put_in_scheme(keys,s,part))
        if self.schemes:menu.addSeparator()
        menu.addAction('新方案…',lambda:self.put_in_scheme(keys,None,part));return menu
    def put_in_scheme(self,keys:list[str],scheme:SavedScheme|None,part='effects'):
        """Adds effects (or parameters) to a saved scheme, checked, with the table's Chinese where there is one.
        None makes a new scheme holding just them. A new effects scheme leaves parameters out, so loading it keeps them;
        a new parameters scheme has no effects, so loading it clears the effects table (the prompt says so)."""
        word=PART_WORD[part];table_names=self.current_names() if part=='effects' else self.current_parameters()[0]
        names={k:v for k,v in table_names.items() if k in keys}
        if scheme is None:
            self.schemes,_=list_schemes(self.scheme_folder)
            hint=f'方案名称（包含 {len(keys)} 个{word}' +('）：' if part=='effects' else '；不含效果名称，载入时效果表格会被清空）：')
            title=self.scheme_title(f'用这些{word}新建方案',hint,next_title(self.schemes))
            if title is None:return
            value=dump_scheme(names,set(keys),title) if part=='effects' else dump_scheme({},set(),title,parameters=(names,set(keys)))
            try:path=self.free_path(title);write_json(path,value)
            except (OSError,ValueError) as error:self.error('新建方案失败：'+str(error));return
            self.preview_path=path;self.refresh_schemes(self.active.title if self.active else None)
            self.log.appendPlainText(f'已用 {len(keys)} 个{word}新建方案“{title}”');self.status.setText(f'已新建方案“{title}”（{len(keys)} 个{word}）');self.toast.notify('已新建方案',f'{title} · {len(keys)} 个{word}');return
        def change(saved,enabled):
            enabled.update(keys);saved.update(names)
        own=set(scheme.names)|scheme.enabled if part=='effects' else set((scheme.parameters or ({},set()))[0])|(scheme.parameters or ({},set()))[1]
        added=len(set(keys)-own)
        if self.edit_scheme(change,set(keys),f'已把 {len(keys)} 个{word}加入方案（新增 {added} 个）',scheme,part=part):
            self.toast.notify(f'已加入方案“{scheme.title}”',f'{len(keys)} 个{word}，其中新增 {added} 个'+self.undo_note('；可在翻译方案页按 {key} 撤销'))
    def copy_column(self,column):
        values=list(dict.fromkeys(self.table.item(row,column).text() for row in self.selected_rows()))
        QApplication.clipboard().setText('\n'.join(values));self.status.setText(f'已复制 {len(values)} 项')
    def reveal_selected(self):
        rows=self.selected_rows()
        if len(rows)==1:self.reveal(self.table.item(rows[0],ORIGINAL).toolTip())
    def reveal(self,path):
        if Path(path).exists():subprocess.Popen(['explorer','/select,',os.path.normpath(path)])
        else:self.error('插件文件不存在：'+path)
    def item_changed(self,item):
        if self._fill or item.column() not in (CHECK,CANDIDATE):return
        key=self.table.item(item.row(),KEY).text()
        if item.column()==CHECK:
            checked=item.checkState()==Qt.CheckState.Checked;anchor=self.check_anchor;self.check_anchor=key
            if anchor and anchor!=key and QApplication.keyboardModifiers()&Qt.KeyboardModifier.ShiftModifier:
                visible=self.visible_rows();keys=[self.table.item(r,KEY).text() for r in visible]
                if anchor in keys:
                    first,last=sorted((keys.index(anchor),visible.index(item.row()) if item.row() in visible else keys.index(key)))
                    self.check_keys(set(keys[first:last+1]),checked);return
            if checked:self.enabled.add(key)
            else:self.enabled.discard(key)
            self.sync_rows({key});self.save_enabled();return
        self.pending[key]=item.text();self.save_translations();self.sync_rows({key});self.selection_changed()
    def save_enabled(self):
        message=self.enabled_store.save()
        if message:self.log.appendPlainText(message)
        self.update_scheme_bar()
    def save_translations(self):
        message=self.store.save()
        if message:self.log.appendPlainText(message)
        self.update_scheme_bar()
    def save_parameters(self):
        for store in (self.parameter_store,self.parameter_enabled_store):
            message=store.save()
            if message:self.log.appendPlainText(message)
        self.update_scheme_bar()
    def open_parameters(self,effect=None):
        if not self.space.live:self.error('所选 AE 尚未适配原生参数汉化');return
        rows=self.selected_rows()
        if effect is None and len(rows)==1:effect=self.table.item(rows[0],KEY).text()
        self.go(PARAMETERS);self.parameter_page.focus_effect(effect)
    def apply_names(self,values,instances=True):
        # Effect names only; parameter labels are applied from the parameters page.
        # instances=False writes the palette catalog alone (the automatic re-apply never edits the open project).
        originals={plugin.match_name:plugin.display_name for plugin in self.plugins};chinese=dict(values)
        # Bilingual writing pairs the effect's own (resource) name with the Chinese; the candidates stay Chinese.
        if getattr(self,'bilingual',False):values={key:bilingual_name(originals.get(key,''),value) for key,value in values.items()}
        originals.update({key:entry['originalName'] for key,entry in self.session.journal()['entries'].items()})
        operation=self.session.apply(values,skip_missing=True)
        keep=getattr(self,'update_kept',None)
        if keep:keep(add={key:chinese[key] for key in operation.get('applied',{}) if key in chinese})
        if not instances:return with_live(operation,self.space.exe)
        originals.update({key:entry['originalName'] for key,entry in self.session.journal()['entries'].items()})
        try:operation['instanceResult']=self.instance_session.apply(operation.get('applied',{}),originals)
        except Exception as error:raise RuntimeError('效果目录名称已应用，但图层效果标题未完成同步；可重试应用或恢复：'+str(error)) from error
        return with_live(operation,self.space.exe)
    def translations_selected(self):
        """The current row's name from the detail field; batches go through the checked items."""
        rows=self.selected_rows()
        if not rows:raise ValueError('请先在表格中点击一个效果')
        key=self.table.item(rows[0],KEY).text();value=self.name_edit.text()
        self.table.setSortingEnabled(False);self.table.item(rows[0],CANDIDATE).setText(value);self.table.setSortingEnabled(True)
        return {key:value}
    def apply_selected(self):
        if not self.space.live:self.error(f'{self.space.label} 不支持实时改名');return
        try:values=self.translations_selected()
        except Exception as e:self.error(str(e));return
        self.status.setText(f'正在应用 {len(values)} 个名称……');self.writing=set(values)
        self.work(lambda progress:self.apply_names(values),self.applied)
    def apply_scheme(self):
        if not self.space.live:self.error(f'{self.space.label} 不支持实时改名');return
        names={k:self.pending[k] for k in self.enabled if self.pending.get(k,'').strip()};blank=len(self.enabled)-len(names)
        if not names:self.error('勾选的效果都还没有中文候选，请先翻译');return
        if blank:self.log.appendPlainText(f'{blank} 个勾选的效果还没有中文候选，本次跳过')
        self.status.setText(f'正在应用翻译方案中的 {len(names)} 个名称……');self.writing=set(names)
        self.work(lambda progress:self.apply_names(names),self.applied)
    def applied(self,result):
        operation,live,read_error=result;applied=operation.get('applied',{});unchanged=set(operation.get('unchanged',[]))
        written={k:v for k,v in applied.items() if k not in unchanged}
        if written:self.toast.notify('实时更名已生效',f'已应用 {len(written)} 项');self.log.appendPlainText(f'已应用 {len(written)} 项：'+brief(written))
        if unchanged:self.log.appendPlainText(f'{len(unchanged)} 项已经是目标名称，未重复写入')
        if shared:=operation.get('sharedNames'):
            # Allowed, only noted: AE lists these effects under the same name; renaming one tells them apart.
            self.log.appendPlainText(f'提示：{len(shared)} 组中文名称与其他效果相同，AE 列表里会出现同名项（需要区分时改其中一个）：'+'；'.join(shared))
        instances=operation.get('instanceResult',{});instance_count=instances.get('changed',0)
        if instance_count:self.toast.notify('图层效果标题已同步',f'已更新 {instance_count} 个实例');self.log.appendPlainText(f'已同步 {instance_count} 个图层效果标题')
        if instances.get('customSkipped'):self.log.appendPlainText(f"保留 {instances['customSkipped']} 个手动命名的效果实例")
        if operation.get('skipped'):self.log.appendPlainText(f"{len(operation['skipped'])} 项当前 AE 未加载，已跳过")
        if problem:=operation.get('unwritable'):
            # Names this AE's legacy encoding cannot hold were left out; the rest are written.
            self.log.appendPlainText(f"{len(problem['keys'])} 项未写入：{problem['message']}")
            self.toast.notify(f"{len(problem['keys'])} 项译名含无法保存的字符，已跳过",problem['message'],error=True)
        if written:self.needs_refresh=True
        self.update_refresh_hint()
        self.status.setText('名称已写入 AE，图层效果标题已同步；目录请点击“刷新 AE 列表”' if written or instance_count else '没有需要写入的名称')
        self.show_reread(live,read_error)
    def restore_selected(self):
        keys=sorted({self.table.item(row,KEY).text() for row in self.selected_rows()})
        if not keys:self.error('请先在表格中点击要恢复的效果');return
        self.work(lambda progress:with_live(self.restore_effect_names(keys),self.space.exe),self.restored)
    def restore_checked(self):
        keys=sorted(self.enabled)
        if not keys:self.error('还没有勾选效果；恢复全部请用“一键恢复全部默认”');return
        self.status.setText(f'正在恢复 {len(keys)} 个勾选项的原名……')
        self.work(lambda progress:with_live(self.restore_effect_names(keys),self.space.exe),self.restored)
    def restore_effect_names(self,keys=None):
        # The palette catalog must not be held hostage by project-instance
        # validation, user-renamed instances, or a blocked scripting relay.
        result={'restored':{}};errors=[]
        try:result=self.session.restore(keys)
        except Exception as error:errors.append('效果目录恢复失败：'+str(error))
        try:result['instanceResult']=self.instance_session.restore(keys)
        except Exception as error:errors.append('图层效果标题恢复失败：'+str(error))
        if errors:result['restoreErrors']=errors
        # Whatever the user restores is no longer put back after AE restarts, even if this restore partly failed.
        keep=getattr(self,'update_kept',None)
        if keep:keep(remove=None if keys is None else keys)
        return result
    def restore_all(self):self.work(lambda progress:with_live(self.restore_names(progress),self.space.exe),self.restored)
    def restore_names(self,progress):
        result=self.restore_effect_names()
        try:result['parameterResult']=self.parameter_session.restore(progress=progress)
        except Exception as error:result.setdefault('restoreErrors',[]).append('参数标签恢复失败：'+str(error))
        if self.close_after:
            if result.get('restoreErrors'):raise RuntimeError('部分原名未恢复，记录已保留：'+'；'.join(result['restoreErrors']))
            if result.get('instanceResult',{}).get('pending'):raise RuntimeError('其他工程或已移动效果仍有实例标题恢复记录；请返回原工程恢复后退出，或取消退出前恢复')
        return result
    # The schemes page writes a saved scheme's effect names to AE, or puts them back, through the same paths as the effects page.
    def scheme_effect_names(self,scheme:SavedScheme)->dict[str,str]:
        return {k:v.strip() for k,v in scheme.names.items() if v.strip()}
    def update_scheme_ae_buttons(self,scheme:SavedScheme|None):
        live=self.live is not None and self.space.live;idle=not self.busy()
        names=self.scheme_effect_names(scheme) if scheme else {};keys=(set(scheme.names)|scheme.enabled) if scheme else set()
        why='' if live else ('先连接 AE' if self.space.live else f'{self.space.label} 不支持实时改名')
        self.scheme_apply_btn.setEnabled(idle and live and bool(names));self.scheme_restore_btn.setEnabled(idle and live and bool(keys))
        self.scheme_apply_btn.setToolTip(f'把此方案中 {len(names)} 个有中文名称的效果写入 AE（不论是否勾选；当前 AE 未加载的会跳过）。不切换方案、不改表格'+(f'\n{why}' if why else ''))
        self.scheme_restore_btn.setToolTip(f'把此方案中的 {len(keys)} 个效果在 AE 中恢复为原名（只恢复本软件改过的）'+(f'\n{why}' if why else ''))
    def apply_saved_scheme(self):
        scheme=self.selected_scheme()
        if scheme is None:return
        if not self.space.live:self.error(f'{self.space.label} 不支持实时改名');return
        try:saved=open_scheme(scheme.path)
        except ValueError as error:self.error(f'方案“{scheme.title}”无法读取：{error}');return
        names=self.scheme_effect_names(saved)
        if not names:self.error(f'方案“{saved.title}”中没有带中文名称的效果');return
        if not confirm(self,f'把方案“{saved.title}”汉化到 AE？',f'将把 {len(names)} 个效果的名称写入 AE，当前 AE 未加载的会跳过。不会切换当前方案，也不改动表格。\n完成后需要刷新 AE“效果和预设”列表才能看到。','汉化'):return
        self.log.appendPlainText(f'从方案“{saved.title}”汉化 {len(names)} 个效果');self.status.setText(f'正在把方案“{saved.title}”中的 {len(names)} 个名称写入 AE……');self.writing=set(names)
        self.work(lambda progress:self.apply_names(names),self.applied)
    def restore_saved_scheme(self):
        scheme=self.selected_scheme()
        if scheme is None:return
        if not self.space.live:self.error(f'{self.space.label} 不支持实时改名');return
        try:saved=open_scheme(scheme.path)
        except ValueError as error:self.error(f'方案“{scheme.title}”无法读取：{error}');return
        keys=sorted(set(saved.names)|saved.enabled,key=str.casefold)
        if not keys:self.error(f'方案“{saved.title}”中没有效果');return
        if not confirm(self,f'还原方案“{saved.title}”中效果的原名？',f'将把这 {len(keys)} 个效果在 AE 中恢复为原名，包括图层上的效果标题；只恢复本软件改过的，其他效果不受影响。方案和表格不会改变。','还原'):return
        self.log.appendPlainText(f'还原方案“{saved.title}”中 {len(keys)} 个效果的原名');self.status.setText(f'正在还原方案“{saved.title}”中 {len(keys)} 个效果的原名……')
        self.work(lambda progress:with_live(self.restore_effect_names(keys),self.space.exe),self.restored)
    def restored(self,result):
        operation,live,read_error=result;restored=operation.get('restored',{})
        if not operation.get('restoreErrors'):self.toast.notify('恢复操作完成',f'已恢复 {len(restored)} 项效果目录名称' if restored else '效果目录没有需要恢复的名称')
        self.log.appendPlainText(f'已恢复 {len(restored)} 项：'+brief(restored) if restored else '所选效果没有需要恢复的更名')
        self.status.setText('已恢复原名；点击“刷新 AE 列表”后即可在 AE 中看到' if restored else '没有需要恢复的名称')
        if restored:self.needs_refresh=True
        self.update_refresh_hint()
        if operation.get('oldProcessEnded'):self.log.appendPlainText('之前更名的 AE 进程已结束，原名已随 AE 重启恢复，旧记录已清理');self.status.setText('之前的 AE 进程已结束，无需恢复')
        instances=operation.get('instanceResult',{})
        if instances:self.log.appendPlainText(f"已恢复 {instances.get('restored',0)} 个图层效果标题，{instances.get('pending',0)} 个实例保留恢复记录")
        self.show_reread(live,read_error)
        if 'parameterResult' in operation:
            result=operation['parameterResult'];self.log.appendPlainText(f"已恢复 {result.get('restored',0)} 个参数标签，{result.get('pending',0)} 个未加载参数保留记录")
            if self.parameter_page.rows:self.parameter_page.status.setText('参数名称已在“效果名称”页恢复，请重新读取以查看 AE 当前名称。')
        if operation.get('restoreErrors'):
            self.error('部分原名未恢复，记录已保留：'+'；'.join(operation['restoreErrors'])+'。效果目录已恢复的部分请手动刷新 AE 列表；失败部分需重试恢复。')
    def show_reread(self,live,read_error):
        if live:
            self.live=live;self.update_names();return
        self.live=None;self.update_names_unknown();self.log.appendPlainText('操作已完成，但重新读取 AE 失败：'+read_error)
    def update_names_unknown(self):self.update_names()
    def exit_restored(self,result):
        self.log.appendPlainText('之前更名的 AE 进程已结束，无需恢复' if result.get('oldProcessEnded') else '退出前已恢复原名')
    def refresh_native(self):
        if not self.live:return
        live=dict(self.live);image=str(self.space.exe)
        def done(result):
            self.needs_refresh=False;self.update_refresh_hint()
            self.status.setText('AE 原生列表已刷新');self.toast.notify('列表已刷新','AE“效果和预设”已显示最新名称')
            if result.get('diagnosticPath'):self.log.appendPlainText('刷新诊断：'+result['diagnosticPath'])
        self.work(lambda progress:refresh_effects(live['pid'],expected_image=image,creation_time=live['creationTime']),done)
    def configure_api(self):
        accepted=ApiDialog(self.settings,self).exec()==QDialog.DialogCode.Accepted
        self.update_api_info();self.update_usage();return accepted
    def translate_selected(self):
        rows=self.selected_rows()
        if not rows:self.error('请先在表格中点击要翻译的效果');return
        self.translate_rows(rows)
    def translate_checked(self,overwrite=False):
        rows=[];seen=set()
        for row in range(self.table.rowCount()):
            key=self.table.item(row,KEY).text()
            if key in self.enabled and key not in seen and (overwrite or not self.table.item(row,CANDIDATE).text().strip()):rows.append(row);seen.add(key)
        if not rows:self.toast.notify('无需翻译','勾选的效果都已有中文候选' if self.enabled else '还没有勾选效果');return
        self.translate_rows(rows)
    def translate_rows(self,rows):
        if not self.settings.ready() and not self.configure_api():return
        unique={self.table.item(row,KEY).text():{'id':self.table.item(row,KEY).text(),'sourceName':self.table.item(row,ORIGINAL).text(),'category':self.table.item(row,CATEGORY).text()} for row in rows}
        self.status.setText(f'正在调用 {self.settings.provider.name} 翻译 {len(unique)} 个名称……')
        s=self.settings
        def translate(progress):
            # Keep finished batches even when a later batch fails, so the user does not pay for them twice.
            try:return translate_names(list(unique.values()),s.key,s.base_url,s.model,progress=lambda done,total:progress(f'正在调用 {s.provider.name} 翻译……已完成 {done}/{total} 项'),service=s.service,usage=record_usage),''
            except TranslationError as failure:return failure.partial,str(failure)
        self.work(translate,self.translated)
    def translated(self,result):
        result,failure=result;self.pending.update(result);self.sync_rows(set(result))
        self.save_translations();self.selection_changed();self.status.setText('翻译完成，请查看中文候选后点击应用')
        if result and not failure:self.toast.notify('翻译完成',f'生成 {len(result)} 个中文候选，确认后应用到 AE')
        if failure:self.error(failure+('；已完成部分的候选已保存' if result else ''))
    # Saved schemes: a library in each version's schemes/ folder. A scheme holds a whole set: effect names
    # and parameter labels, each with its checks. Switching only replaces the tables; AE is never touched.
    def current_names(self)->dict[str,str]:return {k:v for k,v in self.pending.items() if v}
    def current_parameters(self)->tuple[dict[str,str],set[str]]:
        return {k:v for k,v in self.parameter_pending.items() if v},set(self.parameter_enabled)
    def scheme_dirty(self)->bool:
        if self.active is None:return False
        if self.current_names()!=self.active.names or self.enabled!=self.active.enabled:return True
        return self.active.parameters is not None and self.current_parameters()!=self.active.parameters
    def unsaved(self)->bool:
        """Switching schemes would lose the table's current choices."""
        names,checks=self.current_parameters()
        return self.scheme_dirty() if self.active else bool(self.current_names() or self.enabled or names or checks)
    def changes_for(self,scheme:SavedScheme)->dict[str,int]:
        return scheme_changes((self.current_names(),set(self.enabled)),self.current_parameters(),scheme)
    def refresh_schemes(self,title:str|None=None):
        """Re-read the scheme folder and select the scheme called title (None: no scheme)."""
        self.scheme_folder.mkdir(parents=True,exist_ok=True)
        self.schemes,skipped=list_schemes(self.scheme_folder)
        if skipped and skipped!=self.skipped_schemes:self.log.appendPlainText(f'方案文件夹中有 {skipped} 个文件无法识别为翻译方案，已跳过（文件未删除）')
        self.skipped_schemes=skipped;self.active=next((s for s in self.schemes if s.title==title),None)
        self.update_scheme_bar()
    def is_active(self,scheme:SavedScheme|None)->bool:return scheme is not None and self.active is not None and scheme.path==self.active.path
    def scheme_data(self,scheme:SavedScheme,dirty:bool,compact=False)->dict:
        current=self.is_active(scheme);lines=[scheme_counts(scheme)+' · '+ago(scheme.saved)] if compact else [scheme_counts(scheme),'保存于 '+ago(scheme.saved)]
        tip=f'{scheme.title}\n保存于 {scheme.saved:%Y-%m-%d %H:%M}'+('' if current and not dirty else '\n载入后'+describe_changes(self.changes_for(scheme)))
        return {'title':scheme.title,'lines':lines,'current':current,'dirty':current and dirty,'tip':tip}
    def update_scheme_bar(self):
        if not hasattr(self,'detail_stack'):return  # still building the pages
        dirty=self.scheme_dirty();switcher=self.scheme_switcher
        switcher.set_scheme(self.active.title if self.active else '未使用方案',dirty)
        switcher.setToolTip(('当前方案：'+self.active.title+(self.keymap.tip('save_scheme','\n表格有未保存的更改，保存') if dirty else '')) if self.active else '当前表格没有关联方案；点击切换或另存为新方案')
        self.save_scheme_action.setEnabled(not self.busy() and dirty);self.no_scheme_action.setEnabled(not self.busy() and self.active is not None)
        count=len(self.enabled);self.save_as_action.setText(f'另存为新方案（{count} 个勾选效果）…' if count else '新建方案…')
        self.save_as_action.setToolTip(self.keymap.tip('save_as','新方案只包含勾选的效果和它们的中文候选；不切换当前方案' if count else '新建一个空白方案；不切换当前方案'))
        scope=(self.active.path if self.active else None,self.active.saved if self.active else None,self.show_all)
        if scope!=self.scope_key and hasattr(self,'table'):self.scope_key=scope;self.filter_rows()
        self.fill_scheme_list(dirty);self.update_nav()
    def scheme_menu(self)->QMenu:
        """The switcher's menu, rebuilt each time it opens: every scheme, then the scheme actions."""
        menu=self.scheme_switcher.menu();menu.clear();dirty=self.scheme_dirty()
        caption=label(f'{self.space.label} · {len(self.schemes)} 个方案' if self.schemes else f'{self.space.label} · 还没有保存的方案','menuCaption')
        holder=QWidgetAction(menu);holder.setDefaultWidget(caption);holder.setEnabled(False);menu.addAction(holder)
        for scheme in self.schemes:
            row=SchemeRow(self.scheme_data(scheme,dirty,True));action=QWidgetAction(menu);action.setDefaultWidget(row);action.setData(scheme.title)
            row.picked.connect(lambda s=scheme:(menu.close(),self.choose_scheme(s)));menu.addAction(action)
        if self.active:menu.addAction(self.no_scheme_action);menu.addSeparator();self.show_all_action.setChecked(self.show_all);menu.addAction(self.show_all_action)
        menu.addSeparator()
        for action in (self.save_scheme_action,self.save_as_action,self.add_checked_menu,None,self.import_action,self.manage_action):
            if action is None:menu.addSeparator()
            elif isinstance(action,QMenu):action.setTitle(f'把勾选项加入方案（{len(self.enabled)} 项）' if self.enabled else '把勾选项加入方案');menu.addMenu(action)
            else:menu.addAction(action)
        self.fill_add_menu(self.add_checked_menu,sorted(self.enabled,key=str.casefold))
        return menu
    def fill_scheme_list(self,dirty):
        paths=[s.path for s in self.schemes]
        chosen=self.preview_path if self.preview_path in paths else self.active.path if self.active else None
        self.scheme_list.blockSignals(True);self.scheme_list.clear()
        for scheme in self.schemes:
            item=QListWidgetItem(scheme.title);data=self.scheme_data(scheme,dirty);item.setData(Qt.ItemDataRole.UserRole,data);item.setToolTip(data['tip'])
            self.scheme_list.addItem(item)
        row=paths.index(chosen) if chosen in paths else 0 if paths else -1
        self.scheme_list.setCurrentRow(row);self.scheme_list.blockSignals(False);self.filter_scheme_list();self.preview_row(row)
        for widget in (self.scheme_search_field,self.list_hint):widget.setVisible(bool(self.schemes))
    def filter_scheme_list(self):
        query=self.scheme_search.text().casefold().strip()
        for row,scheme in enumerate(self.schemes):self.scheme_list.item(row).setHidden(bool(query) and query not in scheme.title.casefold())
    def preview_row(self,row):
        self.preview_path=self.schemes[row].path if 0<=row<len(self.schemes) else None;self.show_scheme_preview()
    def selected_scheme(self)->SavedScheme|None:
        """The scheme highlighted on the schemes page, which need not be the active one."""
        return next((s for s in self.schemes if s.path==self.preview_path),None)
    def show_scheme_preview(self):
        scheme=self.selected_scheme();idle=not self.busy()
        self.detail_stack.setCurrentIndex(0 if scheme is None else 1)
        if scheme is None:return
        current=self.is_active(scheme);dirty=current and self.scheme_dirty()
        self.preview_title.setText(scheme.title);self.current_tag.setVisible(current);self.dirty_tag.setVisible(dirty)
        changes='与当前表格一致' if current and not dirty else '载入后'+describe_changes(self.changes_for(scheme))
        relative=ago(scheme.saved);relative='' if relative[:1].isdigit() and '前' not in relative else f'（{relative}）'
        self.preview_meta.setText(f'保存于 {scheme.saved:%Y-%m-%d %H:%M}{relative} · {changes}')
        self.scheme_mode='save' if dirty else 'none' if current else 'load'
        self.load_scheme_btn.setText({'save':'保存更改','none':'已是当前方案','load':'载入此方案'}[self.scheme_mode])
        self.load_scheme_btn.setToolTip({'save':self.keymap.tip('save_scheme','把当前表格保存到此方案'),'none':'当前表格与此方案一致','load':'用此方案替换当前的效果和参数表格；不会改动 AE'}[self.scheme_mode])
        self.update_scheme_ae_buttons(scheme)
        self.load_scheme_btn.setEnabled(idle and self.scheme_mode!='none');self.add_effects_btn.setEnabled(idle);self.discard_btn.setVisible(dirty);self.discard_btn.setEnabled(idle);self.scheme_more.setEnabled(idle)
        self.fill_effect_preview(scheme);self.fill_parameter_preview(scheme)
        self.effect_tile.set_value(len(set(scheme.names)|scheme.enabled))
        if scheme.parameters is None:self.param_tile.caption='参数名称（未保存）';self.param_tile.set_blank()
        else:self.param_tile.caption='参数名称';self.param_tile.set_value(len(set(scheme.parameter_names)|scheme.parameter_enabled))
        duplicates=duplicate_names(scheme.names,{p.match_name:p.display_name for p in self.plugins})
        if duplicates:
            shown=list(duplicates.items())[:3]
            self.dup_banner.text.setText(f'{sum(len(v) for v in duplicates.values())} 个效果的中文名称重复，AE 列表里会无法区分：'+'；'.join(f'“{name}”（{"、".join(keys)}）' for name,keys in shown)+(' 等' if len(duplicates)>3 else ''))
        self.dup_banner.setProperty('wanted',bool(duplicates));self.show_preview_tab()
    def diff_text(self,names,checks,key,new_names,new_checks)->str:
        """The current table's Chinese name for key when it differs from the scheme's; checks are not compared here."""
        return '' if names.get(key,'')==new_names.get(key,'') else names.get(key) or '（无中文）'
    # Both previews share one layout: box (the scheme's check), names, Chinese (editable), current table, delete.
    # Rows only in the current table have no box or delete button and show with the differences.
    def preview_tab_table(self)->QTableWidget:return (self.preview_table,self.param_preview_table)[max(self.preview_group.checkedId(),0)]
    def table_part(self,part)->tuple[dict[str,str],set[str]]:
        return (self.current_names(),set(self.enabled)) if part=='effects' else self.current_parameters()
    @staticmethod
    def scheme_part(scheme:SavedScheme,part)->tuple[dict[str,str],set[str]]:
        if part=='effects':return dict(scheme.names),set(scheme.enabled)
        names,checks=scheme.parameters or ({},set());return dict(names),set(checks)
    def parameter_label(self,key)->tuple[str,str]:
        """(effect, label) for a parameter identity; the label AE showed when it was read, else its internal name."""
        try:effect,param=split_identity(key)
        except ValueError:return key,''
        rows=getattr(getattr(self,'parameter_page',None),'rows',[])  # the page asks while it is still being built
        shown={r['id']:r['name'] for r in rows if r['name']!=self.parameter_pending.get(r['id'])}
        return effect,shown.get(key) or self.parameter_labels.get(key) or param
    def key_label(self,key,part)->str:
        if part=='effects':return next((p.display_name for p in self.plugins if p.match_name==key),key)
        effect,name=self.parameter_label(key);return f'{effect} · {name}'
    def fill_preview_rows(self,table,rows):
        """rows: (key, cells, checked, diff, tip, faint, extra), cells being the text columns before the Chinese name."""
        cols=table.cols;self.preview_filling=True;table.setRowCount(len(rows));base=Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable
        for row,(key,cells,chinese,checked,diff,tip,faint,extra) in enumerate(rows):
            check=QTableWidgetItem();check.setToolTip(tip)
            if extra:check.setFlags(base)
            else:check.setFlags(base|Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            for role,value in ((Qt.ItemDataRole.UserRole,bool(diff) or extra),(Qt.ItemDataRole.UserRole+1,extra),(Qt.ItemDataRole.UserRole+2,key)):check.setData(role,value)
            table.setItem(row,cols['check'],check)
            texts=[*cells,chinese,diff or ('仅在当前表格' if extra else '相同'),''];word=PART_WORD[table.part]
            for column,text in enumerate(texts,cols['check']+1):
                item=QTableWidgetItem(text);item.setFlags(base|(Qt.ItemFlag.ItemIsEditable if column==cols['chinese'] and not extra else Qt.ItemFlag.NoItemFlags))
                item.setToolTip(f'从方案中删除此{word}'+self.undo_note('（{key} 可撤销）') if column==cols['trash'] else '双击修改方案中的中文名称' if column==cols['chinese'] and not extra else tip)
                if (column<=cols['chinese'] and (extra or (faint and column==cols['check']+1))) or (column==cols['table'] and not diff):item.setForeground(QColor(ui_kit.FAINT))
                if column==cols['trash']:item.setData(Qt.ItemDataRole.UserRole+3,not extra)
                table.setItem(row,column,item)
        self.preview_filling=False
    def fill_effect_preview(self,scheme:SavedScheme):
        shown={p.match_name:p.display_name for p in self.plugins};names=self.current_names();own=set(scheme.names)|scheme.enabled;rows=[]
        for key in sorted(own|set(names)|self.enabled,key=lambda k:shown.get(k,k).casefold()):
            name=shown.get(key);extra=key not in own
            tip=f'内部标识：{key}'+('' if name else '\n此 AE 版本没有安装这个效果')+('' if not extra else '\n方案里没有这一项；载入后它的中文和勾选会被清除。右键可加入此方案')
            rows.append((key,[name or f'{key}（未安装）'],scheme.names.get(key,''),key in scheme.enabled,self.diff_text(names,self.enabled,key,scheme.names,scheme.enabled),tip,name is None,extra))
        self.fill_preview_rows(self.preview_table,rows)
    def fill_parameter_preview(self,scheme:SavedScheme):
        if scheme.parameters is None:self.param_preview_table.setRowCount(0);return
        new_names,new_checks=scheme.parameters;names,checks=self.current_parameters();own=set(new_names)|new_checks;rows=[]
        for key in own|set(names)|checks:
            effect,label_=self.parameter_label(key);extra=key not in own
            tip=f'效果：{effect}\n参数：{label_}'+('' if not extra else '\n方案里没有这一项；载入后它的中文和勾选会被清除。右键可加入此方案')
            rows.append((key,[effect,label_],new_names.get(key,''),key in new_checks,self.diff_text(names,checks,key,new_names,new_checks),tip,False,extra))
        rows.sort(key=lambda r:(r[1][0].casefold(),r[0]));self.fill_preview_rows(self.param_preview_table,rows)
    def show_preview_tab(self):
        effects=max(self.preview_group.checkedId(),0)==0;word='效果' if effects else '参数'
        self.add_effects_btn.setText(f'添加{word}…');self.dup_banner.setVisible(effects and self.dup_banner.property('wanted') is True)
        self.edit_hint.setText(' · '.join(x for x in ('复选框可按住上下拖动','双击中文名称修改',f'垃圾桶删除单个{word}',self.undo_note(),'右键更多') if x))
        self.filter_preview()
    def filter_preview(self):
        """Shows the chosen tab's table filtered by the search and the difference switch, or a note when there is nothing to list."""
        scheme=self.selected_scheme();tab=max(self.preview_group.checkedId(),0)
        if scheme is None:return
        table=self.preview_tab_table();query=self.preview_search.text().casefold().strip();only=self.diff_only.isChecked();shown=0
        for row in range(table.rowCount()):
            item=table.item(row,0);text=' '.join(table.item(row,c).text()+' '+table.item(row,c).toolTip() for c in range(table.columnCount())).casefold()
            hidden=(bool(query) and query not in text) or (only and not item.data(Qt.ItemDataRole.UserRole)) or (not only and bool(item.data(Qt.ItemDataRole.UserRole+1)))
            table.setRowHidden(row,hidden);shown+=not hidden
        self.update_preview_checks(table)
        if shown:self.preview_stack.setCurrentIndex(tab);return
        what='效果名称' if tab==0 else '参数名称'
        if tab==1 and scheme.parameters is None:self.preview_note.set_icon('params');self.preview_note.show_message('此方案没有保存参数名称','它是在方案包含参数之前保存的；载入时参数表格保持不变。可用“添加参数…”加入，或重新保存后一并保存参数。')
        elif only:self.preview_note.set_icon('check');self.preview_note.show_message('没有不同之处',f'载入此方案后，{what}表格不会改变。')
        elif query:self.preview_note.set_icon('search');self.preview_note.show_message('没有匹配的项目','换个关键词试试。')
        else:self.preview_note.set_icon('inbox');self.preview_note.show_message(f'此方案没有{what}',f'可以用“添加{what[:2]}…”加入。')
        self.preview_stack.setCurrentIndex(2)
    # Editing a saved scheme in place (either tab): check, rename, delete, add, all undoable with Ctrl+Z.
    def preview_key(self,row,table=None)->str:return (table or self.preview_table).item(row,0).data(Qt.ItemDataRole.UserRole+2)
    def preview_extra(self,row,table=None)->bool:return bool((table or self.preview_table).item(row,0).data(Qt.ItemDataRole.UserRole+1))
    def preview_own_rows(self,visible=True,table=None)->list[int]:
        table=table or self.preview_table;return [r for r in range(table.rowCount()) if not self.preview_extra(r,table) and not (visible and table.isRowHidden(r))]
    def preview_checked(self,row,table=None)->bool:return (table or self.preview_table).item(row,0).checkState()==Qt.CheckState.Checked
    def update_preview_checks(self,table=None):
        table=table or self.preview_table;rows=self.preview_own_rows(table=table);table.check_header.set_state(check_state(self.preview_checked(r,table) for r in rows))
        if table is self.preview_tab_table():self.delete_checked_btn.setEnabled(not self.busy() and any(self.preview_checked(r,table) for r in rows))
    def set_checks_in_scheme(self,keys,state,part='effects'):
        if keys:self.edit_scheme(lambda names,enabled:(enabled.update if state else enabled.difference_update)(keys),set(keys),f"已在方案中{'勾选' if state else '取消勾选'} {len(keys)} 个{PART_WORD[part]}",part=part)
    def paint_preview_checks(self,rows,state,table=None):
        table=table or self.preview_table;self.preview_filling=True
        for row in rows:
            if not self.preview_extra(row,table):table.item(row,0).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        self.preview_filling=False
    def finish_preview_checks(self,rows,state,table=None):
        table=table or self.preview_table;self.set_checks_in_scheme([self.preview_key(r,table) for r in rows if not self.preview_extra(r,table)],state,table.part)
    def toggle_preview_checks(self,table=None):
        table=table or self.preview_table;rows=self.preview_own_rows(table=table)
        if rows:self.set_checks_in_scheme([self.preview_key(r,table) for r in rows],not all(self.preview_checked(r,table) for r in rows),table.part)
    def toggle_in_scheme(self,keys,part='effects'):
        scheme=self.selected_scheme()
        if scheme is not None and keys:self.set_checks_in_scheme(keys,not all(k in self.scheme_part(scheme,part)[1] for k in keys),part)
    def preview_edited(self,item,table=None):
        """A box flipped by Space, or a name typed into a preview. The write waits for the editor to close, since it rebuilds the table."""
        table=table or self.preview_table;cols=table.cols;part=table.part
        if self.preview_filling or item.column() not in (cols['check'],cols['chinese']):return
        key=self.preview_key(item.row(),table)
        if item.column()==cols['check']:
            state=item.checkState()==Qt.CheckState.Checked
            self.queued_edit=(lambda names,enabled:(enabled.add if state else enabled.discard)(key),{key},f"已在方案中{'勾选' if state else '取消勾选'} 1 个{PART_WORD[part]}",None,True,part);self.edit_timer.start();return
        name=' '.join(item.text().split());shown=self.key_label(key,part)
        if part=='parameters' and name:
            try:validate_label(name)
            except ValueError as error:self.error(f'参数名称无效：{error}');self.queued_edit='refresh';self.edit_timer.start();return
        self.queued_edit=(lambda names,enabled:names.__setitem__(key,name) if name else names.pop(key,None),{key},f'已把方案中的 {shown} 改为“{name}”' if name else f'已清除方案中 {shown} 的中文名称',None,True,part)
        self.edit_timer.start()
    def run_queued_edit(self):
        edit,self.queued_edit=self.queued_edit,None
        if edit=='refresh':self.show_scheme_preview()
        elif edit:self.edit_scheme(*edit)
    def preview_clicked(self,row,column,table=None):
        table=table or self.preview_table
        if column==table.cols['trash'] and not self.preview_extra(row,table):self.delete_from_scheme([self.preview_key(row,table)],ask=False,table=table)
    def preview_selection(self,table=None)->list[str]:
        """Keys of the selected preview rows that belong to the scheme, in table order."""
        table=table or self.preview_table;rows=sorted({index.row() for index in table.selectionModel().selectedRows()})
        return [self.preview_key(r,table) for r in rows if not table.isRowHidden(r) and not self.preview_extra(r,table)]
    def delete_checked(self):
        table=self.preview_tab_table();self.delete_from_scheme([self.preview_key(r,table) for r in self.preview_own_rows(table=table) if self.preview_checked(r,table)],table=table)
    def delete_from_scheme(self,keys=None,ask=True,table=None):
        table=table or self.preview_table;part=table.part;word=PART_WORD[part]
        keys=keys if keys is not None else self.preview_selection(table);scheme=self.selected_scheme()
        if scheme is None or not keys:return
        listed='、'.join(self.key_label(k,part) for k in keys[:5])+(f' 等 {len(keys)} 个' if len(keys)>5 else '')
        note=f'它是当前方案，表格中这些{word}的中文候选和勾选也会一并清除。' if self.is_active(scheme) else '当前表格和 AE 中的名称不会改变。'
        if ask and not confirm(self,f'从方案“{scheme.title}”中删除 {len(keys)} 个{word}？',f'{listed}\n{note}'+self.undo_note('\n删除后可按 {key} 撤销。'),'删除',danger=True):return
        if self.edit_scheme(lambda names,enabled:[(names.pop(k,None),enabled.discard(k)) for k in keys],set(keys),f'已从方案中删除 {listed}',part=part):
            self.toast.notify('已从方案删除',f'{listed}'+self.undo_note(' · {key} 撤销'))
    def known_names(self,exclude:SavedScheme|None=None)->dict[str,tuple[str,str]]:
        """Chinese names already known for each effect, with their source. Later sources win:
        what AE shows now, then other AE versions on this computer (their schemes, then their tables;
        many plugins are shared between versions), then this version's schemes (newest last) and its table."""
        known={};originals={p.match_name:p.display_name for p in self.plugins}
        for effect in (self.live or {}).get('effects',[]):
            key,name=effect.get('matchName'),effect.get('displayName')
            if key in originals and name and name!=originals[key]:known[key]=(name,'AE 当前名称')
        for space in self.spaces:
            if space is self.space:continue
            # Read only: nothing is created or rewritten in another version's folder.
            for scheme in reversed(list_schemes(space.scheme_folder)[0] if space.scheme_folder.is_dir() else []):
                known.update({k:(v,f'{space.label} 方案“{scheme.title}”') for k,v in scheme.names.items() if v})
            known.update({k:(v,f'{space.label} 中文候选') for k,v in CandidateStore(space.map_path).values.items() if isinstance(v,str) and v.strip()})
        for scheme in reversed(self.schemes):
            if exclude is None or scheme.path!=exclude.path:known.update({k:(v,f'方案“{scheme.title}”') for k,v in scheme.names.items() if v})
        known.update({k:(v,'当前表格') for k,v in self.current_names().items()})
        return known
    def known_parameters(self,exclude:SavedScheme|None=None)->dict[str,tuple[str,str]]:
        """Chinese parameter labels already known, with their source, in the same order as known_names (later wins)."""
        known={}
        for space in self.spaces:
            if space is self.space:continue
            for scheme in reversed(list_schemes(space.scheme_folder)[0] if space.scheme_folder.is_dir() else []):
                known.update({k:(v,f'{space.label} 方案“{scheme.title}”') for k,v in (scheme.parameter_names or {}).items() if v})
            known.update({k:(v,f'{space.label} 参数记录') for k,v in CandidateStore(space.folder/'parameter-translations.json').values.items() if isinstance(v,str) and v.strip()})
        for scheme in reversed(self.schemes):
            if exclude is None or scheme.path!=exclude.path:known.update({k:(v,f'方案“{scheme.title}”') for k,v in (scheme.parameter_names or {}).items() if v})
        known.update({k:(v,'参数记录') for k,v in self.current_parameters()[0].items()})
        return known
    def add_to_scheme(self,keys=None,part=None):
        """Adds effects of this AE version, or parameters this computer has seen, checked, with a Chinese name already known for them."""
        scheme=self.selected_scheme();part=part or self.preview_tab_table().part
        if scheme is None:return
        known=self.known_names(scheme) if part=='effects' else self.known_parameters(scheme)
        if keys is None:
            names,checks=self.scheme_part(scheme,part);taken=set(names)|checks;rows={}
            if part=='effects':
                for plugin in self.plugins:
                    if plugin.match_name not in taken and plugin.match_name not in rows:
                        name,source=known.get(plugin.match_name,('',''));display=plugin.display_name if plugin.display_name==plugin.match_name else f'{plugin.display_name}（{plugin.match_name}）'
                        rows[plugin.match_name]=(plugin.match_name,display,name,source)
                keys=pick_effects(self,sorted(rows.values(),key=lambda r:r[1].casefold()))
            else:
                for key in set(known)|{r['id'] for r in self.parameter_page.rows}|set(self.parameter_labels)|self.parameter_enabled:
                    if key not in taken:name,source=known.get(key,('',''));rows[key]=(key,self.key_label(key,part),name,source)
                keys=pick_parameters(self,sorted(rows.values(),key=lambda r:r[1].casefold()))
        if not keys:return
        def change(names,enabled):
            for key in keys:
                enabled.add(key)
                if key in known and not names.get(key):names[key]=known[key][0]
        self.edit_scheme(change,set(keys),f'已向方案加入 {len(keys)} 个{PART_WORD[part]}',part=part)
    def use_table_name(self,keys,part='effects'):
        names=self.table_part(part)[0]
        self.edit_scheme(lambda saved,enabled:[saved.__setitem__(k,names[k]) for k in keys if names.get(k)],set(keys),f'已用当前表格的中文更新 {len(keys)} 个{PART_WORD[part]}',part=part)
    def edit_scheme(self,change,keys,message,scheme:SavedScheme|None=None,record=True,part='effects')->bool:
        """Rewrites a scheme's file (the highlighted one by default) with change(names, enabled) applied to its effects
        or its parameters. The active scheme is what the tables hold, so the same keys are updated there too.
        The previous values of the keys are kept so Ctrl+Z can put them back."""
        scheme=scheme or self.selected_scheme()
        if scheme is None or self.busy():return False
        try:saved=open_scheme(scheme.path)
        except ValueError as error:self.error(f'方案“{scheme.title}”无法读取：{error}');return False
        names,enabled=self.scheme_part(saved,part);before=(dict(names),set(enabled));change(names,enabled)
        if not keys:return False
        effects=(names,enabled) if part=='effects' else (saved.names,saved.enabled)
        parameters=(names,enabled) if part=='parameters' else saved.parameters
        try:write_json(saved.path,dump_scheme(*effects,saved.title,None,parameters))
        except OSError as error:self.error('修改翻译方案失败：'+str(error));return False
        if record:self.scheme_undo=[*self.scheme_undo[-29:],(saved.path,{k:before[0].get(k,'') for k in keys},{k for k in keys if k in before[1]},set(keys),message,part)]
        active=self.is_active(scheme);table=self.preview_table if part=='effects' else self.param_preview_table;bar=table.verticalScrollBar().value()
        self.refresh_schemes(self.active.title if self.active else None)
        if active:
            store,checks=(self.pending,self.enabled) if part=='effects' else (self.parameter_pending,self.parameter_enabled)
            for key in keys:
                if names.get(key):store[key]=names[key]
                else:store.pop(key,None)
                (checks.add if key in enabled else checks.discard)(key)
            if part=='effects':self.save_translations();self.save_enabled();self.sync_rows(set(keys));self.selection_changed()
            else:self.save_parameters();self.parameter_page.refresh_values()
        table.verticalScrollBar().setValue(bar)
        self.log.appendPlainText(f'方案“{saved.title}”：{message}'+('（已同步到当前表格）' if active else ''));self.status.setText(message+(self.undo_note('；{key} 撤销') if record else ''))
        return True
    def undo_scheme_edit(self):
        if not self.scheme_undo:self.status.setText('没有可以撤销的方案修改');return
        path,names,checks,keys,message,part=self.scheme_undo.pop()
        scheme=next((s for s in self.schemes if s.path==path),None)
        if scheme is None:self.error('无法撤销：该方案已被删除或重命名');return
        def change(saved,enabled):
            for key in keys:
                if names.get(key):saved[key]=names[key]
                else:saved.pop(key,None)
                (enabled.add if key in checks else enabled.discard)(key)
        self.preview_path=path;self.edit_scheme(change,keys,'已撤销：'+message,scheme,record=False,part=part)
    def preview_menu(self,pos,table=None):
        table=table or self.preview_table;menu=self.preview_row_menu(table.rowAt(pos.y()),table)
        if menu.actions():menu.exec(table.viewport().mapToGlobal(pos))
    def preview_row_menu(self,row,table=None)->QMenu:
        """Right-click on a preview: acts on the selected rows, or on the clicked row alone."""
        table=table or self.preview_table;part=table.part;menu=QMenu(self)
        if row<0 or self.selected_scheme() is None or self.busy():return menu
        if self.preview_extra(row,table):
            key=self.preview_key(row,table);menu.addAction('加入此方案',lambda:self.add_to_scheme([key],part));return menu
        if row not in {i.row() for i in table.selectionModel().selectedRows()}:table.selectRow(row)
        keys=self.preview_selection(table);names=self.table_part(part)[0]
        if len(keys)==1:menu.addAction('修改中文名称',lambda:table.editItem(table.item(row,table.cols['chinese'])))
        scheme=self.selected_scheme();saved_names,saved_checks=self.scheme_part(scheme,part);state=not all(k in saved_checks for k in keys)
        menu.addAction(f"{'勾选' if state else '取消勾选'} {len(keys)} 项" if len(keys)>1 else ('勾选' if state else '取消勾选'),lambda:self.toggle_in_scheme(keys,part))
        differs=[k for k in keys if names.get(k) and names[k]!=saved_names.get(k,'')]
        if differs:menu.addAction(f'改用当前表格的中文（{len(differs)} 项）',lambda:self.use_table_name(differs,part))
        menu.addSeparator();menu.addAction(f'从方案中删除 {len(keys)} 项…' if len(keys)>1 else '从方案中删除…',lambda:self.delete_from_scheme(keys,table=table))
        return menu
    def list_menu(self,menu:QMenu|None=None,with_load=True)->QMenu:
        """Actions for the scheme highlighted on the schemes page (right-click and the ⋯ button)."""
        menu=menu if menu is not None else QMenu(self);menu.clear();scheme=self.selected_scheme()
        if scheme is None:return menu
        if with_load and not self.is_active(scheme):menu.addAction('载入此方案',self.load_selected_scheme)
        menu.addAction('用当前表格覆盖…',self.overwrite_selected);menu.addSeparator()
        menu.addAction('重命名…',lambda:self.rename_scheme(scheme));menu.addAction('创建副本',lambda:self.duplicate_scheme(scheme));menu.addAction('导出为文件…',lambda:self.export_scheme(scheme))
        menu.addSeparator();menu.addAction('删除…',lambda:self.delete_scheme(scheme))
        return menu
    def primary_scheme_action(self):
        {'save':self.save_scheme,'load':self.load_selected_scheme}.get(getattr(self,'scheme_mode','load'),lambda:None)()
    def load_selected_scheme(self):
        scheme=self.selected_scheme()
        if scheme and not (self.is_active(scheme) and not self.scheme_dirty()):self.switch_to(scheme)
    def discard_changes(self):
        if not self.active:return
        try:scheme=open_scheme(self.active.path)
        except ValueError as error:self.error(f'方案“{self.active.title}”无法读取：{error}');return
        if not confirm(self,'放弃未保存的更改？',f'当前表格会恢复为方案“{scheme.title}”保存的内容（{describe_changes(self.changes_for(scheme))}）。AE 中已经应用的名称不会改变。','放弃更改',danger=True):return
        self.load_scheme(scheme.names,scheme.enabled,scheme.title,scheme.parameters);self.refresh_schemes(scheme.title)
    def overwrite_selected(self):
        scheme=self.selected_scheme()
        if scheme is None:return
        names,checks=self.current_parameters()
        if confirm(self,f'覆盖方案“{scheme.title}”？',f'用当前表格覆盖它：效果 {len(self.current_names())} 个中文、{len(self.enabled)} 个勾选；参数 {len(names)} 个中文、{len(checks)} 个勾选。覆盖后它会成为当前方案。','覆盖'):self.write_scheme(scheme.path,scheme.title,'已覆盖')
    def remember_active(self):
        try:write_json(self.active_path,{'name':self.active.title if self.active else None})
        except OSError:pass
    def choose_scheme(self,scheme:SavedScheme|None):
        if scheme is None:self.active=None;self.remember_active();self.update_scheme_bar();self.status.setText('当前表格已不再关联方案');return
        self.switch_to(scheme)
    def switch_to(self,scheme:SavedScheme):
        try:scheme=open_scheme(scheme.path)
        except ValueError as error:self.error(f'方案“{scheme.title}”无法读取：{error}');self.refresh_schemes(self.active.title if self.active else None);return
        changes=self.changes_for(scheme);same=not any(changes.values())
        if not same and self.unsaved():
            if not confirm(self,f'切换到方案“{scheme.title}”？',f'当前表格还有没保存到方案的内容，切换后会被替换。{describe_changes(changes)}。\nAE 中已经应用的名称不会自动改变，需要再应用一次。','切换'):self.update_scheme_bar();return
        self.active=scheme;self.remember_active()
        if same:self.status.setText(f'已切换到方案“{scheme.title}”')
        else:self.load_scheme(scheme.names,scheme.enabled,scheme.title,scheme.parameters)
        self.refresh_schemes(scheme.title)
    def scheme_title(self,caption,label_text,text):
        title,ok=ask_text(self,caption,label_text,text)
        if not ok:return None
        try:return clean_title(title)
        except ValueError as error:self.error(str(error));return None
    def free_path(self,title,exclude:SavedScheme|None=None)->Path:
        self.schemes,_=list_schemes(self.scheme_folder);path=scheme_file(self.scheme_folder,title)
        others=[s for s in self.schemes if exclude is None or s.path!=exclude.path]
        if any(s.title.casefold()==title.casefold() for s in others):raise ValueError(f'已经有名为“{title}”的方案，请换一个名称')
        if path.exists() and not (exclude and path.name.casefold()==exclude.path.name.casefold()):raise ValueError(f'方案文件夹中已有文件 {path.name}，请换一个名称')
        return path
    def unique_path(self,base)->tuple[str,Path]:
        title=base;number=2
        while True:
            try:return title,self.free_path(title)
            except ValueError:title=f'{base} {number}' if base.endswith('副本') else f'{base} ({number})';number+=1
    def current_scheme_value(self,title)->dict:
        return dump_scheme(self.current_names(),self.enabled,title,parameters=self.current_parameters())
    def write_scheme(self,path,title,verb):
        try:write_json(path,self.current_scheme_value(title))
        except OSError as error:self.error('保存翻译方案失败：'+str(error));return
        self.refresh_schemes(title);self.remember_active();names,checks=self.current_parameters()
        self.log.appendPlainText(f'{verb}方案“{title}”（效果 {len(self.current_names())} 个中文、{len(self.enabled)} 个勾选；参数 {len(names)} 个中文、{len(checks)} 个勾选）');self.status.setText(f'{verb}方案“{title}”')
        self.toast.notify(f'{verb}方案',title)
    def new_scheme_from_checks(self):
        """The switcher's 新建 / 另存为: a blank scheme when nothing is checked, otherwise one holding only the
        checked effects with their Chinese candidates (parameters left out, so loading it keeps them).
        The current scheme and the table stay as they are: switching would drop the unchecked candidates."""
        self.schemes,_=list_schemes(self.scheme_folder);keys=sorted(self.enabled,key=str.casefold)
        if keys:title=self.scheme_title('另存为新方案',f'方案名称（只包含勾选的 {len(keys)} 个效果）：',next_title(self.schemes))
        else:title=self.scheme_title('新建方案','方案名称（新建空白方案）：',next_title(self.schemes))
        if title is None:return
        names={k:self.pending[k] for k in keys if self.pending.get(k,'').strip()}
        value=dump_scheme(names,set(keys),title) if keys else dump_scheme({},set(),title,parameters=({},set()))
        try:path=self.free_path(title);write_json(path,value)
        except (OSError,ValueError) as error:self.error(('另存' if keys else '新建')+'方案失败：'+str(error));return
        self.preview_path=path;self.refresh_schemes(self.active.title if self.active else None)
        what=f'{len(keys)} 个勾选效果' if keys else '空白方案'
        self.log.appendPlainText(f'已新建方案“{title}”（{what}）');self.status.setText(f'已新建方案“{title}”（{what}）；当前方案未切换')
        self.toast.notify('已新建方案',f'{title} · {what}')
    def save_scheme_as(self):
        self.schemes,_=list_schemes(self.scheme_folder)
        title=self.scheme_title('另存为新方案','方案名称：',next_title(self.schemes))
        if title is None:return
        try:path=self.free_path(title)
        except ValueError as error:self.error(str(error));return
        self.write_scheme(path,title,'已保存新')
    def save_scheme(self):
        if self.active:self.write_scheme(self.active.path,self.active.title,'已保存')
    def rename_scheme(self,scheme:SavedScheme|None=None):
        old=scheme or self.active
        if old is None:return
        was_active=self.is_active(old)
        title=self.scheme_title('重命名方案','新的方案名称：',old.title)
        if title is None or title==old.title:return
        keep=lambda:title if was_active else (self.active.title if self.active else None)
        try:
            path=self.free_path(title,exclude=old);saved=open_scheme(old.path)
            # Renaming keeps the scheme's saved content, not the table's unsaved edits.
            write_json(old.path,dump_scheme(saved.names,saved.enabled,title,saved.saved,saved.parameters))
            if path.name!=old.path.name:os.rename(old.path,path)
        except (OSError,ValueError) as error:self.error('重命名方案失败：'+str(error));self.refresh_schemes(self.active.title if self.active else None);return
        self.preview_path=path;self.refresh_schemes(keep());self.remember_active();self.log.appendPlainText(f'方案“{old.title}”已重命名为“{title}”');self.status.setText('方案已重命名')
    def duplicate_scheme(self,scheme:SavedScheme|None=None):
        scheme=scheme or self.selected_scheme()
        if scheme is None:return
        try:
            saved=open_scheme(scheme.path);title,path=self.unique_path(clean_title(f'{saved.title} 副本'[-60:]))
            write_json(path,dump_scheme(saved.names,saved.enabled,title,parameters=saved.parameters))
        except (OSError,ValueError) as error:self.error('创建副本失败：'+str(error));return
        self.preview_path=path;self.refresh_schemes(self.active.title if self.active else None)
        self.log.appendPlainText(f'已为方案“{saved.title}”创建副本“{title}”');self.status.setText(f'已创建副本“{title}”')
    def delete_scheme(self,scheme:SavedScheme|None=None):
        scheme=scheme or self.active
        if scheme is None:return
        was_active=self.is_active(scheme)
        if not confirm(self,f'删除方案“{scheme.title}”？','只删除这个方案文件；当前表格中的中文候选、勾选以及 AE 中已经应用的名称都不会改变。','删除',danger=True):return
        try:scheme.path.unlink(missing_ok=True)
        except OSError as error:self.error('删除方案失败：'+str(error));return
        self.refresh_schemes(None if was_active else self.active.title if self.active else None);self.remember_active()
        self.log.appendPlainText(f'已删除方案“{scheme.title}”');self.status.setText('方案已删除')
    def export_scheme(self,scheme:SavedScheme|None=None):
        """Exports a saved scheme; for the active one with unsaved edits, asks which version to write."""
        scheme=scheme or self.selected_scheme() or self.active
        if scheme is None:return
        current=self.is_active(scheme) and self.scheme_dirty()
        if current and confirm(self,'导出包含未保存更改的版本？',f'方案“{scheme.title}”在当前表格里还有未保存的更改。选“保存并导出”会先保存再导出；取消则导出上次保存的版本。','保存并导出'):self.save_scheme();scheme=self.active or scheme
        path,_=QFileDialog.getSaveFileName(self,'导出翻译方案',str(Path.home()/scheme_file(Path(),scheme.title).name),'翻译方案 (*.json)')
        if not path:return
        try:saved=open_scheme(scheme.path);write_json(Path(path),dump_scheme(saved.names,saved.enabled,saved.title,saved.saved,saved.parameters))
        except (OSError,ValueError) as error:self.error('导出翻译方案失败：'+str(error));return
        self.log.appendPlainText(f'已导出翻译方案“{scheme.title}”：{path}');self.status.setText('翻译方案已导出');self.toast.notify('已导出方案',scheme.title)
    def import_scheme(self):
        path,_=QFileDialog.getOpenFileName(self,'导入翻译方案',str(Path.home()),'翻译方案 (*.json)')
        if not path:return
        try:scheme=open_scheme(Path(path))
        except ValueError as error:self.error(str(error));return
        title,target=self.unique_path(scheme.title)
        try:write_json(target,dump_scheme(scheme.names,scheme.enabled,title,scheme.saved,scheme.parameters))
        except OSError as error:self.error('导入翻译方案失败：'+str(error));return
        self.log.appendPlainText(f'已把 {Path(path).name} 导入为方案“{title}”')
        previous=self.active.title if self.active else None;self.refresh_schemes(previous)
        imported=next((s for s in self.schemes if s.title==title),None)
        if imported:self.preview_path=imported.path;self.switch_to(imported)
    def load_scheme(self,names,enabled,label_text,parameters=None):
        """Replaces the tables with a scheme; parameter labels only when the scheme carries them."""
        changed=set(self.pending)|set(self.enabled)|set(names)|enabled
        self.pending.clear();self.pending.update(names);self.enabled.clear();self.enabled.update(enabled)
        if parameters is not None:
            self.parameter_pending.clear();self.parameter_pending.update(parameters[0]);self.parameter_enabled.clear();self.parameter_enabled.update(parameters[1])
            self.save_parameters();self.parameter_page.refresh_values()
        self.save_translations();self.save_enabled();self.sync_rows(changed);self.selection_changed()
        extra=f'；参数 {len(parameters[0])} 个中文、{len(parameters[1])} 个勾选' if parameters is not None else '；此方案未包含参数，参数表格保持不变'
        self.log.appendPlainText(f'已载入翻译方案：{label_text}（效果 {len(names)} 个中文、{len(enabled)} 个勾选{extra}）');self.status.setText('翻译方案已载入，可在右侧详情点“应用勾选项”写入 AE')
    def add_directory(self):
        row=self.version_table.currentRow();space=self.spaces[row] if 0<=row<len(self.spaces) else self.space
        path=QFileDialog.getExistingDirectory(self,f'为 {space.label} 添加插件目录')
        if path and Path(path) not in self.roots:space.extra.append(Path(path));self.start_scan()
    def toast_area(self)->QRect:
        """The status line under the pages: wide as the pages, minus the busy indicator's place on the left."""
        left=self.pages.mapTo(self,QPoint(0,0)).x();width=max(self.pages.width()-2*170,240)
        return QRect(left+(self.pages.width()-width)//2,0,width,self.height()-6)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.toast.isVisible():self.toast.place()
    def closeEvent(self,event):
        if self.busy():event.ignore();self.status.setText('请等待当前操作完成后关闭');return
        dialog=getattr(self,'adapter_dialog',None)
        if dialog and dialog.validation and dialog.validation.held:
            event.ignore();dialog.restore_validation();self.status.setText('正在恢复验证改名，恢复完成后可关闭');return
        # A damaged journal cannot be restored from; closing must still work (AE keeps names until it restarts).
        try:entries=self.session.journal().get('entries') or self.parameter_session.journal().get('entries') or self.instance_session.journal().get('entries')
        except Exception:entries=None
        if self.exit_restore.isChecked() and entries and not self.close_after:
            event.ignore();self.close_after=True;self.work(lambda progress:self.restore_names(progress),self.exit_restored);return
        # A running update check is not waited for: it only reads, and its daemon thread ends with the program.
        event.accept()
        if self.pending_installer:self.launch_installer()

def ui_prefs()->dict:
    """ui.json: interface choices only (motion, dark mode, update checks and the like)."""
    try:value=json.loads((data_root()/'ui.json').read_text(encoding='utf8'))
    except (OSError,ValueError):return {}
    return value if isinstance(value,dict) else {}

def main():
    app=QApplication(sys.argv);ui_kit.set_theme(ui_prefs().get('darkMode') is True)    # before any widget, so there is no light flash
    apply_theme(app,ui_kit.stylesheet(),ui_kit.is_dark());ui_kit.install(app)
    window=Window();window.show();sys.exit(app.exec())
if __name__=='__main__':main()
