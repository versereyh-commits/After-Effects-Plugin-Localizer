from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json,tempfile,threading,unittest
from unittest.mock import patch,Mock
from ae_profiles import PROFILES,LOCAL_PROFILES,get_profile,runtime_allowed,trial_adapter
from local_adapters import AUTOMATED,NATIVE,save_validated,load_validated,certificate_path
from adapter_validation import ValidationRun,recover_pending

class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.profile=replace(PROFILES['26.0.0.67'],version='99.0.0.1',native_dynamic=True,enabled=False)
        root=Path(self.temp.name)/'ae-bridge'/'adapter-qa'/'run';self.projects=(root/'QA.aep',root/'QA_second.aep')
    def test_trial_is_worker_local_and_does_not_register_a_build(self):
        self.assertFalse(runtime_allowed(self.profile.version));observed=[]
        with trial_adapter(self.profile,self.projects):
            self.assertIs(get_profile(self.profile.version),self.profile);self.assertTrue(runtime_allowed(self.profile.version))
            thread=threading.Thread(target=lambda:observed.append(runtime_allowed(self.profile.version)));thread.start();thread.join()
        self.assertEqual(observed,[False]);self.assertFalse(runtime_allowed(self.profile.version));self.assertNotIn(self.profile.version,PROFILES)
    def test_trial_rejects_non_qa_paths(self):
        for projects in ((),(Path(self.temp.name)/'user.aep',Path(self.temp.name)/'other.aep')):
            with self.assertRaises(ValueError):
                with trial_adapter(self.profile,projects):pass
    def test_trial_resets_after_failure(self):
        with self.assertRaises(RuntimeError):
            with trial_adapter(self.profile,self.projects):raise RuntimeError('failed')
        self.assertFalse(runtime_allowed(self.profile.version))

class CertificateTests(unittest.TestCase):
    def setUp(self):
        TrialTests.setUp(self);self.profile=replace(self.profile,codec_sha='c'*64);self.root=Path(self.temp.name)/'certificates';self.exe=Path(self.temp.name)/'AfterFX.exe'
        resolver=SimpleNamespace(modules={'FLT.dll':{'sha256':self.profile.flt_sha},'TDB.dll':{'sha256':self.profile.tdb_sha}},version=self.profile.version,complete_static=lambda version:replace(self.profile,registry_vector=0,encoding='utf8'),close=lambda:None)
        p=patch('local_adapters.Resolver',return_value=resolver);p.start();self.addCleanup(p.stop)
        p=patch('local_adapters._crypt',side_effect=lambda data,decrypt=False:data);p.start();self.addCleanup(p.stop)
        self.checks={k:True for k in AUTOMATED};self.native={k:True for k in NATIVE};self.addCleanup(lambda:LOCAL_PROFILES.pop(self.profile.version,None))
    def save(self,pending=0):return save_validated(self.exe,self.profile,self.checks,self.native,pending,self.root)
    def test_complete_validation_registers_only_local_profile(self):
        stored=self.save();self.assertTrue(stored.enabled);self.assertNotIn(stored.version,PROFILES);self.assertIn(stored.version,LOCAL_PROFILES)
        self.assertIsNotNone(load_validated(self.exe,self.root))
    def test_pending_restore_prevents_certificate(self):
        with self.assertRaises(ValueError):self.save(pending=1)
        self.assertFalse(self.root.exists())
    def test_each_required_native_observation_is_mandatory(self):
        self.native['chineseSearchAdd']=False
        with self.assertRaises(ValueError):self.save()
    def test_partial_automated_checks_cannot_enable(self):
        self.checks['translatedRender']=False
        with self.assertRaises(ValueError):self.save()
    def test_plain_candidate_is_not_a_certificate(self):
        path=certificate_path(self.profile,self.root);path.parent.mkdir();path.write_text(json.dumps({'kind':'ae-adapter-analysis','runtimeEnabled':True}))
        with self.assertRaises(ValueError):load_validated(self.exe,self.root)
    def test_modified_structure_is_recomputed_and_rejected(self):
        self.save()
        with patch('local_adapters.Resolver') as factory:
            factory.return_value.modules={'FLT.dll':{'sha256':self.profile.flt_sha},'TDB.dll':{'sha256':self.profile.tdb_sha}}
            factory.return_value.version=self.profile.version;factory.return_value.complete_static.return_value=replace(self.profile,manager=self.profile.manager+8)
            with self.assertRaises(ValueError):load_validated(self.exe,self.root)

