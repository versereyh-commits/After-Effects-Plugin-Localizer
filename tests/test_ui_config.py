import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import ae_ui
from types import SimpleNamespace
import app_live
import settings_store
from app_live import CandidateStore

class FakeUser32:
    def __init__(self,closed=(),highlight=True):self.posts=[];self.closed=set(closed);self.highlight=highlight;self.selected=None;self.cursor=(0,0);self.mouse=[]
    def PostMessageW(self,hwnd,message,wparam,lparam):self.posts.append((hwnd,message,wparam));return 1
    def IsWindow(self,hwnd):return hwnd not in self.closed or not any(h==hwnd and m==0x101 for h,m,w in self.posts)
    def SendMessageTimeoutW(self,hwnd,message,wparam,lparam,flags,timeout,result):
        if message==0x1e5:self.selected=(hwnd,wparam)
        return 1
    def GetMenuState(self,menu,index,flags):return 0x80 if self.highlight and self.selected and self.selected[1]==index else 0
    def GetForegroundWindow(self):return 1
    def SetForegroundWindow(self,hwnd):return 1
    def GetCursorPos(self,point):
        target=ae_ui.C.cast(point,ae_ui.C.POINTER(ae_ui.W.POINT)).contents;target.x,target.y=self.cursor;return 1
    def SetCursorPos(self,x,y):self.cursor=(x,y);return 1
    def ClientToScreen(self,hwnd,point):return 1
    def WindowFromPoint(self,point):return 5
    def mouse_event(self,flag,*args):self.mouse.append(flag)

