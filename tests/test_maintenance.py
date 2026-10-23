"""First maintenance round: failure isolation and data preservation, with no real AE/network."""
import base64,ctypes,json,os,struct,tempfile,threading,time,unittest,shutil,subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import ae_paths,cep_bridge,host_identity,parameter_names as n,settings_store,storage_safety,updater
from favorites import Favorites
import test_gui,test_updater

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_bad_json_is_backed_up_before_settings_can_be_saved(self):
        path=self.root/'api-settings.json';path.write_text('{bad',encoding='utf8')
        with patch.object(settings_store,'data_root',return_value=self.root):s=settings_store.Settings()
        self.assertTrue(s.warnings);self.assertFalse(s.blocked);s.save()
        self.assertEqual(next(self.root.glob('api-settings.corrupt-*.json')).read_text(encoding='utf8'),'{bad')
        self.assertIsInstance(json.loads(path.read_text(encoding='utf8')),dict)
    def test_a_bad_key_does_not_discard_later_providers_or_original_ciphertext(self):
        value={'provider':'custom','providers':{'deepseek':{'model':'keep-first','protectedKey':'broken!'},'custom':{'baseUrl':'https://example.test/v1','model':'keep-later'}}}
        path=self.root/'api-settings.json';path.write_text(json.dumps(value),encoding='utf8')
        with patch.object(settings_store,'data_root',return_value=self.root):s=settings_store.Settings()
        self.assertEqual(s.service,'custom');self.assertEqual(s.profiles['custom']['model'],'keep-later');self.assertEqual(s.profiles['deepseek']['key'],'')
        self.assertTrue(s.warnings);s.save();self.assertEqual(json.loads(path.read_text(encoding='utf8'))['providers']['deepseek']['protectedKey'],'broken!')
        s.remember=False;s.save();self.assertNotIn('protectedKey',json.loads(path.read_text(encoding='utf8'))['providers']['deepseek'])
    def test_dpapi_failure_is_per_provider(self):
        path=self.root/'api-settings.json';path.write_text(json.dumps({'providers':{'deepseek':{'protectedKey':base64.b64encode(b'old-machine').decode()},'custom':{'model':'later'}}}),encoding='utf8')
        with patch.object(settings_store,'data_root',return_value=self.root),patch.object(settings_store,'_crypt',side_effect=OSError('DPAPI')):s=settings_store.Settings()
        self.assertEqual(s.profiles['custom']['model'],'later');self.assertTrue(s.warnings)
    def test_replaced_key_can_be_cleared_without_resurrecting_old_ciphertext(self):
        path=self.root/'api-settings.json';path.write_text(json.dumps({'provider':'deepseek','providers':{'deepseek':{'protectedKey':'broken!'}}}),encoding='utf8')
        with patch.object(settings_store,'data_root',return_value=self.root):s=settings_store.Settings()
        with patch.object(settings_store,'_crypt',side_effect=lambda raw,*a:raw):
            s.key='new';s.save();s.key='';s.save()
        self.assertNotIn('protectedKey',json.loads(path.read_text(encoding='utf8'))['providers']['deepseek'])
    def test_backup_failure_blocks_settings_overwrite(self):
        path=self.root/'api-settings.json';path.write_text('{bad',encoding='utf8')
        with patch.object(settings_store,'data_root',return_value=self.root),patch.object(storage_safety.os,'replace',side_effect=PermissionError()):s=settings_store.Settings()
        with self.assertRaises(OSError):s.save()
        self.assertEqual(path.read_text(encoding='utf8'),'{bad')
    def test_permission_failure_is_not_reclassified_as_corruption(self):
        with patch.object(Path,'read_text',side_effect=PermissionError()),patch.object(storage_safety.os,'replace') as moved:
            with self.assertRaises(PermissionError):storage_safety.read_json(self.root/'a.json')
        moved.assert_not_called()
    def test_favorites_bad_json_is_preserved_and_save_can_continue(self):
        path=self.root/'quick-search.json';path.write_text('{bad',encoding='utf8');f=Favorites(path)
        self.assertTrue(f.warning);f.favorites=['fx'];self.assertEqual(f.save(),'')
        self.assertEqual(next(self.root.glob('quick-search.corrupt-*.json')).read_text(encoding='utf8'),'{bad')
    def test_favorites_backup_failure_blocks_save(self):
        path=self.root/'quick-search.json';path.write_text('{bad',encoding='utf8')
        with patch.object(storage_safety.os,'replace',side_effect=PermissionError()):f=Favorites(path)
        self.assertTrue(f.save());self.assertEqual(path.read_text(encoding='utf8'),'{bad')
    def test_extra_directories_survive_restart_without_crossing_installations(self):
        path=self.root/'paths.json';one=self.root/'one/AfterFX.exe';two=self.root/'two/AfterFX.exe';extra=self.root/'plugins'
        ae_paths.save_extra(path,one,[extra,extra]);ae_paths.save_extra(path,two,[self.root/'other'])
        self.assertEqual(ae_paths.extra_directories(path,one)[0],[Path(ae_paths.path_key(extra))])
        self.assertNotEqual(ae_paths.extra_directories(path,one)[0],ae_paths.extra_directories(path,two)[0])
    def test_old_paths_upgrade_keeps_paths(self):
        path=self.root/'paths.json';path.write_text(json.dumps({'schemaVersion':1,'paths':['keep']}),encoding='utf8')
        ae_paths.save_extra(path,self.root/'AfterFX.exe',[self.root/'plugins'])
        self.assertEqual(ae_paths.read_saved(path)[0],['keep']);self.assertEqual(json.loads(path.read_text(encoding='utf8'))['schemaVersion'],2)

class IdentityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node required for CEP isolation')
    def test_same_version_hosts_cannot_claim_each_others_requests(self):
        root=Path(__file__).resolve().parents[1]
        r=subprocess.run(['node',str(root/'tests/cep_isolation.cjs'),str(root/'cep/main.js')],capture_output=True,text=True,encoding='utf8',timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(all(json.loads(r.stdout).values()))
    @unittest.skipUnless(shutil.which('node'),'Node required for JSX isolation')
    def test_wrong_relay_identity_does_not_touch_project_or_response(self):
        source=(Path(n.__file__).parent/'scripts/parameter_bridge.jsx').read_text(encoding='utf8')
        runner=r"""const vm=require('vm'),fs=require('fs');const request={action:'addEffect',targetHost:{pid:3,creationTime:'0000000000000004',image:'C:/AE/AfterFX.exe'},permit:'p',output:'o',expires:Date.now()+10000};
let writes=0,touches=0;function File(){this.exists=true;this.open=()=>true;this.write=()=>writes++;this.close=()=>{};}
const app={get project(){touches++;throw Error('wrong project touched');}};
function ExternalObject(){return {identity:()=>JSON.stringify({...request.targetHost,pid:4})};}
vm.runInNewContext(fs.readFileSync(0,'utf8').replace('@@REQUEST@@',JSON.stringify(request)),{app,File,ExternalObject});
process.stdout.write(JSON.stringify({writes,touches}));"""
        r=subprocess.run(['node','-e',runner],input=source,capture_output=True,text=True,encoding='utf8',timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),{'writes':0,'touches':0})
    def test_exact_process_identity_not_version(self):
        a=host_identity.target(SimpleNamespace(pid=3,creation_time=0x1fffffffffffffff,image='C:/AE/AfterFX.exe'))
        self.assertEqual(a['creationTime'],'1fffffffffffffff');self.assertTrue(host_identity.same(a,dict(a)))
        for field,value in [('pid',4),('creationTime','0000000000000001'),('image','C:/Other/AfterFX.exe')]:
            self.assertFalse(host_identity.same({**a,field:value},a))
    def test_missing_creation_time_cannot_submit(self):
        with self.assertRaises(RuntimeError):host_identity.target(SimpleNamespace(pid=3,image='AfterFX.exe'))
    def test_identity_native_helper_is_read_only_and_matches_this_process(self):
        dll=Path(n.__file__).parent/'native/ae_host_identity_v1.dll'
        if not dll.exists():self.skipTest('native helper not built yet')
        class Data(ctypes.Union):_fields_=[('integer',ctypes.c_long),('number',ctypes.c_double),('string',ctypes.c_void_p)]
        class Tagged(ctypes.Structure):_fields_=[('data',Data),('type',ctypes.c_long),('filler',ctypes.c_long)]
        lib=ctypes.CDLL(str(dll));lib.identity.argtypes=[ctypes.c_void_p,ctypes.c_long,ctypes.POINTER(Tagged)];lib.ESFreeMem.argtypes=[ctypes.c_void_p]
        out=Tagged();self.assertEqual(lib.identity(None,0,ctypes.byref(out)),0)
        try:value=json.loads(ctypes.string_at(out.data.string).decode('utf8'))
        finally:lib.ESFreeMem(out.data.string)
        self.assertEqual(out.type,4);self.assertEqual(value['pid'],os.getpid());self.assertEqual(len(value['creationTime']),16)

