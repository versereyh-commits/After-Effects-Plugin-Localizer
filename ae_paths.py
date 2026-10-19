"""Locate AE on another Windows machine without executing discovered programs."""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
import hashlib,json,os,winreg
from pathlib import Path
from runtime import write_json_atomic

def path_key(path):return os.path.normcase(os.path.abspath(path))

def locate(value)->dict:
    """Accept an install root, Support Files, Plug-ins or AfterFX.exe."""
    path=Path(os.path.expandvars(str(value).strip().strip('"'))).expanduser()
    candidates=[path] if path.is_file() else [path/'AfterFX.exe',path/'Support Files'/'AfterFX.exe',path.parent/'AfterFX.exe']
    for exe in candidates:
        if exe.name.casefold()!='afterfx.exe' or not exe.is_file():continue
        exe=exe.resolve()
        with exe.open('rb') as stream:
            if stream.read(2)!=b'MZ':raise ValueError('所选 AfterFX.exe 不是 Windows 可执行文件')
        if not (exe.parent/'AfterFXLib.dll').is_file():raise ValueError('缺少 AfterFXLib.dll，请选择完整 AE 安装目录')
        root=exe.parent.parent if exe.parent.name.casefold()=='support files' else exe.parent
        return {'name':root.name,'exe':str(exe),'plugins':str(exe.parent/'Plug-ins')}
    raise ValueError('未找到 AfterFX.exe。请选择 AE 安装目录、Support Files 或 AfterFX.exe')

