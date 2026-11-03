import copy,json,tempfile,unittest,struct,ctypes as C
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import popup_names as pop
from parameter_names import identity

ORIGINAL='Frame Analysis|Temporal Smooth RGB|Temporal Smooth YCC'
VALUES=['逐帧分析','RGB时域平滑','YCC时域平滑']

class TextTests(unittest.TestCase):
    def test_equal_byte_count_survives_copy_on_write(self):
        text=pop.pack_translation(ORIGINAL,VALUES)
        self.assertEqual(len(text.encode()),len(ORIGINAL.encode()))
        self.assertEqual([s.rstrip() for s in text.split('|')],VALUES)
    def test_long_text_counts_utf8_not_characters(self):
        with self.assertRaises(ValueError):pop.pack_translation('A|B',['中文','中文'])
    def test_legacy_encoding_budget_and_padding(self):
        text=pop.pack_translation('Method One|Method Two',['方式一','方式二'],'gbk')
        self.assertEqual(len(text.encode('gbk')),len('Method One|Method Two'.encode('gbk')))
        self.assertEqual([x.rstrip() for x in text.split('|')],['方式一','方式二'])
        with self.assertRaises(UnicodeEncodeError):pop.pack_translation('Method One|Method Two',['😀','方式二'],'gbk')
    def test_count_control_and_separator_guards(self):
        for value in (['一项'],['a|b','x','y'],['\0','x','y'],['','x','y']):
            with self.subTest(value=value),self.assertRaises(ValueError):pop.pack_translation(ORIGINAL,value)
        with self.assertRaises(ValueError):pop.pack_translation('One|(-|Two',['一','分隔','二'])
    def test_separator_is_preserved(self):self.assertIn('|(-|',pop.pack_translation('One|(-|Two',['一','(-','二']))
    def test_placeholders_are_preserved(self):
        with self.assertRaises(ValueError):pop.pack_translation('Value %d|Other',['值','其他'])
        self.assertIn('%d',pop.pack_translation('Value %d|Other',['值%d','其他']))

class RecordTests(unittest.TestCase):
    def setUp(self):
        self.raw=bytearray(0x3c);struct.pack_into('<I',self.raw,0,7);struct.pack_into('<I',self.raw,0x24,0xa42);struct.pack_into('<IHHQ',self.raw,0x2c,1,3,1,0x2000)
        self.text=ORIGINAL.encode()+b'\0';self.protect=4;self.memory_type=0x20000
        case=self
        class Query:
            def __call__(self,handle,address,out,size):
                m=C.cast(out,C.POINTER(pop.MBI)).contents;m.BaseAddress=0x2000;m.RegionSize=0x1000;m.State=0x1000;m.Protect=case.protect;m.Type=case.memory_type;return size
        def read(address,size):
            if address==0x103c:return bytes(self.raw)
            return (self.text+b'\0'*8192)[address-0x2000:address-0x2000+size]
        self.p=SimpleNamespace(k=SimpleNamespace(VirtualQueryEx=Query()),handle=1,read=read);self.row={'address':0x1000}
    def test_type_count_value_and_fields(self):
        r=pop.popup_record(self.p,self.row);self.assertEqual((r['text'],r['flags'],r['default']),(ORIGINAL,0xa42,1))
    def test_other_type_and_invalid_number_are_ignored(self):
        struct.pack_into('<I',self.raw,0,6);self.assertIsNone(pop.popup_record(self.p,self.row))
        struct.pack_into('<I',self.raw,0,7);struct.pack_into('<I',self.raw,0x2c,9);self.assertIsNone(pop.popup_record(self.p,self.row))
    def test_readonly_or_image_memory_is_not_eligible(self):
        self.protect=2;self.assertIsNone(pop.popup_record(self.p,self.row));self.protect=4;self.memory_type=0x1000000;self.assertIsNone(pop.popup_record(self.p,self.row))
    def test_malformed_utf8_or_count_is_rejected(self):
        self.text=b'\xff|A|B\0';self.assertIsNone(pop.popup_record(self.p,self.row));self.text=b'A|B\0';self.assertIsNone(pop.popup_record(self.p,self.row))

