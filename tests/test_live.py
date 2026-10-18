import json
from pathlib import Path
import os,subprocess,sys,time
import tempfile
import unittest
from unittest.mock import Mock,patch
import live_names as live
import ae_ui
from settings_store import _crypt

class FakeProcess:
    pid=123
    creation_time=456
    image='AfterFX.exe'
    def __init__(self):self.memory={1016:live.encode_name('S_Glow'),2016:live.encode_name('Shine')};self.writes=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,address,size):return self.memory[address]
    def write_buffer(self,address,value):
        if address not in self.memory or len(value)!=48:raise AssertionError('Write escaped the name field')
        self.writes.append((address,value));self.memory[address]=value

class FakeRegistry:
    EFFECTS=[(1000,'S_Glow','Sapphire Lighting'),(2000,'tc Shine','RG Blurs and Glows')]
    def __init__(self,process):self.p=process
    def records(self):
        return [{'address':address,'displayName':live.decode_name(self.p.memory[address+16],'utf8'),'matchName':key,'category':category,'guard':b'stable-identity'} for address,key,category in self.EFFECTS]
    def target(self,key):return next(x for x in self.records() if x['matchName']==key)
    def read_record(self,address):return next(x for x in self.records() if x['address']==address)
    unchanged=live.Registry.unchanged

class UnnamedEffectTests(unittest.TestCase):
    """The real Registry leaves out effects a plugin registered without an internal name (seen on AE 2021)."""
    def registry(self,effects):
        from types import SimpleNamespace
        import struct
        memory={0x1000:struct.pack('<QQ',0x2000,0x2000+16*len(effects))};vector=b''
        for i,(key,name) in enumerate(effects):
            at=0x3000+i*0x100;data=bytearray(160);struct.pack_into('<Q',data,0,0x900);data[16:16+len(name.encode())]=name.encode()
            key=key if isinstance(key,bytes) else key.encode()
            data[64:64+len(key)]=key;data[96:100]=b'Test';memory[at]=bytes(data);vector+=struct.pack('<QQ',at,0)
        memory[0x2000]=vector
        registry=live.Registry.__new__(live.Registry)
        registry.p=SimpleNamespace(read=lambda a,n:memory[a][:n]);registry.base=0x800;registry.manager=0x1000
        registry.profile=SimpleNamespace(registry_vector=0,fc_vtable=0x100,encoding='utf8');return registry
    def test_unnamed_effects_are_left_out_and_listed(self):
        registry=self.registry([('S_Glow','S_Glow'),('','渐变色调'),('','聚光灯'),('tc Shine','Shine')])
        self.assertEqual([r['matchName'] for r in registry.records()],['S_Glow','tc Shine']);self.assertEqual(registry.unnamed,['渐变色调','聚光灯'])
    def test_internal_names_follow_the_profiles_match_encoding(self):
        # A profile validated on AE 2021 reads internal names in the system code page, as AE's scripting does.
        registry=self.registry([('S_Glow','S_Glow'),('NWBL 双色','双色')]);registry.profile.match_encoding='mbcs'
        rows=registry.records();self.assertEqual(rows[1]['matchName'],'NWBL 双色'.encode('utf8').decode('mbcs'));self.assertEqual(rows[1]['displayName'],'双色')
    def test_internal_names_this_build_cannot_read_count_as_unnamed(self):
        # AE 2021 reported '' to scripts for seven effects whose internal-name bytes were not empty.
        registry=self.registry([('S_Glow','S_Glow'),('渐变'.encode('gbk')+b'X','渐变色调'),('S_中文','中文标识')])
        self.assertEqual([r['matchName'] for r in registry.records()],['S_Glow','S_中文']);self.assertEqual(registry.unnamed,['渐变色调'])
    def test_writing_others_is_not_blocked(self):
        process=FakeProcess();temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        class WithUnnamed(FakeRegistry):
            def records(self):
                rows=FakeRegistry.records(self)+[{'address':9000,'displayName':'渐变色调','matchName':'','category':'X','guard':b'g'}]*2
                self.unnamed=[r['displayName'] for r in rows if not r['matchName']];return [r for r in rows if r['matchName']]
        for target,value in [('data_root',lambda:Path(temp.name)),('current_process',lambda **kw:process),('Registry',WithUnnamed),('main_window',lambda pid:1),
                             ('native_probe',lambda:{'processes':[{'pid':123}]}),('_mutex_name','Local\\AEPluginNameLocalizer.Test.'+Path(temp.name).name)]:
            handle=patch.object(live,target,value);handle.start();self.addCleanup(handle.stop)
        self.assertEqual(live.NameSession().apply({'S_Glow':'辉光'})['applied'],{'S_Glow':'辉光'})

