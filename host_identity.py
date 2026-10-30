"""Protocol identities use hexadecimal FILETIME strings (never rounded JS numbers)."""
import os,re,ctypes as C
from ctypes import wintypes as W
from pathlib import Path

def query(pid):
    """Only PROCESS_QUERY_LIMITED_INFORMATION, including for public-API quick adds."""
    k=C.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=C.c_void_p
    k.CloseHandle.argtypes=[C.c_void_p]
    k.QueryFullProcessImageNameW.argtypes=[C.c_void_p,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)];k.QueryFullProcessImageNameW.restype=W.BOOL
    k.GetProcessTimes.argtypes=[C.c_void_p,*([C.POINTER(W.FILETIME)]*4)];k.GetProcessTimes.restype=W.BOOL
    handle=k.OpenProcess(0x1000,False,pid)
    if not handle:raise C.WinError(C.get_last_error())
    try:
        path=C.create_unicode_buffer(32768);size=W.DWORD(len(path));created,ended,kernel,user=(W.FILETIME() for _ in range(4))
        if not k.QueryFullProcessImageNameW(handle,0,path,C.byref(size)) or not k.GetProcessTimes(handle,C.byref(created),C.byref(ended),C.byref(kernel),C.byref(user)):raise C.WinError(C.get_last_error())
        return path.value,(created.dwHighDateTime<<32)|created.dwLowDateTime
    finally:k.CloseHandle(handle)

def target(process):
    creation=getattr(process,'creation_time',None)
    if not isinstance(creation,int):raise RuntimeError('无法确认 AE 进程创建时间，请重新连接')
    return {'pid':process.pid,'creationTime':f'{creation:016x}','image':os.path.normcase(os.path.abspath(process.image)).replace('\\','/')}

def valid(value):
    return isinstance(value,dict) and type(value.get('pid')) is int and value['pid']>0 and isinstance(value.get('creationTime'),str) and bool(re.fullmatch('[0-9a-f]{16}',value['creationTime'])) and isinstance(value.get('image'),str) and Path(value['image']).name.casefold()=='afterfx.exe'

def same(actual,expected):
    return valid(actual) and valid(expected) and actual['pid']==expected['pid'] and actual['creationTime']==expected['creationTime'] and actual['image'].replace('\\','/').casefold()==expected['image'].replace('\\','/').casefold()

def key(value):
    if not valid(value):raise RuntimeError('AE 进程身份无效，未提交请求')
    return str(value['pid'])+'-'+value['creationTime']


def running_pids():
    """Enumerate AE process IDs without reading modules or process memory.

    Inaccessible AE processes remain in the set; only a confirmed exited snapshot entry is ignored.
    """
    class Entry(C.Structure):
        _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('th32ProcessID',W.DWORD),('th32DefaultHeapID',C.c_size_t),('th32ModuleID',W.DWORD),('cntThreads',W.DWORD),('th32ParentProcessID',W.DWORD),('pcPriClassBase',C.c_long),('dwFlags',W.DWORD),('szExeFile',W.WCHAR*260)]
    k=C.WinDLL('kernel32',use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];k.CreateToolhelp32Snapshot.restype=C.c_void_p
    k.Process32FirstW.argtypes=[C.c_void_p,C.POINTER(Entry)];k.Process32FirstW.restype=W.BOOL
    k.Process32NextW.argtypes=[C.c_void_p,C.POINTER(Entry)];k.Process32NextW.restype=W.BOOL
    k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=C.c_void_p
    k.GetExitCodeProcess.argtypes=[C.c_void_p,C.POINTER(W.DWORD)];k.GetExitCodeProcess.restype=W.BOOL
    k.CloseHandle.argtypes=[C.c_void_p]
    snapshot=k.CreateToolhelp32Snapshot(2,0)
    if snapshot in (None,C.c_void_p(-1).value):raise C.WinError(C.get_last_error())
    found=set();entry=Entry();entry.dwSize=C.sizeof(entry)
    try:
        more=k.Process32FirstW(snapshot,C.byref(entry))
        if not more and C.get_last_error()!=18:raise C.WinError(C.get_last_error())
        while more:
            if entry.szExeFile.casefold()=='afterfx.exe':
                pid=int(entry.th32ProcessID);handle=k.OpenProcess(0x1000,False,pid);ended=False
                if handle:
                    try:
                        code=W.DWORD()
                        ended=bool(k.GetExitCodeProcess(handle,C.byref(code))) and code.value!=259
                    finally:k.CloseHandle(handle)
                elif C.get_last_error()==87:ended=True # PID disappeared after the snapshot.
                if not ended:found.add(pid)
            more=k.Process32NextW(snapshot,C.byref(entry))
        if C.get_last_error()!=18:raise C.WinError(C.get_last_error())
        return found
    finally:k.CloseHandle(snapshot)
