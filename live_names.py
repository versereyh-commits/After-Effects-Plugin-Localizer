"""Version-pinned FLT registry adapter. No code patches, hooks, or plugin-file edits."""
from __future__ import annotations
from pathlib import Path
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import hashlib,json,os,struct,threading
from win_process import Process
from ae_ui import main_window
from runtime import data_root,native_probe,file_version,write_json_atomic
from ae_profiles import get_profile,supported_text,PROFILES,runtime_allowed

PROFILE_SHA='68b229950e95dedf630f5d09653715268e8beba4830590433abe7cb58e78a357'
MANAGER_RVA=0x14dee0
VTABLE_RVA=0xc8e20
FIELD_SIZE=48
_lock=threading.RLock()
_mutex_name='Local\\AEPluginNameLocalizer.NameSession'

@contextmanager
def _process_lock():
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[C.c_void_p,W.BOOL,W.LPCWSTR];kernel.CreateMutexW.restype=C.c_void_p
    kernel.WaitForSingleObject.argtypes=[C.c_void_p,W.DWORD];kernel.WaitForSingleObject.restype=W.DWORD
    kernel.ReleaseMutex.argtypes=[C.c_void_p];kernel.ReleaseMutex.restype=W.BOOL
    kernel.CloseHandle.argtypes=[C.c_void_p];kernel.CloseHandle.restype=W.BOOL
    handle=kernel.CreateMutexW(None,False,_mutex_name)
    if not handle:raise RuntimeError('无法创建名称操作互斥量：'+str(C.WinError(C.get_last_error())))
    acquired=False
    try:
        result=kernel.WaitForSingleObject(handle,0)
        if result==258:raise RuntimeError('另一个 AE汉化工具实例正在操作，请关闭后重试')
        if result not in (0,128):raise RuntimeError('无法获取名称操作互斥量：'+str(C.WinError(C.get_last_error())))
        acquired=True
        yield
    finally:
        if acquired:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

def _name_bytes(value,encoding='utf8'):
    try:
        if not isinstance(value,str) or len(value)!=FIELD_SIZE*2 or any(c not in '0123456789abcdefABCDEF' for c in value):
            raise ValueError('名称字节必须是 96 个十六进制字符')
        raw=bytes.fromhex(value)
        if b'\0' not in raw:raise ValueError('名称字节缺少 NUL 终止符')
        raw.split(b'\0',1)[0].decode(encoding)
        return raw
    except ValueError as error:
        raise RuntimeError('恢复记录损坏，已停止写入，原文件已保留：'+str(error)) from error

def encode_name(text:str,encoding='utf8')->bytes:
    if not isinstance(text,str):raise ValueError('名称必须是文字')
    text=text.strip()
    if not text or any(ord(c)<32 or ord(c)==127 for c in text):raise ValueError('名称不能为空或包含控制字符')
    try:value=text.encode(encoding)
    except UnicodeEncodeError:
        from runtime import LOCALE_HINT
        raise ValueError('此 AE 的旧式编码无法表示该名称，请换用可表示的中文字符；'+LOCALE_HINT) from None
    if len(value)>47:raise ValueError('名称过长，请缩短（最多 47 个英文字符或约 15 个常见汉字）')
    return (value+b'\0').ljust(FIELD_SIZE,b'\0')

_save=write_json_atomic  # journals; other modules import it under this name

_digests={};_codecs={};_verify_lock=threading.Lock()

def _host_key(p):return (getattr(p,'pid',None),getattr(p,'creation_time',None))

def module_digest(p,path):
    """SHA-256 of a loaded module's file, hashed once per AE process and file identity.

    Hashing FLT/TDB/U on every read cost about a second. A changed path, size,
    timestamp or file ID, or a restarted AE, hashes the file again.
    """
    st=os.stat(path);key=(*_host_key(p),os.path.normcase(os.path.abspath(path)),st.st_size,st.st_mtime_ns,st.st_ino)
    with _verify_lock:digest=_digests.get(key)
    if digest is None:
        digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        with _verify_lock:_digests[key]=digest
    return digest

