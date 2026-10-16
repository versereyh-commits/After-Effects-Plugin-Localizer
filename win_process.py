"""Small Windows process adapter; writes are confined to guarded data buffers."""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import struct

class Process:
    def __init__(self,pid:int,write:bool=False):
        self.pid=pid;self.k=C.WinDLL('kernel32',use_last_error=True)
        self.k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];self.k.OpenProcess.restype=C.c_void_p
        self.k.CloseHandle.argtypes=[C.c_void_p]
        self.k.ReadProcessMemory.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)];self.k.ReadProcessMemory.restype=W.BOOL
        self.k.WriteProcessMemory.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)];self.k.WriteProcessMemory.restype=W.BOOL
        self.k.QueryFullProcessImageNameW.argtypes=[C.c_void_p,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
        self.k.GetProcessTimes.argtypes=[C.c_void_p,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME)]
        self.handle=self.k.OpenProcess(0x438 if write else 0x410,False,pid)
        if not self.handle:raise C.WinError(C.get_last_error())
        name=C.create_unicode_buffer(32768);size=W.DWORD(len(name))
        if not self.k.QueryFullProcessImageNameW(self.handle,0,name,C.byref(size)):
            self.close();raise C.WinError(C.get_last_error())
        self.image=name.value
        if Path(self.image).name.lower()!='afterfx.exe':self.close();raise ValueError('目标不是 AE 进程')
        created,ended,kernel,user=(W.FILETIME() for _ in range(4))
        if not self.k.GetProcessTimes(self.handle,C.byref(created),C.byref(ended),C.byref(kernel),C.byref(user)):
            self.close();raise C.WinError(C.get_last_error())
        self.creation_time=(created.dwHighDateTime<<32)|created.dwLowDateTime
    def close(self):
        if getattr(self,'handle',None):self.k.CloseHandle(self.handle);self.handle=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def read(self,address:int,size:int)->bytes:
        data=C.create_string_buffer(size);got=C.c_size_t()
        if not self.k.ReadProcessMemory(self.handle,C.c_void_p(address),data,size,C.byref(got)) or got.value!=size:
            raise RuntimeError('AE 内存读取失败；进程可能已退出或正在切换状态')
        return data.raw
    def read_into(self,address:int,buffer,size:int|None=None)->int:
        """Read into caller-owned writable storage without copying its contents."""
        view=memoryview(buffer)
        if view.readonly or not view.c_contiguous:raise ValueError('读取缓冲区必须连续且可写')
        count=view.nbytes if size is None else size
        if type(count) is not int or not 0<count<=view.nbytes:raise ValueError('读取长度超出缓冲区')
        if not self.handle:raise RuntimeError('AE 进程句柄已关闭')
        data=(C.c_ubyte*view.nbytes).from_buffer(buffer);got=C.c_size_t()
        if not self.k.ReadProcessMemory(self.handle,C.c_void_p(address),data,count,C.byref(got)) or got.value!=count:
            raise RuntimeError('AE 内存读取失败；进程可能已退出或正在切换状态')
        return got.value
    def ensure_alive(self):
        if not self.handle:raise RuntimeError('AE 进程句柄已关闭')
        self.k.GetExitCodeProcess.argtypes=[C.c_void_p,C.POINTER(W.DWORD)]
        self.k.GetExitCodeProcess.restype=W.BOOL
        code=W.DWORD()
        if not self.k.GetExitCodeProcess(self.handle,C.byref(code)) or code.value!=259:
            raise RuntimeError('AE 进程已退出或无法确认状态，停止扫描')
    def u64(self,address:int)->int:return struct.unpack('<Q',self.read(address,8))[0]
    def write_buffer(self,address:int,data:bytes):
        if not 1<=len(data)<=48:raise ValueError('拒绝超出名称字段范围的写入')
        got=C.c_size_t();buffer=C.create_string_buffer(data,len(data))
        if not self.k.WriteProcessMemory(self.handle,C.c_void_p(address),buffer,len(data),C.byref(got)) or got.value!=len(data):
            raise RuntimeError('名称写入失败：'+str(C.WinError(C.get_last_error())))
        if self.read(address,len(data))!=data:raise RuntimeError('名称写入后回读不一致')
    def modules(self):
        self.k.K32EnumProcessModulesEx.argtypes=[C.c_void_p,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),W.DWORD]
        self.k.K32GetModuleFileNameExW.argtypes=[C.c_void_p,C.c_void_p,W.LPWSTR,W.DWORD]
        array=(C.c_void_p*4096)();needed=W.DWORD()
        if not self.k.K32EnumProcessModulesEx(self.handle,array,C.sizeof(array),C.byref(needed),2):raise C.WinError(C.get_last_error())
        result=[]
        for base in array[:min(needed.value//8,4096)]:
            name=C.create_unicode_buffer(32768)
            if self.k.K32GetModuleFileNameExW(self.handle,base,name,len(name)):
                result.append({'base':base,'path':name.value,'name':Path(name.value).name})
        return result
