"""Fixed-action local transport for owned validation projects, without profile bypasses."""
from pathlib import Path
import json,subprocess,time,uuid
from ae_paths import path_key
from runtime import native_probe,file_version,asset_root,write_json_atomic
from parameter_names import bridge_root
from win_process import Process

class HostBridge:
    def __init__(self,exe,folder):
        self.exe=Path(exe);self.folder=Path(folder).resolve();self.project=self.folder/'QA.aep';self.second=self.folder/'QA_second.aep';self.comp='Localizer_Adapter_'+self.folder.name
        self.version=file_version(self.exe);self.host_version=None;self.identity=None;self.progress=None
        root=(bridge_root()/'adapter-qa').resolve()
        if not self.folder.is_relative_to(root) or self.folder==root:raise ValueError('QA 目录必须属于软件专用交换目录')
        try:self.folder.mkdir(parents=True,exist_ok=True)
        except OSError as error:raise RuntimeError('软件所在目录不可写，无法创建 QA 交换目录；请把完整软件目录放在可写位置（例如安装到当前用户目录）后重试') from error
    def process(self):
        probe=native_probe();running=probe.get('processes',[])
        def reject(message):
            self.diagnostic('host_rejected',processes=running,message=message)
            raise RuntimeError(message+'；诊断记录：'+str(self.folder/'host-diagnostic.json'))
        if probe.get('error'):reject('AE 进程检测失败：'+str(probe['error']))
        if not running:reject('没有检测到正在运行的 AE，请先启动所选版本')
        if len(running)!=1:
            details='、'.join(f"PID {row.get('pid','?')} ({row.get('fileVersion') or '版本未知'}{'; error='+str(row['error']) if row.get('error') else ''})" for row in running[:16])
            reject(f'检测到 {len(running)} 个 AE 进程：{details}。请只运行一个所选 AE')
        row=running[0]
        if row.get('error') or not row.get('imagePath'):reject('无法读取 AE 进程身份或权限不足，未继续验证')
        if path_key(row.get('imagePath',''))!=path_key(self.exe):reject('正在运行的 AE 与所选验证版本不一致')
        try:p=Process(row['pid'],write=False)
        except (OSError,ValueError) as error:reject('无法打开所选 AE 进程：'+str(error))
        if path_key(p.image)!=path_key(self.exe) or file_version(Path(p.image))!=self.version:
            p.close();raise RuntimeError('AE 路径或版本发生变化')
        identity=(p.pid,p.creation_time)
        if self.identity is not None and identity!=self.identity:p.close();raise RuntimeError('AE 已重新启动，请重新开始验证')
        self.identity=identity;return p
    def diagnostic(self,event,processes=None,**details):
        # Persist only host identity and transport state, never module bytes or AI settings.
        data={'schemaVersion':1,'event':event,'timeMs':int(time.time()*1000),'expectedImage':str(self.exe),'relayPid':getattr(self,'_relay_pid',None),**details}
        if processes is not None:
            data['candidateCount']=len(processes)
            data['processes']=[{k:row[k] for k in ('pid','imagePath','fileVersion','error') if k in row} for row in processes[:32]]
        try:
            write_json_atomic(self.folder/'host-diagnostic.json',data)
        except OSError:pass # A diagnostic failure must not change the host/write gate.
    @staticmethod
    def finish_relay(relay,host_pid):
        if relay.pid==host_pid:raise RuntimeError('转交进程身份异常，拒绝终止主 AE')
        if relay.poll() is None:
            try:relay.wait(timeout=1)
            except subprocess.TimeoutExpired:
                # Only our retained Popen handle for this exact -r invocation is stopped.
                relay.terminate();relay.wait(timeout=5)
    def call(self,action,**kwargs):
        if action not in {'status','catalog','prepare','snapshot','render','edit','switch','show','close'}:raise ValueError('QA action rejected')
        extras={'status':{'diagnoseCatalog'},'render':{'frame','disabled'},'edit':{'parameterEffect','parameterMatch'},'switch':{'target'}}
        if set(kwargs)-extras.get(action,set()):raise ValueError('QA 请求不能覆盖身份、许可或输出路径')
        with self.process() as p:pid=p.pid
        request_folder=self.folder/uuid.uuid4().hex;request_folder.mkdir();permit=request_folder/'permit';permit.touch();output=request_folder/'result.json';script=request_folder/'request.jsx'
        progress_file=request_folder/'progress.json';request_id=request_folder.name
        q={'action':action,'requestId':request_id,'hostVersion':self.host_version,'project':self.project.as_posix(),'second':self.second.as_posix(),'comp':self.comp,'permit':permit.as_posix(),'output':output.as_posix(),'progress':progress_file.as_posix(),'expires':int((time.time()+53)*1000),**kwargs}
        if action=='render':
            frame=Path(kwargs['frame']).resolve()
            if frame.parent!=self.folder or frame.suffix!='.png':raise ValueError('QA frame path rejected')
        template=(asset_root()/'scripts'/'adapter_qa.jsx').read_text(encoding='utf8');source=template.replace('@@REQUEST@@',json.dumps(q,ensure_ascii=True));source=''.join(c if ord(c)<128 else '\\u%04x'%ord(c) for c in source)
        script.write_text(source,encoding='ascii')
        relay=subprocess.Popen([str(self.exe),'-r',str(script)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        self._relay_pid=relay.pid;self.diagnostic('request_started',action=action,requestId=request_id,hostPid=pid)
        try:
            until=time.monotonic()+55;last_phase='等待 AE 进入脚本'
            while time.monotonic()<until:
                if progress_file.exists():
                    try:ack=json.loads(progress_file.read_text(encoding='utf-8-sig'))
                    except (ValueError,OSError):ack={}
                    if ack.get('requestId')==request_id and isinstance(ack.get('phase'),str) and ack['phase']!=last_phase:
                        last_phase=ack['phase']
                        if self.progress:self.progress(last_phase)
                if output.exists():
                    try:result=json.loads(output.read_text(encoding='utf-8-sig'))
                    except ValueError:time.sleep(.1);continue
                    if not isinstance(result,dict) or result.get('requestId')!=request_id or result.get('action')!=action:raise RuntimeError('QA 返回的请求身份不匹配')
                    if isinstance(result.get('finishedAt'),(int,float)) and result['finishedAt']>q['expires']:raise TimeoutError('AE 返回已经过期；请等待当前调用结束后恢复或重新验证')
                    # AE can finish the JSX before its separate -r launcher exits. Reap
                    # our launcher before enumerating hosts again; never exclude arbitrary PIDs.
                    permit.unlink(missing_ok=True);self.finish_relay(relay,pid)
                    with self.process():pass
                    if result.get('error'):raise RuntimeError(result['error'])
                    if not isinstance(result.get('hostVersion'),str):raise RuntimeError('QA host identity missing')
                    if self.host_version and result['hostVersion']!=self.host_version:raise RuntimeError('QA host version changed')
                    self.host_version=result['hostVersion'];self.diagnostic('response_verified',action=action,requestId=request_id,hostPid=pid,hostVersion=self.host_version);return result
                time.sleep(.1)
            labels={'status':'读取 AE 状态','catalog':'读取效果目录','prepare':'创建 QA 工程','render':'渲染 QA 帧'}
            if not progress_file.exists():
                # AE never reported entering the script: a blocking dialog or the
                # scripting file-access preference is the usual cause.
                raise TimeoutError(f"{labels.get(action,action)}超时：AE 没有开始执行验证脚本。请关闭 AE 中可能弹出的对话框，并确认“编辑 → 首选项 → 脚本和表达式 → 允许脚本写入文件和访问网络”已勾选，然后重试。已撤销后续操作许可。")
            raise TimeoutError(f"{labels.get(action,action)}超时；最后阶段：{last_phase}。已撤销后续操作许可；AE 可能仍在完成当前调用，请等待返回后再重试或恢复。")
        finally:
            permit.unlink(missing_ok=True)
            try:self.finish_relay(relay,pid)
            finally:self._relay_pid=None
    def assert_owned(self):
        status=self.call('status');path=status.get('project')
        if not path or path_key(path) not in (path_key(self.project),path_key(self.second)):raise RuntimeError('当前不是软件自有 QA 工程，已停止验证')
        return status
