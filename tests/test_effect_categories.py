import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import patch
import effect_categories as E
from dataclasses import replace
from ae_profiles import LOCAL_PROFILES

class Memory:
    pid=123;creation_time=456;image='AfterFX.exe'
    def __init__(self):
        self.blocks={};self.writes=[];self.fail_at=None;self.session=None;self.calls=0
        for i,cat in enumerate(('Lights','Lights','Color')):
            raw=bytearray(160);raw[16:64]=E.encode_name('Effect'+str(i));raw[64:96]=('Key'+str(i)).encode().ljust(32,b'\0');raw[96:160]=cat.encode().ljust(64,b'\0');self.blocks[4096*(i+1)]=raw
    def __enter__(self):return self
    def __exit__(self,*exc):pass
    def read(self,at,count):
        for base,raw in self.blocks.items():
            if base<=at and at+count<=base+len(raw):return bytes(raw[at-base:at-base+count])
        raise RuntimeError('bad memory')
    def write_buffer(self,at,data):
        self.calls+=1
        if self.calls==self.fail_at:raise RuntimeError('write failed')
        if len(data)!=48 or at%4096!=96:raise AssertionError('write escaped first 48 category bytes')
        if self.session and not self.session.path.exists():raise AssertionError('journal not persisted')
        base=at-96;self.blocks[base][96:144]=data;self.writes.append(at)

class Registry:
    def __init__(self,p):self.p=p;self.profile=SimpleNamespace(version='26.5.0.89',flt_sha=E.PROFILES['26.5.0.89'].flt_sha,encoding='utf8',name_base=16,fc_vtable=1)
    def records(self):return [dict(address=base,matchName=bytes(raw[64:96]).split(b'\0')[0].decode(),displayName=bytes(raw[16:64]).split(b'\0')[0].decode(),category=bytes(raw[96:160]).split(b'\0')[0].decode()) for base,raw in self.p.blocks.items()]
    def target(self,key):return next(r for r in self.records() if r['matchName']==key)