class ValidationSafetyTests(unittest.TestCase):
    def run_object(self):
        run=ValidationRun.__new__(ValidationRun);run.profile=SimpleNamespace();run.effects=Mock();run.parameters=Mock();run.bridge=SimpleNamespace(assert_owned=Mock());run.held=True;run.review_ready=False
        run.effects.journal.return_value={'entries':{}};run.parameters.journal.return_value={'entries':{}};return run
    def test_no_pending_names_need_no_host_actions(self):
        run=self.run_object();run.restore();run.bridge.assert_owned.assert_not_called();self.assertFalse(run.held)
    def test_finish_cannot_skip_automatic_validation(self):
        run=self.run_object()
        with self.assertRaises(RuntimeError):run.finish({key:True for key in NATIVE})
    def test_finish_cannot_skip_native_validation(self):
        run=self.run_object();run.review_ready=True;run.checks={key:True for key in AUTOMATED}
        with self.assertRaises(RuntimeError):run.finish({})
        run.bridge.assert_owned.assert_not_called()
    def test_model_config_is_rechecked_without_trusting_evidence_labels(self):
        run=self.run_object();run.report={'kind':'ae-adapter-analysis','version':'99','modules':{'FLT.dll':'a'},'candidateReadConfig':{'manager':123}}
        resolver=SimpleNamespace(version='99',modules={'FLT.dll':{'sha256':'a'}},getters=lambda:[])
        with self.assertRaises(ValueError):run._verify_analysis(resolver)

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'ae-bridge';self.folder=self.root/'adapter-qa'/'run';self.folder.mkdir(parents=True);self.exe=Path(self.temp.name)/'AfterFX.exe'
        (self.folder/'validation-state.json').write_text(json.dumps({'exe':str(self.exe)}),encoding='utf8')
        p=patch('adapter_validation.bridge_root',return_value=self.root);p.start();self.addCleanup(p.stop)
    def test_empty_journals_do_not_load_modules_or_contact_host(self):
        (self.folder/'effects-session.json').write_text(json.dumps({'entries':{}}))
        with patch('adapter_validation.Resolver') as resolver:self.assertIsNone(recover_pending(self.exe));resolver.assert_not_called()
    def test_corrupt_journal_stops_without_overwriting(self):
        path=self.folder/'effects-session.json';path.write_text('{"entries":[]}')
        with self.assertRaises(RuntimeError):recover_pending(self.exe)
        self.assertEqual(path.read_text(),'{"entries":[]}')
    def test_new_validation_cannot_hide_an_older_pending_run(self):
        with patch('adapter_validation.recover_pending',return_value=object()),patch('adapter_validation.HostBridge') as host:
            with self.assertRaises(RuntimeError):ValidationRun(self.exe,{})
            host.assert_not_called()
    def test_access_denied_does_not_count_as_ended_host(self):
        run=ValidationRun.__new__(ValidationRun);run.bridge=SimpleNamespace(identity=(123,456));error=OSError();error.winerror=5
        with patch('win_process.Process',side_effect=error):
            with self.assertRaises(RuntimeError):run.original_ended()
    def test_missing_original_pid_counts_as_ended(self):
        run=ValidationRun.__new__(ValidationRun);run.bridge=SimpleNamespace(identity=(123,456));error=OSError();error.winerror=87
        with patch('win_process.Process',side_effect=error):self.assertTrue(run.original_ended())
