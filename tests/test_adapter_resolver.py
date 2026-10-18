import json,struct
from types import SimpleNamespace
from unittest.mock import patch
import test_adapter_analysis as fixtures
from adapter_resolver import Resolver,registry_offset

class ResolverTests(fixtures.AnalysisTests):
    # Inherit the isolated PE fixture, not the existing fixture's test cases.
    def setUp(self):
        super().setUp();self.resolver=Resolver.__new__(Resolver);self.resolver.__dict__.update(self.tools.__dict__)
    def put(self,module,offset,raw):
        m=self.resolver.modules[module];data=bytearray(m['data']);data[offset:offset+len(raw)]=raw;m['data']=bytes(data);m['pe'].__data__=bytes(data)
    def test_display_cache_tracks_this_alias(self):
        self.put('TDB.dll',512,bytes.fromhex('488bf9488d87e8000000c3'))
        self.assertEqual(self.resolver.display_cache(4096),0xe8)
    def test_display_cache_requires_a_unique_return_offset(self):
        self.put('TDB.dll',512,bytes.fromhex('488d81e8000000488d81f0000000c3'))
        with self.assertRaises(ValueError):self.resolver.display_cache(4096)
    def test_manager_follows_the_tiny_getter_not_cookie(self):
        self.put('FLT.dll',512,b'\xe8'+struct.pack('<i',4128-4101)+b'\xc3')
        self.put('FLT.dll',544,b'\x48\x8b\x05'+struct.pack('<i',8192-4135)+b'\xc3')
        with patch.object(self.resolver,'symbol',return_value=4096),patch.object(self.resolver,'writable',side_effect=lambda module,rva:rva==8192):
            self.assertEqual(self.resolver.manager(),8192)
    def test_match_pool_resolves_rip_relative_data(self):
        self.put('TDB.dll',512,b'\x48\x8b\xd1\x48\x8d\x0d'+struct.pack('<i',8192-4106)+b'\xc3')
        with patch.object(self.resolver,'symbol',return_value=4096),patch.object(self.resolver,'writable',side_effect=lambda module,rva:rva==8192):
            self.assertEqual(self.resolver.match_pool(),8192)
    def rtti(self,offset=0):
        self.put('FLT.dll',512,b'\xc3');self.put('FLT.dll',656,b'.?AVFLT_FCSeqSpec@@\0')
        self.put('FLT.dll',768,struct.pack('<IIIIII',1,offset,0,0x1080,0,0x1100))
        self.put('FLT.dll',832,struct.pack('<QQ',0x140001100,0x140001000))
    def test_primary_rtti_recovers_vtable(self):
        self.rtti();self.assertEqual(self.resolver.primary_vtable('.?AVFLT_FCSeqSpec@@'),0x1148)
    def test_secondary_rtti_cannot_be_used_as_primary(self):
        self.rtti(offset=8)
        with self.assertRaises(ValueError):self.resolver.primary_vtable('.?AVFLT_FCSeqSpec@@')
    def test_ambiguous_primary_vtables_stop_resolution(self):
        self.rtti();self.put('FLT.dll',864,struct.pack('<QQ',0x140001100,0x140001000))
        with self.assertRaises(ValueError):self.resolver.primary_vtable('.?AVFLT_FCSeqSpec@@')

# Avoid repeating the base class tests through inheritance during discovery.
for _name in list(fixtures.AnalysisTests.__dict__):
    if _name.startswith('test_'):setattr(ResolverTests,_name,None)

