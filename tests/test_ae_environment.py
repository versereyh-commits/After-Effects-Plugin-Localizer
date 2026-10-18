"""The outside look at AE's process: rewritten file APIs, where they lead, foreign modules. Fake processes only."""
import json,struct,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ae_environment as env

# Made-up paths in the shapes sync_public.py accepts for the open-source copy (Program Files, Other).
AE=r'C:\Program Files\AE\Support Files'
CRACK=r'D:\Other\Crack'
COMMON=r'C:\Program Files\Common Files\Plug-ins'
WINDOWS=r'C:\Windows'

def pe(size):
    header=bytearray(0x400);struct.pack_into('<I',header,0x3C,0x80);header[0x80:0x84]=b'PE\0\0';struct.pack_into('<I',header,0x80+0x50,size);return bytes(header)

class FakeAE:
    """Memory is a dict of address -> bytes; a read must start at a known address."""
    def __init__(self,modules,memory):self.image=AE+r'\AfterFX.exe';self._modules=modules;self.memory=memory
    def modules(self):return self._modules
    def read(self,address,size):
        for start,data in self.memory.items():
            if start<=address and address+size<=start+len(data):return data[address-start:address-start+size]
        raise RuntimeError('unmapped')

class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.read_file=patch.object(env,'_file_state',lambda path:{'flags':[],'readMs':1});self.read_file.start();self.addCleanup(self.read_file.stop)
        self.windows=patch.dict('os.environ',{'SystemRoot':WINDOWS});self.windows.start();self.addCleanup(self.windows.stop)
        self.clean=b'\x48\x89\x5c\x24\x08\x48\x89\x74\x24\x10\x48\x89\x7c\x24\x18\x4c'
        self.modules=[{'name':'ntdll.dll','path':WINDOWS+r'\System32\ntdll.dll','base':0x7ff000000000},
                      {'name':'hook.dll','path':CRACK+r'\hook.dll','base':0x180000000},
                      {'name':'version.dll','path':AE+r'\version.dll','base':0x190000000},
                      {'name':'FLT.dll','path':AE+r'\FLT.dll','base':0x1a0000000},
                      {'name':'Effect.aex','path':COMMON+r'\Effect.aex','base':0x1b0000000}]
        self.memory={0x7ff000000000:pe(0x200000),0x180000000:pe(0x10000),0x190000000:pe(0x10000),0x1a0000000:pe(0x10000),0x1b0000000:pe(0x10000)}
        self.companies={CRACK+r'\hook.dll':'Crack Team',AE+r'\version.dll':'','r':'',AE+r'\FLT.dll':'Adobe Inc.',COMMON+r'\Effect.aex':'Adobe Systems'}
    def scan(self,entries):
        return env.scan(FakeAE(self.modules,self.memory),local=entries,company_of=lambda path:self.companies.get(path,''))
    def test_a_clean_process_reports_no_hooks(self):
        at=0x7ff000001000;self.memory[at]=self.clean
        result=self.scan({'ntdll.dll!NtReadFile':(at,self.clean)})
        self.assertEqual(result['hooks'],[]);self.assertEqual(env.summary(result),'');self.assertEqual(result['checkedFunctions'],1)
    def test_each_jump_form_leads_to_its_module(self):
        target=0x180001234;cases={}
        at=0x170000000;self.memory[at]=b'\xe9'+struct.pack('<i',target-(at+5))+b'\xcc'*11;cases['kernelbase.dll!ReadFile']=at   # rel32 reaches +-2 GB
        at=0x7ff000003000;slot=0x7ff000003100;self.memory[at]=b'\xff\x25'+struct.pack('<i',slot-(at+6))+b'\xcc'*10;self.memory[slot]=struct.pack('<Q',target);cases['ntdll.dll!NtReadFile']=at
        at=0x7ff000004000;self.memory[at]=b'\x48\xb8'+struct.pack('<Q',target)+b'\xff\xe0'+b'\xcc'*4;cases['ntdll.dll!NtCreateSection']=at
        at=0x7ff000005000;self.memory[at]=b'\x68'+struct.pack('<I',0x19000abc)+b'\xc3'+b'\xcc'*10;cases['kernel32.dll!CreateFileW']=at
        self.modules.append({'name':'low.dll','path':CRACK+r'\low.dll','base':0x19000000});self.memory[0x19000000]=pe(0x10000);self.companies[CRACK+r'\low.dll']='Crack Team'
        result=self.scan({key:(at,self.clean) for key,at in cases.items()})
        found={h['function']:(h['state'],h['target'],h['company']) for h in result['hooks']}
        self.assertEqual(found,{'kernelbase.dll!ReadFile':('rewritten','hook.dll','Crack Team'),'ntdll.dll!NtReadFile':('rewritten','hook.dll','Crack Team'),
                                'ntdll.dll!NtCreateSection':('rewritten','hook.dll','Crack Team'),'kernel32.dll!CreateFileW':('rewritten','low.dll','Crack Team')})
        text=env.summary(result)
        self.assertIn('ReadFile、NtReadFile、NtCreateSection 被 hook.dll（Crack Team）改动',text);self.assertIn('CreateFileW 被 low.dll（Crack Team）改动',text)
    def test_rewritten_without_a_known_jump(self):
        at=0x7ff000006000;self.memory[at]=b'\x90'*16
        result=self.scan({'ntdll.dll!NtReadFile':(at,self.clean)})
        self.assertEqual(result['hooks'],[{'function':'ntdll.dll!NtReadFile','state':'rewritten','target':'not a jump','company':''}])
    def test_foreign_modules_are_those_outside_ae_or_with_a_hijack_name(self):
        result=self.scan({})
        self.assertEqual(result['foreignModules'],[{'module':'hook.dll','company':'Crack Team','where':'outsideAE'},{'module':'version.dll','company':'','where':'hijackName'}])
    def test_nothing_identifying_is_kept(self):
        at=0x170000000;self.memory[at]=b'\xe9'+struct.pack('<i',0x180001234-(at+5))+b'\xcc'*11
        text=json.dumps(self.scan({'kernelbase.dll!ReadFile':(at,self.clean)}),ensure_ascii=False)
        for secret in ('Crack\\\\','Support Files',str(0x180001234),hex(0x180001234),'e9'):self.assertNotIn(secret,text)
    def test_a_failing_scan_is_reported_not_raised(self):
        broken=SimpleNamespace(image=AE+r'\AfterFX.exe',modules=lambda:(_ for _ in ()).throw(OSError('denied')))
        self.assertEqual(env.scan(broken,local={}),{'scanError':'OSError'});self.assertEqual(env.summary({'scanError':'OSError'}),'')
    def test_on_demand_files_are_named(self):
        self.assertIn('FLT.dll 是需要按需下载',env.summary({'hooks':[],'files':{'FLT.dll':{'flags':['recallOnDataAccess']},'TDB.dll':{'flags':[]}}}))
    def test_this_process_entries_are_real(self):
        entries=env._local_entries()
        self.assertIn('ntdll.dll!NtReadFile',entries);self.assertTrue(all(len(code)==env.PROLOGUE for _,code in entries.values()))

