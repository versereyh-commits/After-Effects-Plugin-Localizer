import json,unittest
from pathlib import Path
from unittest.mock import patch
import test_panel_localizer as fixtures
import panel_cache,panel_records,panel_localizer as P

class CacheRecords(unittest.TestCase):
    make=fixtures.Panels.make
    def setUp(self):
        fixtures.Panels.setUp(self);panel_cache.clear();self.addCleanup(panel_cache.clear)
    def test_repeat_reuses_report_not_parser(self):
        first=self.l.extract()
        with patch.object(self.l,'_extract',side_effect=AssertionError('parser must not run')):second=self.l.extract()
        self.assertEqual(first['readMode'],'scan');self.assertEqual(second['readMode'],'cache')
        self.assertEqual(first['terms'],second['terms']);self.assertEqual(first['hashes'],second['hashes'])
    def test_returned_cache_report_is_not_mutable_shared_state(self):
        self.l.extract();r=self.l.extract();r['terms'].clear();self.assertTrue(self.l.extract()['terms'])
    def test_content_hash_checked_even_when_metadata_matches(self):
        with patch.object(panel_cache,'signature',return_value=(1,2,3,4)):
            self.l.extract();(self.root/'index.html').write_text(self.html.replace('Apply','Start'),encoding='utf8')
            r=self.l.extract();self.assertEqual(r['readMode'],'scan');self.assertIn('Start',[x['text'] for x in r['terms']])
    def test_force_parse(self):
        self.l.extract()
        with patch.object(self.l,'_extract',wraps=self.l._extract) as parse:r=self.l.extract(force=True)
        parse.assert_called_once();self.assertEqual(r['readMode'],'scan')
    def test_cached_report_does_not_authorize_write(self):
        self.l.extract();r=self.l.extract();(self.root/'index.html').write_text('External change')
        with self.assertRaisesRegex(ValueError,'变化'):self.l.apply(r,{'Apply':'应用'})
        self.assertEqual((self.root/'index.html').read_text(),'External change')
    def test_cancel_before_cache_use(self):
        self.l.extract()
        with self.assertRaisesRegex(RuntimeError,'取消'):self.l.extract(lambda:True)
    def test_oversized_report_is_not_cached(self):
        with patch.object(panel_cache,'MAX_BYTES',1):
            self.l.extract();self.assertNotIn(self.t.key,panel_cache._reports)
    def test_apply_restore_invalidate_cache(self):
        r=self.l.extract();self.l.apply(r,{'Apply':'应用'});self.assertEqual(self.l.extract()['readMode'],'scan')
        self.l.restore();self.assertEqual(self.l.extract()['readMode'],'scan')
    def test_missing_dependency_never_returns_cached_success(self):
        self.l.extract();(self.root/'index.html').unlink()
        with self.assertRaises(OSError):self.l.extract()
    def test_pending_record_cannot_reuse_cache(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});self.l.extract();r=self.l.files.state();r['status']='prepared';P.write_json_atomic(self.l.files.record,r)
        with self.assertRaisesRegex(ValueError,'未完成'):self.l.extract()
    def test_optional_skipped_file_becomes_readable(self):
        (self.root/'index.html').write_text('<body>Apply<script src="big.js"></script></body>')
        (self.root/'big.js').write_text(' '*400)
        with patch.object(P,'MAX_FILE',180):
            r=self.l.extract();self.assertEqual(len(r['diagnostics']['skippedFiles']),1)
            (self.root/'big.js').write_text('var x={label:"Fresh label"};')
            r=self.l.extract();self.assertEqual(r['readMode'],'scan');self.assertIn('Fresh label',[x['text'] for x in r['terms']])
    def test_cache_is_installation_specific_and_bounded(self):
        import shutil
        other=self.base/'other';shutil.copytree(self.root,other);second=P.Localizer(P.inspect(other),self.base/'data')
        with patch.object(panel_cache,'CAPACITY',1):
            self.l.extract();second.extract();self.assertEqual(len(panel_cache._reports),1);self.assertEqual(self.l.extract()['readMode'],'scan')
    def test_records_have_time_files_and_restore_status(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});rows=panel_records.entries(self.base/'data');r=next(x for x in rows if x['current'])
        self.assertEqual(r['state'],'已汉化');self.assertTrue(r['at']);self.assertEqual(set(r['files']),{'index.html',P.OVERLAY})
        self.l.restore();rows=panel_records.entries(self.base/'data');self.assertEqual(next(x for x in rows if x['current'])['state'],'已恢复');self.assertTrue(any(not x['current'] for x in rows))
    def test_repeated_apply_retains_history(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});self.l.apply(self.l.extract(),{'Apply':'执行'})
        self.assertTrue(any(not r['current'] for r in panel_records.entries(self.base/'data')))
    def test_history_detects_target_conflict_without_writing(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});p=self.root/'index.html';p.write_text('New version');before=self.l.files.record.read_bytes()
        r=next(x for x in panel_records.entries(self.base/'data') if x['current']);self.assertEqual(r['state'],'存在冲突');self.assertTrue(r['issues'])
        self.assertEqual(p.read_text(),'New version');self.assertEqual(self.l.files.record.read_bytes(),before)
    def test_history_detects_bad_backup(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});r=self.l.files.state();(self.l.files.folder/r['files'][0]['backup']).write_bytes(b'bad')
        current=next(x for x in panel_records.entries(self.base/'data') if x['current']);self.assertEqual(current['state'],'存在冲突')
        with self.assertRaises(ValueError):self.l.restore()
    def test_old_records_without_time_supported(self):
        self.l.apply(self.l.extract(),{'Apply':'应用'});r=self.l.files.state()
        for key in ('createdAt','updatedAt','targetName'):r.pop(key,None)
        P.write_json_atomic(self.l.files.record,r);current=next(x for x in panel_records.entries(self.base/'data') if x['current']);self.assertEqual(current['state'],'已汉化');self.assertEqual(current['at'],'')
    def test_corrupt_records_are_reported_not_rewritten(self):
        self.l.files.folder.mkdir(parents=True);self.l.files.record.write_text('{bad')
        rows=panel_records.entries(self.base/'data');self.assertEqual(rows[0]['state'],'记录损坏');self.assertEqual(self.l.files.record.read_text(),'{bad')
    def test_hash_check_put_does_not_save_racing_snapshot(self):
        r=self.l.extract();stamps={p:panel_cache.signature(P.inside(self.root,p)) for p in r['hashes']};panel_cache.clear()
        (self.root/'index.html').write_text('Changed')
        panel_cache.put(self.t.key,self.root,self.l.files,(1,P.MAX_FILE,P.MAX_TOTAL,P.MAX_TERMS),r,stamps)
        self.assertNotIn(self.t.key,panel_cache._reports)
