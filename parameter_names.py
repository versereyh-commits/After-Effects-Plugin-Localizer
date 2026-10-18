"""Pinned AE parameter labels. Discover read-only; mutate on AE's scripting thread.

No parameter values, expressions, keyframes, plugin files or code are patched.
Only currently attached FLT effect streams with verified parent/list/match guards
are eligible. Unsupported layouts fail closed.
"""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
import hashlib,json,os,struct,subprocess,time,uuid,sys,threading
from pathlib import Path
from live_names import current_process,Registry,_lock,_process_lock,_save,module_digest
from runtime import asset_root,data_root,relay_script,write_json_atomic,unencodable,encoding_problem
from ae_profiles import get_profile,trial_projects
from runtime import file_version

TDB_SHA='8e1ea3d5e08d91dd25e899d43e0ad69a525b161716bb4c29250e3711b2591c80'
ROOT_VTABLE=0xc8e80
_cache={}
_project_context={}
_shape_context={}
_scan_reason={}
_unsupported={}
_timing_lock=threading.Lock()

def identity(effect,param):return json.dumps([effect,param],ensure_ascii=False,separators=(',',':'))
def split_identity(key):
    value=json.loads(key)
    if not isinstance(value,list) or len(value)!=2 or not all(isinstance(x,str) and x for x in value):raise ValueError('参数标识格式错误')
    return value
def validate_label(name):
    if not isinstance(name,str):raise ValueError('参数名称必须是文字')
    name=name.strip()
    if not name or any(ord(c)<32 or ord(c)==127 for c in name):raise ValueError('参数名称不能为空或包含控制字符')
    if len(name.encode('utf8'))>31:raise ValueError('参数名称最多 31 个 UTF-8 字节（通常最多 10 个汉字）')
    return name

def validate_pf_name(name):
    if not isinstance(name,str) or not 1<=len(name)<=31 or any(ord(c)<32 or ord(c)==127 for c in name):raise ValueError('底层参数原名格式不正确')
    return name

class MBI(C.Structure):
    _fields_=[('BaseAddress',C.c_void_p),('AllocationBase',C.c_void_p),('AllocationProtect',W.DWORD),('PartitionId',W.WORD),('RegionSize',C.c_size_t),('State',W.DWORD),('Protect',W.DWORD),('Type',W.DWORD)]

