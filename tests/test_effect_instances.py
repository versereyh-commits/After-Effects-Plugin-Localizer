import copy,json,tempfile,unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import effect_instances as instances
import app_live

class InstanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=SimpleNamespace(pid=123,creation_time=456,image='AfterFX.exe');self.token='project-a';self.calls=[]
        self.rows=[dict(compId=1,layerIndex=1,layerId=10,layerName='Solid',effectIndex=i,matchName='CC Drizzle',name='CC Drizzle',value=1) for i in (1,2)]
        def run(p,ops,action,**kwargs):
            if action=='instanceCatalog':return dict(projectToken=self.token,instances=copy.deepcopy(self.rows))
            self.calls.append(copy.deepcopy(ops));self.assertEqual(kwargs['project_token'],self.token)
            for op in ops:
                row=next(r for r in self.rows if instances.identity(self.token,r)==instances.identity(self.token,op))
                self.assertEqual(row['name'],op['before']);row['name']=op['after']
            return {'changed':len(ops)}
        for key,value in [('data_root',lambda:Path(self.temp.name)),('current_process',lambda **kw:nullcontext(self.p)),('_process_lock',lambda:nullcontext()),('run_native',run),('native_probe',lambda:{'processes':[{'pid':123}]})]:
            h=patch.object(instances,key,value);h.start();self.addCleanup(h.stop)
        self.session=instances.EffectInstanceSession()
    def apply(self,name='细雨'):return self.session.apply({'CC Drizzle':name},{'CC Drizzle':'CC Drizzle'})
    def test_all_default_instances_apply_restore_exactly(self):
        before=copy.deepcopy(self.rows);self.assertEqual(self.apply(),{'changed':2,'customSkipped':0})
        self.assertEqual({r['name'] for r in self.rows},{'细雨'});self.assertTrue(all(r['value']==1 for r in self.rows))
        self.assertEqual(self.session.restore(),{'restored':2,'pending':0});self.assertEqual(self.rows,before)
    def test_custom_titles_are_preserved(self):
        self.rows[1]['name']='My Rain';self.assertEqual(self.apply(),{'changed':1,'customSkipped':1})
        self.session.restore();self.assertEqual(self.rows[1]['name'],'My Rain')
    def test_repeat_apply_and_translation_revision(self):
        self.apply();self.assertEqual(self.apply()['changed'],0);self.assertEqual(len(self.calls),1)
        self.apply('小雨');self.session.restore();self.assertEqual({r['name'] for r in self.rows},{'CC Drizzle'})
    def test_new_instances_are_synchronized_on_explicit_reapply(self):
        self.apply();self.rows.append(dict(self.rows[0],effectIndex=3,name='CC Drizzle'))
        self.assertEqual(self.apply()['changed'],1)
    def test_manual_rename_after_apply_is_not_overwritten(self):
        self.apply();self.rows[0]['name']='Custom';self.assertEqual(self.apply()['customSkipped'],1)
        with self.assertRaisesRegex(RuntimeError,'手动修改'):self.session.restore()
        self.assertEqual(len(self.calls),1);self.assertEqual(len(self.session.journal()['entries']),2)
    def test_other_project_keeps_pending_records(self):
        self.apply();self.token='project-b';self.rows=[]
        self.assertEqual(self.session.restore(),{'restored':0,'pending':2});self.assertEqual(len(self.calls),1)
    def test_removed_effect_keeps_restore_record(self):
        self.apply();self.rows.pop();self.assertEqual(self.session.restore(),{'restored':1,'pending':1})
    def test_same_layer_id_moving_index_remains_restorable(self):
        self.apply();self.rows[0]['layerIndex']=4;self.rows[1]['layerIndex']=4
        self.assertEqual(self.session.restore()['restored'],2)
    def test_selected_restore_does_not_restore_other_matches(self):
        self.apply();self.assertEqual(self.session.restore(['Other']),{'restored':0,'pending':2})
    def test_write_failure_keeps_originals_on_disk(self):
        with patch.object(instances,'run_native',side_effect=[dict(projectToken=self.token,instances=self.rows),TimeoutError('expired')]):
            with self.assertRaises(TimeoutError):self.apply()
        self.assertEqual(len(self.session.journal()['entries']),2)
        self.assertEqual(self.session.restore(),{'restored':0,'pending':0})
    def test_invalid_journal_blocks_calls_and_is_preserved(self):
        self.session.path.write_text('{broken',encoding='utf8')
        with patch.object(instances,'run_native') as call:
            with self.assertRaisesRegex(RuntimeError,'记录损坏'):self.apply()
        call.assert_not_called();self.assertEqual(self.session.path.read_text(),'{broken')
    def test_duplicate_identity_blocks_write(self):
        self.rows[1]=dict(self.rows[0])
        with self.assertRaisesRegex(RuntimeError,'不唯一'):self.apply()
        self.assertEqual(self.calls,[]);self.assertFalse(self.session.path.exists())
    def test_empty_apply_does_not_contact_host(self):
        with patch.object(instances,'current_process') as host:self.assertEqual(self.session.apply({},{}),{'changed':0,'customSkipped':0})
        host.assert_not_called()
    def test_probe_error_preserves_restore_records(self):
        self.apply();before=self.session.path.read_bytes()
        with patch.object(instances,'native_probe',return_value={'error':'probe failed'}):
            with self.assertRaisesRegex(RuntimeError,'记录已保留'):self.session.restore()
        self.assertEqual(self.session.path.read_bytes(),before)
    def test_restarted_host_does_not_restore_old_project(self):
        self.apply();self.p.creation_time=999
        self.assertTrue(self.session.restore()['oldProcessEnded']);self.assertEqual(len(self.calls),1)

