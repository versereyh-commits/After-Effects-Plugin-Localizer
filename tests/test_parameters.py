import copy,json,os,re,tempfile,unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import parameter_names as names
import cep_bridge
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
    def test_legacy_encoding_names_the_parameter_and_character(self):
        self.catalog.profile=SimpleNamespace(encoding='mbcs')
        with patch.object(runtime,'data_root',lambda:self.root),patch.object(runtime,'code_page',lambda:936):
            with self.assertRaisesRegex(ValueError,'CC Drizzle/CC Drizzle-0001“滴落率²”里的 “²”.*代码页 936.*诊断记录') as caught:self.session.apply({self.key:'滴落率²'})
        self.assertEqual(self.calls,[]);self.assertTrue(list((self.root/'diagnostics').glob('name-encoding-*.json')),str(caught.exception))
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
            with patch.object(names,'asset_root',lambda:root),patch.object(names,'bridge_root',lambda:root/'ipc'),patch.object(names.subprocess,'Popen',launch),patch.object(names,'file_version',return_value='26.0.0.67'),patch.object(cep_bridge,'_appdata',lambda:root/'appdata'):
                result=names.run_native(SimpleNamespace(pid=3,image='AfterFX.exe'),[{'after':'中文"\\\u2028'}])
            self.assertEqual(result,{'changed':1});self.assertEqual(captured[0]['operations'][0]['after'],'中文"\\\u2028');self.assertEqual(list((root/'ipc').iterdir()),[])
    def test_ae_window_state_is_kept_around_the_hand_over(self):
        import ae_ui
        events=[]
        class Keeper:
            def __init__(self,pid):events.append(('keep',pid))
            def check(self):events.append('check')
            def settle(self):events.append('settle')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'native').mkdir();(root/'native'/'ae_parameters_v8.dll').touch();(root/'scripts').mkdir()
            (root/'scripts'/'parameter_bridge.jsx').write_text('var request=@@REQUEST@@,result=0;',encoding='utf8')
            def launch(args,**kwargs):
                events.append('launch');request=json.loads(Path(args[-1]).read_text(encoding='ascii').split('var request=',1)[1].split(',result=',1)[0])
                Path(request['output']).write_text('{}',encoding='utf8');return SimpleNamespace(pid=8,poll=lambda:0)
            with patch.object(names,'asset_root',lambda:root),patch.object(names,'bridge_root',lambda:root/'ipc'),patch.object(names.subprocess,'Popen',launch),                 patch.object(names,'file_version',return_value='26.0.0.67'),patch.object(ae_ui,'WindowKeeper',Keeper),patch.object(cep_bridge,'_appdata',lambda:root/'appdata'):
                names.run_native(SimpleNamespace(pid=3,image='AfterFX.exe'),[])
        # Read before the hand-over, watched while waiting, settled after the relay is done.
        self.assertEqual(events,[('keep',3),'launch','check','settle'])

