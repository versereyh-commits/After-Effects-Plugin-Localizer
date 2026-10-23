"""Refresh AE's existing native effects palette through its own menu action.

Window messages only: right-button messages posted to the panel's tab open the panel's own menu, and its
"Refresh List" item is highlighted and confirmed with Enter. The real cursor is never moved, nothing is clicked
and AE is not brought to the front (2026-10-21: the user asked for no more simulated mouse clicks)."""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
import sys,threading,time
_lookup_state=threading.local()
def _note(value):
    if hasattr(_lookup_state,'note'):_lookup_state.note(value)

def _user32():
    u=C.WinDLL('user32',use_last_error=True)
    u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    u.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
    u.SendMessageTimeoutW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM,W.UINT,W.UINT,C.POINTER(C.c_size_t)];u.SendMessageTimeoutW.restype=C.c_ssize_t
    u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
    u.GetMenuItemCount.argtypes=[W.HMENU];u.GetMenuItemCount.restype=C.c_int
    u.GetMenuItemID.argtypes=[W.HMENU,C.c_int];u.GetMenuItemID.restype=W.UINT
    u.GetMenuStringW.argtypes=[W.HMENU,W.UINT,W.LPWSTR,C.c_int,W.UINT]
    u.GetMenuState.argtypes=[W.HMENU,W.UINT,W.UINT];u.GetMenuState.restype=W.UINT
    u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
    u.IsWindowEnabled.argtypes=[W.HWND];u.IsWindow.argtypes=[W.HWND]
    u.ScreenToClient.argtypes=[W.HWND,C.POINTER(W.POINT)]
    return u

def main_window(pid:int):
    u=_user32();result=[];callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    @callback
    def visit(hwnd,arg):
        owner=W.DWORD();name=C.create_unicode_buffer(256)
        u.GetWindowThreadProcessId(hwnd,C.byref(owner));u.GetClassNameW(hwnd,name,len(name))
        if owner.value==pid and name.value.startswith('AE_CApplication_'):result.append(hwnd)
        return True
    u.EnumWindows(visit,0)
    if len(result)!=1:raise RuntimeError('未找到唯一的 AE 主窗口')
    if not u.IsWindowEnabled(result[0]):raise RuntimeError('请先关闭 AE 中的对话框，再应用名称')
    return result[0]

class _Placement(C.Structure):
    _fields_=[('length',W.UINT),('flags',W.UINT),('showCmd',W.UINT),('ptMinPosition',W.POINT),('ptMaxPosition',W.POINT),('rcNormalPosition',W.RECT)]

def _rect(rect):return (rect.left,rect.top,rect.right,rect.bottom)

