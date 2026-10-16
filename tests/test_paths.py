import json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import ae_paths,live_names,parameter_names,app_live

class PathTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.install=self.root/'另一个磁盘'/'Adobe After Effects 2026';self.support=self.install/'Support Files';self.support.mkdir(parents=True)
        self.exe=self.support/'AfterFX.exe';self.exe.write_bytes(b'MZtest');(self.support/'AfterFXLib.dll').write_bytes(b'fixture')
    def test_manual_location_accepts_supported_entry_points(self):
        plugins=self.support/'Plug-ins';plugins.mkdir()
        for path in (self.exe,self.install,self.support,plugins):
            result=ae_paths.locate(path);self.assertEqual(result['exe'],str(self.exe.resolve()));self.assertEqual(result['plugins'],str(plugins))
    def test_missing_or_fake_install_is_rejected(self):
        self.exe.write_bytes(b'not-an-executable')
        with self.assertRaises(ValueError):ae_paths.locate(self.exe)
        self.exe.write_bytes(b'MZtest');(self.support/'AfterFXLib.dll').unlink()
        with self.assertRaisesRegex(ValueError,'AfterFXLib'):ae_paths.locate(self.install)
        with self.assertRaises(ValueError):ae_paths.locate(self.root/'missing')
    def test_quoted_environment_path_is_resolved(self):
        with patch.dict(os.environ,{'AE_PATH_TEST':str(self.install)}):self.assertEqual(ae_paths.locate('"%AE_PATH_TEST%"')['exe'],str(self.exe.resolve()))
    def test_discovery_merges_sources_without_duplicate_install(self):
        result=ae_paths.discover([str(self.exe)],registry=[str(self.install)],running=[str(self.exe)],defaults=[self.install.parent])
        self.assertEqual(len(result),1);self.assertEqual(set(result[0]['sources']),{'手动定位','注册表','运行中的 AE','默认目录'})
    def test_missing_saved_path_does_not_block_other_discovery(self):
        result=ae_paths.discover([str(self.root/'removed')],registry=[],running=[str(self.exe)],defaults=[])
        self.assertEqual(len(result),1)
    def test_identical_install_names_have_distinct_stable_keys(self):
        other=self.root/'second'/self.install.name/'Support Files';other.mkdir(parents=True)
        (other/'AfterFX.exe').write_bytes(b'MZtest');(other/'AfterFXLib.dll').write_bytes(b'fixture')
        paths=[self.exe,other/'AfterFX.exe']
        one=ae_paths.discover(paths,registry=[],running=[],defaults=[]);two=ae_paths.discover(list(reversed(paths)),registry=[],running=[],defaults=[])
        self.assertEqual(one,two);self.assertEqual(len({p['key'] for p in one}),2)
        spaces=app_live.discover_spaces(one,lambda path:'26.0.0.67',self.root/'data')
        self.assertTrue(all(s.live for s in spaces));self.assertNotEqual(spaces[0].folder,spaces[1].folder)
    def test_remember_round_trip_and_duplicate(self):
        path=self.root/'paths.json';ae_paths.remember(path,self.exe);ae_paths.remember(path,self.install)
        self.assertEqual(ae_paths.read_saved(path),([str(self.exe.resolve())],''))
    def test_bad_saved_record_is_preserved_and_not_overwritten(self):
        path=self.root/'paths.json';path.write_text('{bad',encoding='utf8')
        self.assertTrue(ae_paths.read_saved(path)[1])
        with self.assertRaises(ValueError):ae_paths.remember(path,self.exe)
        self.assertEqual(path.read_text(encoding='utf8'),'{bad')
    def test_foreign_json_shape_is_rejected(self):
        path=self.root/'paths.json'
        for value in ([],{'schemaVersion':1,'paths':[3]},{'schemaVersion':2,'paths':[]}):
            path.write_text(json.dumps(value),encoding='utf8');self.assertTrue(ae_paths.read_saved(path)[1])
    def test_unknown_version_never_becomes_live(self):
        item=ae_paths.locate(self.exe)
        for version in ('18.2.1.8','25.2.0.1','27.0.0.1','unknown'):
            report=ae_paths.compatibility(item,lambda path:version)
            self.assertFalse(report['effects']);self.assertFalse(report['parameters']);self.assertFalse(report['hostVerified'])
            self.assertNotIn('parameters.json',json.dumps(report))
    def test_matching_version_with_wrong_module_still_refused(self):
        report=ae_paths.compatibility(ae_paths.locate(self.exe),lambda path:'26.0.0.67')
        self.assertFalse(report['effects']);self.assertFalse(report['parameters'])
    def test_static_hash_match_is_not_host_acceptance(self):
        for name in ('FLT.dll','TDB.dll'):(self.support/name).write_bytes(b'fixture')
        digest=__import__('hashlib').sha256(b'fixture').hexdigest()
        import ae_profiles
        from dataclasses import replace
        profile=replace(ae_profiles.PROFILES['26.0.0.67'],flt_sha=digest,tdb_sha=digest)
        with patch.dict(ae_profiles.PROFILES,{'26.0.0.67':profile}):report=ae_paths.compatibility(ae_paths.locate(self.exe),lambda path:'26.0.0.67')
        self.assertTrue(report['parameters']);self.assertFalse(report['hostVerified'])
    def test_no_install_does_not_enable_writer(self):
        self.assertFalse(app_live.discover_spaces([],lambda path:'26.0.0.67',self.root)[0].live)

