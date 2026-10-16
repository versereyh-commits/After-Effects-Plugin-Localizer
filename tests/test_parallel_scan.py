import sys,unittest,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_parameter_scan import Memory
from parameter_scan import scan
from parallel_scan import scan_parallel,consolidate,partition,region_query

class ParallelTests(unittest.TestCase):
    def setup_memory(self,hits):
        self.p=Memory(hits);self.p.pid=11;self.p.creation_time=22;self.p.image='C:/AE/AfterFX.exe';self.clones=[];self.lock=threading.Lock()
    def factory(self):
        p=Memory([]);p.data=self.p.data;p.guid=self.p.guid;p.pid=11;p.creation_time=22;p.image=self.p.image;p.buffers=set();p.close=Mock();original=p.read_into
        def read_into(a,b,n):p.buffers.add(id(b));return original(a,b,n)
        p.read_into=read_into
        with self.lock:self.clones.append(p)
        return p
    def test_workers_match_serial_and_close_all_handles(self):
        for workers in (1,2,4):
            self.setup_memory([0,64,128]);expected=scan(self.p.catalog(),native=True,query=self.p.query,block=64,limit=65536)
            c=self.p.catalog();actual=scan_parallel(c,workers=workers,query=self.p.query,factory=self.factory,job=64,block=64)
            self.assertEqual(expected,actual);self.assertTrue(all(p.close.call_count==1 for p in self.clones));self.assertTrue(all(len(p.buffers)==1 for p in self.clones));self.assertLessEqual(c.timings['scanBufferCapacityBytes'],workers*71)
    def test_merge_and_partition_preserve_regions_and_gaps(self):
        spans=[(4096,4160),(4160,4224),(4288,4352)]
        self.assertEqual(consolidate(spans,True),[(4096,4224),(4288,4352)])
        self.assertEqual(consolidate([(4096,4105),(4105,4113)],True),[(4096,4105),(4105,4113)])
        bins=partition(consolidate(spans,True),4,job=64)
        self.assertEqual(sorted(task for bucket in bins for task in bucket),spans)
    def test_merged_read_failure_retries_original_regions(self):
        self.setup_memory([0,64]);c=self.p.catalog();parts=[(4096,4160),(4160,4224)]
        def factory():
            p=self.factory();read=p.read_into
            def fail_large(a,b,n):
                if n>64:raise RuntimeError('merged read failure')
                return read(a,b,n)
            p.read_into=fail_large;return p
        actual=scan_parallel(c,workers=1,merge=True,query=region_query(parts),factory=factory,job=128,block=128)
        self.assertEqual(actual,{9000});self.assertEqual(c.timings['scanRetryRegions'],2);self.assertGreater(c.timings['scanReadFailures'],0)
    def test_known_gaps_are_never_bulk_read(self):
        self.setup_memory([0,192]);c=self.p.catalog();parts=[(4096,4160),(4288,4352)]
        scan_parallel(c,workers=2,merge=True,query=region_query(parts),factory=self.factory,job=256,block=256)
        for p in self.clones:
            for a,n in p.reads:
                if a==9008:continue
                self.assertFalse(a<4288 and a+n>4160)
    def test_identity_change_rejects_results_and_closes_worker(self):
        self.setup_memory([0])
        def wrong():p=self.factory();p.creation_time=23;return p
        with self.assertRaisesRegex(RuntimeError,'identity'):scan_parallel(self.p.catalog(),workers=2,query=self.p.query,factory=wrong,job=64,block=64)
        self.assertTrue(all(p.close.call_count==1 for p in self.clones))
    def test_worker_exit_is_not_reported_as_empty_success(self):
        self.setup_memory([0])
        def exited():p=self.factory();p.ensure_alive.side_effect=RuntimeError('exited');return p
        with self.assertRaises(RuntimeError):scan_parallel(self.p.catalog(),workers=2,query=self.p.query,factory=exited,job=64,block=64)
        self.assertTrue(all(p.close.call_count==1 for p in self.clones))
    def test_guarded_regions_are_filtered_before_work(self):
        self.setup_memory([0]);m=self.p.query(0);m.Protect=0x104;c=self.p.catalog()
        self.assertEqual(scan_parallel(c,workers=4,query=lambda a:m if a==0 else None,factory=self.factory,block=64),set());self.assertEqual(self.clones,[])
    def test_unaligned_candidates_match_serial(self):
        self.setup_memory([1]);self.assertEqual(scan_parallel(self.p.catalog(),workers=4,query=self.p.query,factory=self.factory,job=64,block=64),set())
    def test_lpt_partition_covers_large_region_without_lost_bytes(self):
        regions=[(4096,5120),(6000,6120),(8192,8300)];bins=partition(regions,4,job=64)
        self.assertEqual(sum(e-s for tasks in bins for s,e in tasks),sum(e-s for s,e in regions))
        for start,end in regions:
            tasks=sorted((s,e) for bucket in bins for s,e in bucket if start<=s<end)
            self.assertEqual(tasks[0][0],start);self.assertEqual(tasks[-1][1],end)
            self.assertTrue(all(tasks[i][1]==tasks[i+1][0] for i in range(len(tasks)-1)))
    def test_invalid_worker_count_is_rejected(self):
        self.setup_memory([])
        with self.assertRaises(ValueError):scan_parallel(self.p.catalog(),workers=8,query=self.p.query,factory=self.factory)

    def test_invalid_partition_sizes_never_return_empty_success(self):
        self.setup_memory([0])
        for kwargs in ({'job':-8},{'job':0},{'block':7},{'workers':True}):
            with self.assertRaises(ValueError):scan_parallel(self.p.catalog(),query=self.p.query,factory=self.factory,**kwargs)