class WindowKeeper:
    """Keeps AE's main window as the user left it while a script is handed to AE.

    Handing AE a script (AfterFX.exe -r) makes AE show its main window again, which takes a maximized or
    minimized window back to its normal size. The state is read before the hand-over; check() puts it back
    as soon as it changes, and gives the foreground back to this program when AE took it. Only asynchronous
    window calls are used, so a busy AE never blocks the caller. Anything unreadable leaves the window alone."""
    def __init__(self,pid:int,u=None,own_pid=None):
        import os
        self.pid=pid;self.own=own_pid if own_pid is not None else os.getpid();self.hwnd=None;self.restored=0
        try:
            self.u=u or _user32();self._api()
            hwnd=self._find()
            if hwnd:self.hwnd=hwnd;self.saved=self._state();self.foreground=self.u.GetForegroundWindow()
        except (OSError,AttributeError,ValueError):self.hwnd=None
    def _api(self):
        u=self.u
        for name,args,result in (('GetWindowPlacement',[W.HWND,C.POINTER(_Placement)],W.BOOL),('GetWindowRect',[W.HWND,C.POINTER(W.RECT)],W.BOOL),
                                 ('ShowWindowAsync',[W.HWND,C.c_int],W.BOOL),('SetWindowPos',[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT],W.BOOL),
                                 ('GetForegroundWindow',[],W.HWND),('SetForegroundWindow',[W.HWND],W.BOOL),('IsWindow',[W.HWND],W.BOOL)):
            fn=getattr(u,name,None)
            if fn is not None and hasattr(fn,'argtypes'):fn.argtypes=args;fn.restype=result
    def _find(self):
        u=self.u;found=[];callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        @callback
        def visit(hwnd,arg):
            owner=W.DWORD();name=C.create_unicode_buffer(256)
            u.GetWindowThreadProcessId(hwnd,C.byref(owner));u.GetClassNameW(hwnd,name,len(name))
            if owner.value==self.pid and name.value.startswith('AE_CApplication_'):found.append(hwnd)
            return True
        u.EnumWindows(visit,0)
        return found[0] if len(found)==1 else None
    def _state(self):
        placement=_Placement();placement.length=C.sizeof(_Placement);rect=W.RECT()
        if not self.u.GetWindowPlacement(self.hwnd,C.byref(placement)) or not self.u.GetWindowRect(self.hwnd,C.byref(rect)):raise ValueError('window state unreadable')
        # showCmd: 1 normal, 2 minimized, 3 maximized.
        return {'show':{2:2,3:3}.get(placement.showCmd,1),'normal':_rect(placement.rcNormalPosition),'rect':_rect(rect)}
    def check(self)->bool:
        """Puts the window back if the hand-over changed it; True when something was put back."""
        if not self.hwnd:return False
        try:
            if not self.u.IsWindow(self.hwnd):self.hwnd=None;return False
            now=self._state()
        except (OSError,ValueError):return False
        saved=self.saved;changed=False
        if now['show']!=saved['show']:
            # SW_MAXIMIZE / SW_SHOWMINNOACTIVE / SW_SHOWNOACTIVATE
            self.u.ShowWindowAsync(self.hwnd,{3:3,2:7,1:4}[saved['show']]);changed=True
        elif saved['show']==1 and now['rect']!=saved['rect']:
            left,top,right,bottom=saved['rect']
            # SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
            self.u.SetWindowPos(self.hwnd,None,left,top,right-left,bottom-top,0x0004|0x0010|0x4000);changed=True
        if changed:self.restored+=1;self.last=time.monotonic()
        # AE may take the foreground along with the hand-over (or with the restore, which is asynchronous).
        # Give it back only shortly after a restore, so a user who clicks AE meanwhile keeps that choice.
        if self.restored and time.monotonic()-self.last<1:
            foreground=self.u.GetForegroundWindow()
            if self.foreground and foreground!=self.foreground and self.u.IsWindow(self.foreground) and _owner(self.u,self.foreground)==self.own and _owner(self.u,foreground)==self.pid:
                self.u.SetForegroundWindow(self.foreground)
        return changed
    def settle(self,wait=.25,step=.05):
        """The final check after the hand-over; when this AE changed its window, watch a moment longer for
        the asynchronous restore and foreground change to land."""
        self.check()
        if not self.restored:return
        until=time.monotonic()+wait
        while time.monotonic()<until:time.sleep(step);self.check()

def _refresh_popup(u,pid):
    result=[];stalled=[];disabled=[];callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    @callback
    def visit(hwnd,arg):
        owner=W.DWORD();name=C.create_unicode_buffer(128)
        u.GetWindowThreadProcessId(hwnd,C.byref(owner));u.GetClassNameW(hwnd,name,len(name))
        if owner.value!=pid or name.value!='#32768':return True
        # A hung AE must not block this process: abort the query instead of waiting forever.
        menu=C.c_size_t()
        if not u.SendMessageTimeoutW(hwnd,0x1e1,0,0,0x0003,500,C.byref(menu)):stalled.append(hwnd);return True
        menu=menu.value;count=u.GetMenuItemCount(menu)
        if not 1<=count<=128:return True
        for i in range(count):
            text=C.create_unicode_buffer(256);u.GetMenuStringW(menu,i,text,len(text),0x400)
            if text.value.replace('&','').strip() not in ('刷新列表','Refresh List'):continue
            state=u.GetMenuState(menu,i,0x400);command=u.GetMenuItemID(menu,i)
            if state==0xffffffff or state&3 or command in (0,0xffffffff):disabled.append(hwnd);continue
            result.append((hwnd,menu,i,command))
        return True
    u.EnumWindows(visit,0)
    # An unreadable menu is not "no menu": stop rather than click blindly.
    if stalled:raise RuntimeError('AE 菜单无响应，已停止操作')
    if disabled:raise RuntimeError('AE 的刷新列表菜单不可用')
    if len(result)>1:raise RuntimeError('检测到多个效果刷新菜单，未执行操作')
    return result[0] if result else None

