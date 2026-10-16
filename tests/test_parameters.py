import copy,json,tempfile,unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import parameter_names as names
import runtime

class ParameterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.key=names.identity('CC Drizzle','CC Drizzle-0001')
        self.rows=[dict(id=self.key,effectMatchName='CC Drizzle',matchName='CC Drizzle-0001',name='Drip Rate',pfName='Drip Rate',address=i,guid='ab'*16,parent=100,value=1) for i in (1000,2000)]
        self.process=SimpleNamespace(pid=3,creation_time=4,image='AfterFX.exe')
        self.catalog=SimpleNamespace(rows=lambda **kw:copy.deepcopy(self.rows));self.calls=[]
        def run(p,operations):
            self.calls.append(copy.deepcopy(operations))
            for op in operations:
                row=next(r for r in self.rows if hex(r['address'])==op['address'])
                self.assertEqual(row['pfName'],op['before'])
                if 'beforeDisplay' in op:self.assertEqual(row['name'],op['beforeDisplay'])
                row['pfName']=op.get('afterPf',op['after']);row['name']=op['after']
            return {'changed':len(operations)}
        def host_names(p):
            result={}
            for row in self.rows:result.setdefault(row['id'],[]).append(row.get('hostName',row['name']))
            return result
        for module,attribute,value in [(names,'_host_names',host_names),(names,'data_root',lambda:self.root),(names,'current_process',lambda:nullcontext(self.process)),(names,'_process_lock',lambda:nullcontext()),(names,'ParameterCatalog',lambda p:self.catalog),(names,'run_native',run),(runtime,'native_probe',lambda:{'processes':[{'pid':3}]})]:
            handle=patch.object(module,attribute,value);handle.start();self.addCleanup(handle.stop)
        self.session=names.ParameterSession()
    def test_apply_restore_preserves_values_and_internal_ids(self):
        before=copy.deepcopy(self.rows);result=self.session.apply({self.key:'滴落率'})
        self.assertEqual(result,{'changed':2,'parameters':1});self.assertEqual({r['value'] for r in self.rows},{1})
        self.assertEqual(self.session.restore(),{'restored':1,'pending':0});self.assertEqual(self.rows,before)
        self.assertEqual(self.session.journal()['entries'],{})
    def test_equal_metadata_still_notifies_host_to_sync_stale_ui(self):
        for row in self.rows:row['pfName']='滴落率'
        self.session.apply({self.key:'滴落率'});self.assertEqual(len(self.calls[0]),2)
        self.assertEqual(self.calls[0][0]['before'],'滴落率')
        self.assertEqual(self.session.journal()['entries'][self.key]['original'],'Drip Rate')
        self.session.restore();self.assertEqual({r['name'] for r in self.rows},{'Drip Rate'})
    def test_localized_display_and_english_metadata_work_for_all_instances(self):
        for row in self.rows:row['name']='重复边缘像素';row['pfName']='Repeat Edge Pixels'
        self.session.apply({self.key:'边缘重复'})
        self.assertEqual({r['name'] for r in self.rows},{'边缘重复'})
        self.assertEqual(len(self.calls[0]),2)
        self.assertTrue(all(op['before']=='Repeat Edge Pixels' for op in self.calls[0]))
        self.assertEqual(self.session.journal()['entries'][self.key]['original'],'重复边缘像素')
        self.session.restore();self.assertEqual({r['name'] for r in self.rows},{'重复边缘像素'})
        self.assertEqual({r['pfName'] for r in self.rows},{'Repeat Edge Pixels'})
    def test_blank_pf_label_and_localized_display_restore_independently(self):
        for row in self.rows:row['name']='重复边缘像素';row['pfName']=' '
        before=copy.deepcopy(self.rows)
        self.session.apply({self.key:'边缘重复'});self.session.restore()
        self.assertEqual(self.rows,before)
    def test_already_chinese_checkbox_is_a_noop_and_has_no_restore_entry(self):
        for row in self.rows:row['name']='重复边缘像素';row['pfName']=' '
        before=copy.deepcopy(self.rows);result=self.session.apply({self.key:'重复边缘像素'})
        self.assertEqual(result['unchanged'],1);self.assertEqual(self.calls,[]);self.assertEqual(self.rows,before);self.assertEqual(self.session.journal()['entries'],{})
    def test_invalid_original_metadata_is_preserved_without_writes(self):
        self.session.apply({self.key:'滴落率'});journal=self.session.journal();journal['entries'][self.key]['originalPf']='bad\x00name';names._save(self.session.path,journal)
        before=self.session.path.read_bytes();calls=len(self.calls)
        with self.assertRaises(ValueError):self.session.restore()
        self.assertEqual(self.session.path.read_bytes(),before);self.assertEqual(len(self.calls),calls)
    def test_already_localized_candidate_does_not_block_other_translations(self):
        localized=names.identity('ADBE Gaussian Blur 2','ADBE Gaussian Blur 2-0003')
        originals=copy.deepcopy(self.rows)
        self.rows[:]=[dict(row,id=localized,name='重复边缘像素',pfName=' ',matchName='ADBE Gaussian Blur 2-0003',effectMatchName='ADBE Gaussian Blur 2') for row in originals]
        self.rows+= [dict(row,address=row['address']+3000) for row in originals]
        self.session.apply({localized:'重复边缘像素',self.key:'滴落率'})
        self.assertEqual(len(self.calls[0]),2)
        self.assertNotIn(localized,self.session.journal()['entries'])
        self.assertEqual({r['pfName'] for r in self.rows if r['id']==localized},{' '})
        self.assertEqual({r['name'] for r in self.rows if r['id']==self.key},{'滴落率'})
    def test_late_preflight_conflict_does_not_change_prior_journal_entry(self):
        self.session.apply({self.key:'滴落率'})
        second=names.identity('other','param')
        self.rows.append(dict(self.rows[0],id=second,address=5000,pfName='Foreign',name='Original'))
        journal=self.session.journal();journal['entries'][second]={'original':'Original','applied':'Applied'};names._save(self.session.path,journal)
        before=self.session.path.read_bytes();calls=len(self.calls)
        with self.assertRaisesRegex(RuntimeError,'外部修改'):self.session.apply({self.key:'雨滴',second:'参数'})
        self.assertEqual(self.session.path.read_bytes(),before);self.assertEqual(len(self.calls),calls)
    def test_original_comes_from_ae_not_a_stale_cache(self):
        for row in self.rows:row['name']=row['pfName']='滴落率';row['hostName']='Drip Rate'
        with patch.object(names,'_host_names',side_effect=[{self.key:['Drip Rate']},{self.key:['滴落率']}]):self.session.apply({self.key:'滴落率'})
        self.assertEqual(self.session.journal()['entries'][self.key]['original'],'Drip Rate')
    def test_every_instance_is_verified(self):
        def partial(p,ops):self.rows[0]['name']=self.rows[0]['pfName']='滴落率';return {'changed':1}
        with patch.object(names,'run_native',partial):
            with self.assertRaisesRegex(RuntimeError,'回读不一致'):self.session.apply({self.key:'滴落率'})
        self.assertIn(self.key,self.session.journal()['entries'])
    def test_foreign_display_name_is_not_overwritten(self):
        self.session.apply({self.key:'滴落率'});self.rows[0]['name']='Foreign';before=len(self.calls)
        with self.assertRaisesRegex(RuntimeError,'外部修改'):self.session.apply({self.key:'雨滴'})
        self.assertEqual(len(self.calls),before)
    def test_foreign_pf_metadata_is_not_overwritten(self):
        self.session.apply({self.key:'滴落率'});self.rows[0]['pfName']='Foreign';before=len(self.calls)
        with self.assertRaisesRegex(RuntimeError,'外部修改'):self.session.apply({self.key:'雨滴'})
        self.assertEqual(len(self.calls),before)
    def test_timeout_keeps_original_restore_record(self):
        with patch.object(names,'run_native',side_effect=TimeoutError('timeout')):
            with self.assertRaises(TimeoutError):self.session.apply({self.key:'滴落率'})
        self.assertEqual(self.session.journal()['entries'][self.key]['original'],'Drip Rate')
    def test_bad_journal_is_preserved(self):
        for value in ('{broken','[]',json.dumps({'schemaVersion':True,'entries':{}}),json.dumps({'schemaVersion':1,'entries':{'bad':{}}})):
            self.session.path.write_text(value,encoding='utf8')
            with self.assertRaises((ValueError,RuntimeError)):self.session.apply({self.key:'滴落率'})
            self.assertEqual(self.session.path.read_text(encoding='utf8'),value)
        self.assertEqual(self.calls,[])
    def test_absent_effect_keeps_pending_restore(self):
        self.session.apply({self.key:'滴落率'});self.rows=[]
        self.assertEqual(self.session.restore(),{'restored':0,'pending':1});self.assertIn(self.key,self.session.journal()['entries'])
    def test_pid_reuse_clears_old_journal_without_writes(self):
        self.session.apply({self.key:'滴落率'});before=len(self.calls);self.process.creation_time=9
        self.assertTrue(self.session.restore()['oldProcessEnded']);self.assertEqual(len(self.calls),before)
    def test_labels_enforce_utf8_capacity_and_controls(self):
        self.assertEqual(names.validate_label('  滴落率  '),'滴落率');self.assertEqual(names.validate_label('中'*10),'中'*10)
        for value in ('中'*11,'','a\nb','a\x00b',3):
            with self.assertRaises(ValueError):names.validate_label(value)
    def test_identity_round_trips_unusual_names(self):
        self.assertEqual(names.split_identity(names.identity('a"b','c/d')),['a"b','c/d'])
        for value in ('{}','[]','[1,2]','["a"]','["a",""]'):
            with self.assertRaises(ValueError):names.split_identity(value)
    def test_public_rows_never_expose_process_addresses(self):
        rows=names.public_rows(self.rows);self.assertEqual(rows[0]['instances'],2)
        self.assertTrue({'address','guid','parent','pfName'}.isdisjoint(rows[0]))
    def test_missing_parameter_preflight_writes_nothing(self):
        with self.assertRaises(ValueError):self.session.apply({names.identity('missing','p'):'参数'})
        self.assertFalse(self.session.path.exists());self.assertEqual(self.calls,[])
    def test_unrepresentable_legacy_label_is_rejected_before_journal_or_host_mutation(self):
        self.catalog.profile=SimpleNamespace(encoding='ascii')
        with self.assertRaisesRegex(ValueError,'系统编码'):self.session.apply({self.key:'滴落率'})
        self.assertFalse(self.session.path.exists());self.assertEqual(self.calls,[])

