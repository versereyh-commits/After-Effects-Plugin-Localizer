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

class HeapTests(unittest.TestCase):
    HEAP=0x10000
    def region(self,base,size,alloc,state=0x1000,kind=0x20000,protect=4):
        return SimpleNamespace(BaseAddress=base,AllocationBase=alloc,RegionSize=size,State=state,Type=kind,Protect=protect)
    def process(self,headers):
        def read(address,size):
            if address not in headers:raise RuntimeError('unreadable')
            return headers[address][:size]
        return SimpleNamespace(read=read)
    def segment(self,heap,signature=0xffeeffee):
        return bytes(16)+signature.to_bytes(4,'little')+bytes(20)+heap.to_bytes(8,'little')
    def query(self,regions):
        def query(address):
            return next((r for r in regions if r.BaseAddress>=address),None)
        return query
    def test_only_default_heap_segments_are_kept(self):
        from parallel_scan import heap_regions
        regions=[self.region(0x10000,0x1000,0x10000),self.region(0x11000,0x1000,0x10000,state=0x2000),   # heap segment 1, reserved tail
                 self.region(0x20000,0x2000,0x20000),                                                     # second segment of the same heap
                 self.region(0x30000,0x8000,0x30000),                                                     # image cache: not a heap
                 self.region(0x40000,0x1000,0x40000),                                                     # another heap
                 self.region(0x50000,0x1000,0x50000),                                                     # header unreadable
                 self.region(0x60000,0x1000,0x60000,protect=2)]                                           # read-only
        p=self.process({0x10000:self.segment(self.HEAP),0x20000:self.segment(self.HEAP),0x30000:bytes(0x30),0x40000:self.segment(0x40000),0x60000:self.segment(self.HEAP)})
        self.assertEqual(heap_regions(p,query=self.query(regions),heap=self.HEAP),[(0x10000,0x11000),(0x20000,0x22000)])
    def test_unrecognised_heap_falls_back_to_everything(self):
        from parallel_scan import heap_regions
        regions=[self.region(0x10000,0x1000,0x10000),self.region(0x20000,0x1000,0x20000)]
        # The heap's own first segment does not carry the NT heap signature (a segment heap, say): no heap-only scan.
        p=self.process({0x10000:self.segment(self.HEAP,0xddeeddee),0x20000:self.segment(self.HEAP)})
        self.assertIsNone(heap_regions(p,query=self.query(regions),heap=self.HEAP))
        from unittest.mock import patch
        with patch('parallel_scan.default_heap',side_effect=OSError('denied')):self.assertIsNone(heap_regions(p,query=self.query(regions)))
    def test_scan_auto_heap_target(self):
        from unittest.mock import patch
        from parallel_scan import scan_auto
        c=SimpleNamespace(timings={},p=object())
        with patch('parallel_scan.os.cpu_count',return_value=8),patch('parallel_scan.heap_regions',return_value=[(4096,8192)]),patch('parallel_scan.scan_parallel',return_value={7}) as parallel:
            self.assertEqual(scan_auto(c,full=False),{7});self.assertEqual(c.timings['scanTarget'],'heap')
            query=parallel.call_args.kwargs['query'];self.assertEqual((query(0).BaseAddress,query(0)),(4096,None))
        with patch('parallel_scan.os.cpu_count',return_value=8),patch('parallel_scan.heap_regions',return_value=None),patch('parallel_scan.scan_parallel',return_value={7}) as parallel:
            scan_auto(c,full=False);self.assertEqual(c.timings['scanTarget'],'full');self.assertIsNone(parallel.call_args.kwargs['query'])
        with patch('parallel_scan.os.cpu_count',return_value=8),patch('parallel_scan.heap_regions') as heap,patch('parallel_scan.scan_parallel',return_value={7}):
            scan_auto(c);heap.assert_not_called();self.assertEqual(c.timings['scanTarget'],'full')
