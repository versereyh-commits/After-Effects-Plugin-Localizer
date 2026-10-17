"""The background CEP extension: install / status, and parameter requests going through it instead of AfterFX.exe -r."""
import json,re,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cep_bridge
import parameter_names as names

EXT=cep_bridge.EXTENSION_ID

class BridgeCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        # A signed build of the extension, as build.py leaves it.
        self.base=self.root/'signed';ext=self.base/EXT
        for name,text in (('CSXS/manifest.xml','<manifest/>'),('main.js','var BRIDGE_ID="abc";'),('META-INF/signatures.xml','<sig/>')):
            (ext/name).parent.mkdir(parents=True,exist_ok=True);(ext/name).write_text(text,encoding='utf-8')
        (self.base/'bridge.id').write_text('abc',encoding='ascii')
        # A fake HKCU: {version: PlayerDebugMode value}; a missing key is no value.
        self.registry={}
        for target,value in (('_appdata',lambda:self.root/'appdata'),('bundled',lambda:self.base),('_registry_get',lambda version:self.registry.get(version)),
                             ('_registry_set',lambda version,value:self.registry.pop(version,None) if value is None else self.registry.__setitem__(version,value))):
            handle=patch.object(cep_bridge,target,value);handle.start();self.addCleanup(handle.stop)
    def beat(self,age=0.0,bridge='abc'):
        cep_bridge.root().mkdir(parents=True,exist_ok=True)
        (cep_bridge.root()/'heartbeat.json').write_text(json.dumps({'time':(time.time()-age)*1000,'bridge':bridge,'host':'24.2.1'}),encoding='utf-8')

class StatusTests(BridgeCase):
    def test_install_and_states(self):
        with patch.object(cep_bridge,'bundled',lambda:None):self.assertEqual(cep_bridge.status(),'unavailable')
        self.assertEqual(cep_bridge.status(),'missing')
        cep_bridge.install()
        self.assertEqual(cep_bridge.installed(),self.root/'appdata'/'Adobe'/'CEP'/'extensions'/EXT)
        self.assertEqual((cep_bridge.installed()/'main.js').read_text(encoding='utf-8'),'var BRIDGE_ID="abc";')
        self.assertEqual(cep_bridge.status(),'installed')            # files current, AE has not loaded them
        self.beat();self.assertEqual(cep_bridge.status(),'connected');self.assertTrue(cep_bridge.connected())
        for age,bridge in ((cep_bridge.FRESH+1,'abc'),(0,'other-build')):
            self.beat(age,bridge);self.assertFalse(cep_bridge.connected());self.assertEqual(cep_bridge.status(),'installed')
        (cep_bridge.installed()/'main.js').write_text('changed',encoding='utf-8');self.assertEqual(cep_bridge.status(),'outdated')
        cep_bridge.install();self.assertEqual(cep_bridge.status(),'installed')   # reinstalling replaces the old copy
        self.assertEqual([p.name for p in cep_bridge.installed().parent.iterdir()],[EXT])
        cep_bridge.remove();self.assertEqual(cep_bridge.status(),'missing');cep_bridge.remove()
    def test_debug_mode_is_switched_on_and_only_our_changes_are_put_back(self):
        # Before: CSXS.10 has it off, CSXS.11 already on (another plugin), CSXS.12 never set, CSXS.13 off.
        self.registry.update({10:'0',11:'1',13:'0'})
        cep_bridge.install()                                           # the startup update never touches it
        self.assertEqual(self.registry,{10:'0',11:'1',13:'0'})
        cep_bridge.install(debug_mode=True)
        self.assertEqual(self.registry,{10:'1',11:'1',12:'1',13:'1'})
        cep_bridge.install(debug_mode=True)                            # a reinstall keeps the first originals
        self.registry[13]='0'                                          # the user switched one off by hand meanwhile
        cep_bridge.remove()
        self.assertEqual(self.registry,{10:'0',11:'1',13:'0'})         # 11 stays on, 12 has no value again
        self.assertFalse(cep_bridge._debug_record().exists())
        cep_bridge.remove();self.assertEqual(self.registry,{10:'0',11:'1',13:'0'})
    def test_already_on_everywhere_changes_nothing(self):
        self.registry.update({v:'1' for v in cep_bridge.CSXS_VERSIONS})
        cep_bridge.install(debug_mode=True);self.assertFalse(cep_bridge._debug_record().exists())
        cep_bridge.remove();self.assertEqual(self.registry,{v:'1' for v in cep_bridge.CSXS_VERSIONS})
    def test_account_folder_with_chinese_and_spaces(self):
        appdata=self.root/'用户 张三'/'AppData'/'Roaming'
        with patch.object(cep_bridge,'_appdata',lambda:appdata):
            cep_bridge.install();self.assertTrue((appdata/'Adobe'/'CEP'/'extensions'/EXT/'main.js').is_file())
            self.beat();self.assertEqual(cep_bridge.status(),'connected')
    def test_unreadable_heartbeat_is_not_connected(self):
        cep_bridge.root().mkdir(parents=True);(cep_bridge.root()/'heartbeat.json').write_text('{',encoding='utf-8')
        self.assertFalse(cep_bridge.connected())
        with patch.object(cep_bridge,'bundled',lambda:None):self.beat();self.assertFalse(cep_bridge.connected())