def running_paths():
    """Only process IDs and executable paths; no process memory or projects."""
    class Entry(C.Structure):
        _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('th32ProcessID',W.DWORD),('th32DefaultHeapID',C.c_size_t),('th32ModuleID',W.DWORD),('cntThreads',W.DWORD),('th32ParentProcessID',W.DWORD),('pcPriClassBase',W.LONG),('dwFlags',W.DWORD),('szExeFile',W.WCHAR*260)]
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];k.CreateToolhelp32Snapshot.restype=C.c_void_p
    k.Process32FirstW.argtypes=[C.c_void_p,C.POINTER(Entry)];k.Process32NextW.argtypes=k.Process32FirstW.argtypes
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=C.c_void_p
    k.QueryFullProcessImageNameW.argtypes=[C.c_void_p,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
    k.CloseHandle.argtypes=[C.c_void_p]
    snapshot=k.CreateToolhelp32Snapshot(2,0);result=[]
    if snapshot in (None,C.c_void_p(-1).value):return result
    entry=Entry();entry.dwSize=C.sizeof(entry)
    try:
        more=k.Process32FirstW(snapshot,C.byref(entry))
        while more:
            if entry.szExeFile.casefold()=='afterfx.exe':
                handle=k.OpenProcess(0x1000,False,entry.th32ProcessID)
                if handle:
                    try:
                        buffer=C.create_unicode_buffer(32768);size=W.DWORD(len(buffer))
                        if k.QueryFullProcessImageNameW(handle,0,buffer,C.byref(size)):result.append(buffer.value)
                    finally:k.CloseHandle(handle)
            more=k.Process32NextW(snapshot,C.byref(entry))
    finally:k.CloseHandle(snapshot)
    return result

def registry_paths():
    result=[]
    for hive in (winreg.HKEY_LOCAL_MACHINE,winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
            def value(path,name):
                try:
                    with winreg.OpenKey(hive,path,0,winreg.KEY_READ|view) as key:return winreg.QueryValueEx(key,name)[0]
                except OSError:return None
            def children(path):
                try:
                    with winreg.OpenKey(hive,path,0,winreg.KEY_READ|view) as key:return [winreg.EnumKey(key,i) for i in range(winreg.QueryInfoKey(key)[0])]
                except OSError:return []
            uninstall=r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
            for child in children(uninstall):
                path=uninstall+'\\'+child;name=value(path,'DisplayName')
                if isinstance(name,str) and 'Adobe After Effects' in name:
                    folder=value(path,'InstallLocation')
                    if isinstance(folder,str) and folder:result.append(folder)
            app=value(r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\AfterFX.exe','')
            if isinstance(app,str) and app:result.append(app)
            adobe=r'SOFTWARE\Adobe\After Effects'
            for child in children(adobe):
                for field in ('InstallPath','InstallLocation','Path'):
                    folder=value(adobe+'\\'+child,field)
                    if isinstance(folder,str) and folder:result.append(folder)
    return result

def discover(saved=(),registry=None,running=None,defaults=None):
    sources=[(p,'手动定位') for p in saved]
    sources += [(p,'注册表') for p in (registry_paths() if registry is None else registry)]
    sources += [(p,'运行中的 AE') for p in (running_paths() if running is None else running)]
    if defaults is None:
        defaults=[]
        for variable in ('ProgramW6432','ProgramFiles','ProgramFiles(x86)'):
            base=os.environ.get(variable)
            if base:defaults.append(Path(base)/'Adobe')
    for root in defaults:
        sources.append((root,'默认目录'))
        try:sources.extend((p,'默认目录') for p in Path(root).iterdir() if p.is_dir() and 'after effects' in p.name.casefold())
        except OSError:pass
    found={}
    for path,source in sources:
        try:item=locate(path)
        except (OSError,ValueError):continue
        key=path_key(item['exe'])
        if key not in found:found[key]={**item,'sources':[]}
        if source not in found[key]['sources']:found[key]['sources'].append(source)
    result=sorted(found.values(),key=lambda x:(x['name'],path_key(x['exe'])),reverse=True)
    counts={}
    for item in result:
        name=item['name'];counts[name]=counts.get(name,0)+1
        item['key']=name if counts[name]==1 else name+'_'+hashlib.sha256(path_key(item['exe']).encode()).hexdigest()[:10]
    return result

def read_saved(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(value,dict) or value.get('schemaVersion')!=1 or not isinstance(value.get('paths'),list):raise ValueError('格式错误')
        if not all(isinstance(p,str) for p in value['paths']):raise ValueError('路径必须为文字')
        return value['paths'],''
    except FileNotFoundError:return [],''
    except (OSError,ValueError) as error:return [],'AE 路径记录无法读取，已保留原文件；手动定位暂不保存：'+str(error)

def remember(path,exe):
    saved,warning=read_saved(path)
    if warning:raise ValueError(warning)
    item=locate(exe);saved=[p for p in saved if path_key(p)!=path_key(item['exe'])]+[item['exe']]
    write_json_atomic(path,{'schemaVersion':1,'paths':saved})
    return item

_flt_digests={}
def installed_flt_sha(exe)->str|None:
    """SHA-256 of an installation's Support Files\\FLT.dll (None if unreadable), cached while the file's path,
    size, modification time and file ID stay the same."""
    path=Path(exe).parent/'FLT.dll'
    try:
        st=path.stat();key=(path_key(path),st.st_size,st.st_mtime_ns,st.st_ino)
        if key not in _flt_digests:
            with path.open('rb') as file:_flt_digests[key]=hashlib.file_digest(file,'sha256').hexdigest()
        return _flt_digests[key]
    except OSError:return None

def compatibility(item,version_of):
    """Static file evidence only; never grants a new runtime adapter."""
    from ae_profiles import get_profile
    exe=Path(item['exe']);report={'name':item['name'],'exe':str(exe),'plugins':item['plugins'],'sources':item.get('sources',[]),'modules':{},'effects':False,'parameters':False}
    try:report['version']=version_of(exe)
    except Exception:report['version']='无法读取'
    try:profile=get_profile(report['version'],installed_flt_sha(exe))   # the built-in or this machine's, by the file
    except RuntimeError:profile=None
    for name,expected in (('FLT.dll',profile.flt_sha if profile else None),('TDB.dll',profile.tdb_sha if profile else None)):
        path=exe.parent/name
        try:
            with path.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
            report['modules'][name]={'sha256':digest,'matchesAdapter':digest==expected}
        except OSError:report['modules'][name]={'matchesAdapter':False,'error':'文件缺失或不可读'}
    report['effects']=bool(profile and profile.enabled and report['modules']['FLT.dll']['matchesAdapter'])
    report['parameters']=report['effects'] and report['modules']['TDB.dll']['matchesAdapter']
    if profile and profile.native_dynamic:
        try:
            with (exe.parent/'U.dll').open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
            report['modules']['U.dll']={'sha256':digest,'matchesAdapter':digest==profile.codec_sha}
        except OSError:report['modules']['U.dll']={'matchesAdapter':False,'error':'文件缺失或不可读'}
        report['effects']=report['effects'] and report['modules']['TDB.dll']['matchesAdapter'] and report['modules']['U.dll']['matchesAdapter']
        report['parameters']=report['effects']
    report['status']='效果与参数构建匹配，连接时继续核验' if report['parameters'] else ('效果构建匹配，参数构建不匹配' if report['effects'] else '未适配实时写入；可扫描、翻译、保存方案')
    report['hostVerified']=False
    return report