class Categories(unittest.TestCase):
    def test_all_enabled_builtin_builds_and_matching_hashes_are_available(self):
        for version,profile in E.PROFILES.items():
            self.assertEqual(E.supported_build(version,profile.flt_sha),profile.enabled)
            self.assertFalse(E.supported_build(version,'0'*64))
        self.assertFalse(E.supported_build('unknown','0'*64))
    def test_validated_local_legacy_layout_is_available_but_disabled_is_not(self):
        legacy=replace(E.PROFILES['26.5.0.89'],version='15.1.2.69',name_base=20,fc_vtable=0,fc_tag=1,fc_tail=164,registry_list=True)
        with patch.dict(LOCAL_PROFILES,{legacy.version:legacy}):
            self.assertTrue(E.supported_build(legacy.version,legacy.flt_sha))
            self.assertEqual(E.CategorySession._span(type('R',(),{'profile':legacy})()),100)
        with patch.dict(LOCAL_PROFILES,{legacy.version:replace(legacy,enabled=False)}):
            self.assertFalse(E.supported_build(legacy.version,legacy.flt_sha))
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=Memory();self.session=E.CategorySession();self.session.path=Path(self.tmp.name)/'category.json';self.p.session=self.session
        for name,value in (('_process_lock',lambda:nullcontext()),('main_window',lambda pid:1),('Registry',Registry),('current_process',lambda **kw:self.p),('running_pids',lambda:{self.p.pid})):
            p=patch.object(E,name,value);p.start();self.addCleanup(p.stop)
    def values(self):return {k:bytes(v) for k,v in self.p.blocks.items()}
    def test_group_apply_repeat_restore_changes_only_category(self):
        original=self.values();self.assertEqual(self.session.group('Key0')['keys'],['Key0','Key1'])
        result=self.session.apply_group('Key0','灯光','Lights');self.assertEqual(len(result['applied']),2)
        for base,before in original.items():
            now=bytes(self.p.blocks[base]);self.assertEqual(now[:96],before[:96]);self.assertEqual(now[144:],before[144:])
        self.session.apply_group('Key0','照明','灯光');self.assertEqual(len(self.session.restore()['restored']),2);self.assertEqual(self.values(),original)
    def test_journal_precedes_write_and_failure_rolls_back(self):
        original=self.values();self.p.fail_at=2
        with self.assertRaisesRegex(RuntimeError,'write failed'):self.session.apply_group('Key0','灯光','Lights')
        self.assertEqual(self.values(),original);self.assertEqual(self.session.journal()['entries'],{})
    def test_external_change_is_not_overwritten_on_restore(self):
        self.session.apply_group('Key0','灯光','Lights');self.p.blocks[4096][96:160]=b'External'.ljust(64,b'\0');before=self.values()
        with self.assertRaisesRegex(RuntimeError,'外部'):self.session.restore()
        self.assertEqual(self.values(),before);self.assertEqual(len(self.session.journal()['entries']),2)
    def test_preview_category_change_and_collision_refused(self):
        for name,expected in (('灯光','Different'),('Color','Lights')):
            with self.assertRaises((ValueError,RuntimeError)):self.session.apply_group('Key0',name,expected)
        self.assertEqual(self.p.writes,[])
    def test_unverified_build_does_not_write(self):
        class Other(Registry):
            def __init__(self,p):super().__init__(p);self.profile.version='18.2.1.8'
        with patch.object(E,'Registry',Other),self.assertRaisesRegex(RuntimeError,'尚未验证'):self.session.apply_group('Key0','灯光','Lights')
        self.assertEqual(self.p.writes,[])
    def test_same_version_other_module_is_not_allowed(self):
        class Other(Registry):
            def __init__(self,p):super().__init__(p);self.profile.flt_sha='0'*64
        with patch.object(E,'Registry',Other):
            self.assertFalse(self.session.group('Key0')['supported'])
            with self.assertRaisesRegex(RuntimeError,'模块尚未验证'):self.session.apply_group('Key0','灯光','Lights')
        self.assertEqual(self.p.writes,[])
    def test_long_original_or_translation_refused(self):
        self.p.blocks[4096][96:160]=b'A'*48+b'\0'*16
        with self.assertRaisesRegex(ValueError,'48字节'):self.session.apply_group('Key0','灯光','A'*48)
        self.assertEqual(self.p.writes,[])
        self.p.blocks[4096][96:160]=b'Lights'.ljust(64,b'\0')
        with self.assertRaises(ValueError):self.session.apply_group('Key0','灯'*20,'Lights')
        self.assertEqual(self.p.writes,[])
    def test_opaque_tail_is_preserved_without_widening_writes(self):
        self.p.blocks[4096][144:160]=bytes(range(16));before=self.values()
        self.session.apply_group('Key0','灯光','Lights');self.assertEqual(bytes(self.p.blocks[4096][144:160]),bytes(range(16)))
        self.session.restore();self.assertEqual(self.values(),before)
    def test_restart_expires_session_without_writing(self):
        self.session.apply_group('Key0','灯光','Lights');self.p.writes=[];self.p.creation_time+=1
        self.assertTrue(self.session.restore()['oldProcessEnded']);self.assertEqual(self.p.writes,[])
    def test_corrupt_journal_is_preserved(self):
        self.session.path.write_text('{bad',encoding='utf8')
        with self.assertRaisesRegex(RuntimeError,'记录损坏'):self.session.apply_group('Key0','灯光','Lights')
        self.assertEqual(self.session.path.read_text(),'{bad');self.assertEqual(self.p.writes,[])
    def test_exited_host_expires_record_without_opening_process(self):
        self.session.apply_group('Key0','灯光','Lights');self.p.writes=[]
        with patch.object(E,'running_pids',return_value=set()),patch.object(self.session,'process',side_effect=AssertionError('must not open process')):self.assertTrue(self.session.restore()['oldProcessEnded'])
        self.assertEqual(self.p.writes,[])
    def test_group_membership_change_requires_new_confirmation(self):
        with self.assertRaisesRegex(RuntimeError,'包含的效果已变化'):self.session.apply_group('Key0','灯光','Lights',['Key0'])
        self.assertEqual(self.p.writes,[])

if __name__=='__main__':unittest.main()