class TimeoutDiagnosticTests(unittest.TestCase):
    """A request AE never answers says why as far as can be told, and leaves a diagnostic file (no paths)."""
    def run_timeout(self,poll=lambda:0,enter=False,mismatch='',bridge=False):
        import ae_ui,win_process
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        (root/'native').mkdir();(root/'native'/'ae_parameters_v8.dll').touch();(root/'scripts').mkdir()
        (root/'scripts'/'parameter_bridge.jsx').write_text('var request=@@REQUEST@@,result=0;',encoding='utf8')
        stopped=[]
        def launch(args,**kwargs):
            if enter:(Path(args[-1]).parent/names.STARTED).write_text(enter if isinstance(enter,str) else 'start',encoding='ascii')   # AE entered the script, then stalled
            return SimpleNamespace(pid=8,poll=poll,terminate=lambda:stopped.append(8),wait=lambda timeout:0)
        class Keeper:
            def __init__(self,pid):pass
            def check(self):pass
            def settle(self):pass
        patches=[patch.object(names,'asset_root',lambda:root),patch.object(names,'bridge_root',lambda:root/'ipc'),patch.object(names.subprocess,'Popen',launch),
                 patch.object(names,'file_version',return_value='26.0.0.67'),patch.object(ae_ui,'WindowKeeper',Keeper),patch.object(cep_bridge,'_appdata',lambda:root/'appdata'),
                 patch.object(win_process,'elevation_mismatch',return_value=mismatch),patch.object(win_process,'elevated',return_value=False),
                 patch.object(cep_bridge,'connected',return_value=bridge),patch.object(cep_bridge,'withdraw',return_value=False),patch.object(cep_bridge,'CLAIM_WAIT',.05)]
        for handle in patches:handle.start();self.addCleanup(handle.stop)
        with self.assertRaises(TimeoutError) as caught:names.run_native(SimpleNamespace(pid=3,image='AfterFX.exe'),[],timeout=.3,action='selection',scope='selected')
        message=str(caught.exception);path=Path(message.split('诊断记录：',1)[1]);details=json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn(self.tmp.name,json.dumps(details,ensure_ascii=False))
        return message,details,stopped
    def test_ae_never_ran_the_relayed_script(self):
        message,details,_=self.run_timeout()
        self.assertIn('AE 没有执行转交的脚本（转交进程已退出，退出码 0）',message);self.assertIn('允许脚本写入文件和访问网络',message)
        self.assertEqual({k:details[k] for k in ('kind','action','readScope','aeVersion','route','scriptStarted','relayLaunched','relayExitCode','aeElevated','selfElevated')},
                         {'kind':'parameter-timeout','action':'selection','readScope':'selected','aeVersion':'26.0x67','route':'relay','scriptStarted':False,
                          'relayLaunched':True,'relayExitCode':0,'aeElevated':False,'selfElevated':False})
    def test_relay_still_running_is_named_and_stopped(self):
        message,details,stopped=self.run_timeout(poll=lambda:None)
        self.assertIn('转交进程仍未退出',message);self.assertIsNone(details['relayExitCode']);self.assertEqual(stopped,[8])
    def test_script_entered_but_did_not_finish(self):
        message,details,_=self.run_timeout(enter=True,mismatch='不应出现')
        self.assertIn('AE 已开始执行脚本，但 0.3 秒内没有完成，停在“进入脚本”这一步：AE 可能正忙',message);self.assertNotIn('不应出现',message)
        self.assertEqual((details['scriptStarted'],details['lastStage']),(True,'start'))
    def test_the_step_it_stopped_at_is_named(self):
        message,details,_=self.run_timeout(enter='loadHelper')
        self.assertIn('停在“加载参数辅助模块”这一步：安全软件',message);self.assertIn('AE 也可能正忙',message);self.assertEqual(details['lastStage'],'loadHelper')
        message,details,_=self.run_timeout(enter='configure')
        self.assertIn('停在“核对 AE 模块与名称编码”这一步：这个 AE 版本可能与参数辅助模块不兼容',message)
        message,details,_=self.run_timeout(enter='odd stage <x>')   # anything unexpected is not echoed into the diagnostic
        self.assertIn('“未知步骤”',message);self.assertEqual(details['lastStage'],'other')
    def test_the_bridge_script_marks_each_step(self):
        source=(Path(names.__file__).parent/'scripts'/'parameter_bridge.jsx').read_text(encoding='utf8')
        marked=set(re.findall(r"stage\('(\w+)'\)",source))
        self.assertEqual(marked,set(names.STAGES)-{'1'});self.assertLess(source.index("stage('loadHelper')"),source.index('new ExternalObject'))
    def test_different_administrator_rights_are_named(self):
        message,_,_=self.run_timeout(mismatch='AE 与本程序的管理员权限不同')
        self.assertIn('AE 没有执行脚本：AE 与本程序的管理员权限不同',message)
    def test_extension_took_the_request(self):
        message,details,_=self.run_timeout(bridge=True)
        self.assertIn('后台扩展已接收请求',message);self.assertEqual((details['route'],details['relayLaunched']),('extension',False))

