import ctypes as C,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import Mock,patch
import ae_ui as ui
import palette_messages as m
import test_ui_config as cases

def row(h,parent,rect,title='',cls='DroverLord - Window Class',visible=True):
    return dict(hwnd=h,parent=parent,rect=rect,title=title,**{'class':cls,'visible':visible})

class GeometryTests(unittest.TestCase):
    def rows(self):return [row(1,None,(0,0,1000,800),'Main'),row(2,1,(0,0,300,700),'DroverLord - Frame Window'),row(3,2,(0,100,300,2000),'OS_ViewContainer'),row(4,3,(1,130,299,1998),'OS_ViewContainer'),row(5,4,(1,130,299,1998),'DroverLord - TabPanel Window'),row(6,5,(30,135,260,155),'OS_EditTextContainer'),row(7,6,(30,135,260,155),'',cls='Edit')]
    def test_expanded_legacy_header_clips_oversized_body(self):
        found=m.search_headers(self.rows());self.assertEqual(len(found),1);self.assertEqual(found[0]['header'],(0,100,300,130));self.assertEqual(found[0]['point'],(286,115));self.assertEqual(found[0]['target'],3)
    def test_dpi_scaled_geometry_yields_same_logical_location(self):
        rows=self.rows()
        for r in rows:r['rect']=tuple(round(v*1.5) for v in r['rect'])
        self.assertEqual(m.search_headers(rows,1.5)[0]['point'],(429,172))
    def test_numeric_fields_far_search_and_frame_bars_rejected(self):
        for change in ('numeric','far','frame'):
            rows=self.rows()
            if change=='numeric':rows[-1]['rect']=(30,135,60,155)
            elif change=='far':rows[-1]['rect']=(30,200,260,220)
            else:rows[2]['title']='DroverLord - Frame Window'
            self.assertEqual(m.search_headers(rows),[])
    def test_hidden_or_clipped_header_not_clicked(self):
        for mode in ('hidden','clipped'):
            rows=self.rows()
            if mode=='hidden':rows[1]['visible']=False
            else:rows[1]['rect']=(0,0,300,90)
            self.assertEqual(m.search_headers(rows),[])
    def test_clipping_loop_fails_closed(self):
        rows=self.rows();rows[0]['parent']=7
        self.assertEqual(m.search_headers(rows),[])
    def test_hits_descend_native_children_not_smallest_raw_area(self):
        u=SimpleNamespace(ChildWindowFromPointEx=lambda h,p,f:{1:2,2:3,3:3}[h],ScreenToClient=lambda h,p:1)
        self.assertEqual(m.point_target(u,self.rows(),[1],(286,115)),3)
    def test_floating_root_is_used_without_main_containment(self):
        rows=[row(1,None,(0,0,100,100)),row(2,None,(200,0,400,100))]
        u=SimpleNamespace(ChildWindowFromPointEx=lambda h,p,f:h,ScreenToClient=lambda h,p:1)
        self.assertEqual(m.point_target(u,rows,[2,1],(250,20)),2)
    def test_physical_context_is_restored_on_failure(self):
        calls=[]
        def context(value):calls.append(value);return 123
        with self.assertRaises(ValueError):
            with m.physical(SimpleNamespace(SetThreadDpiAwarenessContext=context)):raise ValueError()
        self.assertEqual(calls[-1],123)

