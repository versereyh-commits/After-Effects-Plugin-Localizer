import ctypes,struct,unittest
from types import SimpleNamespace
from unittest.mock import Mock
from parameter_scan import scan
from win_process import Process

class Memory:
    def __init__(self,hits,fail=()):
        self.base=4096;self.data=bytearray(256);self.fail=set(fail);self.vt=0x123456789000;self.guid=b'P'*16;self.reads=[];self.ensure_alive=Mock()
        for index,offset in enumerate(hits):
            struct.pack_into('<Q',self.data,offset,self.vt);struct.pack_into('<Q',self.data,offset+32,9000)
    def read(self,address,size):
        self.reads.append((address,size))
        if address==9008:return self.guid
        if self.base<=address and address+size<=self.base+len(self.data):return bytes(self.data[address-self.base:address-self.base+size])
        raise RuntimeError('unreadable')
    def u64(self,address):return struct.unpack('<Q',self.read(address,8))[0]
    def read_into(self,address,buffer,size):
        if address in self.fail:
            memoryview(buffer).cast('B')[:8]=b'BADBYTES';raise RuntimeError('partial')
        data=self.read(address,size);memoryview(buffer).cast('B')[:size]=data;return size
    def query(self,address):
        if address<self.base+len(self.data):return SimpleNamespace(BaseAddress=self.base,RegionSize=len(self.data),State=0x1000,Type=0x20000,Protect=4)
        return None
    def catalog(self):return SimpleNamespace(p=self,flt=0,profile=SimpleNamespace(root_vtable=self.vt),names={self.guid:'ADBE Effect Parade'},timings={})

class ScanTests(unittest.TestCase):
    def test_baseline_and_reuse_match_at_block_edges_and_short_final_block(self):
        for hits in ([0],[56],[64],[120],[192],[200]):
            p=Memory(hits);p.data=p.data[:241]
            for fast in (False,True):
                c=p.catalog();self.assertEqual(scan(c,optimized=fast,query=p.query,block=64,limit=65536),{9000})
                self.assertEqual(c.timings['scanAlignedHits'],1)
    def test_repeated_confirmed_parent_uses_one_guid_check(self):
        p=Memory([0,64,128]);old=p.catalog();new=p.catalog()
        self.assertEqual(scan(old,optimized=False,query=p.query,block=64,limit=65536),scan(new,query=p.query,block=64,limit=65536))
        self.assertEqual(old.timings['scanParentChecks'],3);self.assertEqual(new.timings['scanParentChecks'],1)
        self.assertEqual(new.timings['scanRepeatedParents'],2)
    def test_failed_parent_read_is_not_cached(self):
        p=Memory([0,64]);original=p.read;attempts=[]
        def read(address,size):
            if address==9008:
                attempts.append(1)
                if len(attempts)==1:raise RuntimeError('temporary failure')
            return original(address,size)
        p.read=read;c=p.catalog();self.assertEqual(scan(c,query=p.query,block=64,limit=65536),{9000});self.assertEqual(len(attempts),2)
    def test_unaligned_hit_is_ignored(self):
        p=Memory([1]);c=p.catalog();self.assertEqual(scan(c,query=p.query,block=64,limit=65536),set());self.assertEqual(c.timings['scanHits'],1);self.assertEqual(c.timings['scanAlignedHits'],0)
    def test_failed_chunk_never_searches_stale_buffer(self):
        p=Memory([0],fail={4160,4224,4288});c=p.catalog();self.assertEqual(scan(c,query=p.query,block=64,limit=65536),{9000});self.assertEqual(c.timings['scanAlignedHits'],1);self.assertEqual(c.timings['scanReadFailures'],3)
    def test_final_buffer_tail_is_not_searched(self):
        p=Memory([0]);p.data=p.data[:140];c=p.catalog();scan(c,query=p.query,block=64,limit=65536);self.assertEqual(c.timings['scanAlignedHits'],1)
    def test_guard_and_nonprivate_regions_are_skipped(self):
        p=Memory([0])
        for field,value in [('Protect',0x104),('Type',0x1000000),('State',0x2000),('Protect',2)]:
            m=p.query(0);setattr(m,field,value);c=p.catalog()
            self.assertEqual(scan(c,query=lambda a:m if a==0 else None,block=64,limit=65536),set());self.assertEqual(c.timings['scanReadCalls'],0)
    def test_exited_host_stops_instead_of_returning_empty(self):
        p=Memory([]);p.ensure_alive.side_effect=RuntimeError('exited')
        with self.assertRaisesRegex(RuntimeError,'exited'):scan(p.catalog(),query=p.query,block=64,limit=65536)
    def test_parent_pointer_beyond_chunk_uses_guarded_read(self):
        p=Memory([56]);c=p.catalog();self.assertEqual(scan(c,query=p.query,block=64,limit=65536),{9000});self.assertIn((4184,8),p.reads)
    def test_parent_cache_is_per_scan(self):
        p=Memory([0]);c=p.catalog();scan(c,query=p.query,block=64,limit=65536);p.guid=b'Q'*16
        self.assertEqual(scan(c,query=p.query,block=64,limit=65536),set())

