"""Verified native-menu refresh, including legacy search-header discovery.

Only menu messages, never hardware input or bare WM_COMMAND refresh IDs.
Calibration is scoped to the current host and native panel lifetime, in memory.
"""
from __future__ import annotations
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import json,threading,time
import ae_ui as ui

_calibrated={}
_lock=threading.Lock()

@contextmanager
def physical(u):
    fn=getattr(u,'SetThreadDpiAwarenessContext',None)
    if fn is None:yield;return # isolated API doubles / older Windows
    fn.argtypes=[C.c_void_p];fn.restype=C.c_void_p;old=fn(C.c_void_p(-4))
    if not old:raise RuntimeError('无法统一 AE 面板的 DPI 坐标，未发送消息')
    try:yield
    finally:fn(old)

def dpi(u,h):
    fn=getattr(u,'GetDpiForWindow',None)
    if fn is None:return 96
    fn.argtypes=[W.HWND];fn.restype=W.UINT
    return fn(h) or 96

def inside(rect,p):return rect[0]<=p[0]<rect[2] and rect[1]<=p[1]<rect[3]

def clipped(row,by):
    rect=list(row['rect']);seen=set();current=row
    while current:
        if current['hwnd'] in seen or not current['visible']:return None
        seen.add(current['hwnd']);r=current['rect'];rect=[max(rect[0],r[0]),max(rect[1],r[1]),min(rect[2],r[2]),min(rect[3],r[3])]
        if rect[0]>=rect[2] or rect[1]>=rect[3]:return None
        current=by.get(current.get('parent'))
    return tuple(rect)