class CalibrationCacheTests(unittest.TestCase):
    rows=GeometryTests.rows
    def setUp(self):
        self.key=(1,2,'image');self.c=m.search_headers(self.rows())[0];r=self.c['header']
        h=patch.dict(m._calibrated,{self.key:{'stamp':(3,7,(300,30),96),'x':.9,'y':.5}},clear=True);h.start();self.addCleanup(h.stop)
    def test_relative_position_survives_window_move(self):
        c=dict(self.c,header=(50,150,350,180));self.assertEqual(m.cached_candidate(self.key,[c],96)['point'],(320,165))
    def test_layout_dpi_body_rebuild_and_restart_do_not_reuse(self):
        for c,d,key in [(self.c,120,self.key),(dict(self.c,edit=88),96,self.key),(dict(self.c,header=(0,100,400,130)),96,self.key),(self.c,96,(1,3,'image'))]:
            m._calibrated[self.key]={'stamp':(3,7,(300,30),96),'x':.9,'y':.5}
            self.assertIsNone(m.cached_candidate(key,[c],d))

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        h=patch.dict(m._calibrated,{},clear=True);h.start();self.addCleanup(h.stop)
        h=patch.dict(m._verified,{},clear=True);h.start();self.addCleanup(h.stop)
    def call(self,u=None,calibrate=False,inspect_error=None,late=False):
        u=u or cases.FakeUser32(closed={9});calls=[0]
        def popups(user,pid):calls[0]+=1;return [] if calls[0]==1 else [9]
        def popup(user,pid):
            if late:__import__('time').sleep(.15)
            return 9,77,3,94
        c={'point':(170,20),'target':5,'kind':'native-search-header','button':True,'anchor':3,'edit':7,'header':(0,0,200,40)}
        def cursor(p):q=C.cast(p,C.POINTER(ui.W.POINT)).contents;q.x=170;q.y=20;return 1
        u.GetCursorPos=Mock(side_effect=cursor);u.WindowFromPoint=Mock(return_value=5)
        with ExitStack() as stack:
            for name,value in [('_user32',lambda:u),('_native_api',lambda u:None),('_identity',lambda *a:('test',10)),('main_window',lambda p:1),('_owner',lambda *a:1),('_native_windows',lambda *a:([],[])),('_inspect',Mock(side_effect=inspect_error) if inspect_error else lambda *a:[]),('_popups',popups),('_refresh_popup',popup),('_diagnostic_path',lambda:Path(self.tmp.name)/'d.json')]:stack.enter_context(patch.object(ui,name,value))
            stack.enter_context(patch.object(m,'search_headers',return_value=[c]))
            if calibrate:stack.enter_context(patch.object(m.time,'sleep',return_value=None))
            return m.refresh(1,timeout=.1 if late else 2,calibrate=calibrate),u
    def test_native_fallback_uses_left_messages_and_verified_menu(self):
        result,u=self.call();self.assertEqual(result['locator'],'native-search-header');self.assertEqual(result['commandId'],94);self.assertFalse(result['contentVerified'])
        self.assertEqual([m for h,m,w in u.posts if h==5],[0x200,0x201,0x202])
    def test_uia_error_can_use_validated_native_header(self):
        self.assertEqual(self.call(inspect_error=RuntimeError('UIA unavailable'))[0]['locator'],'native-search-header')
    def test_calibration_verifies_menu_but_never_executes_refresh(self):
        result,u=self.call(calibrate=True);self.assertTrue(result['calibrated']);self.assertFalse(result['nativePaletteRefreshed']);self.assertTrue(m._calibrated)
        self.assertFalse(any(h==9 and w==0x0d for h,msg,w in u.posts))
    def test_expired_menu_result_never_receives_enter(self):
        u=cases.FakeUser32(closed={9})
        with self.assertRaisesRegex(RuntimeError,'超时'):self.call(u,late=True)
        self.assertFalse(any(h==9 and w==0x0d for h,msg,w in u.posts))

