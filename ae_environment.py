"""What else runs inside AE that could get in the way of the parameter helper. Read-only, from outside AE.

On one AE 2025 25.6 the helper's read of AE's own FLT.dll never returned inside AE while the same file read fine from
this program. The usual causes are code injected into AE that rewrites the file APIs (a crack's DLL hijack, or a
security product's behaviour monitor). This module checks, from outside, whether those API entry points in AE differ
from this process's copies -- system DLLs share their addresses across processes within one boot -- and where any
redirected entry jumps to, and lists the modules in AE that are neither Windows nor Adobe and sit outside AE's folder
or carry a commonly hijacked name. Only function names, module file names and company names are kept: no addresses,
bytes or paths."""
from __future__ import annotations
import ctypes,os,struct,time
from ctypes import wintypes as W
from pathlib import Path

FUNCTIONS={'ntdll.dll':('NtReadFile','NtCreateFile','NtCreateSection','NtMapViewOfSection','NtQueryInformationFile'),
           'kernelbase.dll':('ReadFile','CreateFileW','CreateFileMappingW','MapViewOfFile','GetModuleFileNameW'),
           'kernel32.dll':('ReadFile','CreateFileW')}
# DLL names a hijacking DLL placed beside AfterFX.exe usually takes.
HIJACK_NAMES={'version.dll','winmm.dll','winhttp.dll','dinput8.dll','dxgi.dll','d3d9.dll','dbghelp.dll','wininet.dll','iphlpapi.dll','uxtheme.dll','winspool.drv','msimg32.dll'}
PROLOGUE=16

def _local_entries()->dict[str,tuple[int,bytes]]:
    """This process's address and first bytes of every checked function ('dll!name')."""
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.GetModuleHandleW.restype=ctypes.c_void_p;k.GetModuleHandleW.argtypes=[W.LPCWSTR]
    k.GetProcAddress.restype=ctypes.c_void_p;k.GetProcAddress.argtypes=[ctypes.c_void_p,ctypes.c_char_p]
    found={}
    for dll,names in FUNCTIONS.items():
        base=k.GetModuleHandleW(dll)
        for name in names:
            address=k.GetProcAddress(base,name.encode()) if base else None
            if address:found[f'{dll}!{name}']=(address,ctypes.string_at(address,PROLOGUE))
    return found

def jump_target(code:bytes,address:int,read)->int|None:
    """Where a rewritten entry point jumps: jmp rel32, jmp [rip+x], mov rax,imm64 / jmp rax, push imm32 / ret."""
    if code[:1]==b'\xe9':return (address+5+struct.unpack_from('<i',code,1)[0])&0xFFFFFFFFFFFFFFFF
    if code[:2]==b'\xff\x25':
        slot=(address+6+struct.unpack_from('<i',code,2)[0])&0xFFFFFFFFFFFFFFFF
        try:return struct.unpack('<Q',read(slot,8))[0]
        except Exception:return None
    if code[:2]==b'\x48\xb8' and code[10:12]==b'\xff\xe0':return struct.unpack_from('<Q',code,2)[0]
    if code[:1]==b'\x68' and code[5:6]==b'\xc3':return struct.unpack_from('<I',code,1)[0]
    return None

def _module_ranges(process,modules)->list[tuple[int,int,dict]]:
    """(start, end, module) from each module's PE header in AE's memory."""
    ranges=[]
    for m in modules:
        try:
            header=process.read(m['base'],0x400);offset=struct.unpack_from('<I',header,0x3C)[0]
            if offset+0x54>len(header) or header[offset:offset+4]!=b'PE\0\0':continue
            size=struct.unpack_from('<I',header,offset+0x50)[0]   # OptionalHeader.SizeOfImage
            ranges.append((m['base'],m['base']+size,m))
        except Exception:continue
    return ranges

