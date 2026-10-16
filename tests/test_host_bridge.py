from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch,Mock
import subprocess
import json,re,tempfile,unittest
from adapter_host import HostBridge

class HostBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.bridge=HostBridge.__new__(HostBridge);self.bridge.exe=Path(self.temp.name)/'AfterFX.exe';self.bridge.folder=Path(self.temp.name);self.bridge.project=self.bridge.folder/'QA.aep';self.bridge.second=self.bridge.folder/'QA_second.aep';self.bridge.comp='qa';self.bridge.host_version=None;self.bridge.progress=None
        p=patch.object(self.bridge,'process',side_effect=lambda:nullcontext(SimpleNamespace(pid=1)));p.start();self.addCleanup(p.stop)
    def launch(self,mode):
        def factory(args,**kwargs):
            script=Path(args[-1]);source=script.read_text(encoding='ascii');request=json.loads(re.search(r'var q=(\{.*?\}),r=',source).group(1));self.request=request
            Path(request['progress']).write_text(json.dumps({'requestId':request['requestId'],'phase':'保存自有 QA 工程'}),encoding='utf8')
            response={'requestId':request['requestId'],'action':request['action'],'hostVersion':'23.0x59','finishedAt':request['expires']-1}
            if mode=='wrong_nonce':response['requestId']='other'
            if mode=='wrong_action':response['action']='other'
            if mode=='expired':response['finishedAt']=request['expires']+1
            if mode!='timeout':Path(request['output']).write_text(json.dumps(response),encoding='utf8')
            return SimpleNamespace(pid=2,poll=lambda:0)
        return patch('adapter_host.subprocess.Popen',side_effect=factory)
    def test_matching_response_is_accepted_and_permit_revoked(self):
        with self.launch('ok'):self.assertEqual(self.bridge.call('status')['hostVersion'],'23.0x59')
        self.assertFalse(Path(self.request['permit']).exists())
    def test_wrong_nonce_or_action_is_rejected(self):
        for mode in ('wrong_nonce','wrong_action'):
            with self.launch(mode):
                with self.assertRaisesRegex(RuntimeError,'身份'):self.bridge.call('status')
    def test_late_output_is_not_accepted_as_success(self):
        with self.launch('expired'):
            with self.assertRaises(TimeoutError):self.bridge.call('prepare')
        self.assertFalse(Path(self.request['permit']).exists())
    def test_timeout_reports_last_acknowledged_phase(self):
        with self.launch('timeout'),patch('adapter_host.time.monotonic',side_effect=[0,1,56]),patch('adapter_host.time.sleep'):
            with self.assertRaises(TimeoutError) as error:self.bridge.call('prepare')
        self.assertIn('创建 QA 工程',str(error.exception));self.assertIn('保存自有 QA 工程',str(error.exception));self.assertFalse(Path(self.request['permit']).exists())
    def test_kwargs_cannot_override_output_or_identity(self):
        with self.assertRaises(ValueError):self.bridge.call('status',output='user.json')

    def test_live_owned_relay_is_reaped_before_host_recheck(self):
        live=[False];events=[]
        def process():
            events.append('probe')
            if live[0]:raise RuntimeError('请只运行一个所选 AE；当前没有唯一验证宿主')
            return nullcontext(SimpleNamespace(pid=1))
        def launch(args,**kwargs):
            source=Path(args[-1]).read_text(encoding='ascii')
            request=json.loads(re.search(r'var q=(\{.*?\}),r=',source).group(1))
            Path(request['output']).write_text(json.dumps({'requestId':request['requestId'],'action':'status','hostVersion':'24.2.1x2','finishedAt':request['expires']-1}),encoding='utf8')
            live[0]=True
            def wait(timeout):events.append('wait');live[0]=False;return 0
            return SimpleNamespace(pid=2,poll=lambda:None if live[0] else 0,wait=wait,terminate=Mock())
        with patch.object(self.bridge,'process',side_effect=process),patch('adapter_host.subprocess.Popen',side_effect=launch):
            self.assertEqual(self.bridge.call('status')['hostVersion'],'24.2.1x2')
        self.assertEqual(events,['probe','wait','probe'])

    def test_slow_owned_relay_is_stopped_before_recheck(self):
        relay=Mock(pid=2);relay.poll.return_value=None;relay.wait.side_effect=[subprocess.TimeoutExpired('relay',1),0]
        HostBridge.finish_relay(relay,1)
        relay.terminate.assert_called_once();self.assertEqual(relay.wait.call_count,2)

    def test_main_host_can_never_be_terminated_as_relay(self):
        relay=Mock(pid=1);relay.poll.return_value=None
        with self.assertRaisesRegex(RuntimeError,'主 AE'):HostBridge.finish_relay(relay,1)
        relay.terminate.assert_not_called();relay.wait.assert_not_called()

class HostSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.bridge=HostBridge.__new__(HostBridge);self.bridge.exe=Path(self.temp.name)/'AfterFX.exe';self.bridge.folder=Path(self.temp.name);self.bridge.version='24.2.1.2';self.bridge.identity=None
    def test_empty_multiple_and_inaccessible_hosts_have_distinct_diagnostics(self):
        cases=[([], '没有检测到'),([{'pid':1,'imagePath':str(self.bridge.exe)},{'pid':2,'error':5}],'2 个'),([{'pid':2,'error':5}],'权限')]
        for rows,message in cases:
            with self.subTest(rows=rows),patch('adapter_host.native_probe',return_value={'processes':rows}),patch('adapter_host.Process') as opened:
                with self.assertRaisesRegex(RuntimeError,message):self.bridge.process()
                opened.assert_not_called()
                record=json.loads((self.bridge.folder/'host-diagnostic.json').read_text(encoding='utf8'))
                self.assertEqual(record['candidateCount'],len(rows));self.assertEqual(record['event'],'host_rejected')
    def test_wrong_install_is_rejected_and_logged(self):
        with patch('adapter_host.native_probe',return_value={'processes':[{'pid':2,'imagePath':'C:/Other/AfterFX.exe'}]}),patch('adapter_host.Process') as opened:
            with self.assertRaisesRegex(RuntimeError,'不一致'):self.bridge.process()
            opened.assert_not_called()
        self.assertTrue((self.bridge.folder/'host-diagnostic.json').exists())
    def test_restart_identity_still_blocks(self):
        self.bridge.identity=(3,100)
        target=SimpleNamespace(pid=3,creation_time=101,image=str(self.bridge.exe),close=Mock())
        with patch('adapter_host.native_probe',return_value={'processes':[{'pid':3,'imagePath':str(self.bridge.exe)}]}),patch('adapter_host.Process',return_value=target),patch('adapter_host.file_version',return_value=self.bridge.version):
            with self.assertRaisesRegex(RuntimeError,'重新启动'):self.bridge.process()
        target.close.assert_called_once()