class ApplicationIntegrationTests(unittest.TestCase):
    def test_catalog_already_chinese_still_synchronizes_instances(self):
        catalog=Mock();catalog.journal.return_value={'entries':{'CC Drizzle':{'originalName':'CC Drizzle'}}}
        catalog.apply.return_value={'applied':{'CC Drizzle':'细雨'},'unchanged':['CC Drizzle']}
        instance=Mock();instance.apply.return_value={'changed':1,'customSkipped':0}
        window=SimpleNamespace(plugins=[],session=catalog,instance_session=instance,space=SimpleNamespace(exe='test.exe'))
        with patch.object(app_live,'with_live',side_effect=lambda result,image:result):result=app_live.Window.apply_names(window,{'CC Drizzle':'细雨'})
        instance.apply.assert_called_once_with({'CC Drizzle':'细雨'},{'CC Drizzle':'CC Drizzle'})
        self.assertEqual(result['instanceResult']['changed'],1)
    def test_partial_sync_failure_is_not_reported_as_full_success(self):
        catalog=Mock();catalog.journal.return_value={'entries':{}};catalog.apply.return_value={'applied':{'CC Drizzle':'细雨'}}
        window=SimpleNamespace(plugins=[],session=catalog,instance_session=Mock(),space=SimpleNamespace(exe='test.exe'))
        window.instance_session.apply.side_effect=RuntimeError('blocked')
        with self.assertRaisesRegex(RuntimeError,'图层效果标题未完成'):app_live.Window.apply_names(window,{'CC Drizzle':'细雨'})
    def test_restore_includes_instance_titles_without_palette_refresh(self):
        window=SimpleNamespace(session=Mock(),instance_session=Mock());window.session.restore.return_value={'restored':{}}
        window.instance_session.restore.return_value={'restored':2,'pending':0}
        with patch.object(app_live,'refresh_effects') as refresh:result=app_live.Window.restore_effect_names(window,['CC Drizzle'])
        refresh.assert_not_called();window.instance_session.restore.assert_called_once_with(['CC Drizzle']);self.assertEqual(result['instanceResult']['restored'],2)
    def test_instance_failure_does_not_prevent_catalog_restore(self):
        events=[];window=SimpleNamespace(session=Mock(),instance_session=Mock())
        window.session.restore.side_effect=lambda keys:events.append('catalog') or {'restored':{'CC Drizzle':'CC Drizzle'}}
        def instance_failure(keys):events.append('instances');raise RuntimeError('manually renamed')
        window.instance_session.restore.side_effect=instance_failure
        result=app_live.Window.restore_effect_names(window,['CC Drizzle'])
        self.assertEqual(events,['catalog','instances']);self.assertEqual(result['restored'],{'CC Drizzle':'CC Drizzle'})
        self.assertIn('图层效果标题恢复失败',result['restoreErrors'][0])
    def test_catalog_failure_is_reported_without_blocking_instance_restore(self):
        window=SimpleNamespace(session=Mock(),instance_session=Mock());window.session.restore.side_effect=RuntimeError('catalog guard')
        window.instance_session.restore.return_value={'restored':1,'pending':0}
        result=app_live.Window.restore_effect_names(window)
        self.assertEqual(result['restored'],{});self.assertEqual(result['instanceResult']['restored'],1);self.assertIn('效果目录恢复失败',result['restoreErrors'][0])
    def test_parameter_failure_does_not_prevent_catalog_or_instance_restore(self):
        window=SimpleNamespace(restore_effect_names=Mock(return_value={'restored':{'CC Drizzle':'CC Drizzle'},'instanceResult':{'restored':1,'pending':0}}),parameter_session=Mock(),close_after=False)
        window.parameter_session.restore.side_effect=RuntimeError('parameter guard')
        result=app_live.Window.restore_names(window,None)
        self.assertEqual(result['restored'],{'CC Drizzle':'CC Drizzle'});self.assertIn('参数标签恢复失败',result['restoreErrors'][0]);window.restore_effect_names.assert_called_once()
    def test_exit_stays_open_when_any_restore_stage_fails(self):
        window=SimpleNamespace(restore_effect_names=Mock(return_value={'restored':{},'restoreErrors':['catalog guard']}),parameter_session=Mock(),close_after=True)
        window.parameter_session.restore.return_value={'restored':0,'pending':0}
        with self.assertRaisesRegex(RuntimeError,'部分原名未恢复'):app_live.Window.restore_names(window,None)
    def test_all_stage_errors_are_reported(self):
        window=SimpleNamespace(session=Mock(),instance_session=Mock(),parameter_session=Mock(),close_after=False)
        window.session.restore.side_effect=RuntimeError('catalog');window.instance_session.restore.side_effect=RuntimeError('instance');window.parameter_session.restore.side_effect=RuntimeError('parameter')
        window.restore_effect_names=lambda:app_live.Window.restore_effect_names(window)
        self.assertEqual(len(app_live.Window.restore_names(window,None)['restoreErrors']),3)
