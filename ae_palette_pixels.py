"""Local menu-glyph fallback; pixels never saved or uploaded."""
import ctypes as C
from ctypes import wintypes as W
class Header(C.Structure):
    _fields_=[('size',W.DWORD),('width',W.LONG),('height',W.LONG),('planes',W.WORD),('bits',W.WORD),('compression',W.DWORD),('imageSize',W.DWORD),('xppm',W.LONG),('yppm',W.LONG),('colors',W.DWORD),('important',W.DWORD)]
def capture(u,hwnd):
    rect=W.RECT()
    if not u.GetWindowRect(hwnd,C.byref(rect)):return None
    width,height=rect.right-rect.left,rect.bottom-rect.top
    if not 100<=width<=8192 or not 40<=height<=4320 or width*height>18000000:return None
    g=C.WinDLL('gdi32',use_last_error=True)
    u.GetDC.argtypes=[W.HWND];u.GetDC.restype=W.HDC;u.ReleaseDC.argtypes=[W.HWND,W.HDC]
    for name,args,result in (
        ('CreateCompatibleDC',[W.HDC],W.HDC),('CreateDIBSection',[W.HDC,C.c_void_p,W.UINT,C.POINTER(C.c_void_p),W.HANDLE,W.DWORD],W.HBITMAP),
        ('SelectObject',[W.HDC,W.HANDLE],W.HANDLE),('DeleteObject',[W.HANDLE],W.BOOL),('DeleteDC',[W.HDC],W.BOOL),
        ('BitBlt',[W.HDC,C.c_int,C.c_int,C.c_int,C.c_int,W.HDC,C.c_int,C.c_int,W.DWORD],W.BOOL)):
        fn=getattr(g,name);fn.argtypes=args;fn.restype=result
    screen=u.GetDC(None);memory=g.CreateCompatibleDC(screen);bitmap=None;old=None
    try:
        header=Header(C.sizeof(Header),width,-height,1,32,0,0,0,0,0,0);bits=C.c_void_p()
        bitmap=g.CreateDIBSection(screen,C.byref(header),0,C.byref(bits),None,0)
        if not bitmap:return None
        old=g.SelectObject(memory,bitmap)
        if not g.BitBlt(memory,0,0,width,height,screen,rect.left,rect.top,0x40cc0020):return None
        return C.string_at(bits,width*height*4),width,height,rect.left,rect.top
    finally:
        if old:g.SelectObject(memory,old)
        if bitmap:g.DeleteObject(bitmap)
        if memory:g.DeleteDC(memory)
        if screen:u.ReleaseDC(None,screen)
def menu_glyphs(data,width,height):
    def light(x,y):
        if not (0<=x<width and 0<=y<height):return False
        o=(y*width+x)*4;b,g,r=data[o],data[o+1],data[o+2]
        return min(b,g,r)>=100 and max(b,g,r)-min(b,g,r)<=36
    rows=set()
    for y in range(28,height-5):
        x=0
        while x<width:
            if not light(x,y):x+=1;continue
            start=x
            while x<width and light(x,y):x+=1
            length=x-start
            if 6<=length<=22:rows.add((start,length,y))
    found=[]
    for x,length,y in sorted(rows,key=lambda v:v[2]):
        for gap in range(2,7):
            if (x,length,y+gap) not in rows or (x,length,y+gap*2) not in rows:continue
            if any(light(xx,yy) for xx in range(x,x+length) for yy in (y+gap//2,y+gap+gap//2)):continue
            if any(abs(x-a)<4 and abs(y-b)<8 for a,b in found):break
            found.append((x+length//2,y+gap));break
    return found[:24]
def candidates(u,windows,pid):
    u.WindowFromPoint.argtypes=[W.POINT];u.WindowFromPoint.restype=W.HWND
    result=[]
    for hwnd in windows:
        if not u.IsWindowVisible(hwnd):continue
        snapshot=capture(u,hwnd)
        if not snapshot:continue
        data,width,height,left,top=snapshot
        for x,y in menu_glyphs(data,width,height):
            x+=left;y+=top;target=u.WindowFromPoint(W.POINT(x,y));owner=W.DWORD();u.GetWindowThreadProcessId(target,C.byref(owner))
            if owner.value==pid:result.append({'target':target,'x':x,'y':y,'kind':'verified-menu-glyph','button':True})
    return result[:24]