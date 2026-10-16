"""Refresh AE's existing native effects palette through its own menu action."""
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
        ('GetForegroundWindow',[],W.HWND),('SetForegroundWindow',[W.HWND],W.BOOL),
        ('SetCursorPos',[C.c_int,C.c_int],W.BOOL),('GetCursorPos',[C.POINTER(W.POINT)],W.BOOL),
        ('ClientToScreen',[W.HWND,C.POINTER(W.POINT)],W.BOOL),
        ('WindowFromPoint',[W.POINT],W.HWND),('mouse_event',[W.DWORD,W.DWORD,W.DWORD,W.DWORD,C.c_size_t],None),
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
        rows.append({'hwnd':hwnd,'class':name.value,'title':title.value,'visible':bool(u.IsWindowVisible(hwnd)),'rect':(rect.left,rect.top,rect.right,rect.bottom)})
    @callback
    def top(hwnd,arg):
        if _owner(u,hwnd)==pid:tops.append(hwnd);record(hwnd)
        return True
    @callback
    def child(hwnd,arg):record(hwnd);return True
    u.EnumWindows(top,0)
    for hwnd in tops:u.EnumChildWindows(hwnd,child,0)
    return rows,tops

def _has_edit(u,hwnd,visible=False):
    found=[];callback=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    @callback
    def visit(child,arg):
        cls=C.create_unicode_buffer(128);u.GetClassNameW(child,cls,len(cls))
        if cls.value=='Edit' and (not visible or u.IsWindowVisible(child)):found.append(child);return False
        return True
    u.EnumChildWindows(hwnd,visit,0);return bool(found)

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

