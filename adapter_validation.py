"""The candidate -> independent read check -> owned QA -> local certificate chain."""
from dataclasses import replace
from pathlib import Path
import json,shutil,time,uuid,threading
from ae_profiles import trial_adapter
from adapter_resolver import CatalogMismatch,Resolver,registry_layout
from adapter_host import HostBridge
from parameter_names import bridge_root,ParameterSession,ParameterCatalog,identity,_host_names,run_native,module_read_blocked
from live_names import NameSession,read_live,_save
from local_adapters import save_validated
from local_adapters import profile_data
from ae_paths import path_key
from runtime import legacy_text_problem,write_diagnostic,write_json_atomic

EFFECT_LABEL='适配验证效果';PARAMETER_LABEL='适配参数'
DRIZZLE_LABELS={'0001':'滴落率','0002':'存续时间','0003':'涟漪','0004':'位移','0005':'波纹高度','0006':'扩散','0007':'光照','0015':'明暗'}

def pixel_hash(path):
    import hashlib
    from PySide6.QtGui import QImage
    image=QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
    if image.isNull():raise RuntimeError('QA 渲染结果不是可读取图片')
    return hashlib.sha256(bytes(image.constBits())).hexdigest()

def values_only(rows):return [{k:v for k,v in row.items() if k!='name'} for row in rows]