class ExtensionSourceTests(unittest.TestCase):
    def test_extension_accepts_every_action_the_program_sends(self):
        source=(Path(cep_bridge.__file__).parent/'cep'/'main.js').read_text(encoding='utf-8')
        accepted=set(re.findall(r"'(\w+)'",re.search(r'ACTIONS = \[(.*?)\]',source).group(1)))
        sent=set()
        for module in ('parameter_names.py','effect_instances.py','live_names.py'):
            sent|=set(re.findall(r"action='(\w+)'",(Path(cep_bridge.__file__).parent/module).read_text(encoding='utf-8')))
        self.assertTrue(sent);self.assertLessEqual(sent,accepted)
        self.assertIn('@@BRIDGE_ID@@',source);self.assertNotIn('evalFile',source)    # only its own template, never a file it is handed
        manifest=(Path(cep_bridge.__file__).parent/'cep'/'CSXS'/'manifest.xml').read_text(encoding='utf-8')
        self.assertIn(f'Id="{EXT}"',manifest);self.assertIn('<AutoVisible>false</AutoVisible>',manifest);self.assertIn('<Type>Custom</Type>',manifest)

class ExtensionScriptTests(unittest.TestCase):
    """cep/main.js itself. A raw U+2028 once ended up inside a regular expression: JavaScript reads it as a line
    break, the whole script failed to parse inside AE and every request quietly fell back to the relay."""
    source=Path(cep_bridge.__file__).parent/'cep'/'main.js'
    def test_no_raw_line_separators_and_ascii_code(self):
        text=self.source.read_text(encoding='utf-8')
        self.assertNotIn(' ',text);self.assertNotIn(' ',text)
        self.assertEqual([n for n,line in enumerate(text.splitlines(),1) if n>1 and not line.isascii()],[])
    @unittest.skipUnless(__import__('shutil').which('node'),'Node.js is not installed')
    def test_runs_against_mock_cep_apis(self):
        import subprocess
        mock=Path(__file__).parent/'cep_mock.cjs'
        out=subprocess.run(['node',str(mock),str(self.source)],capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(out.returncode,0,out.stderr);result=json.loads(out.stdout)
        self.assertEqual(result['heartbeat']['host'],'24.2.1');self.assertTrue(result['claimed'])
        script=result['scripts'][0];self.assertTrue(script.startswith('var request={"action":"codec"'))
    @unittest.skipUnless(__import__('shutil').which('node'),'Node.js is not installed')
    def test_user_folder_with_chinese_and_spaces(self):
        # Many users' Windows account names are Chinese; CEP hands such paths over URL-encoded.
        import os,subprocess
        with tempfile.TemporaryDirectory(prefix='张三 的 ') as tmp:
            out=subprocess.run(['node',str(Path(__file__).parent/'cep_mock.cjs'),str(self.source)],capture_output=True,text=True,encoding='utf-8',timeout=30,env={**os.environ,'TEMP':tmp,'TMP':tmp})
        self.assertEqual(out.returncode,0,out.stderr);result=json.loads(out.stdout)
        self.assertTrue(result['heartbeat']);self.assertTrue(result['claimed']);self.assertIn('张三 的 ',result['scripts'][0])

class FakeExtension(threading.Thread):
    """Claims requests the way cep/main.js does and answers them; `mode` decides how."""
    def __init__(self,mode='answer',delay=0.0):
        super().__init__(daemon=True);self.mode=mode;self.delay=delay;self.seen=[];self.stop=threading.Event()
    def run(self):
        time.sleep(self.delay)
        while not self.stop.is_set():
            for folder in cep_bridge.root().glob('*'):
                request=folder/'request.json'
                if not request.exists() or self.mode=='ignore':continue
                try:request.replace(folder/'claimed.json')
                except OSError:continue
                data=json.loads((folder/'claimed.json').read_text(encoding='ascii'));self.seen.append(data)
                if self.mode=='answer':Path(data['output']).write_text(json.dumps({'changed':2,'requestId':data['requestId'],'action':data['action']}),encoding='utf-8')
                else:(folder/'bridge-error.json').write_text('{"error":"evalScript"}',encoding='utf-8')
            time.sleep(.02)

class RunThroughBridgeTests(BridgeCase):
    def setUp(self):
        super().setUp()
        assets=self.root/'assets';(assets/'native').mkdir(parents=True);(assets/'native'/'ae_parameters_v8.dll').touch();(assets/'scripts').mkdir()
        (assets/'scripts'/'parameter_bridge.jsx').write_text('var request=@@REQUEST@@,result=0;',encoding='utf8')
        self.launches=[];self.kept=[]
        test=self
        class Keeper:
            def __init__(self,pid):test.kept.append(pid)
            def check(self):pass
            def settle(self):pass
        def launch(args,**kwargs):
            self.launches.append(args);script=Path(args[-1])
            request=json.loads(script.read_text(encoding='ascii').split('var request=',1)[1].split(',result=',1)[0])
            Path(request['output']).write_text('{"changed":1}',encoding='utf8');return SimpleNamespace(pid=8,poll=lambda:0)
        import ae_ui
        for target,attr,value in ((names,'asset_root',lambda:assets),(names,'bridge_root',lambda:self.root/'relay'),(names.subprocess,'Popen',launch),
                                  (names,'file_version',lambda path:'26.0.0.67'),(ae_ui,'WindowKeeper',Keeper)):
            handle=patch.object(target,attr,value);handle.start();self.addCleanup(handle.stop)
        self.p=SimpleNamespace(pid=3,image='AfterFX.exe',creation_time=4)
    def extension(self,**kwargs):
        ext=FakeExtension(**kwargs);ext.start();self.addCleanup(ext.join,2);self.addCleanup(ext.stop.set);return ext
    def test_connected_extension_runs_the_request_without_a_second_ae(self):
        self.beat();ext=self.extension()
        result=names.run_native(self.p,[{'after':'中文'}],action='labels')
        self.assertEqual(result['changed'],2);self.assertEqual(self.launches,[]);self.assertEqual(self.kept,[])   # no relay, no window keeping needed
        request=ext.seen[0];folder=cep_bridge.root()/request['requestId']
        self.assertEqual(request['operations'],[{'after':'中文'}]);self.assertTrue(request['output'].startswith(folder.as_posix()+'/'))
        self.assertTrue(request['permit'].startswith(folder.as_posix()+'/'))
        self.assertFalse(folder.exists())                          # the request folder is cleaned up
    def test_not_connected_uses_the_relay(self):
        self.beat(age=cep_bridge.FRESH+5)
        self.assertEqual(names.run_native(self.p,[],action='codec'),{'changed':1})
        self.assertEqual(len(self.launches),1);self.assertEqual(self.kept,[3]);self.assertTrue(Path(self.launches[0][-1]).is_relative_to(self.root/'relay'))
    def test_request_not_taken_in_time_falls_back_to_the_relay(self):
        self.beat();self.extension(mode='ignore')
        with patch.object(cep_bridge,'CLAIM_WAIT',.2):result=names.run_native(self.p,[],action='codec')
        self.assertEqual(result,{'changed':1});self.assertEqual(len(self.launches),1)
        # The same request ran once, through the relay, from its own folder.
        self.assertEqual(Path(self.launches[0][-1]).parent.parent,cep_bridge.root());self.assertEqual(list(cep_bridge.root().glob('*/request.json')),[])
    def test_claimed_at_the_last_moment_is_not_run_twice(self):
        self.beat();ext=self.extension(delay=.25)
        with patch.object(cep_bridge,'CLAIM_WAIT',.25),patch.object(cep_bridge,'withdraw',side_effect=lambda folder:False):
            result=names.run_native(self.p,[],action='codec')
        self.assertEqual(result['changed'],2);self.assertEqual(self.launches,[]);self.assertEqual(len(ext.seen),1)
    def test_ae_refusing_the_script_is_reported(self):
        self.beat();self.extension(mode='refuse')
        with self.assertRaisesRegex(cep_bridge.Rejected,'对话框'):names.run_native(self.p,[],action='codec')
        self.assertEqual(self.launches,[]);self.assertEqual([p for p in cep_bridge.root().iterdir() if p.is_dir()],[])

from test_gui import GuiCase
import app_live

class SettingsTests(GuiCase):
    def setUp(self):
        super().setUp();BridgeCase.setUp(self)  # the fake signed build and its own APPDATA
    def test_install_and_remove_from_the_settings_page(self):
        w=self.window;w.go(app_live.SETTINGS)
        self.assertIn('未安装',w.bridge_info.text());self.assertFalse(w.bridge_btn.isHidden());self.assertTrue(w.bridge_remove.isHidden())
        with patch.object(app_live,'confirm',return_value=False):w.install_bridge()
        self.assertEqual(cep_bridge.status(),'missing')
        self.assertEqual(self.registry,{})                             # declining changes nothing
        with patch.object(app_live,'confirm',return_value=True) as asked:w.install_bridge()
        self.assertIn(str(cep_bridge.installed()),asked.call_args.args[2]);self.assertIn('调试模式',asked.call_args.args[2]);self.assertEqual(cep_bridge.status(),'installed')
        self.assertEqual(self.registry,{v:'1' for v in cep_bridge.CSXS_VERSIONS})
        self.assertIn('重启 AE 后生效',w.bridge_info.text());self.assertTrue(w.bridge_btn.isHidden());self.assertFalse(w.bridge_remove.isHidden())
        BridgeCase.beat(self);w.update_bridge_info();self.assertIn('已连接',w.bridge_info.text())
        with patch.object(app_live,'confirm',return_value=True):w.remove_bridge()
        self.assertEqual(cep_bridge.status(),'missing');self.assertIn('未安装',w.bridge_info.text());self.assertEqual(self.registry,{})
    def test_an_installed_copy_from_another_build_is_updated_at_start(self):
        cep_bridge.install();(cep_bridge.installed()/'main.js').write_text('old build',encoding='utf-8')
        self.assertEqual(cep_bridge.status(),'outdated')
        self.window.refresh_bridge()
        self.assertEqual(cep_bridge.status(),'installed');self.assertIn('已更新为本版本',self.window.log.toPlainText())
    def test_not_installed_is_left_alone_at_start(self):
        self.window.refresh_bridge();self.assertEqual(cep_bridge.status(),'missing')
    def test_build_without_the_extension_hides_the_buttons(self):
        with patch.object(cep_bridge,'bundled',lambda:None):self.window.update_bridge_info()
        self.assertIn('没有附带',self.window.bridge_info.text());self.assertTrue(self.window.bridge_btn.isHidden());self.assertTrue(self.window.bridge_remove.isHidden())

if __name__=='__main__':unittest.main()
