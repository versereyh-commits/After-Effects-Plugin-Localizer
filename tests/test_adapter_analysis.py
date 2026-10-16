import json,struct,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import adapter_analysis as analysis
from ae_profiles import PROFILES

def tiny_pe():
    """Synthetic x64 PE with one executable section and a field getter."""
    data=bytearray(1024);data[:2]=b'MZ';struct.pack_into('<I',data,60,128);data[128:132]=b'PE\0\0'
    struct.pack_into('<HHIIIHH',data,132,0x8664,1,0,0,0,240,0x2022)
    opt=152;struct.pack_into('<H',data,opt,0x20b);struct.pack_into('<Q',data,opt+24,0x140000000)
    struct.pack_into('<II',data,opt+32,4096,512);struct.pack_into('<II',data,opt+56,8192,512);struct.pack_into('<I',data,opt+108,16)
    section=opt+240;data[section:section+8]=b'.text\0\0\0'
    struct.pack_into('<IIII',data,section+8,512,4096,512,512);struct.pack_into('<I',data,section+36,0x60000020)
    data[512:518]=bytes.fromhex('488d4140c3cc');return bytes(data)

class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name)/'AE'/'Support Files';folder.mkdir(parents=True);self.exe=folder/'AfterFX.exe'
        for name in ('AfterFX.exe','AfterFXLib.dll','FLT.dll','TDB.dll'):(folder/name).write_bytes(tiny_pe())
        self.tools=analysis.ModuleTools(self.exe,version_of=lambda p:'23.0.0.59');self.addCleanup(self.tools.close)
        symbols=[SimpleNamespace(name=b'?GetMatchName@FLT_FCSpec@@UEBAPEBEXZ',address=4096,forwarder=None)]
        self.tools.modules['FLT.dll']['pe'].DIRECTORY_ENTRY_EXPORT=SimpleNamespace(symbols=symbols)
        self.config={'service':'ollama','key':'','base_url':'http://localhost:1/v1','model':'test'}
        check=patch.object(analysis,'file_version',lambda p:'23.0.0.59');check.start();self.addCleanup(check.stop)
    def inspect(self):return self.tools.call('inspect',{})
    def selected(self):
        self.inspect();return [key for key,value in self.tools.evidence.items() if value.get('field')]
    def finish(self):return {'finish':{'summary':'静态字段已识别；尚未完成写入验证','selectedEvidence':self.selected(),'missing':['宿主验证']}}
    def run_agent(self,turn,cancel=None):return analysis.run_analysis(self.exe,self.config,cancel=cancel,turn=turn,tools_factory=lambda exe:self.tools)
    def test_getter_offset_is_derived_from_instructions(self):
        report=self.inspect();fields={e['field']:e['value'] for e in report['derived']}
        self.assertEqual(fields,{'match_get':4096,'match_offset':64})
    def test_module_tool_cannot_read_arbitrary_path(self):
        with self.assertRaises(ValueError):self.tools.call('read_module',{'module':'../api-settings.json','rva':0,'size':32})
        with self.assertRaises(ValueError):self.tools.call('inspect',{'path':'C:/Users'})
    def test_container_offset_derived_without_version_table(self):
        data=bytearray(self.tools.modules['TDB.dll']['data']);data[512:533]=bytes.fromhex('488b8188020000482b818002000048c1f803c3cccccc')
        self.tools.modules['TDB.dll']['data']=bytes(data)
        self.tools.modules['TDB.dll']['pe'].DIRECTORY_ENTRY_EXPORT=SimpleNamespace(symbols=[SimpleNamespace(name=b'?GetNumStreams@TDB_IndexedStreamGroup@@UEBAIXZ',address=4096,forwarder=None)])
        fields={e['field']:e['value'] for e in self.inspect()['derived']}
        self.assertEqual(fields['group_vector'],0x280)
    def test_out_of_range_and_bool_offsets_rejected(self):
        for rva in (-1,True,8192,4600):
            with self.assertRaises(ValueError):self.tools.call('read_module',{'module':'FLT.dll','rva':rva,'size':256})
    def test_tools_do_not_expose_shell_or_write(self):
        for tool in ('exec','shell','write_memory','save_file','enable_profile'):
            with self.assertRaises(ValueError):self.tools.call(tool,{})
    def test_disassembly_is_local_evidence(self):
        result=self.tools.call('disassemble',{'module':'FLT.dll','rva':4096,'count':8})
        self.assertEqual(result['value']['instructions'][0]['instruction'],'lea rax, [rcx + 0x40]')
        self.assertEqual(len(result['value']['instructions']),2)
    def test_candidate_copies_only_locally_observed_fields(self):
        before=dict(PROFILES);report=self.run_agent(lambda *args:self.finish())
        self.assertEqual(report['candidateReadConfig']['match_offset'],64)
        self.assertFalse(report['runtimeEnabled']);self.assertFalse(report['hostVerified']);self.assertEqual(before,PROFILES)
        self.assertNotIn(str(self.temp.name),json.dumps(report));self.assertNotIn('key',report)
    def test_fake_evidence_and_enabled_status_are_rejected(self):
        for finish in ({'summary':'测试','selectedEvidence':['E999'],'missing':[]},{'summary':'测试','enabled':True}):
            with self.assertRaises(ValueError):self.tools.candidate(finish)
    def test_agent_executes_tool_calls_and_returns_audit_trace(self):
        replies=iter([{'tool':'inspect','args':{}},{'tool':'disassemble','args':{'module':'FLT.dll','rva':4096,'count':4}},self.finish()])
        report=self.run_agent(lambda *args:next(replies))
        self.assertEqual([x['tool'] for x in report['trace']],['inspect','disassemble']);self.assertEqual(report['state'],'candidate')
    def test_unknown_tool_never_executes(self):
        report=self.run_agent(lambda *args:{'tool':'shell','args':{'command':'test'}})
        self.assertEqual(report['state'],'blocked');self.assertEqual(report['trace'],[])
    def test_invalid_tool_arguments_are_returned_to_model(self):
        replies=iter([{'tool':'inspect','args':{}},{'tool':'exports','args':{'module':'other.dll'}},self.finish()])
        report=self.run_agent(lambda *args:next(replies));self.assertIn('error',report['trace'][1]['result'])
    def test_cancel_before_network(self):
        cancel=threading.Event();cancel.set()
        with self.assertRaises(analysis.AnalysisCancelled):self.run_agent(lambda *a:self.fail('network called'),cancel)
    def test_cancel_after_reply_does_not_execute_tool(self):
        cancel=threading.Event()
        def turn(*args):cancel.set();return {'tool':'inspect','args':{}}
        with self.assertRaises(analysis.AnalysisCancelled):self.run_agent(turn,cancel)
    def test_round_limit_returns_partial_report(self):
        report=self.run_agent(lambda *args:{'tool':'inspect','args':{}})
        self.assertEqual(report['state'],'blocked');self.assertEqual(len(report['trace']),analysis.MAX_TURNS)
    def test_changed_module_discards_finished_candidate(self):
        def turn(*args):
            reply=self.finish();(self.exe.parent/'FLT.dll').write_bytes(b'changed');return reply
        report=self.run_agent(turn);self.assertEqual(report['state'],'blocked');self.assertEqual(report['candidateReadConfig'],{})
    def test_report_is_separate_from_runtime_profiles(self):
        report=self.run_agent(lambda *args:self.finish());folder=Path(self.temp.name)/'reports'
        path=analysis.save_report(report,folder);loaded=json.loads(path.read_text(encoding='utf8'))
        self.assertEqual(loaded['kind'],'ae-adapter-analysis');self.assertFalse(loaded['runtimeEnabled'])
    def test_cached_report_is_revalidated_without_network(self):
        report=self.run_agent(lambda *args:self.finish());folder=Path(self.temp.name)/'reports';analysis.save_report(report,folder)
        with patch.object(analysis,'ModuleTools',return_value=self.tools),patch('adapter_resolver.Resolver') as resolver:loaded=analysis.load_report(self.exe,folder)
        self.assertEqual(loaded['candidateReadConfig'],report['candidateReadConfig']);self.assertFalse(loaded['runtimeEnabled'])
    def test_cached_invented_field_is_rejected(self):
        report=self.run_agent(lambda *args:self.finish());report['candidateReadConfig']['manager']=123;folder=Path(self.temp.name)/'reports';analysis.save_report(report,folder)
        with patch.object(analysis,'ModuleTools',return_value=self.tools):
            with self.assertRaises(ValueError):analysis.load_report(self.exe,folder)
    def test_cached_claim_of_runtime_enable_is_rejected(self):
        report=self.run_agent(lambda *args:self.finish());report['runtimeEnabled']=True;folder=Path(self.temp.name)/'reports';analysis.save_report(report,folder)
        with patch.object(analysis,'ModuleTools',return_value=self.tools):
            with self.assertRaises(ValueError):analysis.load_report(self.exe,folder)
    def test_legacy_report_defaults_to_ai_and_static_is_rechecked(self):
        report=self.run_agent(lambda *args:self.finish());report.pop('analysisSource');folder=Path(self.temp.name)/'reports';analysis.save_report(report,folder)
        with patch.object(analysis,'ModuleTools',return_value=self.tools),patch('adapter_resolver.Resolver') as resolver:
            loaded=analysis.load_report(self.exe,folder);resolver.return_value.complete_static.assert_called_once_with('')
        self.assertEqual(loaded.get('analysisSource','ai'),'ai')
    def test_code_report_can_be_reused_without_model(self):
        report=self.run_agent(lambda *args:self.finish());report['analysisSource']='code';folder=Path(self.temp.name)/'reports';analysis.save_report(report,folder)
        with patch.object(analysis,'ModuleTools',return_value=self.tools),patch('adapter_resolver.Resolver'),patch.object(analysis,'analysis_turn',side_effect=AssertionError('network')):
            self.assertEqual(analysis.load_report(self.exe,folder)['analysisSource'],'code')
    def test_cached_candidate_requires_supported_static_structure(self):
        report=self.run_agent(lambda *args:self.finish());folder=Path(self.temp.name)/'reports';analysis.save_report(report,folder)
        with patch.object(analysis,'ModuleTools',return_value=self.tools),patch('adapter_resolver.Resolver') as resolver:
            resolver.return_value.complete_static.side_effect=ValueError('unknown layout')
            with self.assertRaises(ValueError):analysis.load_report(self.exe,folder)
    def test_loaded_read_rejects_writable_section_before_process_access(self):
        self.tools.modules['FLT.dll']['pe'].sections[0].Characteristics|=0x80000000
        with patch.object(analysis,'native_probe') as probe:
            with self.assertRaises(ValueError):self.tools.call('read_loaded_module',{'module':'FLT.dll','rva':4096,'size':6})
            probe.assert_not_called()