class ValidationRun:
    def __init__(self,exe,analysis_report):
        if recover_pending(exe) is not None:raise RuntimeError('存在未恢复的验证会话，请先使用“恢复未完成验证”')
        self.exe=Path(exe);self.report=analysis_report;self.profile=None;self.checks={};self.fixture=None;self.before=None;self.held=False;self.stage='candidate';self.error='';self.review_ready=False;self.cancel_requested=threading.Event()
        self.folder=bridge_root()/'adapter-qa'/uuid.uuid4().hex;self.bridge=HostBridge(exe,self.folder)
        self.effects=NameSession();self.effects.expected_image=self.exe;self.effects.journal_path=self.folder/'effects-session.json'
        self.parameters=ParameterSession();self.parameters.expected_image=self.exe;self.parameters.path=self.folder/'parameters-session.json'
    @property
    def projects(self):return (self.bridge.project,self.bridge.second)
    def notify(self,progress,text):
        if self.cancel_requested.is_set():raise RuntimeError('已取消验证；正在恢复本次修改')
        self.stage=text
        if progress:progress(text)
    def check(self,name,value):
        self.checks[name]=bool(value)
        if not value:raise RuntimeError('验证失败：'+name)
    def _verify_analysis(self,resolver):
        if self.report.get('kind')!='ae-adapter-analysis' or self.report.get('version')!=resolver.version:raise ValueError('候选报告与所选构建不一致')
        if self.report.get('modules')!={name:m['sha256'] for name,m in resolver.modules.items()}:raise ValueError('识别后模块发生变化')
        evidence={e['field']:e['value'] for e in resolver.getters()}
        if self.report.get('analysisSource','ai') not in ('code','ai') or self.report.get('runtimeEnabled') is not False or self.report.get('hostVerified') is not False:raise ValueError('候选来源或状态无效')
        fields=self.report.get('candidateReadConfig')
        if not fields or any(k not in evidence or evidence[k]!=v for k,v in fields.items()):raise ValueError('候选字段无法由本机重新复现')
    def _catalog_diagnostic(self,details)->str:
        """Saves how AE's catalog and the memory catalog differed (effect names and field bytes, no addresses,
        project or personal data), so a user can send it; returns the note naming the file."""
        path=self.bridge.folder/'catalog-diagnostic.json'
        try:write_json_atomic(path,{'schemaVersion':1,'kind':'catalog-diagnostic','aeVersion':self.bridge.version,**details},ensure_ascii=False,indent=1)
        except OSError:return ''
        return '；诊断记录：'+str(path)
    def _helper_failure(self,p,asked,codec)->str:
        """Why the helper's module and encoding check failed: a stalled module read it gave up (after 10 s) is named,
        and an outside look at AE (rewritten file APIs, foreign modules) is saved as a diagnostic and summarized."""
        import ae_environment
        environment=ae_environment.scan(p);found=ae_environment.summary(environment)
        path=write_diagnostic('ae-environment',{'aeVersion':self.bridge.version,'helperFailed':True,**environment})
        return (module_read_blocked(asked) or '原生结构或编码核验失败，未开始中文写入')+(f'。{found}' if found else '')+(f'；诊断记录：{path}' if path else '')
    def run(self,progress=None):
        """Requires the selected AE ready with an empty project. No user projects are closed."""
        self.bridge.progress=progress
        self.notify(progress,'独立核验 候选与模块结构……')
        resolver=Resolver(self.exe)
        try:
            self._verify_analysis(resolver);status=self.bridge.call('status')
            if status.get('project') or status.get('items'):raise RuntimeError('请先自行保存并关闭当前工程，使所选 AE 保持空工程')
            profile=resolver.complete_static(status['hostVersion']);host=self.bridge.call('catalog')['effects']
            with self.bridge.process() as p:
                try:layout=registry_layout(p,profile,host)
                except CatalogMismatch as error:raise RuntimeError(str(error)+self._catalog_diagnostic(error.details)) from None
            # How AE reads internal names is kept with the profile (AE 2021: system code page, unlike its names).
            self.profile=replace(profile,registry_vector=layout['offset'],match_encoding=layout['matchEncoding'])
            resolver.verify_files()
        finally:resolver.close()
        self.notify(progress,'创建软件自有 QA 工程……')
        self.fixture=self.bridge.call('prepare')['fixture'];shutil.copy2(self.bridge.project,self.bridge.second)
        try:
            self.notify(progress,'核对原生接口与实际编码……')
            with trial_adapter(self.profile,self.projects),self.bridge.process() as p:
                _host_names(p);asked=time.time();codec=run_native(p,[],action='codec')
                if codec.get('error') or codec.get('codec') not in (1,2):raise RuntimeError(self._helper_failure(p,asked,codec))
                self.profile=replace(self.profile,encoding='utf8' if codec['codec']==1 else 'mbcs')
            if self.profile.encoding=='mbcs':
                problem=legacy_text_problem([EFFECT_LABEL,PARAMETER_LABEL,*DRIZZLE_LABELS.values()])
                if problem:raise RuntimeError(problem)
            with trial_adapter(self.profile,self.projects):
                self.bridge.assert_owned();live=read_live(expected_image=self.exe)['effects'];host=self.bridge.call('catalog')['effects']
                # Effects without an internal name are left out of the live read, so they are left out here too.
                mapping=lambda rows:{r['matchName']:(r['displayName'],r['category']) for r in rows if r['matchName']}
                self.check('catalogMatches',mapping(live)==mapping(host))
                self.before=self.bridge.call('snapshot')['rows']
                self.notify(progress,'核对参数标识池与当前参数树……')
                with self.bridge.process() as p:
                    actual=_host_names(p);rows=ParameterCatalog(p).rows(rescan=True,progress=progress)
                    self.check('parameterNamesMatch',bool(rows) and all(row['id'] in actual and row['name'] in actual[row['id']] for row in rows))
                self.labels=self.parameter_labels({row['id'] for row in rows});self.effect_names={self.fixture['testEffect']:EFFECT_LABEL}
                self.notify(progress,'验证有效渲染对照……');self.baseline=self.render('baseline');disabled=self.render('disabled',disabled=True)
                self.check('visibleRenderEffect',self.baseline!=disabled)
                self.notify(progress,'受控写入效果与参数中文名……');self.apply()
                after=self.bridge.call('snapshot')['rows'];self.check('valuesPreserved',values_only(self.before)==values_only(after));self.check_names(after)
                edit=self.bridge.call('edit',**{k:self.fixture[k] for k in ('parameterEffect','parameterMatch')})
                expected=self.labels[identity(self.fixture['parameterEffect'],self.fixture['parameterMatch'])]
                self.check('valueEdit',edit['changed']==edit['before']+1 and edit['restored']==edit['before'] and edit['nameBefore']==expected and edit['nameAfter']==expected)
                self.check('translatedRender',self.render('translated')==self.baseline)
                self.notify(progress,'验证三轮恢复与重应用……')
                for cycle in range(3):
                    self.notify(progress,f'验证恢复与重应用：第 {cycle+1}/3 轮……');self.restore();self.apply();self.check_names(self.bridge.call('snapshot')['rows'])
                self.check('repeatCycles',True)
                self.notify(progress,'验证 QA 工程切换……');self.bridge.call('switch',target=self.bridge.second.as_posix());self.apply();self.check_names(self.bridge.call('snapshot')['rows']);self.check('projectSwitch',True)
                self.restore();self.bridge.call('switch',target=self.bridge.project.as_posix())
                self.check('restoredExactly',self.bridge.call('snapshot')['rows']==self.before);self.check('restoredRender',self.render('restored')==self.baseline)
                self.apply();self.bridge.call('show');self.held=True;self.notify(progress,'自动验证通过，等待原生面板核对');self.review_ready=True
                return {'stage':'native_review','checks':dict(self.checks),'catalogCount':len(live),'profileVersion':self.profile.version}
        except Exception as error:
            self.error=str(error)
            try:self.restore()
            except Exception:self.error+='；恢复未完成，记录已保留，请勿删除 QA 目录'
            self.stage='failed';raise RuntimeError(self.error) from None
    def parameter_labels(self,available):
        fx=self.fixture['parameterEffect'];target=identity(fx,self.fixture['parameterMatch'])
        labels={target:PARAMETER_LABEL}
        if fx=='CC Drizzle':
            # Another build may lack some of these streams; test those it has.
            labels={key:value for k,value in DRIZZLE_LABELS.items() if (key:=identity(fx,'CC Drizzle-'+k)) in available}
        if target not in labels or target not in available:raise RuntimeError('QA 测试参数在此 AE 中不可识别：'+self.fixture['parameterMatch'])
        return labels
    def apply(self):
        self.bridge.assert_owned();self.checkpoint();self.effects.apply(self.effect_names);self.parameters.apply(self.labels);self.held=True;self.checkpoint()
    def restore(self):
        if not self.profile:return
        if not self.pending():self.held=False;return
        if self.original_ended():
            for path in (self.effects.journal_path,self.parameters.path):_save(path,{'schemaVersion':1,'entries':{}})
            self.held=False;return
        with trial_adapter(self.profile,self.projects):
            self.bridge.assert_owned();self.parameters.restore();self.effects.restore();self.held=False
    def render(self,name,disabled=False):
        frame=self.folder/(name+'-'+uuid.uuid4().hex+'.png');self.bridge.call('render',frame=frame.as_posix(),disabled=disabled);return pixel_hash(frame)
    def check_names(self,rows):
        names={identity(row['effect'],row['matchName']):row['name'] for row in rows}
        self.check('translatedNames',all(names.get(k)==v for k,v in self.labels.items()))
        catalog=self.bridge.call('catalog')['effects']
        if not all(any(row['matchName']==key and row['displayName']==value for row in catalog) for key,value in self.effect_names.items()):raise RuntimeError('宿主效果中文名称回读失败')
    def refresh(self):
        from ae_ui import refresh_effects
        self.bridge.assert_owned()
        with self.bridge.process() as p:return refresh_effects(p.pid,expected_image=p.image,creation_time=p.creation_time)
    def pending(self):return len(self.parameters.journal()['entries'])+len(self.effects.journal()['entries'])
    def finish(self,native,progress=None):
        if not self.review_ready:raise RuntimeError('尚未完成自动验证')
        from local_adapters import validation_complete
        if not validation_complete(self.checks,native):raise RuntimeError('请完成全部原生面板核对')
        self.notify(progress,'恢复原名并核验最终状态……')
        with trial_adapter(self.profile,self.projects):
            self.restore();self.bridge.call('switch',target=self.bridge.project.as_posix())
            self.check('restoredExactly',self.bridge.call('snapshot')['rows']==self.before)
            self.check('restoredRender',self.render('final-restored')==self.baseline)
            if self.pending():raise RuntimeError('仍有待恢复记录，不能启用')
        stored=save_validated(self.exe,self.profile,self.checks,native,self.pending())
        self.stage='enabled';self.review_ready=False;self.write_result(native)
        return {'stage':'enabled','version':stored.version,'checks':self.checks,'pending':0}
    def cancel(self):
        if self.profile:
            self.restore()
            if self.pending():raise RuntimeError('恢复未完成，不能关闭验证窗口')
        self.stage='cancelled';self.review_ready=False;self.write_result({});return {'stage':'cancelled','pending':0}
    def write_result(self,native):
        _save(self.folder/'validation-result.json',{'schemaVersion':1,'stage':self.stage,'version':self.profile.version if self.profile else None,'checks':self.checks,'native':native,'pending':self.pending(),'error':self.error})
    def checkpoint(self):
        if self.profile:
            _save(self.folder/'validation-state.json',{'schemaVersion':1,'kind':'owned-adapter-qa','exe':str(self.exe),'profile':profile_data(self.profile),'identity':self.bridge.identity,'fixture':self.fixture,'checks':self.checks})
    def original_ended(self):
        if not self.bridge.identity:return False
        from win_process import Process
        import ctypes as C
        from ctypes import wintypes as W
        pid,created=self.bridge.identity
        try:
            with Process(pid,write=False) as p:
                if p.creation_time!=created:return True
                p.k.GetExitCodeProcess.argtypes=[C.c_void_p,C.POINTER(W.DWORD)];p.k.GetExitCodeProcess.restype=W.BOOL
                code=W.DWORD()
                if not p.k.GetExitCodeProcess(p.handle,C.byref(code)):raise C.WinError(C.get_last_error())
                return code.value!=259
        except OSError as error:
            if error.winerror==87:return True
            raise RuntimeError('无法确认原 AE 会话是否结束，已保留恢复记录') from None
        except ValueError:return True # PID was reused by a non-AE process.