class ReadIntoTests(unittest.TestCase):
    def test_exact_read_reuses_storage(self):
        p=Process.__new__(Process);p.handle=1
        def read(handle,address,data,size,got):
            ctypes.memmove(data,b'abcd',size);ctypes.cast(got,ctypes.POINTER(ctypes.c_size_t))[0]=size;return True
        p.k=SimpleNamespace(ReadProcessMemory=read);buffer=bytearray(8)
        self.assertEqual(p.read_into(100,buffer,4),4);self.assertEqual(buffer[:4],b'abcd')
        for storage,size in ((bytes(4),4),(buffer,9),(buffer,-1)):
            with self.assertRaises(ValueError):p.read_into(100,storage,size)
    def test_partial_read_raises(self):
        p=Process.__new__(Process);p.handle=1;p.k=SimpleNamespace(ReadProcessMemory=lambda *args:False)
        with self.assertRaises(RuntimeError):p.read_into(100,bytearray(8),8)
    def test_closed_handle_is_rejected(self):
        p=Process.__new__(Process);p.handle=None
        with self.assertRaises(RuntimeError):p.read_into(100,bytearray(8),8)

class NativeSearchTests(unittest.TestCase):
    def test_native_matches_python_for_random_buffers_and_alignments(self):
        import random
        from scan_search import matches,native_function
        self.assertIsNotNone(native_function());rng=random.Random(7405);pattern=struct.pack('<Q',0x1122334455667788)
        for size in (0,1,7,8,15,16,63,64,65,257,4097):
            for alignment in range(8):
                data=bytearray(rng.randbytes(size+32));valid=size
                for offset in range(0,max(0,size-7),17):data[offset:offset+8]=pattern
                old=[i for i in matches(data,valid,valid,alignment,pattern) if (alignment+i)%8==0]
                stats={};new=list(matches(data,valid,valid,alignment,pattern,native=True,stats=stats,capacity=2))
                self.assertEqual(old,new);self.assertNotIn('scanEngine',stats)
    def test_overflow_is_paginated_without_truncation(self):
        from scan_search import matches
        pattern=struct.pack('<Q',77);data=bytearray(pattern*10000)
        self.assertEqual(list(matches(data,len(data),len(data),0,pattern,native=True,capacity=7)),list(range(0,len(data),8)))
    def test_unavailable_native_falls_back_completely(self):
        from unittest.mock import patch
        from scan_search import matches
        pattern=struct.pack('<Q',77);data=bytearray(pattern*3);stats={}
        with patch('scan_search.native_function',return_value=None):self.assertEqual(list(matches(data,len(data),len(data),0,pattern,native=True,stats=stats)),[0,8,16])
        self.assertEqual(stats['scanEngine'],'reuse-fallback')
    def test_invalid_native_return_uses_python_fallback(self):
        from unittest.mock import patch
        from scan_search import matches
        pattern=struct.pack('<Q',77);data=bytearray(pattern*3)
        with patch('scan_search.native_function',return_value=lambda *args:-1):self.assertEqual(list(matches(data,len(data),len(data),0,pattern,native=True)),[0,8,16])
    def test_whole_native_scan_has_same_candidates_as_original(self):
        p=Memory([0,64,128]);a=scan(p.catalog(),optimized=False,query=p.query,block=64,limit=65536)
        b=scan(p.catalog(),native=True,query=p.query,block=64,limit=65536);self.assertEqual(a,b)