def search_headers(rows,scale=1):
    """Search near a TabPanel body's top, with a distinct expanded native header.
    Frame title bars and arbitrary Edit fields are not menu-button anchors.
    """
    by={r['hwnd']:r for r in rows};found={}
    for edit in rows:
        e=edit['rect']
        if edit.get('class')!='Edit' or not clipped(edit,by) or e[2]-e[0]<80*scale or not 6*scale<=e[3]-e[1]<=32*scale:continue
        current=by.get(edit.get('parent'));seen=set();body=None
        while current and len(seen)<12 and current['hwnd'] not in seen:
            seen.add(current['hwnd'])
            if current['title']=='DroverLord - TabPanel Window':body=current;break
            current=by.get(current.get('parent'))
        if body is None or not 0<=e[1]-body['rect'][1]<=40*scale:continue
        current=by.get(body.get('parent'));seen=set()
        while current and len(seen)<8 and current['hwnd'] not in seen:
            seen.add(current['hwnd']);r=current['rect'];b=body['rect'];height=b[1]-r[1]
            if current['title']=='OS_ViewContainer' and 8*scale<=height<=48*scale and abs(r[0]-b[0])<=8*scale and abs(r[2]-b[2])<=8*scale:
                visible=clipped(current,by)
                if visible:
                    header=(max(r[0],visible[0]),max(r[1],visible[1]),min(r[2],visible[2]),min(b[1],visible[3]))
                    if header[2]-header[0]>=90*scale and header[3]>header[1]:
                        point=(round(header[2]-14*scale),(header[1]+header[3])//2)
                        found[current['hwnd']]={'point':point,'target':current['hwnd'],'anchor':current['hwnd'],'edit':edit['hwnd'],'header':header,'kind':'native-search-header','button':True}
                break
            current=by.get(current.get('parent'))
    return sorted(found.values(),key=lambda x:(x['header'][1],x['header'][0]))[:8]

def point_target(u,rows,tops,point):
    by={r['hwnd']:r for r in rows}
    roots=[h for h in tops if h in by and by[h]['visible'] and inside(by[h]['rect'],point)]
    if not roots:raise RuntimeError('无法核对面板所属的 AE 顶层窗口')
    current=roots[0];seen=set();fn=u.ChildWindowFromPointEx;fn.argtypes=[W.HWND,W.POINT,W.UINT];fn.restype=W.HWND
    for _ in range(64):
        if current in seen:break
        seen.add(current);p=W.POINT(*point)
        if not u.ScreenToClient(current,C.byref(p)):break
        child=fn(current,p,7)
        if not child or child==current:return current
        current=child
    raise RuntimeError('AE 子窗口命中测试失败')

def cached_candidate(key,headers,density):
    saved=_calibrated.get(key)
    if not saved:return None
    for c in headers:
        r=c['header'];size=(r[2]-r[0],r[3]-r[1])
        if (c['anchor'],c['edit'],size,density)==saved['stamp']:
            return dict(c,kind='calibrated-button',point=(round(r[0]+saved['x']*size[0]),round(r[1]+saved['y']*size[1])))
    _calibrated.pop(key,None);return None

def refresh(pid,timeout=10,*,expected_image=None,creation_time=None,calibrate=False,progress=None):
    if not _lock.acquire(False):raise RuntimeError('列表刷新或校准正在进行，请稍候')
    try:
        u=ui._user32();ui._native_api(u)
        with physical(u):return _run(u,pid,timeout,expected_image,creation_time,calibrate,progress)
    finally:_lock.release()

def _run(u,pid,timeout,image,created,calibrate,progress):
    started=time.monotonic();deadline=started+timeout;path=ui._diagnostic_path();trace=[];owned=set();opened=False;stage='验证宿主'
    def check(target=None):
        if time.monotonic()>=deadline:raise TimeoutError('AE 效果列表刷新超时，未继续操作')
        ui._identity(pid,image,created)
        if target and (not u.IsWindow(target) or ui._owner(u,target)!=pid):raise RuntimeError('AE 面板窗口已变化，未继续操作')
    def dismiss():
        for h in tuple(owned):
            if u.IsWindow(h) and ui._owner(u,h)==pid:
                u.PostMessageW(h,0x100,0x1b,0);u.PostMessageW(h,0x101,0x1b,0)
        until=min(deadline,time.monotonic()+.75)
        while time.monotonic()<until and any(u.IsWindow(h) for h in owned):time.sleep(.03)
        if any(u.IsWindow(h) for h in owned):return False
        owned.clear();return True
    def locate():
        try:points=ui._inspect(main,pid,u,min(deadline,time.monotonic()+2))
        except Exception as error:
            check();trace.append({'uiaError':str(error)});points=[]
        check()
        if len(points)>1:raise RuntimeError('检测到多个“效果和预设”面板标签，未执行操作')
        rows,tops=ui._native_windows(u,pid);headers=search_headers(rows,density/96)
        saved=cached_candidate(key,headers,density)
        if calibrate:
            p=W.POINT();u.GetCursorPos.argtypes=[C.POINTER(W.POINT)]
            if not u.GetCursorPos(C.byref(p)):raise RuntimeError('无法读取校准位置')
            trace.append({'calibrationPoint':[p.x,p.y],'headers':[list(c['header']) for c in headers]})
            u.WindowFromPoint.argtypes=[W.POINT];u.WindowFromPoint.restype=W.HWND
            if ui._owner(u,u.WindowFromPoint(p))!=pid:raise RuntimeError('请把鼠标停在 AE 可见的菜单按钮上，不要停在本软件上')
            matches=[c for c in headers if inside(c['header'],(p.x,p.y))]
            if len(matches)!=1:raise RuntimeError('该位置不是可核对的搜索面板标题，请展开“效果和预设”后重新校准')
            return [dict(matches[0],point=(p.x,p.y),kind='calibrated-button')]
        candidates=[saved] if saved else []
        if points:
            target=ui._hit_target(u,rows,tops,points[0]);candidates.append({'point':points[0],'target':target,'kind':'uia-tab','button':False})
        candidates+=headers
        seen=set();unique=[]
        for c in candidates:
            mark=(c['target'],c['point'],c.get('button',False))
            if mark not in seen:unique.append(c);seen.add(mark)
        trace.append({'uiaTabs':len(points),'nativeHeaders':len(headers),'candidates':len(unique)})
        return unique
    try:
        image,created=ui._identity(pid,image,created);key=(pid,created,image);main=ui.main_window(pid);density=dpi(u,main)
        if ui._popups(u,pid):raise RuntimeError('AE 菜单正在打开，请先关闭菜单后重试')
        if calibrate:
            for seconds in range(5,0,-1):
                if progress:progress(f'请把鼠标停在 AE“效果和预设”的 ≡ 按钮上，{seconds} 秒后校准；不用点击')
                time.sleep(1);check()
        stage='定位效果面板';candidates=locate()
        if not candidates and not calibrate:
            command=ui._panel_command(u,main);trace.append({'windowMenu':command})
            if command['checked']:raise RuntimeError('效果和预设面板已打开，但没有找到它的标签或可核对的搜索标题：请展开面板，或使用“更多 → 校准刷新菜单按钮”')
            check(main)
            if not u.PostMessageW(main,0x111,command['command'],0):raise RuntimeError('无法打开效果和预设面板')
            opened=True
            while not candidates:time.sleep(.1);check();candidates=locate()
        for c in candidates:
            target=c['target'];check(target);p=W.POINT(*c['point'])
            if not u.ScreenToClient(target,C.byref(p)):raise RuntimeError('无法换算菜单位置')
            packed=((p.y&65535)<<16)|(p.x&65535);down,up,keys=(0x201,0x202,1) if c.get('button') else (0x204,0x205,2)
            pressed=False
            try:
                for message,mask in ((0x200,0),(down,keys),(up,0)):
                    check(target)
                    if not u.PostMessageW(target,message,mask,packed):raise RuntimeError('AE 面板消息发送失败')
                    if message==down:pressed=True
                    if message==up:pressed=False
            finally:
                if pressed and u.IsWindow(target) and ui._owner(u,target)==pid:u.PostMessageW(target,up,0,packed)
            stage='核对刷新菜单';popup=None;until=min(deadline,time.monotonic()+.8)
            while time.monotonic()<until:
                check();owned.update(ui._popups(u,pid));popup=ui._refresh_popup(u,pid);check()
                if popup:break
                if owned:time.sleep(.05);check();popup=ui._refresh_popup(u,pid);check();break
                time.sleep(.03)
            trace.append({'locator':c['kind'],'menuOpened':bool(owned),'refreshItemFound':bool(popup)})
            if not popup:
                if c['kind']=='calibrated-button':_calibrated.pop(key,None)
                if not dismiss():raise RuntimeError('本次临时菜单未关闭，已停止')
                continue
            handle,menu,index,command_id=popup
            if handle not in owned:raise RuntimeError('刷新菜单不属于本次操作')
            if calibrate:
                r=c['header'];_calibrated[key]={'stamp':(c['anchor'],c['edit'],(r[2]-r[0],r[3]-r[1]),density),'x':(c['point'][0]-r[0])/(r[2]-r[0]),'y':(c['point'][1]-r[1])/(r[3]-r[1])}
                if not dismiss():_calibrated.pop(key,None);raise RuntimeError('校准菜单未关闭，未保存校准')
                stage='校准完成';return {'calibrated':True,'nativePaletteRefreshed':False,'commandId':command_id,'dispatchMethod':'calibration-only','contentVerified':False,'diagnosticPath':str(path)}
            stage='执行刷新菜单';check(handle);ignored=C.c_size_t();remaining=max(1,min(250,int((deadline-time.monotonic())*1000)))
            if not u.SendMessageTimeoutW(handle,0x1e5,index,0,0x0003,remaining,C.byref(ignored)):raise RuntimeError('AE 菜单无响应，未追加操作')
            if not u.GetMenuState(menu,index,0x400)&0x80:raise RuntimeError('未能选中“刷新列表”菜单项，未执行操作')
            for message in (0x100,0x101):
                check(handle)
                if not u.PostMessageW(handle,message,0x0d,0):raise RuntimeError('AE 刷新消息发送失败')
            while time.monotonic()<deadline and u.IsWindow(handle):time.sleep(.03)
            check()
            if u.IsWindow(handle):raise RuntimeError('AE 菜单未关闭，刷新结果不确定')
            owned.clear();stage='完成'
            return {'nativePaletteRefreshed':True,'commandId':command_id,'panelOpened':opened,'panelExpanded':False,'dispatchMethod':'menu-messages','locator':c['kind'],'contentVerified':False,'diagnosticPath':str(path)}
        raise RuntimeError('菜单中未找到可用的“刷新列表”，未执行其他菜单项；可使用“更多 → 校准刷新菜单按钮”')
    except Exception as error:
        trace.append({'error':str(error)});raise RuntimeError(str(error)+'；诊断记录：'+str(path)) from None
    finally:
        dismiss()
        try:path.write_text(json.dumps({'schemaVersion':3,'pid':pid,'stage':stage,'panelOpened':opened,'elapsedMs':round((time.monotonic()-started)*1000),'trace':trace},ensure_ascii=False,indent=2),encoding='utf8')
        except OSError:pass