def _point(u,hwnd,box):
    left,top,right,bottom=box
    if right<=left or bottom<=top:return None
    point=W.POINT((left+right)//2,(top+bottom)//2)
    if not u.ScreenToClient(hwnd,C.byref(point)):return None
    return hwnd,((point.y&0xffff)<<16)|(point.x&0xffff)

def _raw_nodes(automation,root,deadline,limit=96,walker=None):
    walker=walker or automation.RawViewWalker;stack=[(root,())];count=0
    while stack and count<limit:
        _note('遍历面板节点 '+str(count))
        if time.monotonic()>=deadline:raise TimeoutError('定位 AE 面板超时')
        node,parents=stack.pop();count+=1;yield node,parents
        if len(parents)>=7:continue
        child=walker.GetFirstChildElement(node);children=[]
        while child and len(children)<12:
            children.append(child);child=walker.GetNextSiblingElement(child)
        stack.extend((child,parents+(node,)) for child in reversed(children))

def _locate_menu_button(hwnd,pid,u,deadline):
    """Only reads geometry. No UIA Invoke or delayed inputs in this worker."""
    import comtypes.client
    from comtypes.gen import UIAutomationClient as UIA
    _note('创建 UIA 接口')
    automation=comtypes.client.CreateObject(UIA.CUIAutomation8,interface=UIA.IUIAutomation)
    newer=automation.QueryInterface(UIA.IUIAutomation2);newer.ConnectionTimeout=500;newer.TransactionTimeout=500
    _note('读取原生窗口')
    windows,tops=_native_windows(u,pid);native=[r for r in windows if _palette(r['title']) or _palette(r['class'])]
    native_handles={r['hwnd'] for r in native};buttons={};headers={};names=[]
    if not native_handles:
        from ae_palette_pixels import candidates as pixel_candidates
        pixels=pixel_candidates(u,[hwnd]+[h for h in tops if h!=hwnd],pid)
        # A palette/search frame owns its persistent native Edit child. This
        # excludes workspace, properties and audio menu buttons, whose opening
        # would expand unrelated accordion panels.
        pixels=[p for p in pixels if p['target']!=hwnd and _has_edit(u,p['target'])]
        if pixels:return {'button':None,'header':None,'nativeCandidates':0,'candidates':pixels,'strategy':'native-menu-glyph'}
        candidates={}
        for handle in [hwnd]+[h for h in tops if h!=hwnd and u.IsWindowVisible(h)]:
            _note('批量读取面板树')
            request=automation.CreateCacheRequest();request.TreeScope=UIA.TreeScope_Subtree;request.TreeFilter=automation.ControlViewCondition
            for prop in (UIA.UIA_ClassNamePropertyId,UIA.UIA_NamePropertyId,UIA.UIA_BoundingRectanglePropertyId,UIA.UIA_IsOffscreenPropertyId):request.AddProperty(prop)
            root=automation.ElementFromHandleBuildCache(handle,request);stack=[root];count=0
            while stack and count<256:
                if time.monotonic()>=deadline:raise TimeoutError('定位 AE 面板超时')
                node=stack.pop();count+=1;_note('读取缓存面板节点 '+str(count))
                children=node.GetCachedChildren()
                if children:stack.extend(children.GetElement(i) for i in reversed(range(min(children.Length,128))))
                kind=node.CachedClassName or node.CachedName or ''
                if not any(key in kind for key in ('Frame Window','TabPanel Window','OS_ViewContainer')):continue
                rect=node.CachedBoundingRectangle;box=(rect.left,rect.top,rect.right,rect.bottom)
                if node.CachedIsOffscreen or box[2]-box[0]<90 or box[3]-box[1]<10:continue
                # Container bounds come from AE's provider. No screen/template
                # coordinates are saved or reused; menu contents identify it.
                if 'OS_ViewContainer' in kind and box[3]-box[1]>48:continue
                target=handle
                x=(box[0]+box[2])//2;y=box[1]+min(12,(box[3]-box[1])//2)
                key=(target,x,y)
                candidates[key]={'target':target,'x':x,'y':y,'rect':box,'kind':kind}
        return {'button':None,'header':None,'nativeCandidates':0,'classes':sorted({r['class'] for r in windows})[:80],
                'candidates':sorted(candidates.values(),key=lambda c:(0 if 'TabPanel' in c['kind'] else 1,c['rect'][3]-c['rect'][1]))[:24]}
    roots=list(native_handles)
    for handle in native_handles:
        parent=u.GetParent(handle)
        for depth in range(12):
            if not parent or _owner(u,parent)!=pid:break
            cls=C.create_unicode_buffer(256);u.GetClassNameW(parent,cls,len(cls))
            if 'frame' in cls.value.casefold():roots.append(parent);break
            parent=u.GetParent(parent)
    if not roots:roots=[hwnd]+[h for h in tops if h!=hwnd and u.IsWindowVisible(h)]
    for handle in dict.fromkeys(roots):
        root=automation.ElementFromHandle(handle)
        condition=None
        for value in ('UI_PictureButton','AE Effects Palette','效果和预设','Effects & Presets'):
            part=automation.CreatePropertyCondition(UIA.UIA_NamePropertyId,value)
            condition=part if condition is None else automation.CreateOrCondition(condition,part)
        found=root.FindAll(UIA.TreeScope_Subtree,condition)
        for i in range(found.Length):
            if time.monotonic()>=deadline:raise TimeoutError('定位 AE 面板超时')
            node=found.GetElement(i);ancestors=[];parent=automation.RawViewWalker.GetParentElement(node)
            for depth in range(16):
                if not parent:break
                ancestors.append(parent)
                if parent.CurrentNativeWindowHandle==handle:break
                parent=automation.RawViewWalker.GetParentElement(parent)
            parents=tuple(reversed(ancestors))
            name=node.CurrentName or '';cls=node.CurrentClassName or '';is_palette=_palette(name) or _palette(cls)
            if is_palette:names.append(name or cls)
            if name!='UI_PictureButton' and not is_palette:continue
            box=node.CurrentBoundingRectangle;rect=(box.left,box.top,box.right,box.bottom)
            if node.CurrentIsOffscreen or rect[2]<=rect[0] or rect[3]<=rect[1]:continue
            chain=parents+(node,);target=next((p.CurrentNativeWindowHandle for p in reversed(chain) if p.CurrentNativeWindowHandle),handle)
            if _owner(u,target)!=pid:continue
            if is_palette and rect[3]-rect[1]<=60:
                point=_point(u,target,rect)
                if point:headers[point]=point
            if name!='UI_PictureButton':continue
            marked=handle in native_handles or any(_palette(p.CurrentName or '') or _palette(p.CurrentClassName or '') for p in parents)
            if not marked:
                frame=next((p for p in reversed(parents) if 'frame' in (p.CurrentClassName or '').casefold()),None)
                if frame is None:continue
                f=frame.CurrentBoundingRectangle
                if not any(f.left<=r['rect'][0] and f.top<=r['rect'][1] and f.right>=r['rect'][2] and f.bottom>=r['rect'][3] for r in native):continue
            point=_point(u,target,rect)
            if point:buttons[point]=point
    if len(buttons)>1:raise RuntimeError('无法唯一定位效果面板菜单，已停止操作')
    return {'button':next(iter(buttons.values()),None),'header':next(iter(headers.values()),None) if len(headers)==1 else None,
            'nativeCandidates':len(native),'classes':sorted({r['class'] for r in windows})[:80],'paletteNames':sorted(set(names))[:16]}

def _inspect(hwnd,pid,u,deadline):
    results=[];errors=[];finished=threading.Event();notes=['初始化 COM']
    def locate():
        initialized=False
        try:
            _lookup_state.note=lambda value:notes.append(value)
            sys.coinit_flags=2
            import comtypes;comtypes.CoInitializeEx(2);initialized=True
            results.append(_locate_menu_button(hwnd,pid,u,deadline))
        except Exception as error:errors.append(error)
        finally:
            finished.set()
            if initialized:comtypes.CoUninitialize()
    threading.Thread(target=locate,daemon=True).start()
    if not finished.wait(max(0,deadline-time.monotonic())):raise TimeoutError('定位 AE 效果面板菜单超时，未执行操作；最后阶段：'+notes[-1])
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

def refresh_effects(pid:int,timeout:float=10,*,expected_image=None,creation_time=None)->dict:
    import json
    started=time.monotonic();deadline=started+timeout;u=_user32();_native_api(u);foreground=u.GetForegroundWindow()
    path=_diagnostic_path();trace=[];stage='验证宿主';before=set();owned=set();opened=False;expanded=False
    cursor=W.POINT();u.GetCursorPos(C.byref(cursor));last_cursor=None;matched_frame=None;edit_visible_before=None
    def check(target=None):
        if time.monotonic()>=deadline:raise TimeoutError('AE 效果列表刷新超时，未继续操作')
        _identity(pid,image,created)
        if target and (not u.IsWindow(target) or _owner(u,target)!=pid):raise RuntimeError('AE 面板窗口已变化，未刷新（窗口 '+str(target)+'）')
    def click(point,screen=None):
        nonlocal last_cursor
        target,packed=point
        if screen is None:
            screen_point=W.POINT(C.c_short(packed&0xffff).value,C.c_short((packed>>16)&0xffff).value)
            if not u.ClientToScreen(target,C.byref(screen_point)):raise RuntimeError('无法转换面板菜单坐标')
            screen=(screen_point.x,screen_point.y)
        check(target)
        if not u.SetCursorPos(*screen):raise RuntimeError('无法定位面板菜单指针')
        last_cursor=screen
        check(target);current=W.POINT();u.GetCursorPos(C.byref(current))
        if (current.x,current.y)!=screen or _owner(u,u.WindowFromPoint(current))!=pid:raise RuntimeError('面板被遮挡或指针已移动，已停止操作')
        # Always balance our button-down, even if input raises or time expires.
        try:u.mouse_event(2,0,0,0,0)
        finally:u.mouse_event(4,0,0,0,0)
    def context_menu(candidates):
        nonlocal matched_frame,edit_visible_before
        for candidate in candidates:
            check(candidate['target']);x,y=candidate['x'],candidate['y']
            was_visible=_has_edit(u,candidate['target'],visible=True)
            if candidate.get('button'):click(_point(u,candidate['target'],(x-1,y-1,x+1,y+1)),screen=(x,y))
            elif not u.PostMessageW(candidate['target'],0x7b,candidate['target'],((y&0xffff)<<16)|(x&0xffff)):raise RuntimeError('无法打开 AE 面板菜单')
            until=min(deadline,time.monotonic()+.18)
            while time.monotonic()<until:
                check();menus=set(_popups(u,pid))-before;owned.update(menus)
                found=_refresh_popup(u,pid)
                if found:matched_frame=candidate['target'];edit_visible_before=was_visible;return found
                if menus:
                    time.sleep(min(.035,max(0,deadline-time.monotonic())));check()
                    found=_refresh_popup(u,pid)
                    if found:matched_frame=candidate['target'];edit_visible_before=was_visible;return found
                    for handle in menus:
                        check()
                        if u.IsWindow(handle) and _owner(u,handle)==pid:u.PostMessageW(handle,0x100,0x1b,0)
                    closing=min(deadline,time.monotonic()+.25)
                    while time.monotonic()<closing and any(u.IsWindow(h) for h in menus):time.sleep(.01)
                    check()
                    if any(u.IsWindow(h) for h in menus):raise RuntimeError('AE 临时菜单未关闭，已停止操作')
                    owned.difference_update(menus);break
                time.sleep(.015)
        return None
    try:
        image,created=_identity(pid,expected_image,creation_time);hwnd=main_window(pid);before=set(_popups(u,pid))
        if before:raise RuntimeError('AE 菜单正在打开，请先关闭菜单后刷新')
        check(hwnd);u.SetForegroundWindow(hwnd)
        stage='定位效果面板'
        native_rows,_=_native_windows(u,pid)
        trace.append({'nativeClasses':sorted({r['class'] for r in native_rows}),
                      'nativePalettes':[{k:r[k] for k in ('hwnd','class','title','visible','rect')} for r in native_rows if _palette(r['title']) or _palette(r['class'])]})
        located=_inspect(hwnd,pid,u,deadline);trace.append(located)
        popup=context_menu(located.get('candidates',[])) if not located['button'] else None
        if not located['button'] and not popup:
            stage='打开或展开效果面板';check(hwnd);command=_panel_command(u,hwnd);trace.append({'windowMenu':command})
            if not command['checked']:
                check(hwnd)
                if not u.PostMessageW(hwnd,0x111,command['command'],0):raise RuntimeError('无法打开效果和预设面板')
                opened=True
            elif located['header']:click(located['header']);expanded=True
            else:raise RuntimeError('效果和预设面板已打开，但未能定位其标题或菜单')
            time.sleep(min(.15,max(0,deadline-time.monotonic())));check()
            stage='重新定位效果面板';located=_inspect(hwnd,pid,u,deadline);trace.append(located)
            popup=context_menu(located.get('candidates',[])) if not located['button'] else None
        if not located['button'] and not popup:raise RuntimeError('无法定位效果和预设面板菜单，未执行刷新')
        stage='打开效果面板菜单'
        if not popup:click(located['button'])
        while time.monotonic()<deadline:
            check();owned.update(set(_popups(u,pid))-before);popup=popup or _refresh_popup(u,pid)
            if popup:
                handle,menu,index,command_id=popup
                if handle not in owned:raise RuntimeError('刷新菜单不属于本次操作')
                stage='执行刷新列表';check(handle);ignored=C.c_size_t()
                remaining=max(1,min(250,int((deadline-time.monotonic())*1000)))
                if not u.SendMessageTimeoutW(handle,0x1e5,index,0,0x0003,remaining,C.byref(ignored)):raise RuntimeError('AE 菜单无响应，已停止操作')
                if not u.GetMenuState(menu,index,0x400)&0x80:raise RuntimeError('未能选中“刷新列表”菜单项，未执行操作')
                for message in (0x100,0x101):
                    check(handle)
                    if not u.PostMessageW(handle,message,0x0d,0):raise RuntimeError('AE 刷新消息发送失败')
                while time.monotonic()<deadline and u.IsWindow(handle):time.sleep(.03)
                check()
                if u.IsWindow(handle):raise RuntimeError('AE 效果列表刷新未完成')
                if matched_frame and edit_visible_before is False and u.IsWindow(matched_frame):expanded=expanded or _has_edit(u,matched_frame,visible=True)
                owned.clear();stage='完成'
                return {'nativePaletteRefreshed':True,'commandId':command_id,'panelOpened':opened,'panelExpanded':expanded,'diagnosticPath':str(path)}
            if owned:raise RuntimeError('面板菜单中未找到可用的“刷新列表”，未执行其他菜单操作')
            time.sleep(.03)
        raise TimeoutError('AE 效果列表刷新超时，未继续操作')
    except Exception as error:
        trace.append({'error':str(error)})
        raise RuntimeError(str(error)+'；诊断记录：'+str(path)) from None
    finally:
        for handle in owned:
            if u.IsWindow(handle) and _owner(u,handle)==pid:u.PostMessageW(handle,0x100,0x1b,0)
        import os
        current_cursor=W.POINT()
        if time.monotonic()<deadline:
            if last_cursor and u.GetCursorPos(C.byref(current_cursor)) and (current_cursor.x,current_cursor.y)==last_cursor:u.SetCursorPos(cursor.x,cursor.y)
            if foreground and u.IsWindow(foreground) and _owner(u,foreground)==os.getpid() and _owner(u,u.GetForegroundWindow())==pid:u.SetForegroundWindow(foreground)
        try:path.write_text(json.dumps({'schemaVersion':1,'pid':pid,'stage':stage,'panelOpened':opened,'panelExpanded':expanded,'elapsedMs':round((time.monotonic()-started)*1000),'trace':trace},ensure_ascii=False,indent=2),encoding='utf8')
        except OSError:pass