class RelayPathTests(unittest.TestCase):
    """AE runs nothing handed over with -r from a path with a space (checked on AE 2024), so the script moves."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def paths(self,folder,short=lambda p:str(p),environ=None):
        with patch.object(runtime,'short_path',short),patch.object(runtime,'data_root',lambda:self.root/'data'),patch.dict(os.environ,environ or {},clear=environ is not None):
            return runtime.relay_script(folder)
    def test_request_folder_is_used_when_it_has_no_space(self):
        folder=self.root/'ae-bridge'/'abc';folder.mkdir(parents=True)
        self.assertEqual(self.paths(folder),folder/'request.jsx')
    def test_short_form_of_a_folder_with_spaces(self):
        folder=self.root/'ae O.o  2026'/'abc';folder.mkdir(parents=True)
        self.assertEqual(self.paths(folder,short=lambda p:'C:\\AEO~1\\ABC' if p==folder else str(p)),Path('C:\\AEO~1\\ABC')/'request.jsx')
    def test_folder_without_a_short_form_moves_the_script_only(self):
        folder=self.root/'ae O.o  2026'/'abc';folder.mkdir(parents=True)
        self.assertEqual(self.paths(folder),self.root/'data'/'relay'/'abc.jsx')
        # The data folder has a space too (a user name with a space): the next candidate without one.
        spaced=self.root/'John Smith';(spaced).mkdir()
        with patch.object(runtime,'short_path',lambda p:str(p)),patch.object(runtime,'data_root',lambda:spaced),patch.dict(os.environ,{'TEMP':str(spaced),'PUBLIC':str(self.root/'Public')},clear=True):
            self.assertEqual(runtime.relay_script(folder),self.root/'Public'/'AE-Localizer'/'relay'/'abc.jsx')
        with patch.object(runtime,'short_path',lambda p:str(p)),patch.object(runtime,'data_root',lambda:spaced),patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'不含空格的文件夹.*后台连接'):runtime.relay_script(folder)
    def test_run_native_hands_over_a_path_without_spaces_and_removes_it(self):
        import ae_ui
        assets=self.root/'assets';(assets/'native').mkdir(parents=True);(assets/'native'/'ae_parameters_v8.dll').touch();(assets/'scripts').mkdir()
        (assets/'scripts'/'parameter_bridge.jsx').write_text('var request=@@REQUEST@@,result=0;',encoding='utf8')
        handed=[]
        def launch(args,**kwargs):
            path=Path(args[-1]);handed.append(path);self.assertNotIn(' ',str(path))
            request=json.loads(path.read_text(encoding='ascii').split('var request=',1)[1].split(',result=',1)[0])
            self.assertIn('ae O.o  2026',request['output']);Path(request['output']).write_text('{"changed":0}',encoding='utf8')
            return SimpleNamespace(pid=8,poll=lambda:0)
        class Keeper:
            def __init__(self,pid):pass
            def check(self):pass
            def settle(self):pass
        with patch.object(names,'asset_root',lambda:assets),patch.object(names,'bridge_root',lambda:self.root/'ae O.o  2026'/'ae-bridge'),patch.object(names.subprocess,'Popen',launch),\
             patch.object(names,'file_version',return_value='26.0.0.67'),patch.object(ae_ui,'WindowKeeper',Keeper),patch.object(cep_bridge,'_appdata',lambda:self.root/'appdata'),\
             patch.object(runtime,'short_path',lambda p:str(p)),patch.object(runtime,'data_root',lambda:self.root/'data'):
            self.assertEqual(names.run_native(SimpleNamespace(pid=3,image='AfterFX.exe'),[]),{'changed':0})
        self.assertEqual(handed[0].parent,self.root/'data'/'relay');self.assertFalse(handed[0].exists())
        self.assertEqual(list((self.root/'ae O.o  2026'/'ae-bridge').iterdir()),[])

class ElevationTests(unittest.TestCase):
    def test_elevation_query(self):
        import win_process
        self.assertIn(win_process.elevated(os.getpid()),(True,False));self.assertIsNone(win_process.elevated(0xFFFFFFF0))
        with patch.object(win_process,'elevated',side_effect=lambda pid:pid==3):
            self.assertIn('AE：以管理员身份运行，本程序：普通权限',win_process.elevation_mismatch(3));self.assertEqual(win_process.elevation_mismatch(os.getpid()),'')
        with patch.object(win_process,'elevated',return_value=None):self.assertEqual(win_process.elevation_mismatch(3),'')

if __name__=='__main__':unittest.main()