class Registry:
    def __init__(self,p:Process):
        self.p=p
        self.profile=get_profile(file_version(Path(p.image)))
        self.modules=p.modules() # one enumeration (~80 ms) per read, shared with ParameterCatalog
        module=next((m for m in self.modules if m['name'].lower()=='flt.dll'),None)
        if not module:raise RuntimeError('AE 尚未加载效果模块，请等待启动完成')
        self.base=module['base'];self.path=module['path']
        if module_digest(p,self.path)!=self.profile.flt_sha:
            raise RuntimeError('当前 AE 效果模块哈希与该构建的适配器不匹配，未执行写入')
        if self.profile.native_dynamic:
            for name,digest in (('tdb.dll',self.profile.tdb_sha),('u.dll',self.profile.codec_sha)):
                other=next((m for m in self.modules if m['name'].casefold()==name),None)
                if not other or module_digest(p,other['path'])!=digest:raise RuntimeError('本机适配的依赖模块发生变化，请重新验证')
            # The encoding a running AE uses cannot change; ask it once per process.
            # The native helper still checks the encoding itself before every write.
            key=(*_host_key(p),self.profile.flt_sha,self.profile.tdb_sha,self.profile.codec_sha)
            with _verify_lock:actual=_codecs.get(key)
            if actual is None:
                from parameter_names import run_native
                codec=run_native(p,[],action='codec')
                if codec.get('error') or codec.get('codec') not in (1,2):raise RuntimeError('本机适配的实际名称编码发生变化，已停止操作')
                actual=codec['codec']
                with _verify_lock:_codecs[key]=actual
            if actual!=(1 if self.profile.encoding=='utf8' else 2):raise RuntimeError('本机适配的实际名称编码发生变化，已停止操作')
        for rva,expected in ((self.profile.name_get,b'\x48\x8d\x41\x10\xc3'),(self.profile.match_get,b'\x48\x8d\x41\x40\xc3'),(self.profile.category_get,b'\x48\x8d\x41\x60\xc3')):
            if p.read(self.base+rva,len(expected))!=expected:raise RuntimeError('AE 名称字段接口校验失败，未执行写入')
        self.manager=p.u64(self.base+self.profile.manager)
    def records(self):
        begin,end=struct.unpack('<QQ',self.p.read(self.manager+self.profile.registry_vector,16))
        if end<begin or (end-begin)%16 or not 1<=(end-begin)//16<32768:raise RuntimeError('AE 效果目录结构不符，未执行写入')
        vector=self.p.read(begin,end-begin)
        return [self.read_record(address) for address in struct.unpack(f'<{len(vector)//8}Q',vector)[::2] if address]
    def read_record(self,address):
        data=self.p.read(address,160)
        if struct.unpack_from('<Q',data)[0]!=self.base+self.profile.fc_vtable:raise RuntimeError('AE 效果目录对象类型不符，未执行写入')
        fields=[]
        for start,end_field in ((16,64),(64,96),(96,160)):
            raw=data[start:end_field]
            if b'\0' not in raw:raise RuntimeError('AE 效果名称字段未终止，未执行写入')
            fields.append(raw.split(b'\0',1)[0].decode(self.profile.encoding if start!=64 else 'ascii'))
        return {'address':address,'displayName':fields[0],'matchName':fields[1],'category':fields[2],'guard':data[:16]+data[64:160]}
    def unchanged(self,row):
        # One catalog snapshot is taken before a batch; each object is then re-read just before its write.
        now=self.read_record(row['address'])
        return now['matchName']==row['matchName'] and now['guard']==row['guard']
    def target(self,key):
        targets=[r for r in self.records() if r['matchName']==key]
        if len(targets)!=1:raise RuntimeError('当前 AE 中未找到唯一的效果：'+key)
        return targets[0]

UNSUPPORTED='此 AE 构建尚未适配实时写入'

def describe(process:dict)->str:
    folder=Path(process.get('imagePath') or '').parent.parent.name or 'AE'
    return f"{folder}（{process.get('fileVersion') or '版本未知'}）"

def current_process(write=False,expected_image=None):
    processes=native_probe().get('processes',[])
    if not processes:raise RuntimeError('没有找到正在运行的 AE，请先启动所选 AE')
    if len(processes)!=1:raise RuntimeError('检测到多个 AE 进程：'+'、'.join(map(describe,processes))+'。请只保留一个 AE 进程')
    process=processes[0]
    if expected_image:
        from ae_paths import path_key
        if path_key(process.get('imagePath') or '')!=path_key(expected_image):
            raise RuntimeError('正在运行的 AE 与所选路径不一致。请启动所选 AE：'+str(expected_image))
    if not runtime_allowed(process.get('fileVersion')):
        # Other builds keep the catalog elsewhere; never guess addresses in them.
        raise RuntimeError(f'检测到 {describe(process)}。{UNSUPPORTED}；已验证构建：{supported_text()}。扫描插件、翻译和方案功能不受影响')
    target=Process(process['pid'],write)
    if expected_image:
        from ae_paths import path_key
        try:
            if path_key(target.image)!=path_key(expected_image) or file_version(Path(target.image))!=process.get('fileVersion'):
                raise RuntimeError('打开进程后 AE 路径或版本发生变化，已停止操作')
        except Exception:
            target.close();raise
    return target