def recover_pending(exe):
    """Reconstruct only task-owned, independently re-parsed pending runs."""
    root=bridge_root()/'adapter-qa'
    if not root.is_dir():return None
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or folder.is_junction():continue
        state=folder/'validation-state.json'
        if not state.is_file():continue
        if state.stat().st_size>131072:raise RuntimeError('验证恢复文件异常，已保留')
        data=json.loads(state.read_text(encoding='utf8'))
        if path_key(data.get('exe',''))!=path_key(exe):continue
        # Empty journals need no further decoding or expensive module analysis.
        entries=[]
        for filename in ('effects-session.json','parameters-session.json'):
            path=folder/filename
            if path.is_file():
                value=json.loads(path.read_text(encoding='utf8'))
                if not isinstance(value,dict) or not isinstance(value.get('entries'),dict):raise RuntimeError('验证恢复日志损坏，已保留')
                entries.append(bool(value['entries']))
        if not any(entries):continue
        if data.get('schemaVersion')!=1 or data.get('kind')!='owned-adapter-qa':raise RuntimeError('验证恢复文件身份异常')
        from ae_profiles import Profile
        raw=dict(data['profile']);raw['count_bytes']=bytes.fromhex(raw['count_bytes']);profile=Profile(**raw)
        resolver=Resolver(exe)
        try:
            fresh=resolver.complete_static(profile.host_version)
            for key,value in profile_data(fresh).items():
                if key not in ('registry_vector','encoding','match_encoding','enabled') and profile_data(profile)[key]!=value:raise RuntimeError('恢复配置与模块不匹配，已保留记录')
        finally:resolver.close()
        if profile.encoding not in ('utf8','mbcs') or profile.match_encoding not in ('','utf8','mbcs') or type(profile.registry_vector) is not int or profile.registry_vector not in range(0,129,8):raise RuntimeError('恢复配置字段不符')
        identity=data.get('identity')
        if not isinstance(identity,list) or len(identity)!=2 or not all(type(i) is int and i>0 for i in identity):raise RuntimeError('恢复会话身份损坏')
        run=ValidationRun.__new__(ValidationRun);run.exe=Path(exe);run.folder=folder;run.report=None;run.profile=profile;run.bridge=HostBridge(exe,folder);run.bridge.identity=tuple(identity);run.bridge.host_version=profile.host_version
        run.fixture=data.get('fixture');run.checks=data.get('checks',{});run.before=None;run.held=True;run.review_ready=False;run.stage='recovery';run.error='';run.cancel_requested=threading.Event()
        run.effects=NameSession();run.effects.expected_image=run.exe;run.effects.journal_path=folder/'effects-session.json';run.parameters=ParameterSession();run.parameters.expected_image=run.exe;run.parameters.path=folder/'parameters-session.json'
        run.pending() # Use the sessions' strict journal validators too.
        return run
    return None