def company(path:str)->str:
    """The CompanyName in a file's version resource, or ''."""
    try:
        v=ctypes.WinDLL('version')
        size=v.GetFileVersionInfoSizeW(path,None)
        if not size:return ''
        buffer=ctypes.create_string_buffer(size)
        if not v.GetFileVersionInfoW(path,0,size,buffer):return ''
        pointer=ctypes.c_void_p();length=ctypes.c_uint()
        if not v.VerQueryValueW(buffer,'\\VarFileInfo\\Translation',ctypes.byref(pointer),ctypes.byref(length)) or length.value<4:return ''
        lang,codepage=struct.unpack('<HH',ctypes.string_at(pointer,4))
        if not v.VerQueryValueW(buffer,f'\\StringFileInfo\\{lang:04x}{codepage:04x}\\CompanyName',ctypes.byref(pointer),ctypes.byref(length)):return ''
        return ctypes.wstring_at(pointer,length.value).rstrip('\0').strip()[:80]
    except (OSError,ValueError,AttributeError):return ''

def _file_state(path:Path)->dict:
    """Attributes that make a file download on access, and how long reading it here takes."""
    try:
        attributes=ctypes.windll.kernel32.GetFileAttributesW(str(path))
        flags=[name for bit,name in ((0x400,'reparsePoint'),(0x1000,'offline'),(0x40000,'recallOnOpen'),(0x400000,'recallOnDataAccess')) if attributes!=0xFFFFFFFF and attributes&bit]
        started=time.perf_counter()
        with open(path,'rb') as stream:
            while stream.read(1<<20):pass
        return {'flags':flags,'readMs':round((time.perf_counter()-started)*1000)}
    except OSError as error:return {'error':type(error).__name__}

def scan(process,local=None,company_of=company)->dict:
    """A read-only look at AE's process: rewritten file APIs and where they lead, notable foreign modules, and the
    state of AE's FLT/TDB/U files. Never raises: a failure is reported as 'scanError'."""
    try:
        modules=process.modules();ranges=_module_ranges(process,modules)
        windows=os.environ.get('SystemRoot',r'C:\Windows').casefold();ae=str(Path(process.image).parent).casefold()
        companies={}
        def maker(m):
            if m['path'] not in companies:companies[m['path']]=company_of(m['path'])
            return companies[m['path']]
        hooks=[];entries=local if local is not None else _local_entries()
        for key,(address,mine) in entries.items():
            try:theirs=process.read(address,PROLOGUE)
            except Exception:hooks.append({'function':key,'state':'unreadable'});continue
            if theirs==mine:continue
            target=jump_target(theirs,address,process.read);owner=next((m for start,end,m in ranges if target is not None and start<=target<end),None)
            hooks.append({'function':key,'state':'rewritten','target':owner['name'] if owner else ('unknown' if target is not None else 'not a jump'),
                          'company':maker(owner) if owner else ''})
        foreign=[]
        for m in modules:
            path=m['path'].casefold()
            if path.startswith(windows) or m['name'].casefold().startswith('ae_parameters_'):continue   # Windows, or this program's own helper
            outside=not path.startswith(ae);hijack=not outside and m['name'].casefold() in HIJACK_NAMES
            if not (outside or hijack):continue
            maker_name=maker(m)
            if 'adobe' in maker_name.casefold():continue
            foreign.append({'module':m['name'],'company':maker_name,'where':'outsideAE' if outside else 'hijackName'})
        files={name:_file_state(Path(process.image).parent/name) for name in ('FLT.dll','TDB.dll','U.dll')}
        return {'checkedFunctions':len(entries),'hooks':hooks,'foreignModules':foreign[:60],'foreignCount':len(foreign),'moduleCount':len(modules),'files':files}
    except Exception as error:return {'scanError':type(error).__name__}

def summary(result:dict)->str:
    """One sentence on what the scan found that can explain a stalled file read inside AE, or ''."""
    rewritten=[h for h in result.get('hooks',[]) if h.get('state')=='rewritten']
    if rewritten:
        by={}
        for h in rewritten:by.setdefault((h['target'],h.get('company','')),[]).append(h['function'].split('!')[1])
        parts=[f"{'、'.join(dict.fromkeys(names))} 被 {target}{'（'+maker+'）' if maker else ' '}改动" for (target,maker),names in by.items()]
        return '检测到 AE 里的文件函数 '+'；'.join(parts)+'，这通常是破解补丁或安全软件注入了 AE，会拦截辅助模块读取 AE 自身的文件'
    slow=[name for name,state in result.get('files',{}).items() if state.get('flags')]
    if slow:return f"AE 的 {'、'.join(slow)} 是需要按需下载或带特殊属性的文件（例如网盘占位文件），在 AE 里读取可能被挂起"
    return ''