class ScanCostTests(unittest.TestCase):
    def test_records_ask_for_what_each_step_needs(self):
        calls=[]
        catalog=SimpleNamespace(rows=lambda rescan=False,progress=None,full=False:calls.append((rescan,full)) or [])
        session=pop.PopupSession(Path(tempfile.gettempdir())/'unused-popup-session.json')
        session._records(None,catalog,{});session._records(None,catalog,{},full=True);session._records(None,catalog,{},full=True,fresh=True)
        self.assertEqual(calls,[(False,False),(False,True),(True,True)])

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'popup-session.json'
        self.timings=[];self.session=pop.PopupSession(self.path);self.p=SimpleNamespace(pid=3,creation_time=4,image=str(Path('AfterFX.exe').absolute()),ensure_alive=lambda:None)
        self.profile=SimpleNamespace(flt_sha=pop.HASHES[0],tdb_sha=pop.HASHES[1],codec_sha=pop.HASHES[2],encoding='utf8')
        self.key=identity('BCC VR Flicker Fixer','Method');self.rows=[dict(id=self.key,effectMatchName='BCC VR Flicker Fixer',name='Method',pfName='Method',text=ORIGINAL,address=n,guid='ab'*16,parent=100,pointer=n+50,flags=0xa42,default=1,value=1) for n in (1000,2000)]
        self.calls=[];self.token='project';self.shape=1
        def host(p):
            pop._project_context[(3,4)]=self.token
            return {self.key:['Method']*self.shape} if self.shape else {}
        def run(p,ops,**kw):
            # Persist originals before any write, without native addresses.
            journal=self.session.journal();self.assertIn(self.key,journal['entries']);self.assertNotIn('address',json.dumps(journal));self.calls.append(copy.deepcopy(ops))
            for op in ops:
                row=next(r for r in self.rows if hex(r['address'])==op['address']);self.assertEqual(row['text'],op['before']);row['text']=op['after']
            return {'changed':len(ops)}
        replacements=[('current_process',lambda **kw:nullcontext(self.p)),('_process_lock',lambda:nullcontext()),('ParameterCatalog',lambda p:SimpleNamespace(profile=self.profile)),('file_version',lambda p:pop.BUILD),('_host_names',host),('running_pids',lambda:{3}),('query',lambda pid:(self.p.image,self.p.creation_time)),('run_native',run),('record_read_timing',lambda metrics:self.timings.append(metrics))]
        for name,value in replacements:
            h=patch.object(pop,name,value);h.start();self.addCleanup(h.stop)
        self.record_patch=patch.object(self.session,'_records',lambda *a,**k:copy.deepcopy(self.rows));self.record_patch.start();self.addCleanup(self.record_patch.stop)
    def binding(self):return self.session.read('project')
    def apply(self):return self.session.apply({self.key:VALUES},self.binding())
    def test_reader_shares_the_read_and_reports_failure_instead_of_raising(self):
        catalog=SimpleNamespace(profile=self.profile);pop._project_context[(3,4)]='project'
        info=self.session.reader('project')(self.p,catalog,{self.key:['Method']},{})
        self.assertEqual(info['popups']['projectToken'],'project');self.assertEqual(info['popups']['parameters'][0]['id'],self.key)
        with patch.object(self.session,'_records',side_effect=OSError('读不到')):self.assertEqual(self.session.reader('project')(self.p,catalog,{self.key:['Method']},{}),{'popupError':'读不到'})
    def test_only_a_restore_scans_again(self):
        scans=[]
        def records(*a,full=False,fresh=False,**k):scans.append((full,fresh));return copy.deepcopy(self.rows)
        with patch.object(self.session,'_records',records):
            binding=self.binding();self.assertEqual(scans,[(False,False)]);scans.clear()
            self.session.apply({self.key:VALUES},binding);self.assertEqual(scans,[(False,False),(False,False)]);scans.clear()
            self.session.restore();self.assertEqual(scans,[(True,True),(False,False)])
    def test_too_few_copies_in_the_structure_asks_for_all_of_memory(self):
        binding=self.binding();self.shape=2;found=[self.rows[0]];scans=[]
        def records(*a,full=False,fresh=False,**k):
            scans.append(full)
            return copy.deepcopy(self.rows if full or len(scans)>2 else found)
        with patch.object(self.session,'_records',records):self.session.apply({self.key:VALUES},binding)
        self.assertEqual(scans,[False,True,False])
    def test_a_write_logs_where_the_time_went_without_names(self):
        self.apply();row=self.timings[-1]
        self.assertEqual((row['event'],row['popups'],row['operations']),('popupWrite',1,len(self.rows)))
        self.assertTrue({'structureMs','scanMs','nativeMs','readbackMs'}<=set(row));self.assertNotIn(self.key,str(row))
    def test_readback_still_catches_a_copy_left_unwritten(self):
        # The read back after the write uses cached structures but live text: a copy still holding the old text stops it.
        rows=self.rows
        def run(p,ops,**kw):
            for op in ops[1:]:next(r for r in rows if hex(r['address'])==op['address'])['text']=op['after']
            return {'changed':len(ops)-1}
        with patch.object(pop,'run_native',run):
            with self.assertRaisesRegex(RuntimeError,'回读不一致'):self.apply()
        self.assertIn(self.key,self.session.journal()['entries'])
    def test_apply_restore_keeps_values_and_tracks_undo_history(self):
        self.assertEqual(self.apply()['parameters'],1);self.assertTrue(all(r['value']==1 for r in self.rows))
        self.assertEqual(self.session.restore()['parameters'],1);self.assertEqual({r['text'] for r in self.rows},{ORIGINAL})
        entry=self.session.journal()['entries'][self.key];self.assertEqual(entry['state'],'restored');self.assertTrue(entry['variants'])
    def test_undo_after_restore_can_be_restored_again(self):
        self.apply();text=self.rows[0]['text'];self.session.restore()
        for r in self.rows:r['text']=text
        self.session.restore();self.assertEqual({r['text'] for r in self.rows},{ORIGINAL})
    def test_retranslation_retains_all_prior_variants(self):
        self.apply();first=self.rows[0]['text'];self.session.apply({self.key:['帧分析','RGB平滑','YCC平滑']},self.binding())
        self.rows[0]['text']=first;self.session.restore();self.assertEqual({r['text'] for r in self.rows},{ORIGINAL})
    def test_external_conflict_never_writes(self):
        self.apply();self.rows[0]['text']='Other|Different|External';count=len(self.calls)
        with self.assertRaises(RuntimeError):self.session.restore()
        self.assertEqual(len(self.calls),count)
    def test_wrong_host_and_changed_project_never_write(self):
        b=self.binding();b['host']['creationTime']='0000000000000009'
        with self.assertRaises(RuntimeError):self.session.apply({self.key:VALUES},b)
        b=self.binding();self.token='changed'
        with self.assertRaises(RuntimeError):self.session.apply({self.key:VALUES},b)
        self.assertEqual(self.calls,[])
    def test_module_change_rejected(self):
        self.profile.flt_sha='0'*64
        with self.assertRaises(RuntimeError):self.binding()
    def test_other_build_uses_its_verified_parameter_profile(self):
        with patch.object(pop,'file_version',return_value='15.1.2.69'):
            self.assertEqual(len(self.binding()['parameters']),1)
    def test_invalid_codec_is_rejected(self):
        self.profile.encoding='utf16'
        with self.assertRaises(RuntimeError):self.binding()
    def test_cancelled_before_journal_or_write(self):
        with self.assertRaises(RuntimeError):self.session.apply({self.key:VALUES},self.binding(),cancelled=lambda:True)
        self.assertFalse(self.path.exists());self.assertEqual(self.calls,[])
    def test_failure_keeps_recovery_record(self):
        with patch.object(pop,'run_native',return_value={'error':'failed'}),self.assertRaises(RuntimeError):self.apply()
        self.assertEqual(self.session.journal()['entries'][self.key]['state'],'applied')
    def test_absent_parameters_keep_pending_record(self):
        self.apply();self.rows=[];self.shape=0
        self.assertEqual(self.session.restore()['pending'],1);self.assertIn(self.key,self.session.journal()['entries'])
    def test_dormant_restored_history_does_not_block_exit(self):
        self.apply();self.session.restore();self.rows=[];self.shape=0
        self.assertEqual(self.session.restore()['pending'],0)
    def test_enumeration_error_does_not_clear_recovery(self):
        self.apply();before=self.path.read_bytes()
        with patch.object(pop,'running_pids',side_effect=OSError('enumeration failed')),self.assertRaises(OSError):self.session.restore()
        self.assertEqual(self.path.read_bytes(),before)
    def test_pid_reuse_clears_old_host_without_writing_new_host(self):
        self.apply();count=len(self.calls)
        with patch.object(pop,'query',return_value=(self.p.image,99)):result=self.session.restore()
        self.assertTrue(result['oldProcessEnded']);self.assertEqual(self.session.journal()['entries'],{});self.assertEqual(len(self.calls),count)
    def test_corrupt_journal_preserved(self):
        self.path.write_text('{bad',encoding='utf8')
        with self.assertRaises(RuntimeError):self.session.journal()
        self.assertEqual(self.path.read_text(),'{bad')
    def test_empty_project_never_scans(self):
        self.shape=0
        with patch.object(self.session,'_records') as records:self.assertEqual(self.binding()['parameters'],[])
        records.assert_not_called()
    def test_mixed_options_not_offered(self):
        self.rows[1]['text']='Other|Different|External'
        self.assertEqual(self.binding()['parameters'],[])
    def test_snapshot_cannot_authorize_changed_original(self):
        b=self.binding()
        for r in self.rows:r['text']='Other|Different|External'
        with self.assertRaises(RuntimeError):self.session.apply({self.key:VALUES},b)
        self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