def _popups(u,pid):
    result=[];callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    @callback
    def visit(hwnd,arg):
        owner=W.DWORD();name=C.create_unicode_buffer(128)
        u.GetWindowThreadProcessId(hwnd,C.byref(owner));u.GetClassNameW(hwnd,name,len(name))
        if owner.value==pid and name.value=='#32768':result.append(hwnd)
        return True
    u.EnumWindows(visit,0)
    return result

def _label(value):
    import re
    value=value.replace('&','').split('\t')[0].strip().rstrip('.…').strip()
    return re.sub(r'\s*\([A-Za-z]\)$','',value).casefold()

def _palette(value):return _label(value).replace(' ','').replace('_','') in ('效果和预设','effectspresets','aeeffectspalette')

def _native_api(u):
    for name,args,result in (
        ('GetMenu',[W.HWND],W.HMENU),('GetSubMenu',[W.HMENU,C.c_int],W.HMENU),
        ('GetParent',[W.HWND],W.HWND),
        ('GetWindowTextW',[W.HWND,W.LPWSTR,C.c_int],C.c_int),('IsWindowVisible',[W.HWND],W.BOOL)):
        fn=getattr(u,name);fn.argtypes=args;fn.restype=result

def _owner(u,hwnd):
    pid=W.DWORD();u.GetWindowThreadProcessId(hwnd,C.byref(pid));return pid.value

def _native_windows(u,pid):
    rows=[];tops=[];seen=set();callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    def record(hwnd):
        if hwnd in seen or len(rows)>=4096 or _owner(u,hwnd)!=pid:return
        seen.add(hwnd);name=C.create_unicode_buffer(256);title=C.create_unicode_buffer(256);rect=W.RECT()
        u.GetClassNameW(hwnd,name,len(name));u.GetWindowTextW(hwnd,title,len(title));u.GetWindowRect(hwnd,C.byref(rect))
        rows.append({'hwnd':hwnd,'class':name.value,'title':title.value,'visible':bool(u.IsWindowVisible(hwnd)),'rect':(rect.left,rect.top,rect.right,rect.bottom),'parent':getattr(u,'GetParent',lambda h:None)(hwnd)})
    @callback
    def top(hwnd,arg):
        if _owner(u,hwnd)==pid:tops.append(hwnd);record(hwnd)
        return True
    @callback
    def child(hwnd,arg):record(hwnd);return True
    u.EnumWindows(top,0)
    for hwnd in tops:u.EnumChildWindows(hwnd,child,0)
    return rows,tops

def _panel_command(u,hwnd):
    menu=u.GetMenu(hwnd);matches=[]
    for i in range(max(0,min(u.GetMenuItemCount(menu),128))):
        text=C.create_unicode_buffer(256);u.GetMenuStringW(menu,i,text,len(text),0x400)
        if _label(text.value) not in ('窗口','window'):continue
        sub=u.GetSubMenu(menu,i)
        ignored=C.c_size_t()
        if not u.SendMessageTimeoutW(hwnd,0x117,sub,i,0x0003,250,C.byref(ignored)):raise RuntimeError('AE 窗口菜单无响应')
        for j in range(max(0,min(u.GetMenuItemCount(sub),128))):
            u.GetMenuStringW(sub,j,text,len(text),0x400)
            if not _palette(text.value):continue
            command=u.GetMenuItemID(sub,j);state=u.GetMenuState(sub,j,0x400)
            if state==0xffffffff or state&3 or command in (0,0xffffffff):raise RuntimeError(f'AE 效果和预设菜单不可用（command={command}, state={state}）')
            matches.append({'command':command,'checked':bool(state&8)})
    if len(matches)!=1:raise RuntimeError('无法唯一识别 AE“窗口”菜单中的“效果和预设”')
    return matches[0]

