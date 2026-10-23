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
