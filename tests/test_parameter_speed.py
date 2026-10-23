import json,struct,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import Mock,patch
import parameter_names as n

class SpeedTests(unittest.TestCase):
    def setUp(self):
        for name in ('_cache','_project_context','_shape_context','_scan_reason','_unsupported','_scan_kind'):
            h=patch.dict(getattr(n,name),{},clear=True);h.start();self.addCleanup(h.stop)
        self.p=SimpleNamespace(pid=11,creation_time=22);self.key=(11,22);self.param=n.identity('fx','p')
    def host(self,rows,token='project'):
        with patch.object(n,'run_native',return_value={'projectToken':token,'parameters':rows}):return n._host_names(self.p)
    def test_same_shape_keeps_cache_but_names_are_fresh(self):
        self.host([{'id':self.param,'name':'Old'}]);n._cache[self.key]={100}
        self.assertEqual(self.host([{'id':self.param,'name':'New'}])[self.param],['New']);self.assertIn(self.key,n._cache)
    def test_add_delete_duplicate_and_undo_invalidate_write_structure_cache(self):
        one=[{'id':self.param,'name':'name'}];self.host(one)
        for rows in (one+one,one,[],one):
            n._cache[self.key]={100};self.host(rows);self.assertNotIn(self.key,n._cache)
    def test_read_keeps_structure_and_trusted_shape_when_counts_change(self):
        one=[{'id':self.param,'name':'name'}];self.host(one);n._cache[self.key]={100};trusted=n._shape_context[self.key]
        with patch.object(n,'run_native',return_value={'projectToken':'project','parameters':one+one}):n._host_names(self.p,strict=False)
        self.assertIn(self.key,n._cache);self.assertEqual(n._shape_context[self.key],trusted)
        self.host(one+one);self.assertNotIn(self.key,n._cache)
    def test_read_without_structure_records_shape_like_a_write(self):
        with patch.object(n,'run_native',return_value={'projectToken':'project','parameters':[{'id':self.param,'name':'n'}]}):n._host_names(self.p,strict=False)
        self.assertEqual(n._shape_context[self.key],((self.param,1),))
    def test_project_read_rescans_once_for_a_new_parameter_type(self):
        other=n.identity('new','q');c=Mock();c.timings={'modulesMs':1,'matchPoolMs':2,'scanMs':0,'scanCount':0,'mode':'cache'}
        row=lambda key,fx:{'id':key,'effectMatchName':fx,'matchName':'p','name':'x','address':1,'parent':2,'guid':'g','pfName':'x'}
        c.rows.side_effect=lambda rescan,progress:[row(self.param,'fx'),row(other,'new')] if rescan else [row(self.param,'fx')]
        with patch.object(n,'current_process',return_value=nullcontext(self.p)),patch.object(n,'_process_lock',return_value=nullcontext()),patch.object(n,'_host_names',return_value={self.param:['A'],other:['B']}),patch.object(n,'ParameterCatalog',return_value=c),patch.object(n,'record_read_timing'):
            r=n.read_parameters()
        self.assertEqual(len(r['parameters']),2);self.assertEqual([x.args[0] for x in c.rows.call_args_list],[False,True]);self.assertEqual(n._shape_context[self.key],tuple(sorted(((self.param,1),(other,1)))))
    def test_project_switch_invalidates_even_same_parameter_ids(self):
        rows=[{'id':self.param,'name':'Name'}];self.host(rows);n._cache[self.key]={100}
        self.host(rows,'another');self.assertNotIn(self.key,n._cache)
    def catalog(self,invalid=False):
        c=n.ParameterCatalog.__new__(n.ParameterCatalog);c.p=self.p;c.flt=1000;c.profile=SimpleNamespace(root_vtable=10,group_vector=64);c.names={b'A'*16:'ADBE Effect Parade'}
        parades={100,200};memory={108:b'A'*16,164:struct.pack('<QQ',300,308),300:struct.pack('<Q',400),208:b'A'*16,264:struct.pack('<QQ',500,508),500:struct.pack('<Q',600)}
        def read(addr,size):
            if invalid and addr==208:raise RuntimeError('stale')
            return memory[addr]
        self.p.read=read;self.p.u64=lambda addr:{400:1010,432:100,600:1010,632:200}[addr]
        c._scan_parades=Mock(return_value={100} if invalid else parades);return c
    def test_warm_roots_skip_full_scan_and_match_forced(self):
        c=self.catalog();n._cache[self.key]={100,200}
        self.assertEqual(c.roots(),[400,600]);c._scan_parades.assert_not_called()
        self.assertEqual(c.roots(rescan=True),[400,600]);self.assertEqual(c._scan_parades.call_count,1)
    def test_partial_invalid_cache_rescans_once_even_with_valid_roots(self):
        c=self.catalog(invalid=True);n._cache[self.key]={100,200}
        self.assertEqual(c.roots(),[400]);c._scan_parades.assert_called_once();self.assertEqual(n._cache[self.key],{100})
    def test_new_process_creation_does_not_reuse_old_pointers(self):
        c=self.catalog();n._cache[(11,21)]={999};self.assertEqual(c.roots(),[400,600]);c._scan_parades.assert_called_once()
    def heap_catalog(self,found):
        c=self.catalog()
        def scan(full):c.timings['scanTarget']='full' if full else 'heap';return {100,200} if full else found
        c._scan_parades=Mock(side_effect=scan);return c
    def test_heap_structure_is_replaced_by_a_full_scan_for_writes(self):
        c=self.heap_catalog({100});self.assertEqual(c.roots(),[400]);self.assertEqual(n._scan_kind[self.key],'heap')
        self.assertEqual(c.roots(full=True),[400,600]);self.assertEqual([x.args[0] for x in c._scan_parades.call_args_list],[False,True])
        self.assertEqual(n._scan_kind[self.key],'full');self.assertEqual(c.timings['scanHeapMissed'],1)
        # A full structure serves both reads and writes without scanning again.
        c.roots();c.roots(full=True);self.assertEqual(c._scan_parades.call_count,2)
    def read_with(self,c,host):
        with patch.object(n,'current_process',return_value=nullcontext(self.p)),patch.object(n,'_process_lock',return_value=nullcontext()),patch.object(n,'_host_names',return_value=host),patch.object(n,'ParameterCatalog',return_value=c),patch.object(n,'record_read_timing'):
            return n.read_parameters()
    def scanning_mock(self,heap_parents):
        """First read scans the heap (finds heap_parents), a full scan finds parents 1 and 2."""
        c=Mock();c.timings={'modulesMs':1,'matchPoolMs':2,'scanMs':0,'scanCount':0,'mode':'scan'}
        row=lambda parent:{'id':self.param,'effectMatchName':'fx','matchName':'p','name':'x','address':parent,'parent':parent,'guid':'g','pfName':'x'}
        def rows(rescan,progress,full=False):
            c.timings['scanCount']+=1;n._scan_kind[self.key]='full' if full else 'heap';n._cache[self.key]={1}
            return [row(x) for x in ((1,2) if full else heap_parents)]
        c.rows.side_effect=rows;return c
    def test_heap_scan_short_of_visible_instances_escalates_to_full(self):
        c=self.scanning_mock((1,));r=self.read_with(c,{self.param:['A','B']})
        self.assertEqual([x.kwargs.get('full',False) for x in c.rows.call_args_list],[False,True]);self.assertEqual(r['timings']['scanEscalated'],1)
    def test_complete_heap_scan_is_trusted_for_reads(self):
        c=self.scanning_mock((1,2));r=self.read_with(c,{self.param:['A','B']})
        self.assertEqual(c.rows.call_count,1);self.assertNotIn('scanEscalated',r['timings']);self.assertEqual(r['parameters'][0]['instances'],2)
    def test_heap_scan_missing_an_effect_type_escalates_to_full(self):
        c=self.scanning_mock(());self.read_with(c,{self.param:['A']})
        self.assertEqual([x.kwargs.get('full',False) for x in c.rows.call_args_list],[False,True])
    def test_empty_host_skips_module_pool_and_memory_scan(self):
        with patch.object(n,'current_process',return_value=nullcontext(self.p)),patch.object(n,'_process_lock',return_value=nullcontext()),patch.object(n,'_host_names',return_value={}),patch.object(n,'ParameterCatalog') as catalog,patch.object(n,'record_read_timing'):
            r=n.read_parameters();catalog.assert_not_called();self.assertEqual(r['parameters'],[]);self.assertEqual(r['timings']['scanCount'],0)
    def test_read_returns_current_host_names_and_filters_unsupported(self):
        c=Mock();c.timings={'modulesMs':1,'matchPoolMs':2,'scanMs':0,'scanCount':0,'mode':'cache'}
        c.rows.return_value=[{'id':self.param,'effectMatchName':'fx','matchName':'p','name':'stale','address':123,'parent':456,'guid':'xx','pfName':'p'}, {'id':n.identity('unsupported','p'),'name':'x','effectMatchName':'unsupported','matchName':'p'}]
        with patch.object(n,'current_process',return_value=nullcontext(self.p)),patch.object(n,'_process_lock',return_value=nullcontext()),patch.object(n,'_host_names',return_value={self.param:['Fresh','Other']}),patch.object(n,'ParameterCatalog',return_value=c),patch.object(n,'record_read_timing'):
            r=n.read_parameters();self.assertEqual(len(r['parameters']),1);row=r['parameters'][0];self.assertEqual(row['name'],'Fresh');self.assertEqual(row['instances'],2);self.assertTrue(row['mixedNames']);self.assertNotIn('address',row);c.rows.assert_called_once_with(False,None)
    def test_cached_child_read_failure_retries_full_scan_once(self):
        c=Mock();c.timings={'modulesMs':1,'matchPoolMs':2,'scanMs':0,'scanCount':0,'mode':'cache'}
        c.rows.side_effect=[RuntimeError('stale child'),[]]
        with patch.object(n,'current_process',return_value=nullcontext(self.p)),patch.object(n,'_process_lock',return_value=nullcontext()),patch.object(n,'_host_names',return_value={self.param:['Name']}),patch.object(n,'ParameterCatalog',return_value=c),patch.object(n,'record_read_timing'):
            n.read_parameters();self.assertEqual(c.rows.call_count,2);self.assertEqual(c.rows.call_args.args[0],True)
    def test_timing_log_excludes_content_and_pointers(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(n,'data_root',return_value=Path(tmp)):
            n.record_read_timing({'event':'read','totalMs':1,'project':'secret','address':123,'name':'secret'})
            row=json.loads((Path(tmp)/'parameter-read-timing.jsonl').read_text());self.assertEqual(row,{'event':'read','totalMs':1})

class AsciiTests(unittest.TestCase):
    def test_normal_string_uses_one_block(self):
        p=SimpleNamespace(read=Mock(return_value=b'Example\0'+b'x'*24));self.assertEqual(n.bounded_ascii(p,0x1000),'Example');p.read.assert_called_once_with(0x1000,32)
    def test_page_boundary_is_never_crossed(self):
        calls=[]
        def read(addr,size):
            self.assertLessEqual(size,4096-addr%4096);calls.append((addr,size));return b'Z' if addr==4095 else b'\0'+b'x'*(size-1)
        self.assertEqual(n.bounded_ascii(SimpleNamespace(read=read),4095),'Z');self.assertEqual(calls[0],(4095,1))
    def test_short_readable_region_falls_back_to_smaller_reads(self):
        raw=b'Abc\0'
        def read(addr,size):
            if addr+size>len(raw):raise RuntimeError('unreadable')
            return raw[addr:addr+size]
        self.assertEqual(n.bounded_ascii(SimpleNamespace(read=read),0),'Abc')
    def test_missing_terminator_stops_at_limit(self):
        p=SimpleNamespace(read=lambda addr,size:b'A'*size)
        with self.assertRaisesRegex(RuntimeError,'未终止'):n.bounded_ascii(p,4096)
    def test_unreadable_or_non_ascii_is_not_accepted(self):
        with self.assertRaises(RuntimeError):n.bounded_ascii(SimpleNamespace(read=Mock(side_effect=RuntimeError('bad'))),0)
        with self.assertRaises(UnicodeDecodeError):n.bounded_ascii(SimpleNamespace(read=lambda a,s:b'\xff\0'+b'x'*(s-2)),0)
    def test_non_ascii_internal_names_are_left_out_of_the_pool(self):
        # Pool: 1 bucket -> node A ('Glow') -> node B (a Chinese internal name); B is skipped, A kept.
        memory={0x10:struct.pack('<Q',0x50),0x50:struct.pack('<Q',0x100),0x100:struct.pack('<QQQ',0x200,0,0x400).ljust(64,b'\0')+b'A'*16,
                0x200:struct.pack('<QQQ',0,0,0x500).ljust(64,b'\0')+b'B'*16,0x400:b'Glow\0'.ljust(32,b'x'),0x500:'中文\0'.encode('utf8').ljust(32,b'x')}
        def read(addr,size):
            for start,raw in memory.items():
                if start<=addr and addr+size<=start+len(raw):return raw[addr-start:addr-start+size]
            raise RuntimeError('unmapped')
        catalog=object.__new__(n.ParameterCatalog);catalog.p=SimpleNamespace(read=read,u64=lambda a:struct.unpack('<Q',read(a,8))[0])
        catalog.tdb=0;catalog.profile=SimpleNamespace(match_pool=0x1000)
        memory[0x1000]=struct.pack('<QQQQQQ',0,1,0,0,0,0x10)
        self.assertEqual(catalog.match_pool(),{b'A'*16:'Glow'})