class LegacyEncodingTests(unittest.TestCase):
    """An mbcs build stores names in the system code page (GBK on a Chinese system): names it cannot hold are
    left out and named, with a diagnostic; the rest are written."""
    def setUp(self):
        import runtime
        from types import SimpleNamespace
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.process=FakeProcess()
        class Legacy(FakeRegistry):
            profile=SimpleNamespace(encoding='mbcs',flt_sha='test')
            def records(self):return [{**row,'displayName':live.decode_name(self.p.memory[row['address']+16],'mbcs')} for row in FakeRegistry.records(self)]
        for module,target,value in [(live,'data_root',lambda:Path(self.temp.name)),(runtime,'data_root',lambda:Path(self.temp.name)),(live,'current_process',lambda **kw:self.process),
                                    (live,'Registry',Legacy),(live,'main_window',lambda pid:1),(live,'native_probe',lambda:{'processes':[{'pid':123}]}),
                                    (live,'_mutex_name','Local\\AEPluginNameLocalizer.Test.'+Path(self.temp.name).name),(runtime,'code_page',lambda:936)]:
            handle=patch.object(module,target,value);handle.start();self.addCleanup(handle.stop)
        self.session=live.NameSession()
    def test_names_the_code_page_cannot_hold_are_skipped_and_named(self):
        result=self.session.apply({'S_Glow':'蓝宝石辉光™','tc Shine':'闪耀'},skip_missing=True)
        self.assertEqual(result['applied'],{'tc Shine':'闪耀'});self.assertEqual(self.process.memory[1016],live.encode_name('S_Glow'))
        problem=result['unwritable'];self.assertEqual(problem['keys'],['S_Glow'])
        self.assertIn('S_Glow“蓝宝石辉光™”里的 “™”',problem['message']);self.assertIn('代码页 936',problem['message'])
        details=json.loads(Path(problem['diagnostic']).read_text(encoding='utf-8'))
        self.assertEqual((details['kind'],details['codePage'],details['encoding'],details['items']),('name-encoding',936,'mbcs',[{'item':'S_Glow','name':'蓝宝石辉光™','chars':['™ U+2122']}]))
        self.assertEqual(Path(problem['diagnostic']).parent,Path(self.temp.name)/'diagnostics')
    def test_nothing_writable_stops_with_the_reason(self):
        with self.assertRaisesRegex(ValueError,'S_Glow“辉光•”里的 “•”.*诊断记录'):self.session.apply({'S_Glow':'辉光•'},skip_missing=True)
        self.assertEqual(self.process.writes,[])
    def test_a_system_that_cannot_hold_chinese_names_the_locale(self):
        with patch('live_names.unencodable',side_effect=lambda text,encoding:list(text)),patch('runtime.unencodable',side_effect=lambda text,encoding:list(text)):
            with self.assertRaisesRegex(ValueError,'无法表示中文，2 个效果译名未写入.*更改系统区域设置'):self.session.apply({'S_Glow':'辉光','tc Shine':'闪耀'},skip_missing=True)
    def test_diagnostics_keep_the_newest_twenty(self):
        import runtime
        for _ in range(23):runtime.write_diagnostic('name-encoding',{})
        self.assertEqual(len(list((Path(self.temp.name)/'diagnostics').glob('*.json'))),20)

class LiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.process=FakeProcess()
        for target,value in [('data_root',lambda:Path(self.temp.name)),('current_process',lambda **kw:self.process),('Registry',FakeRegistry),('main_window',lambda pid:1),('native_probe',lambda:{'processes':[{'pid':123}]})]:
            handle=patch.object(live,target,value);handle.start();self.addCleanup(handle.stop)
        self.session=live.NameSession()
        handle=patch.object(live,'_mutex_name','Local\\AEPluginNameLocalizer.Test.'+Path(self.temp.name).name);handle.start();self.addCleanup(handle.stop)
    def _journal(self):
        return {'schemaVersion':1,'pid':123,'creationTime':456,'entries':{'S_Glow':{
            'originalHex':live.encode_name('S_Glow').hex(),'appliedHex':live.encode_name('辉光').hex(),'originalName':'S_Glow','newName':'辉光'}}}
    def _assert_bad_journal(self,content):
        self.session.journal_path.write_bytes(content);before=self.process.memory.copy()
        for action in (self.session.journal,lambda:self.session.apply({'S_Glow':'辉光'}),self.session.restore):
            with self.assertRaisesRegex(RuntimeError,'恢复记录损坏，已停止写入，原文件已保留'):action()
            self.assertEqual(self.process.writes,[]);self.assertEqual(self.process.memory,before)
            self.assertEqual(self.session.journal_path.read_bytes(),content)
            self.assertEqual(list(Path(self.temp.name).iterdir()),[self.session.journal_path])
    def test_missing_journal_is_empty(self):
        self.assertEqual(self.session.journal(),{'schemaVersion':1,'entries':{}})
        self.assertFalse(self.session.journal_path.exists())
    def test_bad_json_is_preserved_without_writes(self):
        self._assert_bad_journal(b'{broken json')
    def test_list_journal_is_preserved_without_writes(self):
        self._assert_bad_journal(b'[]')
    def test_invalid_journal_encoding_is_preserved_without_writes(self):
        self._assert_bad_journal(b'\xff')
    def test_short_hex_is_preserved_without_writes(self):
        for field in ('originalHex','appliedHex'):
            with self.subTest(field=field):
                journal=self._journal();journal['entries']['S_Glow'][field]='4100'
                self._assert_bad_journal(json.dumps(journal).encode())
    def test_hex_without_nul_is_preserved_without_writes(self):
        for field in ('originalHex','appliedHex'):
            with self.subTest(field=field):
                journal=self._journal();journal['entries']['S_Glow'][field]=(b'A'*48).hex()
                self._assert_bad_journal(json.dumps(journal).encode())
    def test_non_utf8_applied_hex_is_preserved_without_writes(self):
        # What this program wrote is always readable; an original name may be any bytes a plugin shipped.
        journal=self._journal();journal['entries']['S_Glow']['appliedHex']=(b'\xff'+b'\0'*47).hex()
        self._assert_bad_journal(json.dumps(journal).encode())
        journal=self._journal();journal['entries']['S_Glow']['originalHex']=(b'\xff'+b'\0'*47).hex()
        self.session.journal_path.write_text(json.dumps(journal),encoding='utf8');self.assertEqual(self.session.journal()['entries']['S_Glow']['originalHex'],'ff'+'00'*47)
    def test_names_shipped_in_another_encoding_apply_and_restore_byte_for_byte(self):
        # A plugin with GBK names in a UTF-8 build: shown as AE shows them, renamed, and restored to the exact bytes.
        gbk=('键控与混合'.encode('gbk')+b'\0').ljust(48,b'\0');self.process.memory[1016]=gbk
        self.assertEqual(FakeRegistry(self.process).records()[0]['displayName'],'键控与混合'.encode('gbk').decode('latin-1'))
        result=self.session.apply({'S_Glow':'辉光'});self.assertEqual(result['applied'],{'S_Glow':'辉光'})
        self.assertEqual(self.process.memory[1016],live.encode_name('辉光'))
        self.assertEqual(self.session.restore()['restored'],{'S_Glow':'键控与混合'.encode('gbk').decode('latin-1')})
        self.assertEqual(self.process.memory[1016],gbk);self.assertEqual(self.session.journal()['entries'],{})
    def test_hex_requires_exact_length_and_hex_characters(self):
        for value in ('00'*49,'GG'*48,'00 '*32,0,None):
            for field in ('originalHex','appliedHex'):
                with self.subTest(value=value,field=field):
                    journal=self._journal();journal['entries']['S_Glow'][field]=value
                    self._assert_bad_journal(json.dumps(journal).encode())
    def test_invalid_journal_structure_is_preserved_without_writes(self):
        cases=[('schemaVersion',value) for value in (None,0,2,'1',True,1.0)]+[('entries',value) for value in (None,[],{'S_Glow':[]})]
        cases += [(field,value) for field in ('pid','creationTime') for value in (None,'123',True,123.0)]
        for field,value in cases:
            with self.subTest(field=field,value=value):
                journal=self._journal();journal[field]=value
                self._assert_bad_journal(json.dumps(journal).encode())
        for field in ('schemaVersion','entries','pid','creationTime'):
            with self.subTest(missing=field):
                journal=self._journal();del journal[field]
                self._assert_bad_journal(json.dumps(journal).encode())
        for field in ('originalName','newName'):
            for value in (None,123,[],True):
                with self.subTest(field=field,value=value):
                    journal=self._journal();journal['entries']['S_Glow'][field]=value
                    self._assert_bad_journal(json.dumps(journal).encode())
            with self.subTest(missing=field):
                journal=self._journal();del journal['entries']['S_Glow'][field]
                self._assert_bad_journal(json.dumps(journal).encode())
    def test_name_bytes_only_decode_before_nul(self):
        raw=b'A\0'+b'\xff'*46
        self.assertEqual(live._name_bytes(raw.hex().upper()),raw)
    def test_utf8_capacity_and_control_characters(self):
        self.assertEqual(len(live.encode_name('辉'*15)),48)
        for value in ['辉'*16,'a\0b','a\nb','', ' '*3]:
            with self.assertRaises(ValueError):live.encode_name(value)
    def test_verification_mismatch_restores_bytes_and_journal(self):
        before=self.process.memory.copy();write=self.process.write_buffer
        def interfere(address,value):
            # Another program renames a different effect while ours is being written.
            write(address,value);self.process.memory[2016]=live.encode_name('External')
        with patch.object(self.process,'write_buffer',side_effect=interfere):
            with self.assertRaisesRegex(RuntimeError,'更名后目录校验不一致'):self.session.apply({'S_Glow':'辉光'})
        self.assertEqual(self.process.memory[1016],before[1016])
        self.assertEqual(self.session.journal()['entries'],{})
    def test_nth_write_failure_completely_rolls_back(self):
        before=self.process.memory.copy();write=self.process.write_buffer
        for nth in (1,2):
            for partial in (False,True):
                with self.subTest(nth=nth,partial=partial):
                    calls=0
                    def fail(address,value):
                        nonlocal calls
                        calls+=1
                        if calls==nth:
                            if partial:write(address,value)
                            raise RuntimeError('模拟第 N 次写入失败')
                        write(address,value)
                    with patch.object(self.process,'write_buffer',side_effect=fail):
                        with self.assertRaisesRegex(RuntimeError,'已完全回滚'):
                            self.session.apply({'S_Glow':'辉光','tc Shine':'闪耀'})
                    self.assertEqual(self.process.memory,before)
                    self.assertEqual(self.session.journal(),{'schemaVersion':1,'entries':{}})
    def test_complete_rollback_restores_previous_journal(self):
        self.session.apply({'S_Glow':'辉光'})
        before=self.process.memory.copy();previous=self.session.journal()
        write=self.process.write_buffer
        def fail(address,value):
            if address==2016 and value==live.encode_name('闪耀'):raise RuntimeError('模拟第二字段写入失败')
            write(address,value)
        with patch.object(self.process,'write_buffer',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'已完全回滚'):self.session.apply({'S_Glow':'柔光','tc Shine':'闪耀'})
        self.assertEqual(self.process.memory,before);self.assertEqual(self.session.journal(),previous)
    def test_failed_rollback_keeps_records_and_continues_other_fields(self):
        before=self.process.memory.copy();target=FakeRegistry.target;write=self.process.write_buffer
        for failure in ('target','guard','address','write'):
            with self.subTest(failure=failure):
                calls=0
                def find(registry,key):
                    nonlocal calls
                    row=target(registry,key)
                    if key=='tc Shine':
                        calls+=1
                        if calls==1:
                            if failure=='target':raise RuntimeError('模拟目标查询失败')
                            if failure=='guard':row['guard']=b'changed-identity'
                            if failure=='address':row['address']=3000
                    return row
                def fail(address,value):
                    if failure=='write' and address==2016 and value==before[2016]:raise RuntimeError('模拟回滚写入失败')
                    write(address,value)
                    # Both fields are written, then another program changes S_Glow: the final verification fails and rolls back.
                    if address==2016 and value==live.encode_name('闪耀'):self.process.memory[1016]=live.encode_name('External')
                with patch.object(FakeRegistry,'target',find),patch.object(self.process,'write_buffer',side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError,'部分字段未能回滚，记录已保留'):
                        self.session.apply({'S_Glow':'辉光','tc Shine':'闪耀'})
                self.assertEqual(self.process.memory[1016],before[1016]);self.assertEqual(self.process.memory[2016],live.encode_name('闪耀'))
                journal=self.session.journal();self.assertEqual(set(journal['entries']),{'tc Shine'})
                self.assertEqual(journal['entries']['tc Shine']['originalHex'],before[2016].hex())
                self.assertEqual(journal['entries']['tc Shine']['appliedHex'],self.process.memory[2016].hex())
                self.assertEqual(self.session.restore()['restored'],{'tc Shine':'Shine'})
                self.assertEqual(self.process.memory,before)
    def test_partial_rollback_preserves_original_bytes_on_reapply(self):
        self.session.apply({'S_Glow':'辉光'})
        previous=self.session.journal();write=self.process.write_buffer
        def fail(address,value):
            if address==2016:raise RuntimeError('模拟第二字段写入失败')
            if value==live.encode_name('辉光'):raise RuntimeError('模拟第一字段回滚失败')
            write(address,value)
        with patch.object(self.process,'write_buffer',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'部分字段未能回滚，记录已保留'):self.session.apply({'S_Glow':'柔光','tc Shine':'闪耀'})
        journal=self.session.journal()
        self.assertEqual(journal['entries']['S_Glow']['originalHex'],previous['entries']['S_Glow']['originalHex'])
        self.assertEqual(journal['entries']['S_Glow']['appliedHex'],live.encode_name('柔光').hex())
        self.session.restore()
        self.assertEqual(self.process.memory,{1016:live.encode_name('S_Glow'),2016:live.encode_name('Shine')})
    def test_partial_rollback_retains_only_failed_and_previous_entries(self):
        write=self.process.write_buffer
        for previous_entry in (False,True):
            with self.subTest(previous_entry=previous_entry):
                if previous_entry:
                    self.session.apply({'tc Shine':'闪耀'})
                previous=self.session.journal()
                def fail(address,value):
                    if value==live.encode_name('辉光'):
                        write(address,value);raise RuntimeError('模拟写后回读失败')
                    raise RuntimeError('模拟回滚失败')
                with patch.object(self.process,'write_buffer',side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError,'部分字段未能回滚，记录已保留'):self.session.apply({'S_Glow':'辉光','tc Shine':'柔光'})
                journal=self.session.journal()
                if previous_entry:self.assertEqual(journal['entries']['tc Shine'],previous['entries']['tc Shine'])
                else:self.assertEqual(set(journal['entries']),{'S_Glow'})
                self.assertEqual(journal['entries']['S_Glow']['appliedHex'],live.encode_name('辉光').hex())
                self.session.restore()
    def test_apply_restore_keeps_other_effect_and_identity(self):
        before=self.process.memory.copy()
        self.session.apply({'S_Glow':'辉光'})
        self.assertEqual(self.process.memory[2016],before[2016])
        self.session.restore()
        self.assertEqual(self.process.memory,before)
        self.assertEqual(self.session.journal()['entries'],{})
    def test_apply_and_restore_never_touch_the_palette_menu(self):
        before=self.process.memory.copy()
        with patch.object(ae_ui,'refresh_effects',side_effect=AssertionError('palette must only refresh on request')) as refresh:
            self.assertEqual(self.session.apply({'S_Glow':'辉光','tc Shine':'闪耀'})['applied'],{'S_Glow':'辉光','tc Shine':'闪耀'})
            self.assertEqual(self.session.restore(),{'restored':{'S_Glow':'S_Glow','tc Shine':'Shine'}})
            refresh.assert_not_called()
        self.assertEqual(self.process.memory,before);self.assertEqual(self.session.journal()['entries'],{})
    def test_restore_write_failure_removes_only_restored_entries(self):
        before=self.process.memory.copy();write=self.process.write_buffer
        self.session.apply({'S_Glow':'辉光','tc Shine':'闪耀'})
        def fail(address,value):
            if address==2016:raise RuntimeError('模拟恢复第二字段失败')
            write(address,value)
        with patch.object(self.process,'write_buffer',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'模拟恢复第二字段失败'):self.session.restore()
        self.assertEqual(self.process.memory[1016],before[1016]);self.assertEqual(self.process.memory[2016],live.encode_name('闪耀'))
        self.assertEqual(set(self.session.journal()['entries']),{'tc Shine'})
        self.assertEqual(self.session.restore()['restored'],{'tc Shine':'Shine'})
        self.assertEqual(self.process.memory,before);self.assertEqual(self.session.journal()['entries'],{})
    def test_restore_revalidates_original_bytes_before_write(self):
        self.session.apply({'S_Glow':'辉光'})
        content=self.session.journal_path.read_bytes();journal=self.session.journal();unchanged=FakeRegistry.unchanged
        def check(registry,row):
            # The record changes between the first check and the write.
            journal['entries'][row['matchName']]['originalHex']='4100'
            return unchanged(registry,row)
        before=len(self.process.writes)
        with patch.object(self.session,'journal',return_value=journal),patch.object(FakeRegistry,'unchanged',check):
            with self.assertRaisesRegex(RuntimeError,'恢复记录损坏，已停止写入，原文件已保留'):self.session.restore()
        self.assertEqual(len(self.process.writes),before);self.assertEqual(self.session.journal_path.read_bytes(),content)
    def test_save_uses_pid_temporary_file_and_atomic_replace(self):
        replace=os.replace
        with patch.object(live.os,'replace',wraps=replace) as called:live._save(self.session.journal_path,{'schemaVersion':1,'entries':{}})
        temporary,path=called.call_args.args
        self.assertEqual(path,self.session.journal_path);self.assertIn('.'+str(os.getpid())+'.',temporary.name)
        self.assertFalse(temporary.exists());self.assertEqual(self.session.journal(),{'schemaVersion':1,'entries':{}})
    def test_abandoned_mutex_is_acquired_and_released(self):
        kernel=Mock();kernel.CreateMutexW.return_value=99;kernel.WaitForSingleObject.return_value=128
        with patch.object(live.C,'WinDLL',return_value=kernel):
            with live._process_lock():pass
        kernel.WaitForSingleObject.assert_called_once_with(99,0)
        kernel.ReleaseMutex.assert_called_once_with(99);kernel.CloseHandle.assert_called_once_with(99)
    def test_mutex_timeout_closes_handle_without_release(self):
        kernel=Mock();kernel.CreateMutexW.return_value=99;kernel.WaitForSingleObject.return_value=258
        with patch.object(live.C,'WinDLL',return_value=kernel):
            with self.assertRaisesRegex(RuntimeError,'另一个 AE汉化工具实例正在操作，请关闭后重试'):
                with live._process_lock():self.fail('Contended mutex was acquired')
        kernel.ReleaseMutex.assert_not_called();kernel.CloseHandle.assert_called_once_with(99)
    @unittest.skipUnless(os.name=='nt','Windows 具名互斥量测试')
    def test_subprocess_mutex_blocks_apply_and_restore_without_writes(self):
        ready=Path(self.temp.name)/'mutex-ready'
        code="import live_names as live,sys\nfrom pathlib import Path\nlive._mutex_name=sys.argv[1]\nwith live._process_lock():\n    Path(sys.argv[2]).write_text('ready')\n    sys.stdin.readline()\n"
        child=subprocess.Popen([sys.executable,'-c',code,live._mutex_name,str(ready)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=Path(live.__file__).parent)
        try:
            deadline=time.monotonic()+5
            while not ready.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(0.02)
            self.assertTrue(ready.exists(),'Child did not acquire the mutex')
            for action in (lambda:self.session.apply({'S_Glow':'辉光'}),self.session.restore):
                started=time.monotonic()
                with self.assertRaisesRegex(RuntimeError,'另一个 AE汉化工具实例正在操作，请关闭后重试'):action()
                self.assertLess(time.monotonic()-started,2)
            self.assertEqual(self.process.writes,[]);self.assertFalse(self.session.journal_path.exists())
        finally:
            try:child.communicate('\n' if child.poll() is None else None,timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.communicate(timeout=5)
        self.assertEqual(child.returncode,0)
        result=self.session.apply({'S_Glow':'辉光'})
        self.assertEqual(result['applied'],{'S_Glow':'辉光'});self.assertEqual(result['pid'],123)
    def test_foreign_name_change_is_not_overwritten(self):
        self.session.apply({'S_Glow':'辉光'})
        self.process.memory[1016]=live.encode_name('External change');before=len(self.process.writes)
        with self.assertRaises(RuntimeError):self.session.restore()
        self.assertEqual(len(self.process.writes),before)
    def test_reused_pid_does_not_restore_old_process_addresses(self):
        self.session.apply({'S_Glow':'辉光'})
        self.process.creation_time=789;before=len(self.process.writes)
        result=self.session.restore()
        self.assertTrue(result['oldProcessEnded']);self.assertEqual(len(self.process.writes),before)
    def test_scheme_apply_skips_effects_not_loaded_in_this_ae(self):
        with self.assertRaisesRegex(ValueError,'当前 AE 未加载此效果'):self.session.apply({'S_Glow':'辉光','Missing FX':'缺失'})
        self.assertEqual(self.process.writes,[])
        result=self.session.apply({'S_Glow':'辉光','Missing FX':'缺失'},skip_missing=True)
        self.assertEqual((result['applied'],result['skipped']),({'S_Glow':'辉光'},['Missing FX']))
        self.assertEqual(self.process.memory[1016],live.encode_name('辉光'))
    def test_only_missing_effects_write_nothing(self):
        result=self.session.apply({'Missing FX':'缺失'},skip_missing=True)
        self.assertEqual((result['applied'],result['skipped'],self.process.writes),({},['Missing FX'],[]))
        self.assertFalse(self.session.journal_path.exists())
    def test_different_effects_may_share_a_name_and_are_reported(self):
        # Different effects may share a name (user choice); it is written and reported with their original names.
        result=self.session.apply({'S_Glow':'光','tc Shine':'光'})
        self.assertEqual(result['applied'],{'S_Glow':'光','tc Shine':'光'});self.assertEqual(result['sharedNames'],['“光”（S_Glow / Shine）'])
        self.assertEqual(live.NameSession.journal(self.session)['entries']['tc Shine']['originalName'],'Shine')
        self.session.restore()
        # Taking the name another effect shows now (a Chinese AE's own effect) is allowed and reported too.
        result=self.session.apply({'S_Glow':'Shine'});self.assertEqual(result['sharedNames'],['“Shine”（S_Glow / Shine）'])
        self.session.restore();self.assertEqual(self.process.memory,{1016:live.encode_name('S_Glow'),2016:live.encode_name('Shine')})
    def test_effects_that_already_share_a_name_are_not_reported(self):
        # BCC ships each transition as an effect and a Premiere copy, both listed by AE under one name.
        self.process.memory[3016]=live.encode_name('Shine')
        with patch.object(FakeRegistry,'EFFECTS',FakeRegistry.EFFECTS+[(3000,'tc ShinePrTr','RG Blurs and Glows')]):
            result=self.session.apply({'tc Shine':'闪耀','tc ShinePrTr':'闪耀'})
            self.assertEqual(result['applied'],{'tc Shine':'闪耀','tc ShinePrTr':'闪耀'});self.assertEqual(result['sharedNames'],[])
            # One of the pair already renamed: its original name, from the record, still matches.
            self.session.restore(['tc ShinePrTr']);self.assertEqual(self.session.apply({'tc ShinePrTr':'闪耀'})['sharedNames'],[])
            self.assertEqual(self.session.apply({'S_Glow':'闪耀'})['sharedNames'],['“闪耀”（S_Glow / Shine）'])
    def test_effect_already_showing_target_name_is_not_rewritten(self):
        self.session.apply({'S_Glow':'辉光'})
        writes=len(self.process.writes)
        result=self.session.apply({'S_Glow':'辉光'})
        self.assertEqual((result['unchanged'],len(self.process.writes)),(['S_Glow'],writes))
        self.assertIn('S_Glow',self.session.journal()['entries'])
    def test_batch_restore_writes_journal_once(self):
        self.session.apply({'S_Glow':'辉光','tc Shine':'闪耀'})
        with patch.object(live,'_save',wraps=live._save) as saved:
            self.assertEqual(set(self.session.restore()['restored']),{'S_Glow','tc Shine'})
        self.assertEqual(saved.call_count,1);self.assertEqual(self.session.journal()['entries'],{})
    def test_restore_retains_record_when_catalog_display_readback_disagrees(self):
        self.session.apply({'S_Glow':'辉光'});original=FakeRegistry.read_record
        def mismatch(registry,address):
            row=original(registry,address)
            if registry.p.memory[address+16]==live.encode_name('S_Glow'):row['displayName']='仍然中文'
            return row
        with patch.object(FakeRegistry,'read_record',mismatch):
            with self.assertRaisesRegex(RuntimeError,'恢复回读不一致'):self.session.restore()
        self.assertIn('S_Glow',self.session.journal()['entries'])
        self.assertEqual(self.session.restore()['restored'],{'S_Glow':'S_Glow'})
    def test_dpapi_round_trip(self):
        value=b'test-only-not-a-user-key';encrypted=_crypt(value)
        self.assertNotIn(value,encrypted);self.assertEqual(_crypt(encrypted,True),value)

class ProcessSelectionTests(unittest.TestCase):
    """Only the profiled AE build is ever opened; other builds get a clear refusal."""
    def run_with(self,processes):
        with patch.object(live,'native_probe',lambda:{'processes':processes}),patch.object(live,'Process',side_effect=AssertionError('must not open')):
            with self.assertRaises(RuntimeError) as caught:live.current_process()
        return str(caught.exception)
    def test_other_build_is_named_and_never_opened(self):
        message=self.run_with([{'pid':7,'fileVersion':'18.2.1.9','imagePath':r'C:\AE\Adobe After Effects 2021\Support Files\AfterFX.exe'}])
        self.assertIn('Adobe After Effects 2021（18.2.1.9）',message);self.assertIn(live.UNSUPPORTED,message)
    def test_missing_and_multiple_processes(self):
        self.assertIn('没有找到正在运行的 AE',self.run_with([]))
        message=self.run_with([{'pid':1,'fileVersion':'26.0.0.67','imagePath':r'C:\A\Adobe After Effects 2026\Support Files\AfterFX.exe'},{'pid':2,'fileVersion':'25.1.0.1'}])
        self.assertIn('检测到多个 AE 进程',message);self.assertIn('Adobe After Effects 2026（26.0.0.67）',message);self.assertIn('AE（25.1.0.1）',message)
    def test_supported_build_is_opened(self):
        with patch.object(live,'native_probe',lambda:{'processes':[{'pid':5,'fileVersion':'26.0.0.67'}]}),patch.object(live,'Process',return_value='opened') as opened:
            self.assertEqual(live.current_process(write=True),'opened')
        opened.assert_called_once_with(5,True)

if __name__=='__main__':unittest.main()