class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.path=self.root/'translations.json'
    def test_missing_translations_are_empty_and_saveable(self):
        store=CandidateStore(self.path);self.assertEqual((store.values,store.unsafe),({},False))
        store.values['S_Glow']='辉光';self.assertEqual(store.save(),'')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf8')),{'S_Glow':'辉光'})
    def test_valid_translations_load_without_backup(self):
        self.path.write_text(json.dumps({'S_Glow':'辉光'}),encoding='utf8')
        store=CandidateStore(self.path);self.assertEqual((store.values,store.secure()),({'S_Glow':'辉光'},''))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['translations.json'])
    def test_malformed_translations_are_backed_up_before_overwrite(self):
        for content in ('[1,2]','{broken',json.dumps({'S_Glow':'辉光','tc Shine':3})):
            for item in self.root.iterdir():item.unlink()
            self.path.write_text(content,encoding='utf8')
            store=CandidateStore(self.path)
            self.assertTrue(all(isinstance(v,str) for v in store.values.values()));self.assertTrue(store.unsafe)
            self.assertIn('备份为',store.secure())
            backups=[p for p in self.root.iterdir() if '.invalid-' in p.name]
            self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_text(encoding='utf8'),content)
            self.assertEqual(self.path.read_text(encoding='utf8'),content)
            store.values['S_Glow']='辉光';store.save()
            self.assertEqual(json.loads(self.path.read_text(encoding='utf8'))['S_Glow'],'辉光')
    def test_failed_backup_never_overwrites_original(self):
        self.path.write_text('[1,2]',encoding='utf8');store=CandidateStore(self.path)
        with patch.object(app_live.shutil,'copy2',side_effect=PermissionError('locked')):
            self.assertIn('不会覆盖',store.secure())
            store.values['S_Glow']='辉光';self.assertIn('不会覆盖',store.save())
        self.assertEqual(self.path.read_text(encoding='utf8'),'[1,2]');self.assertTrue(store.unsafe)
        store.save()
        self.assertEqual(json.loads(self.path.read_text(encoding='utf8')),{'S_Glow':'辉光'})
        self.assertEqual(len([p for p in self.root.iterdir() if '.invalid-' in p.name]),1)
    def test_settings_with_wrong_shape_fall_back_to_defaults(self):
        with patch.object(settings_store,'data_root',lambda:self.root):
            for content in ('[]',json.dumps({'baseUrl':5,'model':['x'],'protectedKey':7})):
                (self.root/'api-settings.json').write_text(content,encoding='utf8')
                value=settings_store.Settings()
                self.assertEqual((value.base_url,value.model,value.key),('https://api.deepseek.com','deepseek-flash',''))
    def test_settings_from_before_services_are_migrated(self):
        with patch.object(settings_store,'data_root',lambda:self.root):
            path=self.root/'api-settings.json';secret=__import__('base64').b64encode(settings_store._crypt(b'test-only')).decode()
            path.write_text(json.dumps({'baseUrl':'https://api.deepseek.com','model':'deepseek-chat','protectedKey':secret}),encoding='utf8')
            value=settings_store.Settings()
            self.assertEqual((value.service,value.model,value.key,value.remember),('deepseek','deepseek-chat','test-only',True))
            path.write_text(json.dumps({'baseUrl':'https://llm.example.com/v1','model':'x'}),encoding='utf8')
            value=settings_store.Settings();self.assertEqual((value.service,value.base_url,value.model),('custom','https://llm.example.com/v1','x'))
    def test_each_service_keeps_its_own_key_and_model(self):
        with patch.object(settings_store,'data_root',lambda:self.root):
            value=settings_store.Settings();value.key='deepseek-test-only'
            value.service='anthropic';value.key='claude-test-only';value.model='claude-sonnet-5-5';value.remember=True;value.save()
            text=(self.root/'api-settings.json').read_text(encoding='utf8');self.assertNotIn('test-only',text)
            again=settings_store.Settings()
            self.assertEqual((again.service,again.key,again.model),('anthropic','claude-test-only','claude-sonnet-5-5'))
            again.service='deepseek';self.assertEqual(again.key,'deepseek-test-only')
            again.remember=False;again.save();self.assertNotIn('protectedKey',(self.root/'api-settings.json').read_text(encoding='utf8'))
    def test_ready_needs_key_only_for_remote_services(self):
        with patch.object(settings_store,'data_root',lambda:self.root):
            value=settings_store.Settings();self.assertFalse(value.ready())
            value.service='ollama';self.assertTrue(value.ready())
            value.service='custom';value.key='k';self.assertFalse(value.ready())
            value.base_url='https://llm.example.com/v1';value.model='m';self.assertTrue(value.ready())
    def test_finished_operation_survives_failed_reread(self):
        with patch.object(app_live,'read_live',side_effect=RuntimeError('请仅运行一个 AE 2026 进程')):
            self.assertEqual(app_live.with_live({'restored':{},'oldProcessEnded':True}),({'restored':{},'oldProcessEnded':True},None,'请仅运行一个 AE 2026 进程'))