class RegistryOffsetTests(fixtures.AnalysisTests):
    def setUp(self):
        super().setUp();self.host=[{'matchName':'A','displayName':'Alpha','category':'Test'},{'matchName':'B','displayName':'Beta','category':'Test'}]
        self.profile=SimpleNamespace(manager=0x100,fc_vtable=0x200);self.memory={0x100100:struct.pack('<Q',0x200000)}
        self.memory[0x200020]=struct.pack('<QQ',0x300000,0x300020)
        self.memory[0x300000]=struct.pack('<QQQQ',0x400000,0,0x400100,0)
        for at,key,name in ((0x400000,'A','Alpha'),(0x400100,'B','Beta')):
            data=bytearray(160);struct.pack_into('<Q',data,0,0x100200);data[16:16+len(name)]=name.encode();data[64]=ord(key);data[96:100]=b'Test';self.memory[at]=bytes(data)
        self.process=SimpleNamespace(modules=lambda:[{'name':'FLT.dll','base':0x100000}],u64=lambda a:struct.unpack('<Q',self.memory[a])[0],read=lambda a,n:self.memory[a][:n] if a in self.memory else (_ for _ in ()).throw(RuntimeError('not mapped')))
    def test_vector_is_selected_only_by_full_catalog(self):self.assertEqual(registry_offset(self.process,self.profile,self.host),32)
    def test_two_matching_vectors_are_rejected(self):
        self.memory[0x200010]=self.memory[0x200020]
        with self.assertRaises(ValueError):registry_offset(self.process,self.profile,self.host)
    def test_mismatched_category_is_rejected(self):
        self.host[0]['category']='Other'
        with self.assertRaises(ValueError):registry_offset(self.process,self.profile,self.host)
    def rename(self,at,name=None,key=None):
        data=bytearray(self.memory[at])
        if name is not None:data[16:64]=name.ljust(48,b'\0')
        if key is not None:data[64:96]=key.ljust(32,b'\0')
        self.memory[at]=bytes(data)
    def test_names_the_encoding_cannot_read_match_as_ae_shows_them(self):
        # A plugin shipping GBK names into a UTF-8 build: AE's scripting shows them one character per byte.
        gbk='BCC 键控与混合'.encode('gbk');self.rename(0x400000,name=gbk);self.host[0]['displayName']=gbk.decode('latin-1')
        self.assertEqual(registry_offset(self.process,self.profile,self.host),32)
        self.host[0]['displayName']='BCC 键控与合成'.encode('gbk').decode('latin-1')   # other bytes: still exact
        with self.assertRaises(ValueError):registry_offset(self.process,self.profile,self.host)
    def test_chinese_internal_names_match(self):
        self.rename(0x400100,key='B中文'.encode('utf8'));self.host[1]['matchName']='B中文'
        self.assertEqual(registry_offset(self.process,self.profile,self.host),32)
    def test_readable_bytes_are_never_matched_one_character_per_byte(self):
        self.rename(0x400000,name='Alpha辉光'.encode('utf8'));self.host[0]['displayName']='Alpha辉光'.encode('utf8').decode('latin-1')
        with self.assertRaises(ValueError):registry_offset(self.process,self.profile,self.host)
    def test_failure_says_how_the_closest_catalog_differed(self):
        from adapter_resolver import CatalogMismatch
        self.host[0]['category']='Other'
        with self.assertRaises(CatalogMismatch) as caught:registry_offset(self.process,self.profile,self.host)
        self.assertIn('最接近的目录有 1 项不一致，例如 A 的分类',str(caught.exception))
        candidate=caught.exception.details['candidates'][0];self.assertEqual(candidate['offset'],32)
        self.assertEqual(candidate['utf8']['examples'],[{'matchName':'A','field':'category','memoryHex':b'Test'.hex(),'memoryText':['Test'],'host':'Other'}])
        self.assertNotIn(str(0x400000),json.dumps(caught.exception.details),'no addresses in the diagnostic')
    def test_failure_names_counts_and_unreadable_objects(self):
        from adapter_resolver import CatalogMismatch
        self.host.append({'matchName':'C','displayName':'Gamma','category':'Test'})
        with self.assertRaisesRegex(CatalogMismatch,'AE 报告 3 个效果，内存里没有数量相同的目录；找到的目录数量：2'):registry_offset(self.process,self.profile,self.host)
        self.host.pop();data=bytearray(self.memory[0x400000]);data[16:64]=b'X'*48;self.memory[0x400000]=bytes(data)
        with self.assertRaisesRegex(CatalogMismatch,'第 1 项显示名称没有结尾符'):registry_offset(self.process,self.profile,self.host)
    def test_wrong_object_type_is_rejected(self):
        data=bytearray(self.memory[0x400000]);struct.pack_into('<Q',data,0,0);self.memory[0x400000]=bytes(data)
        with self.assertRaises(ValueError):registry_offset(self.process,self.profile,self.host)
    def test_a_plugin_ae_lists_twice_is_compared_once(self):
        # Two copies of one plugin (Plug-ins and Common\Plug-ins\7.0\MediaCore): AE lists it twice, memory once.
        self.host.append(dict(self.host[1]));self.assertEqual(registry_offset(self.process,self.profile,self.host),32)
    def test_memory_must_still_hold_each_effect_once(self):
        from adapter_resolver import CatalogMismatch
        self.host.append(dict(self.host[1]))
        self.memory[0x300000]=struct.pack('<QQQQ',0x400000,0,0x400000,0)   # the memory vector repeats A instead of holding B
        with self.assertRaises(CatalogMismatch) as caught:registry_offset(self.process,self.profile,self.host)
        self.assertIn('AE 列表里有 1 个效果重复出现（例如 B），可能同一插件装了两份',str(caught.exception))
        self.assertEqual(caught.exception.details['duplicates'],[{'matchName':'B','times':2,'names':[{'displayName':'Beta','category':'Test'}]}])
    def test_copies_with_different_names_stop_and_are_named(self):
        from adapter_resolver import CatalogMismatch
        self.host.append({'matchName':'B','displayName':'Beta 2','category':'Test'})
        with self.assertRaisesRegex(CatalogMismatch,'1 个内部标识对应不同的名称（例如 B：Beta / Beta 2）.*删掉多余的一份') as caught:
            registry_offset(self.process,self.profile,self.host)
        self.assertEqual(caught.exception.details['conflicts'],1)
    def test_too_few_effects_is_a_diagnosed_mismatch(self):
        from adapter_resolver import CatalogMismatch
        with self.assertRaisesRegex(CatalogMismatch,'效果数量异常（1 个）'):registry_offset(self.process,self.profile,self.host[:1])

for _name in list(fixtures.AnalysisTests.__dict__):
    if _name.startswith('test_'):setattr(RegistryOffsetTests,_name,None)