def read_live(expected_image=None):
    with (current_process(expected_image=expected_image) if expected_image else current_process()) as p:
        records=Registry(p).records()
        profile=get_profile(file_version(Path(p.image)))
        return {'pid':p.pid,'creationTime':p.creation_time,'image':p.image,'version':profile.version,'profileSha256':profile.flt_sha,'effects':[{k:v for k,v in r.items() if k not in ('address','guard')} for r in records]}

class NameSession:
    def __init__(self):self.journal_path=data_root()/'live-session.json';self.expected_image=None
    def process(self):return current_process(write=True,expected_image=self.expected_image) if self.expected_image else current_process(write=True)
    def journal(self):
        try:
            journal=json.loads(self.journal_path.read_text(encoding='utf8'))
            if not isinstance(journal,dict):raise ValueError('顶层必须是对象')
            if type(journal.get('schemaVersion')) is not int or journal['schemaVersion']!=1:raise ValueError('schemaVersion 必须是 1')
            if journal.get('encoding','utf8') not in ('utf8','mbcs'):raise ValueError('名称编码不受支持')
            if not isinstance(journal.get('entries'),dict):raise ValueError('entries 必须是对象')
            if journal['entries'] and (type(journal.get('pid')) is not int or type(journal.get('creationTime')) is not int):
                raise ValueError('非空记录的 pid 和 creationTime 必须是整数')
            for entry in journal['entries'].values():
                if not isinstance(entry,dict):raise ValueError('名称条目必须是对象')
                if not isinstance(entry.get('originalName'),str) or not isinstance(entry.get('newName'),str):raise ValueError('原始名称和新名称必须是文字')
                _name_bytes(entry.get('originalHex'),journal.get('encoding','utf8'));_name_bytes(entry.get('appliedHex'),journal.get('encoding','utf8'))
            return journal
        except FileNotFoundError:return {'schemaVersion':1,'entries':{}}
        except ValueError as error:
            raise RuntimeError('恢复记录损坏，已停止写入，原文件已保留：'+str(error)) from error
    def _identity(self,p,journal):
        if journal.get('entries') and (journal.get('pid')!=p.pid or journal.get('creationTime')!=p.creation_time):
            # These entries belong to a terminated/different AE instance. Never write their addresses.
            return {'schemaVersion':1,'entries':{}}
        return journal
    def apply(self,translations:dict[str,str],skip_missing=False):
        if not translations:raise ValueError('请先选择插件并输入中文名称')
        encoded={key:encode_name(value) for key,value in translations.items()}
        with _lock,_process_lock(),self.process() as p:
            main_window(p.pid)
            registry=Registry(p);records=registry.records();by_key={r['matchName']:r for r in records}
            profile=getattr(registry,'profile',None);encoding=profile.encoding if profile else 'utf8'
            encoded={key:encode_name(value,encoding) for key,value in translations.items()}
            if len(by_key)!=len(records):raise RuntimeError('效果内部标识不唯一，未执行写入')
            skipped=sorted(key for key in encoded if key not in by_key)
            if skipped and not skip_missing:raise ValueError('当前 AE 未加载此效果：'+skipped[0])
            for key in skipped:del encoded[key]
            if not encoded:return {'applied':{},'unchanged':[],'skipped':skipped,'pid':p.pid}
            final={key:encoded[key].split(b'\0',1)[0].decode(encoding) if key in encoded else row['displayName'] for key,row in by_key.items()}
            seen={};conflicts=[]
            for key,name in final.items():
                folded=name.casefold()
                if folded in seen and (key in encoded or seen[folded] in encoded):conflicts.append(name)
                seen[folded]=key
            if conflicts:
                # Report every clash at once so a whole scheme can be fixed in one pass.
                names=sorted(set(conflicts));shown='、'.join(names[:15])+(f' 等 {len(names)} 个' if len(names)>15 else '')
                raise ValueError('名称与其他效果重复，请修改：'+shown)
            journal=self._identity(p,self.journal());previous=json.loads(json.dumps(journal));staged=[];unchanged=[]
            journal.update({'pid':p.pid,'creationTime':p.creation_time,'image':p.image,'profileSha256':profile.flt_sha if profile else PROFILE_SHA,'encoding':encoding})
            for key,value in encoded.items():
                if key not in by_key:raise ValueError('当前 AE 未加载此效果：'+key)
                row=by_key[key];before=p.read(row['address']+16,FIELD_SIZE)
                try:_name_bytes(before.hex(),encoding)
                except RuntimeError:raise RuntimeError('AE 当前名称字段格式异常，未执行写入：'+key) from None
                if before==value:unchanged.append(key);continue
                entry=journal['entries'].get(key)
                if entry and before.hex()!=entry['appliedHex']:raise RuntimeError('此效果名称已被其他程序修改，未覆盖：'+key)
                journal['entries'][key]={'originalHex':entry['originalHex'] if entry else before.hex(),'appliedHex':value.hex(),'originalName':entry['originalName'] if entry else row['displayName'],'newName':final[key]}
                staged.append((row,before,value))
            applied={key:final[key] for key in encoded}
            if not staged:return {'applied':applied,'unchanged':unchanged,'skipped':skipped,'pid':p.pid}
            _save(self.journal_path,journal)
            attempted=[]
            try:
                snapshot={r['matchName']:r['address'] for r in registry.records()}
                for row,before,value in staged:
                    if snapshot.get(row['matchName'])!=row['address'] or not registry.unchanged(row) or p.read(row['address']+16,FIELD_SIZE)!=before:
                        raise RuntimeError('AE 目录在操作期间发生变化，已停止')
                    attempted.append((row,before));p.write_buffer(row['address']+16,value)
                # AE's palette is refreshed only when the user asks for it (the refresh button).
                verified=registry.records()
                if {r['matchName']:r['displayName'] for r in verified}!=final:raise RuntimeError('更名后目录校验不一致')
                return {'applied':applied,'unchanged':unchanged,'skipped':skipped,'pid':p.pid}
            except Exception as error:
                failed=[]
                for row,before in reversed(attempted):
                    try:
                        actual=registry.target(row['matchName'])
                        if actual['address']!=row['address'] or actual['guard']!=row['guard']:raise RuntimeError('回滚目标守卫不符')
                        p.write_buffer(row['address']+16,_name_bytes(before.hex(),encoding))
                    except Exception:failed.append(row['matchName'])
                if failed:
                    retained=json.loads(json.dumps(previous))
                    retained.update({k:v for k,v in journal.items() if k!='entries'})
                    for key in failed:retained['entries'][key]=journal['entries'][key]
                    _save(self.journal_path,retained)
                else:_save(self.journal_path,previous)
                state='部分字段未能回滚，记录已保留' if failed else '已完全回滚'
                raise RuntimeError('应用名称失败，'+state+'：'+str(error)) from error
    def restore(self,keys:list[str]|None=None):
        with _lock,_process_lock():
            journal=self.journal()
            wanted=list(journal['entries']) if keys is None else [k for k in keys if k in journal['entries']]
            if not wanted:return {'restored':{}}
            if not any(row.get('pid')==journal.get('pid') for row in native_probe().get('processes',[])):
                _save(self.journal_path,{'schemaVersion':1,'entries':{}})
                return {'restored':{},'oldProcessEnded':True}
            with self.process() as p:
                if journal.get('pid')!=p.pid or journal.get('creationTime')!=p.creation_time:
                    _save(self.journal_path,{'schemaVersion':1,'entries':{}})
                    return {'restored':{},'oldProcessEnded':True}
                main_window(p.pid);registry=Registry(p);restored={}
                records=registry.records();rows={r['matchName']:r for r in records}
                if len(rows)!=len(records):raise RuntimeError('效果内部标识不唯一，未执行恢复')
                for key in wanted:
                    if key not in rows:raise RuntimeError('当前 AE 中未找到唯一的效果：'+key)
                    entry=journal['entries'][key];current=p.read(rows[key]['address']+16,FIELD_SIZE)
                    if current.hex() not in (entry['appliedHex'],entry['originalHex']):raise RuntimeError('效果名称已被外部修改，未强制恢复：'+key)
                try:
                    for key in wanted:
                        entry=journal['entries'][key];row=rows[key]
                        if not registry.unchanged(row):raise RuntimeError('AE 目录在操作期间发生变化，已停止：'+key)
                        p.write_buffer(row['address']+16,_name_bytes(entry['originalHex'],journal.get('encoding','utf8')))
                        actual=registry.read_record(row['address'])
                        if actual['matchName']!=key or actual['guard']!=row['guard'] or actual['displayName']!=entry['originalName'] or p.read(row['address']+16,FIELD_SIZE).hex()!=entry['originalHex'].lower():
                            raise RuntimeError('效果目录恢复回读不一致，记录已保留：'+key)
                        restored[key]=entry['originalName'];del journal['entries'][key]
                finally:
                    # Saved once (also on failure) instead of per entry: a large restore would otherwise rewrite the journal thousands of times.
                    if restored:_save(self.journal_path,journal)
                return {'restored':restored}