def bounded_ascii(p,address,limit=256,block=32):
    """Bound every read by a page; narrow failures without accepting unreadable bytes."""
    raw=bytearray()
    while len(raw)<limit:
        at=address+len(raw);size=min(block,limit-len(raw),4096-(at%4096))
        while True:
            try:data=p.read(at,size);break
            except (OSError,RuntimeError):
                if size==1:raise
                size=max(1,size//2)
        if len(data)!=size:raise RuntimeError('参数标识读取不完整')
        end=data.find(b'\0')
        if end>=0:return bytes(raw+data[:end]).decode('ascii')
        raw.extend(data)
    raise RuntimeError('参数内部标识未终止')

def record_read_timing(metrics):
    """A bounded statistics-only log. No host/project identity, strings or pointers."""
    allowed={'event','readId','setupMs','hostMs','modulesMs','matchPoolMs','scanMs','treeMs','totalMs','tableMs','scanCount','mode','rows','instances','scanEngine','scanMeasuredMs','scanEnumMs','scanReadMs','scanSearchMs','scanValidateMs','scanBufferMs','scanRegions','scanEligibleRegions','scanReadCalls','scanReadBytes','scanReadFailures','scanHits','scanAlignedHits','scanParentChecks','scanRepeatedParents','scanValidationReads','scanBufferCapacityBytes','scanMergedRegions','scanJobs','scanWorkerCount','scanRetryRegions','scanCoalesced','scanTimingMode','scanResourceFallback'}
    allowed.add('readScope')
    safe={k:v for k,v in metrics.items() if k in allowed and isinstance(v,(str,int,float))}
    try:
        with _timing_lock:
            path=data_root()/'parameter-read-timing.jsonl'
            if path.exists() and path.stat().st_size>262144:
                lines=path.read_text(encoding='utf8').splitlines()[-128:];path.write_text('\n'.join(lines)+'\n',encoding='utf8')
            with path.open('a',encoding='utf8') as f:f.write(json.dumps(safe,ensure_ascii=False)+'\n')
    except OSError:pass

class ParameterCatalog:
    def __init__(self,p):
        started=time.perf_counter();self.timings={'scanMs':0.0,'scanCount':0};self.p=p;registry=Registry(p);self.profile=registry.profile # independently pins FLT hash/getters
        mods={Path(m['path']).name.lower():m for m in registry.modules};self.flt=mods['flt.dll']['base'];t=mods['tdb.dll'];self.tdb=t['base']
        if module_digest(p,t['path'])!=self.profile.tdb_sha:raise RuntimeError('TDB.dll 构建不匹配，参数汉化已停止')
        for rva,expected in ((self.profile.parent_get,bytes.fromhex('488b4120c3')),(self.profile.stream_match_get,bytes.fromhex('488d4108c3')),(self.profile.count_get,self.profile.count_bytes)):
            if p.read(self.tdb+rva,len(expected))!=expected:raise RuntimeError('参数结构接口不匹配，未执行写入')
        self.timings['modulesMs']=(time.perf_counter()-started)*1000
        started=time.perf_counter();self.names=self.match_pool();self.timings['matchPoolMs']=(time.perf_counter()-started)*1000
    def match_pool(self):
        p=self.p;pool=self.tdb+self.profile.match_pool;count=p.u64(pool+8)
        if not 1<=count<=1048576 or count&(count-1):raise RuntimeError('参数内部标识池结构不匹配')
        buckets=p.read(p.u64(pool+40),count*8);seen=set();names={}
        for bucket in struct.unpack('<'+'Q'*count,buckets):
            node=p.u64(bucket) if bucket else 0
            while node and node not in seen:
                if len(seen)>200000:raise RuntimeError('参数内部标识池过大')
                seen.add(node);b=p.read(node,80);ptr=struct.unpack_from('<Q',b,16)[0] or node+24
                # A plugin's non-ASCII internal name (Chinese) is left out: its effect / parameters are simply not listed.
                try:names[b[64:80]]=bounded_ascii(p,ptr)
                except UnicodeDecodeError:pass
                node=struct.unpack_from('<Q',b)[0]
        return names
    def _scan_parades(self):
        from parallel_scan import scan_auto
        return scan_auto(self)
    def roots(self,rescan=False,progress=None,_reason=None):
        p=self.p;key=(p.pid,p.creation_time);vt=self.flt+self.profile.root_vtable
        if not hasattr(self,'timings'):self.timings={'scanMs':0.0,'scanCount':0}
        full=rescan or key not in _cache
        if full:
            reason=_reason or ('手动完整扫描' if rescan else _scan_reason.get(key,'首次扫描'));_scan_reason.pop(key,None)
            if progress:progress(reason+'，正在识别 AE 参数结构……')
            started=time.perf_counter();parades=self._scan_parades()
            self.timings['scanMs']+=(time.perf_counter()-started)*1000;self.timings['scanCount']+=1;self.timings['mode']=reason
            _cache[key]=set(parades)
        else:
            parades=set(_cache[key]);self.timings['mode']='读取缓存结构'
            if progress:progress('读取缓存结构，核对当前参数……')
        roots=[];invalid=False
        for parade in parades:
            try:
                if self.names.get(p.read(parade+8,16))!='ADBE Effect Parade':invalid=True;continue
                begin,end=struct.unpack('<QQ',p.read(parade+self.profile.group_vector,16))
                if end<begin or (end-begin)%8 or end-begin>8192*8:invalid=True;continue
                for root in struct.unpack('<'+'Q'*((end-begin)//8),p.read(begin,end-begin)):
                    if root and p.u64(root)==vt and p.u64(root+32)==parade:roots.append(root)
                    elif root:invalid=True
            except (OSError,RuntimeError):invalid=True
        if not full and (invalid or (parades and not roots)):
            if progress:progress('缓存对象失效，重新扫描……')
            result=self.roots(rescan=True,progress=progress,_reason='缓存失效重扫');self.timings['mode']='缓存失效重扫';return result
        return sorted(set(roots))
    def children(self,root):
        p=self.p
        if self.profile.named_vector:
            begin,end=struct.unpack('<QQ',p.read(root+self.profile.group_vector,16))
            if end<begin or (end-begin)%8 or end-begin>8192*8:raise RuntimeError('参数向量结构不符')
            for address in struct.unpack('<'+'Q'*((end-begin)//8),p.read(begin,end-begin)):
                if address:yield address,p.read(address+8,16)
        else:
            head=p.u64(root+getattr(self.profile,'named_map',0x228));node=p.u64(head);seen=set()
            while node!=head:
                if node in seen or len(seen)>8192:raise RuntimeError('参数列表循环或过大，已停止')
                seen.add(node);b=p.read(node,40);yield struct.unpack_from('<Q',b,32)[0],b[16:32];node=struct.unpack_from('<Q',b)[0]
    def rows(self,rescan=False,progress=None):
        result=[];p=self.p
        for root in self.roots(rescan,progress):
            effect=self.names.get(p.read(root+8,16))
            if not effect:continue
            for address,guid in self.children(root):
                if p.read(address+8,16)!=guid or p.u64(address+32)!=root:raise RuntimeError('参数归属校验失败，已停止')
                param=self.names.get(guid);label=p.read(address+0x40,32)
                if param and b'\0' in label:
                    try:pf_name=label.split(b'\0',1)[0].decode(self.profile.encoding)
                    except UnicodeError:pf_name=''
                    if pf_name:
                        offset=self.profile.display_cache
                        name=pf_name;cache=p.read(address+offset,32);length,capacity=struct.unpack_from('<QQ',cache,16)
                        if not 0<=length<=capacity<=4096:raise RuntimeError('参数显示缓存结构不匹配')
                        if length:
                            pointer=address+offset if capacity<=7 else struct.unpack_from('<Q',cache)[0]
                            text=p.read(pointer,(length+1)*2)
                            if text[-2:]!=b'\0\0':raise RuntimeError('参数显示缓存未终止')
                            name=text[:-2].decode('utf-16le')
                        result.append({'id':identity(effect,param),'effectMatchName':effect,'matchName':param,'name':name,'pfName':pf_name,'address':address,'guid':guid.hex(),'parent':root})
        return result

def public_rows(rows):
    unique={}
    for row in rows:
        if row['id'] not in unique:unique[row['id']]={k:v for k,v in row.items() if k not in ('address','guid','parent','pfName')};unique[row['id']]['instances']=0
        unique[row['id']]['instances']+=1
    return sorted(unique.values(),key=lambda r:(r['effectMatchName'],r['matchName']))

def read_parameters(rescan=False,progress=None,expected_image=None,scope='project'):
    if scope not in ('selected','project'):raise ValueError('未知参数读取范围')
    started=time.perf_counter()
    with _lock,_process_lock(),(current_process(expected_image=expected_image) if expected_image else current_process()) as p:
        setup_ms=(time.perf_counter()-started)*1000;host_start=time.perf_counter();info={'readScope':scope}
        key=(p.pid,p.creation_time)
        if scope=='selected':
            actual,info=_selected_names(p)
            # The structure scan always covers the whole AE process, so a new
            # selection reuses it. Before any scan (no structure yet, or another
            # project) take the project-wide shape too, so writes can trust it.
            if actual and (_project_context.get(key)!=info['projectToken'] or key not in _cache):_host_names(p)
        else:actual=_host_names(p,strict=False)
        host_ms=(time.perf_counter()-host_start)*1000
        metrics={'event':'read','readId':uuid.uuid4().hex,'readScope':scope,'setupMs':setup_ms,'hostMs':host_ms,'modulesMs':0.0,'matchPoolMs':0.0,'scanMs':0.0,'treeMs':0.0,'scanCount':0,'mode':'空范围' if scope=='selected' else '空工程'}
        if actual:
            catalog=ParameterCatalog(p);tree=time.perf_counter();scanned=bool(rescan)
            try:rows=catalog.rows(rescan,progress)
            except (OSError,RuntimeError):
                if rescan or catalog.timings['scanCount']:raise
                if progress:progress('缓存参数失效，重新扫描……')
                rows=catalog.rows(True,progress);catalog.timings['mode']='缓存失效重扫';scanned=True
            scanned=scanned or bool(catalog.timings.get('scanCount'))
            # Coverage is judged per effect type: all instances of an effect share
            # its parameters, and every effect also has unsupported built-in
            # groups. Once a scan has seen a type, its missing ids are simply
            # unsupported; only a type absent from the structure needs a scan.
            types=lambda ids:{split_identity(i)[0] for i in ids}
            bare=_unsupported.setdefault(key,set()) # effect types with no supported parameter
            missing=types(actual)-{r['effectMatchName'] for r in rows}-bare
            if missing and not scanned:
                # A newly added effect type: record the project shape this scan
                # is trusted for, then scan once.
                if scope=='selected':_host_names(p)
                else:_shape_context[key]=_name_signature(actual)
                if progress:progress('出现新的效果类型，重新扫描……')
                rows=catalog.rows(True,progress);catalog.timings['mode']='出现新的效果类型，重新扫描';scanned=True
            if scanned:
                if scope=='project':_shape_context[key]=_name_signature(actual)
                # Present before a fresh full scan yet without any supported parameter.
                bare.update(types(actual)-{r['effectMatchName'] for r in rows})
            metrics.update(catalog.timings);metrics['treeMs']=max(0,(time.perf_counter()-tree)*1000-metrics['scanMs'])
            public=public_rows([r for r in rows if r['id'] in actual])
            for row in public:
                row['name']=actual[row['id']][0];row['instances']=len(actual[row['id']]);row['mixedNames']=len(set(actual[row['id']]))>1
        else:
            if scope=='project':_cache.pop(key,None)
            public=[]
            if progress:progress('当前读取范围没有参数，无需扫描')
        if scope=='selected':
            verify_start=time.perf_counter()
            # Results are discarded if the selection or project moved meanwhile.
            # The whole-process structure stays valid and is kept.
            _,latest=_selected_names(p,action='selection')
            if (latest['projectToken'],latest['selectionSnapshot'])!=(info['projectToken'],info['selectionSnapshot']):raise RuntimeError('读取期间工程或选中图层发生变化，请重新读取参数')
            p.ensure_alive()
            metrics['hostMs']+=(time.perf_counter()-verify_start)*1000
            if not public and not info.get('emptyReason'):info['emptyReason']='noSupportedParameters'
        metrics.update(totalMs=(time.perf_counter()-started)*1000,rows=len(public),instances=sum(r['instances'] for r in public))
        record_read_timing(metrics)
        return {'pid':p.pid,'creationTime':p.creation_time,'parameters':public,'timings':metrics,**info}


def bridge_root():
    base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
    return base/'ae-bridge'

STARTED='started'   # parameter_bridge.jsx writes this into the request folder as soon as AE enters the script
# ...and rewrites it with each step it reaches; a timeout names the last one and what usually holds AE there.
STAGES={'1':('进入脚本',''),'start':('进入脚本',''),'instances':('读取效果实例',''),'collect':('读取参数列表',''),
        'loadHelper':('加载参数辅助模块','安全软件（360、火绒等）可能拦截了这个 DLL 的加载，请留意它们的提示并允许；'),
        'configure':('核对 AE 模块与名称编码','这个 AE 版本可能与参数辅助模块不兼容，请把诊断文件发给开发者；'),
        'labels':('写入参数名称',''),'result':('写回结果','')}

def _stage(folder)->str|None:
    try:return (folder/STARTED).read_text(encoding='ascii',errors='replace').strip()[:32] or 'start'
    except OSError:return None

def _timeout_message(p,folder,request,timeout,use_bridge,launcher)->str:
    """Why a request timed out, as far as can be told from outside AE, and a diagnostic file beside it (versions,
    route, whether AE entered the script, administrator rights; no paths, projects or names)."""
    import cep_bridge
    from win_process import elevated,elevation_mismatch
    stage=_stage(folder);started=stage is not None;exit_code=launcher.poll() if launcher is not None else None
    step,hint=STAGES.get(stage,('未知步骤',''))
    route='extension' if use_bridge and launcher is None else 'relay'   # the relay runs only when the extension did not take it
    mismatch='' if started else elevation_mismatch(p.pid)
    if started:cause=f'AE 已开始执行脚本，但 {timeout} 秒内没有完成，停在“{step}”这一步：{hint}AE {"也" if hint else ""}可能正忙或显示了对话框，请关闭 AE 中的对话框后重试'
    elif mismatch:cause='AE 没有执行脚本：'+mismatch
    elif route=='extension':cause='后台扩展已接收请求，但 AE 没有开始执行：AE 可能正忙或显示了对话框。请关闭 AE 中的对话框后重试；仍不行可在设置页移除后台连接，改用转交方式'
    else:cause=('AE 没有执行转交的脚本'+('' if launcher is None else '（转交进程仍未退出）' if exit_code is None else f'（转交进程已退出，退出码 {exit_code}）')+
                '。请关闭 AE 中可能弹出的对话框，并确认“编辑 → 首选项 → 脚本和表达式 → 允许脚本写入文件和访问网络”已勾选后重试')
    details={'schemaVersion':1,'kind':'parameter-timeout','action':request.get('action'),'readScope':request.get('readScope'),'aeVersion':request.get('hostVersion'),
             'timeoutSeconds':timeout,'route':route,'extensionStatus':cep_bridge.status(),'extensionClaimed':(folder/'claimed.json').exists(),'scriptStarted':started,'lastStage':stage if stage in STAGES else ('other' if started else None),
             'relayLaunched':launcher is not None,'relayExitCode':exit_code,'folderPathHasSpace':' ' in str(folder),'aeElevated':elevated(p.pid),'selfElevated':elevated(os.getpid())}
    note=''
    try:write_json_atomic(folder/'timeout-diagnostic.json',details,ensure_ascii=False,indent=1);note='；诊断记录：'+str(folder/'timeout-diagnostic.json')
    except OSError:pass
    return 'AE 参数操作超时，已撤销执行许可。'+cause+note

def run_native(p,operations,timeout=25,action='labels',project_token=None,scope=None):
    if scope is not None and (scope not in ('selected','project') or action not in ('catalog','selection')):raise ValueError('读取范围不能用于写入请求')
    # With the background extension loaded in AE (cep_bridge) the request goes through the folder it watches:
    # no second AfterFX.exe, so AE keeps its window state and focus. Otherwise -- or when the extension does not
    # take the request in time -- the AfterFX.exe -r relay runs the same request.
    # The relay must use a directory visible to both the EXE and AE. In the
    # development host AE sees a different LocalAppData view from the executor;
    # a portable-folder IPC directory also avoids that ambiguity in production.
    import cep_bridge
    use_bridge=cep_bridge.connected()
    folder=(cep_bridge.root() if use_bridge else bridge_root())/uuid.uuid4().hex
    try:folder.mkdir(parents=True)
    except OSError as e:
        if use_bridge:raise RuntimeError('无法写入后台连接目录：'+str(cep_bridge.root())) from e
        raise RuntimeError('软件所在目录不可写，请把完整软件目录放在可写位置后重试') from e
    script=folder/'request.jsx';output=folder/'result.json';permit=folder/'permit';permit.write_text('pending',encoding='ascii')
    profile=get_profile(file_version(Path(p.image)))
    helper=asset_root()/'native'/('ae_parameters_v9.dll' if profile.native_dynamic else 'ae_parameters_v8.dll')
    if not helper.is_file():raise RuntimeError('参数辅助模块缺失，请保留完整 EXE 目录')
    request={'action':action,'requestId':folder.name,'hostVersion':profile.host_version,'projectToken':project_token if project_token is not None else _project_context.get((p.pid,getattr(p,'creation_time',None))),'operations':operations,'output':output.as_posix(),'permit':permit.as_posix(),'expires':int((time.time()+timeout-2)*1000),'helper':helper.as_posix()}
    if scope is not None:request['readScope']=scope
    if trial_projects():request['qaProjects']=list(trial_projects())
    if profile.native_dynamic:
        request['adapter']={'flt':profile.flt_sha,'tdb':profile.tdb_sha,'codec':profile.codec_sha,'layout':','.join(format(v,'x') for v in (profile.root_vtable,profile.group_vector,profile.named_map,int(profile.named_vector),profile.parent_get,profile.stream_match_get,profile.count_get)),'encoding':profile.encoding}
    launcher=keeper=None
    def relay():
        nonlocal launcher,keeper,script
        script=relay_script(folder)   # AE runs nothing from a path with a space
        template=(asset_root()/'scripts'/'parameter_bridge.jsx').read_text(encoding='utf8')
        # AE's command-line script loader otherwise may interpret non-ASCII source
        # using a Windows legacy code page. Escape every non-ASCII
        # source character; all paths/names are JSON literals, never executable text.
        source=template.replace('@@REQUEST@@',json.dumps(request,ensure_ascii=True))
        source=''.join(c if ord(c)<128 else '\\u%04x'%ord(c) for c in source)
        # AE 26.0's -r relay silently ignores the BOM-prefixed file on this host.
        script.write_text(source,encoding='ascii')
        # The hand-over makes AE show its main window again; keep it maximized / minimized as the user left it.
        from ae_ui import WindowKeeper
        keeper=WindowKeeper(p.pid)
        launcher=subprocess.Popen([p.image,'-r',str(script)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    claim_by=None
    if use_bridge:cep_bridge.submit(folder,request);claim_by=time.monotonic()+cep_bridge.CLAIM_WAIT
    else:relay()
    until=time.monotonic()+timeout;finished=False
    try:
        while time.monotonic()<until:
            if keeper:keeper.check()
            if output.exists():
                try:
                    result=json.loads(output.read_text(encoding='utf-8-sig'))
                    if action in ('instanceCatalog','instanceNames') and (result.get('requestId')!=folder.name or result.get('action')!=action):raise RuntimeError('效果标题响应身份不匹配')
                    if scope is not None and (result.get('requestId')!=folder.name or result.get('action')!=action or result.get('readScope')!=scope):raise RuntimeError('参数读取范围响应身份不匹配')
                    finished=True;return result
                except ValueError:pass
            if use_bridge:
                reason=cep_bridge.problem(folder)
                if reason:finished=True;raise cep_bridge.Rejected(reason)
                # Not taken in time (AE busy loading, an older extension): take it back and use the relay.
                if claim_by is not None and time.monotonic()>claim_by:
                    claim_by=None
                    if cep_bridge.withdraw(folder):relay()
            time.sleep(.1)
        raise TimeoutError(_timeout_message(p,folder,request,timeout,use_bridge,launcher))
    finally:
        permit.unlink(missing_ok=True)
        # Only the secondary CLI relay may be terminated; never the primary AE.
        if launcher is not None and launcher.poll() is None and launcher.pid!=p.pid:
            launcher.terminate();launcher.wait(timeout=5)
        if keeper:keeper.settle()
        # A script moved out of the request folder (a path with spaces) has no use once the permit is gone.
        if script.name!='request.jsx':script.unlink(missing_ok=True)
        if finished or output.exists():
            # Only these task-owned files are removed. Never recurse into a
            # user-selected path or delete the parent portable directory.
            for owned in (script,output,folder/STARTED,*(folder/name for name in cep_bridge.OWNED)):owned.unlink(missing_ok=True)
            try:folder.rmdir()
            except OSError:pass

def _name_signature(names):return tuple(sorted((key,len(value)) for key,value in names.items()))

def _selected_names(p,action='catalog'):
    result=run_native(p,[],action=action,scope='selected')
    if result.get('error'):raise RuntimeError('选中图层参数读取失败：'+result['error'])
    token=result.get('projectToken');snapshot=result.get('selectionSnapshot')
    if result.get('readScope')!='selected' or not isinstance(token,str) or not token or not isinstance(snapshot,dict) or set(snapshot)!= {'compId','layers'}:raise RuntimeError('选中图层响应身份不完整')
    comp=snapshot['compId'];layers=snapshot['layers']
    if (comp is not None and (type(comp) is not int or comp<=0)) or not isinstance(layers,list) or (comp is None and layers):raise RuntimeError('选中图层快照格式错误')
    seen=set()
    for layer in layers:
        if not isinstance(layer,dict) or set(layer)!={'index','id','name'}:raise RuntimeError('图层身份格式错误')
        index=layer['index'];lid=layer['id']
        if type(index) is not int or index<=0 or index in seen or (lid is not None and (type(lid) is not int or lid<=0)) or not isinstance(layer['name'],str):raise RuntimeError('图层身份格式错误')
        seen.add(index)
    info={'readScope':'selected','projectToken':token,'selectionSnapshot':snapshot}
    if comp is None:info['emptyReason']='noActiveComp'
    elif not layers:info['emptyReason']='noSelectedLayers'
    names={}
    for row in result.get('parameters',[]):
        split_identity(row['id'])
        if not isinstance(row['name'],str):raise RuntimeError('AE 参数名称回读格式错误')
        names.setdefault(row['id'],[]).append(row['name'])
    if names and not layers:raise RuntimeError('空选择返回了参数，已拒绝')
    return names,info

def _host_names(p,strict=True):
    """Current project-wide names. strict (writes/restores): any instance-count
    change drops the structure cache. Reads pass strict=False and instead check
    that every parameter type is covered (read_parameters)."""
    result=run_native(p,[],action='catalog')
    if result.get('error'):raise RuntimeError('AE 实际参数名称读取失败：'+result['error'])
    token=result.get('projectToken')
    if not isinstance(token,str) or not token:raise RuntimeError('AE 工程身份回读不完整，未执行写入')
    key=(p.pid,p.creation_time)
    if _project_context.get(key)!=token:
        _cache.pop(key,None);_shape_context.pop(key,None);_scan_reason[key]='工程变化，重新扫描' if key in _project_context else '首次扫描'
    _project_context[key]=token
    names={}
    for row in result.get('parameters',[]):
        split_identity(row['id'])
        if not isinstance(row['name'],str):raise RuntimeError('AE 参数名称回读格式错误')
        names.setdefault(row['id'],[]).append(row['name'])
    signature=_name_signature(names)
    # _shape_context keeps the shape the cached scan was trusted for; a read
    # that reuses the cache must not move it, or a later write would miss the change.
    if strict or key not in _cache:
        if key in _shape_context and _shape_context[key]!=signature:
            _cache.pop(key,None);_scan_reason[key]='参数或实例变化，重新扫描'
        _shape_context[key]=signature
    return names

class ParameterSession:
    def __init__(self):self.path=data_root()/'parameter-session.json';self.expected_image=None
    def process(self):return current_process(expected_image=self.expected_image) if self.expected_image else current_process()
    def journal(self):
        try:value=json.loads(self.path.read_text(encoding='utf8'))
        except FileNotFoundError:return {'schemaVersion':1,'entries':{}}
        except (ValueError,OSError) as e:raise RuntimeError('参数恢复记录损坏，已停止并保留原文件') from e
        if not isinstance(value,dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion']!=1 or not isinstance(value.get('entries'),dict):raise RuntimeError('参数恢复记录格式不正确，已停止')
        for key,item in value['entries'].items():
            split_identity(key)
            if not isinstance(item,dict) or not isinstance(item.get('original'),str) or not isinstance(item.get('applied'),str):raise RuntimeError('参数恢复条目格式不正确')
            validate_label(item['original']);validate_label(item['applied'])
            if 'previous' in item:validate_label(item['previous'])
            if 'originalPf' in item:validate_pf_name(item['originalPf'])
        if value['entries'] and (type(value.get('pid')) is not int or type(value.get('creationTime')) is not int):raise RuntimeError('参数恢复会话身份不正确')
        return value
    def apply(self,names,progress=None):
        names={k:validate_label(v) for k,v in names.items()}
        for key in names:split_identity(key)
        if not names:raise ValueError('请先选择参数并填写中文候选')
        with _lock,_process_lock(),self.process() as p:
            catalog=ParameterCatalog(p)
            encoding=getattr(getattr(catalog,'profile',None),'encoding','utf8')
            if encoding!='utf8':
                bad=[('/'.join(split_identity(key)),name) for key,name in names.items() if unencodable(name,encoding)]
                if bad:raise ValueError(encoding_problem(bad,encoding,'参数译名')[0])
            if any(len(name.encode(encoding))>31 for name in names.values()):raise ValueError('译名超过当前 AE 名称缓冲区容量')
            actual=_host_names(p);rows=[r for r in catalog.rows(progress=progress) if r['id'] in actual];journal=self.journal()
            if (journal.get('pid'),journal.get('creationTime'))!=(p.pid,p.creation_time):journal={'schemaVersion':1,'entries':{}}
            journal.update(pid=p.pid,creationTime=p.creation_time)
            existing={r['id'] for r in rows};missing=set(names)-existing
            if missing:raise ValueError('部分参数已不在当前 AE 中，请重新读取后再应用')
            originals={}
            for key in names:
                entry=journal['entries'].get(key)
                if entry:
                    if any(value not in (entry['original'],entry['applied'],entry.get('previous')) for value in actual[key]):raise RuntimeError('参数名称已被外部修改，未覆盖')
                    originals[key]=entry['original']
                else:
                    if len(set(actual[key]))!=1:raise RuntimeError('同一参数的实例名称不同，暂不支持统一改名后精确恢复')
                    originals[key]=validate_label(actual[key][0])
                    if originals[key]!=actual[key][0]:raise ValueError('原始参数名称含首尾空白，暂不支持安全恢复')
            unchanged={key for key in names if key not in journal['entries'] and all(value==names[key] for value in actual[key]) and all(row['name']==names[key] for row in rows if row['id']==key)}
            operations=[];pf_originals={}
            for row in rows:
                key=row['id']
                if key not in names or key in unchanged:continue
                entry=journal['entries'].get(key)
                if entry and originals[key] not in (entry['original'],entry['applied']):raise RuntimeError('参数名称已被外部修改，未覆盖：'+row['matchName'])
                pf=row.get('pfName',row['name'])
                if entry and pf not in (entry['original'],entry['applied'],entry.get('previous'),entry.get('originalPf')):raise RuntimeError('参数元数据已被外部修改，未覆盖：'+row['matchName'])
                if entry is None:
                    validate_pf_name(pf)
                    if key in pf_originals and pf_originals[key]!=pf:raise RuntimeError('同一参数的底层原名不同，暂不支持统一改名后精确恢复')
                    pf_originals[key]=pf
                # Even an equal PF name may have a stale English UI cache after
                # undo/value editing. Re-notify the host instead of skipping it.
                operations.append({'address':hex(row['address']),'guid':row['guid'],'parent':hex(row['parent']),'before':pf,'after':names[key],'afterPf':names[key],'beforeDisplay':row['name']})
            # Every instance is checked against the journal as it stood before
            # this batch. A planned first instance is not a previous host write:
            # Localized UI names can differ from raw PF labels (including the
            # blank label used by an AE checkbox). Native exact-before checks
            # still protect each operation against changes after this preflight.
            for key in names:
                if key in unchanged:continue
                entry=journal['entries'].get(key)
                if entry is None:journal['entries'][key]={'original':originals[key],'applied':names[key],'originalPf':pf_originals[key]}
                elif entry['applied']!=names[key]:entry['previous']=entry['applied'];entry['applied']=names[key]
            # Durable logical identities before any host writes. No process addresses stored here.
            _save(self.path,journal)
            if not operations:return {'changed':0,'parameters':len(names),'unchanged':len(unchanged)}
            result=run_native(p,operations)
            if result.get('error'):raise RuntimeError('参数应用未完成，恢复记录已保留：'+result['error'])
            after=_host_names(p)
            if any(not after.get(k) or any(v!=name for v in after[k]) for k,name in names.items()):raise RuntimeError('参数更名回读不一致，恢复记录已保留')
            for entry in journal['entries'].values():entry.pop('previous',None)
            _save(self.path,journal);result={'changed':result.get('changed',0),'parameters':len(names)}
            if unchanged:result['unchanged']=len(unchanged)
            return result
    def restore(self,keys=None,progress=None):
        with _lock,_process_lock():
            journal=self.journal();wanted=set(journal['entries']) if keys is None else set(keys)&set(journal['entries'])
            if not wanted:return {'restored':0}
            from runtime import native_probe
            if not any(x.get('pid')==journal.get('pid') for x in native_probe().get('processes',[])):
                _save(self.path,{'schemaVersion':1,'entries':{}});return {'restored':0,'oldProcessEnded':True}
            with self.process() as p:
                if (journal.get('pid'),journal.get('creationTime'))!=(p.pid,p.creation_time):
                    _save(self.path,{'schemaVersion':1,'entries':{}});return {'restored':0,'oldProcessEnded':True}
                catalog=ParameterCatalog(p);actual=_host_names(p);rows=[r for r in catalog.rows(progress=progress) if r['id'] in actual];operations=[];present=set()
                for row in rows:
                    key=row['id']
                    if key not in wanted:continue
                    entry=journal['entries'][key];present.add(key)
                    if any(name not in (entry['original'],entry['applied'],entry.get('previous')) for name in actual[key]):raise RuntimeError('参数名称已被外部修改，未强制恢复')
                    if row.get('pfName',row['name']) not in (entry['original'],entry['applied'],entry.get('previous'),entry.get('originalPf')):raise RuntimeError('参数元数据名称已被外部修改，未强制恢复')
                    operations.append({'address':hex(row['address']),'guid':row['guid'],'parent':hex(row['parent']),'before':row.get('pfName',row['name']),'after':entry['original'],'afterPf':entry.get('originalPf',entry['original']),'beforeDisplay':row['name']})
                if operations:
                    result=run_native(p,operations)
                    if result.get('error'):raise RuntimeError('参数恢复未完成，记录已保留：'+result['error'])
                after=_host_names(p)
                for key in present:
                    if not after.get(key) or any(name!=journal['entries'][key]['original'] for name in after[key]):raise RuntimeError('参数恢复回读不一致，记录已保留')
                # Keep absent effects for later: an undo or a project switch may bring them back.
                for key in present:del journal['entries'][key]
                _save(self.path,journal);return {'restored':len(present),'pending':len(wanted-present)}