class Refresh131Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for cache in (m._verified,m._calibrated):
            h=patch.dict(cache,{},clear=True);h.start();self.addCleanup(h.stop)
        self.rows=GeometryTests.rows(self);self.header=m.search_headers(self.rows)[0];self.key=(1,10,'test')
    def run_refresh(self,*,drop=0,missing=False,timeout=3,cache=False,stalled=False,changed=False,right=False):
        u=cases.FakeUser32(closed={9});clock=[0.0];inspect=Mock(return_value=[])
        def attempts():return sum(h==3 and msg in (0x202,0x205) for h,msg,w in u.posts)
        def popups(*a):return [9] if attempts()>drop and not missing else []
        def popup(*a):return (9,77,3,94) if popups() else None
        def send(*a):
            if stalled:return 0
            return cases.FakeUser32.SendMessageTimeoutW(u,*a)
        u.SendMessageTimeoutW=send
        if right:self.header=dict(self.header,button=False)
        if cache:m._verified[self.key]=m._locator_stamp(self.header,self.rows,96)
        if changed:self.rows[-1]['rect']=(30,135,250,155)
        self.user=u;self.inspect=inspect
        with ExitStack() as stack:
            for name,value in [('_identity',lambda *a:('test',10)),('main_window',lambda p:1),('_owner',lambda *a:1),('_native_windows',lambda *a:(self.rows,[1])),('_inspect',inspect),('_popups',popups),('_refresh_popup',popup),('_diagnostic_path',lambda:Path(self.temp.name)/'d.json')]:stack.enter_context(patch.object(ui,name,value))
            stack.enter_context(patch.object(m,'search_headers',return_value=[self.header]))
            stack.enter_context(patch.object(m.time,'monotonic',lambda:clock[0]))
            stack.enter_context(patch.object(m.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds)))
            return m._run(u,1,timeout,'test',10,False,None)
    def test_dropped_first_menu_click_retries_only_open(self):
        result=self.run_refresh(drop=1,right=True)
        self.assertTrue(result['nativePaletteRefreshed'])
        self.assertEqual(sum(h==3 and msg==0x205 for h,msg,w in self.user.posts),2)
        self.assertEqual(self.user.posts.count((9,0x100,0x0d)),1)
    def test_missing_menu_has_at_most_three_open_attempts_no_enter(self):
        with self.assertRaisesRegex(RuntimeError,'未找到可用'):self.run_refresh(missing=True,right=True)
        self.assertEqual(sum(h==3 and msg==0x205 for h,msg,w in self.user.posts),3)
        self.assertNotIn((9,0x100,0x0d),self.user.posts)
    def test_left_menu_button_is_never_toggled_twice(self):
        with self.assertRaises(RuntimeError):self.run_refresh(missing=True)
        self.assertEqual(sum(h==3 and msg==0x202 for h,msg,w in self.user.posts),1)
    def test_timeout_does_not_send_a_late_retry(self):
        with self.assertRaisesRegex(RuntimeError,'超时'):self.run_refresh(missing=True,timeout=.2)
        self.assertEqual(sum(h==3 and msg==0x202 for h,msg,w in self.user.posts),1)
    def test_unchanged_verified_header_skips_uia_but_checks_menu(self):
        result=self.run_refresh(cache=True)
        self.assertEqual(result['locator'],'verified-header-cache');self.inspect.assert_not_called()
        self.assertEqual(self.user.selected,(9,3))
    def test_layout_change_runs_discovery_again(self):
        self.run_refresh(cache=True,changed=True);self.inspect.assert_called_once()
    def test_verified_success_populates_session_cache(self):
        self.run_refresh();self.assertIn(self.key,m._verified)
    def test_cached_menu_failure_rediscovery_once_then_fails(self):
        with self.assertRaises(RuntimeError):self.run_refresh(cache=True,missing=True,timeout=4)
        self.inspect.assert_called_once();self.assertNotIn(self.key,m._verified)
    def test_unresponsive_select_stops_without_retry_or_enter(self):
        with self.assertRaisesRegex(RuntimeError,'无响应'):self.run_refresh(cache=True,stalled=True)
        self.assertEqual(sum(h==3 and msg==0x202 for h,msg,w in self.user.posts),1)
        self.assertNotIn((9,0x100,0x0d),self.user.posts);self.assertNotIn(self.key,m._verified)
    def test_restarted_identity_and_dpi_never_reuse_header(self):
        for key,density in [((1,11,'test'),96),((1,10,'other'),96),(self.key,120)]:
            m._verified[self.key]=m._locator_stamp(self.header,self.rows,96)
            self.assertIsNone(m._reuse_locator(key,[self.header],self.rows,density))
    def test_rebuilt_or_hidden_ancestor_invalidates_cache(self):
        for mode in ('rebuild','hidden'):
            rows=GeometryTests.rows(self);m._verified[self.key]=m._locator_stamp(self.header,rows,96)
            if mode=='rebuild':rows[-1]['hwnd']=88
            else:rows[1]['visible']=False
            self.assertIsNone(m._reuse_locator(self.key,m.search_headers(rows),rows,96))

if __name__=='__main__':unittest.main()