class DownloadRoutingTests(unittest.TestCase):
    def test_refused_proxy_moves_to_direct(self):
        with tempfile.TemporaryDirectory() as folder:
            server=test_updater.Server()
            def refused(*a,**kw):raise ConnectionRefusedError()
            with patch.object(updater,'routes',return_value=[('proxy','http://dead:1'),('direct',None)]),patch.object(updater,'_connect',side_effect=[refused,server]) as connect:
                result=updater.download(test_updater.manifest(),Path(folder),wait=lambda _:None)
            self.assertEqual(result.read_bytes(),test_updater.PAYLOAD);self.assertEqual(connect.call_count,2)
    def test_rerouting_preserves_range(self):
        with tempfile.TemporaryDirectory() as folder:
            first=test_updater.Server(drops=[1000]);second=test_updater.Server()
            with patch.object(updater,'routes',return_value=[('proxy','http://dead:1'),('direct',None)]),patch.object(updater,'_connect',side_effect=[first,second]):
                updater.download(test_updater.manifest(),Path(folder),wait=lambda _:None)
            self.assertEqual(second.asked,['bytes=1000-'])
    def test_http_error_does_not_switch_routes(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(updater,'routes',return_value=[('proxy','http://dead:1'),('direct',None)]),patch.object(updater,'_connect',return_value=test_updater.Server(status=503)) as connect:
                with self.assertRaises(updater.UpdateError):updater.download(test_updater.manifest(),Path(folder),wait=lambda _:None)
            self.assertEqual(connect.call_count,1)

class CleanupTests(unittest.TestCase):
    def test_expired_protocol_files_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for i in range(4):
                p=root/(str(i)*32);p.mkdir();(p/'owner.json').write_text(json.dumps({'expires':0 if i<3 else time.time()*1000+100000}),encoding='utf8')
                if i==1:(p/'user.aep').touch()
                if i!=2:os.utime(p,(0,0))
            n.cleanup_requests(root)
            self.assertFalse((root/('0'*32)).exists());self.assertTrue((root/('1'*32)/'user.aep').exists());self.assertTrue((root/('2'*32)).exists());self.assertTrue((root/('3'*32)).exists())
    def test_cleanup_permission_does_not_replace_original_error(self):
        with patch.object(Path,'unlink',side_effect=PermissionError()):n.safe_unlink(Path('unused'))

class ParameterDecodeTests(unittest.TestCase):
    def test_invalid_utf16_is_a_controlled_structure_error(self):
        root=0x1000;address=0x2000;effect=b'e'*16;guid=b'p'*16
        cache=b'\x00\xd8\0\0'+b'\0'*12+struct.pack('<QQ',1,7)
        def read(a,size):
            if a==root+8:return effect
            if a==address+8:return guid
            if a==address+0x40:return b'English\0'.ljust(32,b'\0')
            if a==address+0x100:return cache[:size]
            raise AssertionError('unexpected read')
        c=object.__new__(n.ParameterCatalog);c.p=SimpleNamespace(read=read,u64=lambda a:root);c.names={effect:'fx',guid:'param'}
        c.profile=SimpleNamespace(encoding='utf8',display_cache=0x100);c.roots=lambda *a,**kw:[root];c.children=lambda r:[(address,guid)]
        with self.assertRaisesRegex(RuntimeError,'非法字符'):c.rows()

class TaskTests(test_gui.GuiCase):
    def test_borrowed_versions_are_numeric_with_unknown_last(self):
        import app_live
        w=self.window;key=n.identity('fx','param');w.parameter_pending={};w.parameter_enabled=set()
        for version,label in [('9.0.2.40','旧'),('26.5.0.89','新'),('版本未知','未知')]:
            folder=Path(self.temp.name)/version;folder.mkdir()
            (folder/'parameter-translations.json').write_text(json.dumps({key:label}),encoding='utf8')
            (folder/'parameter-enabled.json').write_text(json.dumps([key]),encoding='utf8')
            w.spaces.append(app_live.VersionSpace(version,version,version,None,False,folder))
        self.assertEqual(w.quick_parameter_names('fx')[key],'新')
        w.parameter_pending[key]='当前';w.parameter_enabled.add(key)
        self.assertEqual(w.quick_parameter_names('fx')[key],'当前')
    def test_background_task_blocks_other_entrypoints_until_result_is_processed(self):
        w=self.window;entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        self.assertTrue(w.background('qa',lambda:(entered.set(),release.wait(3))))
        self.assertTrue(entered.wait(1));self.assertTrue(w.busy());self.assertFalse(w.background('other',lambda:None))
        old=w.space;w.choose_version(0);self.assertIs(w.space,old)
        release.set();w._tasks['qa'][0].join(2);self.app.processEvents();self.assertFalse(w.busy())
    def test_exit_waits_without_terminating_task(self):
        w=self.window;w.tray=None;w.quitting=True;release=threading.Event();self.addCleanup(release.set)
        w.background('qa',lambda:release.wait(3));event=Mock();w.closeEvent(event)
        event.ignore.assert_called_once();self.assertTrue(w._stopping.is_set());self.assertTrue(w.busy())
        self.assertFalse(w.background('late',lambda:None));release.set();w._tasks['qa'][0].join(2);self.app.processEvents()
    def test_busy_layer_entry_does_not_start_another_thread(self):
        w=self.window
        with patch.object(w,'busy',return_value=True),patch.object(w,'background') as launch:w.layer_parameters()
        launch.assert_not_called()

if __name__=='__main__':unittest.main()