def _panel_window(rows,point):
    """The AE window under a screen point: the smallest visible AE window containing it, whatever covers it on
    screen (messages reach it either way)."""
    x,y=point;inside=[r for r in rows if r['visible'] and r['rect'][0]<=x<r['rect'][2] and r['rect'][1]<=y<r['rect'][3]]
    if not inside:return None
    return min(inside,key=lambda r:(r['rect'][2]-r['rect'][0])*(r['rect'][3]-r['rect'][1]))['hwnd']

def _locate_tab(hwnd,pid,u,deadline):
    """Screen centres of the Effects & Presets panel's tab (UIA, read only): an element named with the panel's title
    that is a short strip (the panel body itself is named "AE Effects Palette")."""
    import comtypes.client
    from comtypes.gen import UIAutomationClient as UIA
    _note('创建 UIA 接口')
    automation=comtypes.client.CreateObject(UIA.CUIAutomation8,interface=UIA.IUIAutomation)
    newer=automation.QueryInterface(UIA.IUIAutomation2);newer.ConnectionTimeout=500;newer.TransactionTimeout=500
    _,tops=_native_windows(u,pid);points=[];condition=None
    for value in ('效果和预设','Effects & Presets'):
        part=automation.CreatePropertyCondition(UIA.UIA_NamePropertyId,value)
        condition=part if condition is None else automation.CreateOrCondition(condition,part)
    for handle in [hwnd]+[h for h in tops if h!=hwnd and u.IsWindowVisible(h)]:
        if time.monotonic()>=deadline:raise TimeoutError('定位 AE 效果面板超时')
        _note('查找面板标签')
        found=automation.ElementFromHandle(handle).FindAll(UIA.TreeScope_Subtree,condition)
        for i in range(found.Length):
            node=found.GetElement(i);box=node.CurrentBoundingRectangle
            if node.CurrentIsOffscreen or not 8<=box.bottom-box.top<=48 or box.right-box.left<16:continue
            points.append(((box.left+box.right)//2,(box.top+box.bottom)//2))
    return sorted(set(points))

def _inspect(hwnd,pid,u,deadline):
    results=[];errors=[];finished=threading.Event();notes=['初始化 COM']
    def locate():
        initialized=False
        try:
            _lookup_state.note=lambda value:notes.append(value)
            sys.coinit_flags=2
            import comtypes;comtypes.CoInitializeEx(2);initialized=True
            from palette_messages import physical
            with physical(u):results.append(_locate_tab(hwnd,pid,u,deadline))
        except Exception as error:errors.append(error)
        finally:
            finished.set()
            if initialized:comtypes.CoUninitialize()
    threading.Thread(target=locate,daemon=True).start()
    if not finished.wait(max(0,deadline-time.monotonic())):raise TimeoutError('定位 AE 效果面板超时，未执行操作；最后阶段：'+notes[-1])
    if errors:raise errors[0]
    return results[0]

def _identity(pid,expected_image=None,creation_time=None):
    from win_process import Process
    from ae_paths import path_key
    with Process(pid,write=False) as p:
        if expected_image and path_key(p.image)!=path_key(expected_image):raise RuntimeError('AE 安装路径已变化，未刷新')
        if creation_time is not None and p.creation_time!=creation_time:raise RuntimeError('AE 已重新启动，请重新连接后刷新')
        return p.image,p.creation_time

def _diagnostic_path():
    from parameter_names import bridge_root
    import uuid
    folder=bridge_root()/'refresh'/uuid.uuid4().hex;folder.mkdir(parents=True,exist_ok=True);return folder/'diagnostic.json'

def _hit_target(u,rows,tops,point):
    from palette_messages import point_target
    return point_target(u,rows,tops,point)


def refresh_effects(pid:int,timeout:float=10,*,expected_image=None,creation_time=None)->dict:
    from palette_messages import refresh
    return refresh(pid,timeout,expected_image=expected_image,creation_time=creation_time)


def calibrate_effects(pid:int,timeout:float=10,*,expected_image=None,creation_time=None,progress=None)->dict:
    from palette_messages import refresh
    return refresh(pid,timeout,expected_image=expected_image,creation_time=creation_time,calibrate=True,progress=progress)