class RefreshTests(unittest.TestCase):
    def patch(self,user,locate,popups,refresh_popup=None):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        if popups and popups[0] is None:popups.pop(0)
        query=refresh_popup or (lambda u,pid:popups.pop(0) if popups else None)
        native_popups=[0]
        def owned(u,pid):native_popups[0]+=1;return [] if native_popups[0]==1 else [9]
        def adapt(hwnd,pid,u,deadline):
            result=locate(hwnd,pid,u)
            return result if isinstance(result,dict) else {'button':result,'header':None}
        for target,value in [('main_window',lambda pid:1),('_user32',lambda:user),('_native_api',lambda u:None),('_native_windows',lambda *a:([],[])),('_owner',lambda u,h:1),('_identity',lambda *a:('test/AfterFX.exe',10)),('_diagnostic_path',lambda:Path(self.temp.name)/'diagnostic.json'),('_locate_menu_button',adapt),('_refresh_popup',query),('_popups',owned)]:
            handle=patch.object(ae_ui,target,value);handle.start();self.addCleanup(handle.stop)
    def test_slow_locator_never_clicks_after_timeout(self):
        user=FakeUser32();entered=threading.Event();done=threading.Event()
        def slow(hwnd,pid,u):entered.set();time.sleep(.4);done.set();return 5,0
        self.patch(user,slow,[])
        with self.assertRaisesRegex(RuntimeError,'定位 AE 效果面板菜单超时'):ae_ui.refresh_effects(1,timeout=.1)
        self.assertTrue(entered.is_set());self.assertTrue(done.wait(2));time.sleep(.1)
        self.assertEqual(user.posts,[])
    def test_locator_error_is_reported_without_clicks(self):
        user=FakeUser32()
        def missing(hwnd,pid,u):raise RuntimeError('请在 AE 中打开“效果和预设”面板，再应用名称')
        self.patch(user,missing,[])
        with self.assertRaisesRegex(RuntimeError,'效果和预设'):ae_ui.refresh_effects(1,timeout=1)
        self.assertEqual(user.posts,[])
    def test_refresh_selects_menu_item_once(self):
        user=FakeUser32(closed={9})
        self.patch(user,lambda hwnd,pid,u:(5,0),[None,(9,77,3,94)])
        result=ae_ui.refresh_effects(1,timeout=1)
        self.assertTrue(result['nativePaletteRefreshed']);self.assertEqual(result['commandId'],94);self.assertFalse(result['panelOpened'])
        # Palette button is clicked; the menu item is chosen by position and Enter, never by a posted mouse click.
        self.assertEqual(user.selected,(9,3))
        self.assertEqual(user.mouse,[2,4]);self.assertEqual(user.posts,[(9,0x100,0x0d),(9,0x101,0x0d)])
    def test_unconfirmed_highlight_never_presses_enter(self):
        user=FakeUser32(highlight=False)
        self.patch(user,lambda hwnd,pid,u:(5,0),[None,(9,77,3,94)])
        with self.assertRaisesRegex(RuntimeError,'未能选中'):ae_ui.refresh_effects(1,timeout=1)
        self.assertNotIn((9,0x100,0x0d),user.posts);self.assertIn((9,0x100,0x1b),user.posts)
    def test_menu_found_after_deadline_is_not_clicked(self):
        user=FakeUser32();calls=[]
        def late(u,pid):
            calls.append(1)
            time.sleep(.3);return 9,77,3,94
        self.patch(user,lambda hwnd,pid,u:(5,0),[],late)
        with self.assertRaisesRegex(RuntimeError,'超时'):ae_ui.refresh_effects(1,timeout=.2)
        self.assertNotIn((9,0x100,0x0d),user.posts);self.assertIn((9,0x100,0x1b),user.posts)
    def test_unreadable_menu_stops_before_clicking(self):
        user=FakeUser32()
        def stalled(u,pid):raise RuntimeError('AE 菜单无响应，已停止操作')
        self.patch(user,lambda hwnd,pid,u:(5,0),[],stalled)
        with self.assertRaisesRegex(RuntimeError,'无响应'):ae_ui.refresh_effects(1,timeout=1)
        self.assertNotIn((9,0x100,0x0d),user.posts)

    def test_closed_panel_is_opened_once_and_rediscovered(self):
        user=FakeUser32(closed={9});states=[{'button':None,'header':None},{'button':(5,0),'header':None}]
        self.patch(user,lambda *a:states.pop(0),[(9,77,3,94)])
        with patch.object(ae_ui,'_panel_command',return_value={'checked':False,'command':1234}):result=ae_ui.refresh_effects(1,timeout=1)
        self.assertTrue(result['panelOpened']);self.assertEqual(user.posts.count((1,0x111,1234)),1)
    def test_collapsed_panel_expands_without_toggling_window_command(self):
        user=FakeUser32(closed={9});states=[{'button':None,'header':(5,2)},{'button':(5,0),'header':None}]
        self.patch(user,lambda *a:states.pop(0),[(9,77,3,94)])
        with patch.object(ae_ui,'_panel_command',return_value={'checked':True,'command':1234}):result=ae_ui.refresh_effects(1,timeout=1)
        self.assertTrue(result['panelExpanded']);self.assertFalse(any(m==0x111 for _,m,_ in user.posts))
    def test_checked_but_unidentified_panel_is_not_toggled_closed(self):
        user=FakeUser32();self.patch(user,lambda *a:{'button':None,'header':None},[])
        with patch.object(ae_ui,'_panel_command',return_value={'checked':True,'command':1234}):
            with self.assertRaisesRegex(RuntimeError,'已打开'):ae_ui.refresh_effects(1)
        self.assertEqual(user.posts,[])
    def test_mismatched_menu_is_dismissed_without_enter(self):
        user=FakeUser32();self.patch(user,lambda *a:(5,0),[])
        with self.assertRaisesRegex(RuntimeError,'未找到可用'):ae_ui.refresh_effects(1)
        self.assertNotIn((9,0x100,0x0d),user.posts);self.assertIn((9,0x100,0x1b),user.posts)
    def test_restarted_host_stops_before_any_input(self):
        user=FakeUser32();self.patch(user,lambda *a:(5,0),[])
        with patch.object(ae_ui,'_identity',side_effect=[('test',10),RuntimeError('AE 已重新启动')]):
            with self.assertRaisesRegex(RuntimeError,'重新启动'):ae_ui.refresh_effects(1)
        self.assertEqual(user.posts,[])
    def test_overlay_from_another_process_blocks_mouse_input(self):
        user=FakeUser32();self.patch(user,lambda *a:(5,0),[])
        with patch.object(user,'WindowFromPoint',return_value=99),patch.object(ae_ui,'_owner',side_effect=lambda u,h:2 if h==99 else 1):
            with self.assertRaisesRegex(RuntimeError,'遮挡'):ae_ui.refresh_effects(1)
        self.assertEqual(user.mouse,[])