class TransportTests(unittest.TestCase):
    def test_changed_project_token_invalidates_only_its_process_cache(self):
        process=SimpleNamespace(pid=30,creation_time=40);key=(30,40);other=(31,41)
        with patch.dict(names._cache,{key:[1],other:[2]},clear=True),patch.dict(names._project_context,{key:'old'},clear=True),patch.object(names,'run_native',return_value={'projectToken':'new','parameters':[]}):
            self.assertEqual(names._host_names(process),{});self.assertNotIn(key,names._cache);self.assertEqual(names._cache[other],[2]);self.assertEqual(names._project_context[key],'new')
    def test_missing_project_identity_never_reuses_old_cache(self):
        process=SimpleNamespace(pid=30,creation_time=40)
        with patch.object(names,'run_native',return_value={'parameters':[]}):
            with self.assertRaisesRegex(RuntimeError,'工程身份'):names._host_names(process)
    def test_ascii_request_unicode_data_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'native').mkdir();(root/'native'/'ae_parameters_v8.dll').touch();(root/'scripts').mkdir()
            (root/'scripts'/'parameter_bridge.jsx').write_text((Path(names.__file__).parent/'scripts'/'parameter_bridge.jsx').read_text(encoding='utf8'),encoding='utf8')
            captured=[]
            def launch(args,**kwargs):
                path=Path(args[-1]);raw=path.read_bytes();self.assertFalse(raw.startswith(b'\xef\xbb\xbf'));source=raw.decode('ascii')
                request=json.loads(source.split('var request=',1)[1].split(',result=',1)[0]);captured.append(request)
                Path(request['output']).write_text('{"changed":1}',encoding='utf8')
                return SimpleNamespace(pid=8,poll=lambda:0,terminate=lambda:self.fail('completed relay terminated'))
            with patch.object(names,'asset_root',lambda:root),patch.object(names,'bridge_root',lambda:root/'ipc'),patch.object(names.subprocess,'Popen',launch),patch.object(names,'file_version',return_value='26.0.0.67'):
                result=names.run_native(SimpleNamespace(pid=3,image='AfterFX.exe'),[{'after':'中文"\\\u2028'}])
            self.assertEqual(result,{'changed':1});self.assertEqual(captured[0]['operations'][0]['after'],'中文"\\\u2028');self.assertEqual(list((root/'ipc').iterdir()),[])

if __name__=='__main__':unittest.main()