class ProcessPathTests(unittest.TestCase):
    def process(self,path):return {'pid':3,'imagePath':path,'fileVersion':'26.0.0.67'}
    def test_different_running_install_never_opens_process(self):
        with patch.object(live_names,'native_probe',return_value={'processes':[self.process(r'C:\Other\AfterFX.exe')]}),patch.object(live_names,'Process') as opened:
            with self.assertRaisesRegex(RuntimeError,'所选路径不一致'):live_names.current_process(True,r'D:\AE\AfterFX.exe')
        opened.assert_not_called()
    def test_opened_image_is_rechecked_and_closed_on_change(self):
        target=SimpleNamespace(image=r'C:\Different\AfterFX.exe',close=Mock())
        with patch.object(live_names,'native_probe',return_value={'processes':[self.process(r'C:\AE\AfterFX.exe')]}),patch.object(live_names,'Process',return_value=target):
            with self.assertRaisesRegex(RuntimeError,'路径或版本发生变化'):live_names.current_process(True,r'C:\AE\AfterFX.exe')
        target.close.assert_called_once()
    def test_version_read_failure_closes_handle(self):
        path=r'C:\AE\AfterFX.exe';target=SimpleNamespace(image=path,close=Mock())
        with patch.object(live_names,'native_probe',return_value={'processes':[self.process(path)]}),patch.object(live_names,'Process',return_value=target),patch.object(live_names,'file_version',side_effect=OSError('missing')):
            with self.assertRaises(OSError):live_names.current_process(True,path)
        target.close.assert_called_once()
    def test_matching_target_is_opened(self):
        path=r'C:\AE\AfterFX.exe';target=SimpleNamespace(image=path,close=Mock())
        with patch.object(live_names,'native_probe',return_value={'processes':[self.process(path)]}),patch.object(live_names,'Process',return_value=target),patch.object(live_names,'file_version',return_value='26.0.0.67'):
            self.assertIs(live_names.current_process(True,path),target)
        target.close.assert_not_called()
    def test_sessions_forward_selected_image(self):
        with tempfile.TemporaryDirectory() as temp:
            for module,cls in ((live_names,live_names.NameSession),(parameter_names,parameter_names.ParameterSession)):
                with patch.object(module,'data_root',return_value=Path(temp)),patch.object(module,'current_process') as current:
                    session=cls();session.expected_image=r'D:\AE\AfterFX.exe';session.process()
                    self.assertEqual(current.call_args.kwargs['expected_image'],session.expected_image)

if __name__=='__main__':unittest.main()
