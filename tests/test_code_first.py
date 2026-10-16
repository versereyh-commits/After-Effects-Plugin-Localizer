import threading,unittest
from unittest.mock import Mock,patch
from adapter_resolver import run_static_analysis
from adapter_analysis import AnalysisCancelled

class StaticTests(unittest.TestCase):
    def resolver(self):
        r=Mock();r.version='24.2.1.2';r.modules={'FLT.dll':{'sha256':'a'},'TDB.dll':{'sha256':'b'}}
        r.evidence={'E1':{'field':'name_get','value':100,'evidenceId':'E1'}}
        r.getters.return_value=list(r.evidence.values());return r
    def test_offline_success_without_network_or_host(self):
        r=self.resolver()
        with patch('adapter_analysis.analysis_turn',side_effect=AssertionError('network')),patch('adapter_host.HostBridge',side_effect=AssertionError('host')):
            report=run_static_analysis('unused',resolver_factory=lambda _:r)
        self.assertEqual(report['state'],'candidate');self.assertEqual(report['analysisSource'],'code')
        self.assertFalse(report['runtimeEnabled']);self.assertFalse(report['hostVerified']);r.complete_static.assert_called_once_with('');r.close.assert_called_once()
        self.assertNotIn('encoding',report['candidateReadConfig']);self.assertNotIn('registry_vector',report['candidateReadConfig'])
    def test_unknown_layout_blocks_candidate(self):
        r=self.resolver();r.complete_static.side_effect=ValueError('unknown layout')
        report=run_static_analysis('unused',resolver_factory=lambda _:r)
        self.assertEqual(report['state'],'blocked');self.assertEqual(report['candidateReadConfig'],{});self.assertIn('unknown layout',report['summary'])
    def test_conflicting_fields_are_rejected(self):
        r=self.resolver();r.getters.return_value += [{'field':'name_get','value':200,'evidenceId':'E2'}]
        report=run_static_analysis('unused',resolver_factory=lambda _:r)
        self.assertEqual(report['state'],'blocked');r.complete_static.assert_not_called()
    def test_changed_modules_do_not_return_candidate(self):
        r=self.resolver();r.verify_files.side_effect=ValueError('changed')
        report=run_static_analysis('unused',resolver_factory=lambda _:r)
        self.assertEqual(report['state'],'blocked');self.assertEqual(report['candidateReadConfig'],{})
    def test_cancel_before_open(self):
        cancel=threading.Event();cancel.set();factory=Mock()
        with self.assertRaises(AnalysisCancelled):run_static_analysis('unused',cancel=cancel,resolver_factory=factory)
        factory.assert_not_called()
    def test_cancel_after_current_phase_discards_result(self):
        r=self.resolver();cancel=threading.Event();r.verify_files.side_effect=lambda:cancel.set()
        with self.assertRaises(AnalysisCancelled):run_static_analysis('unused',cancel=cancel,resolver_factory=lambda _:r)
        r.close.assert_called_once()
    def test_module_open_failure_is_a_blocked_report(self):
        report=run_static_analysis('unused',resolver_factory=Mock(side_effect=OSError('missing')))
        self.assertEqual(report['failureStage'],'读取模块');self.assertFalse(report['runtimeEnabled'])
