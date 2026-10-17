"""AE's main window keeps its maximized / minimized / normal state while scripts are handed to AE."""
import ctypes as C,unittest
from ctypes import wintypes as W
from unittest.mock import patch
import ae_ui

AE,OURS,OTHER=101,202,303      # window handles
AE_PID,OUR_PID=4000,5000

class FakeUser32:
    """Just the window calls WindowKeeper makes; records what it changes."""
    def __init__(self,show=3,rect=(0,0,1920,1040),normal=(100,100,900,700),windows=None,readable=True):
        self.show=show;self.rect=rect;self.normal=normal;self.readable=readable;self.foreground=OURS;self.calls=[]
        self.windows=windows if windows is not None else {AE:(AE_PID,'AE_CApplication_24.2'),OURS:(OUR_PID,'Qt'),OTHER:(AE_PID,'AE_Panel')}
    def EnumWindows(self,callback,arg):
        for hwnd in list(self.windows):callback(hwnd,arg)
        return True
    def GetWindowThreadProcessId(self,hwnd,ref):ref._obj.value=self.windows.get(hwnd,(0,''))[0];return 1
    def GetClassNameW(self,hwnd,buffer,size):buffer.value=self.windows.get(hwnd,(0,''))[1];return len(buffer.value)
    def IsWindow(self,hwnd):return hwnd in self.windows
    def GetWindowPlacement(self,hwnd,ref):
        if not self.readable:return 0
        placement=ref._obj;placement.showCmd=self.show;placement.rcNormalPosition=W.RECT(*self.normal);return 1
    def GetWindowRect(self,hwnd,ref):ref._obj.left,ref._obj.top,ref._obj.right,ref._obj.bottom=self.rect;return 1
    def GetForegroundWindow(self):return self.foreground
    def SetForegroundWindow(self,hwnd):self.calls.append(('foreground',hwnd));self.foreground=hwnd;return 1
    def ShowWindowAsync(self,hwnd,command):self.calls.append(('show',hwnd,command));self.show={3:3,7:2,4:1}[command];return 1
    def SetWindowPos(self,hwnd,after,x,y,cx,cy,flags):self.calls.append(('pos',hwnd,(x,y,cx,cy),flags));self.rect=(x,y,x+cx,y+cy);return 1

def keeper(u):return ae_ui.WindowKeeper(AE_PID,u=u,own_pid=OUR_PID)

class WindowKeeperTests(unittest.TestCase):
    def test_maximized_window_is_maximized_again(self):
        u=FakeUser32(show=3);k=keeper(u)
        self.assertFalse(k.check());self.assertEqual(u.calls,[])
        u.show=1;u.rect=u.normal                     # AE showed itself: back to its normal size
        self.assertTrue(k.check());self.assertEqual(u.calls,[('show',AE,3)]);self.assertEqual(k.restored,1)
    def test_minimized_window_is_minimized_again_without_activating(self):
        u=FakeUser32(show=2);k=keeper(u);u.show=1
        self.assertTrue(k.check());self.assertEqual(u.calls,[('show',AE,7)])
    def test_normal_window_returns_to_its_place_and_size(self):
        u=FakeUser32(show=1,rect=(100,100,900,700));k=keeper(u)
        u.show=3;self.assertTrue(k.check());self.assertEqual(u.calls[-1],('show',AE,4))
        u=FakeUser32(show=1,rect=(100,100,900,700));k=keeper(u);u.rect=(0,0,640,480)
        self.assertTrue(k.check());(_,hwnd,box,flags)=u.calls[-1]
        self.assertEqual((hwnd,box),(AE,(100,100,800,600)));self.assertEqual(flags&0x4014,0x4014)   # no z-order, no activation, async
    def test_foreground_goes_back_only_right_after_a_restore(self):
        u=FakeUser32(show=3);k=keeper(u)
        u.foreground=AE;self.assertFalse(k.check());self.assertEqual(u.calls,[])   # the user clicked AE: left alone
        u.foreground=OURS;u.show=1;k.check();u.foreground=AE;k.check()             # asynchronous activation lands later
        self.assertEqual(u.calls,[('show',AE,3),('foreground',OURS)])
        with patch.object(ae_ui.time,'monotonic',return_value=k.last+5):
            u.foreground=AE;k.check()
        self.assertEqual(u.calls[-1],('foreground',OURS))
        self.assertEqual(sum(1 for c in u.calls if c[0]=='foreground'),1)
    def test_foreground_that_was_not_ours_is_not_taken(self):
        u=FakeUser32(show=3);u.foreground=OTHER;k=keeper(u);u.show=1;u.foreground=AE;k.check()
        self.assertEqual(u.calls,[('show',AE,3)])
    def test_anything_unclear_leaves_the_window_alone(self):
        for u in (FakeUser32(windows={OURS:(OUR_PID,'Qt')}),
                  FakeUser32(windows={AE:(AE_PID,'AE_CApplication_24.2'),AE+1:(AE_PID,'AE_CApplication_24.2')}),
                  FakeUser32(readable=False)):
            k=keeper(u);u.show=1;self.assertFalse(k.check());self.assertEqual(u.calls,[])
        u=FakeUser32(show=3);k=keeper(u);u.readable=False;u.show=1;self.assertFalse(k.check())
        del u.windows[AE];self.assertFalse(k.check());self.assertIsNone(k.hwnd);self.assertEqual(u.calls,[])
    def test_settle_watches_a_moment_only_when_this_ae_changed_its_window(self):
        u=FakeUser32(show=3);k=keeper(u)
        with patch.object(ae_ui.time,'sleep') as sleep:k.settle()
        sleep.assert_not_called()
        u.show=1;k.check();u.show=3;u.foreground=AE
        k.settle(wait=.2,step=.05);self.assertEqual(u.calls[-1],('foreground',OURS))

if __name__=='__main__':unittest.main()