class SelfCheckFailureTests(unittest.TestCase):
    def test_helper_failure_names_injected_code_and_saves_the_scan(self):
        import tempfile,runtime,adapter_validation,parameter_names
        found={'hooks':[{'function':'kernelbase.dll!ReadFile','state':'rewritten','target':'hook.dll','company':''}],'foreignModules':[]}
        run=adapter_validation.ValidationRun.__new__(adapter_validation.ValidationRun);run.bridge=SimpleNamespace(version='25.6.0.101')
        with tempfile.TemporaryDirectory() as tmp,patch.object(runtime,'data_root',lambda:Path(tmp)),patch.object(env,'scan',return_value=found),\
             patch.object(adapter_validation,'module_read_blocked',return_value='辅助模块在 AE 里读取 FLT.dll 计算哈希（映射文件，等待 10 秒后已放弃）'):
            message=run._helper_failure(SimpleNamespace(),0.0,{'error':'x'})
            saved=json.loads(next((Path(tmp)/'diagnostics').glob('ae-environment-*.json')).read_text(encoding='utf-8'))
        self.assertIn('等待 10 秒后已放弃）。检测到 AE 里的文件函数 ReadFile 被 hook.dll 改动',message);self.assertIn('诊断记录：',message)
        self.assertEqual((saved['aeVersion'],saved['helperFailed'],saved['hooks']),('25.6.0.101',True,found['hooks']))
        with patch.object(env,'scan',return_value={'hooks':[]}),patch.object(adapter_validation,'module_read_blocked',return_value=''),patch.object(adapter_validation,'write_diagnostic',return_value=None):
            self.assertEqual(run._helper_failure(SimpleNamespace(),0.0,{'error':'x'}),'原生结构或编码核验失败，未开始中文写入')

if __name__=='__main__':unittest.main()