class NativeDiscoveryTests(unittest.TestCase):
    def test_main_and_floating_windows_are_both_discovered(self):
        def owner(hwnd,p):ae_ui.C.cast(p,ae_ui.C.POINTER(ae_ui.W.DWORD)).contents.value=1 if hwnd!=3 else 2
        def cls(hwnd,buffer,size):buffer.value={1:'AE_CApplication_24.2',2:'Floating',11:'Edit',21:'Panel'}.get(hwnd,'Other')
        def rect(hwnd,p):
            r=ae_ui.C.cast(p,ae_ui.C.POINTER(ae_ui.W.RECT)).contents;r.left=0;r.top=0;r.right=100;r.bottom=100;return 1
        u=SimpleNamespace(GetWindowThreadProcessId=owner,GetClassNameW=cls,GetWindowTextW=lambda h,b,s:None,GetWindowRect=rect,IsWindowVisible=lambda h:True,
                          EnumWindows=lambda cb,arg:[cb(h,arg) for h in (1,2,3)],EnumChildWindows=lambda h,cb,arg:cb(h*10+1,arg))
        rows,tops=ae_ui._native_windows(u,1)
        self.assertEqual(tops,[1,2]);self.assertEqual({r['hwnd'] for r in rows},{1,2,11,21})
    def test_window_menu_command_is_resolved_by_text_in_both_languages(self):
        for window,palette in [('窗口(&W)','效果和预设\tCtrl+5'),('&Window','Effects & Presets')]:
            def text(menu,index,buffer,size,flags):buffer.value=window if menu==100 else palette;return len(buffer.value)
            u=SimpleNamespace(GetMenu=lambda h:100,GetMenuItemCount=lambda m:1,GetMenuStringW=text,GetSubMenu=lambda m,i:200,
                              GetMenuItemID=lambda m,i:524313,GetMenuState=lambda m,i,f:8,SendMessageTimeoutW=lambda *a:1)
            self.assertEqual(ae_ui._panel_command(u,1),{'command':524313,'checked':True})
    def test_window_menu_duplicate_is_rejected(self):
        def text(menu,index,buffer,size,flags):buffer.value='窗口' if menu==100 else '效果和预设';return len(buffer.value)
        u=SimpleNamespace(GetMenu=lambda h:100,GetMenuItemCount=lambda m:1 if m==100 else 2,GetMenuStringW=text,GetSubMenu=lambda m,i:200,
                          GetMenuItemID=lambda m,i:100+i,GetMenuState=lambda m,i,f:0,SendMessageTimeoutW=lambda *a:1)
        with self.assertRaisesRegex(RuntimeError,'唯一'):ae_ui._panel_command(u,1)

if __name__=='__main__':unittest.main()