class AutoTests(unittest.TestCase):
    def test_single_core_retains_serial(self):
        from unittest.mock import patch
        from parallel_scan import scan_auto
        c=SimpleNamespace(timings={})
        with patch('parallel_scan.os.cpu_count',return_value=1),patch('parallel_scan.serial_scan',return_value={7}) as serial,patch('parallel_scan.scan_parallel') as parallel:
            self.assertEqual(scan_auto(c),{7});serial.assert_called_once();parallel.assert_not_called()
    def test_workers_are_bounded(self):
        from unittest.mock import patch
        from parallel_scan import scan_auto
        for cores,expected in ((2,2),(3,2),(4,4),(32,4)):
            c=SimpleNamespace(timings={})
            with patch('parallel_scan.os.cpu_count',return_value=cores),patch('parallel_scan.scan_parallel',return_value={7}) as parallel:
                self.assertEqual(scan_auto(c),{7});self.assertEqual(parallel.call_args.kwargs['workers'],expected)
    def test_resource_failure_rechecks_host_then_uses_serial(self):
        from unittest.mock import patch
        from parallel_scan import scan_auto
        c=SimpleNamespace(timings={},p=SimpleNamespace(ensure_alive=Mock()))
        with patch('parallel_scan.os.cpu_count',return_value=8),patch('parallel_scan.scan_parallel',side_effect=OSError('resource')),patch('parallel_scan.serial_scan',return_value={7}) as serial:
            self.assertEqual(scan_auto(c),{7});c.p.ensure_alive.assert_called_once();serial.assert_called_once();self.assertEqual(c.timings['scanResourceFallback'],1)
    def test_runtime_guard_failure_is_not_bypassed(self):
        from unittest.mock import patch
        from parallel_scan import scan_auto
        c=SimpleNamespace(timings={})
        with patch('parallel_scan.os.cpu_count',return_value=8),patch('parallel_scan.scan_parallel',side_effect=RuntimeError('identity')),patch('parallel_scan.serial_scan') as serial:
            with self.assertRaises(RuntimeError):scan_auto(c)
            serial.assert_not_called()
