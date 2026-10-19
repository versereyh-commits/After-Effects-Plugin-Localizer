import json,struct,unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_adapter_analysis as fixtures
from adapter_resolver import Resolver,registry_offset
def _decodes(raw,encoding):
    try:raw.decode(encoding);return True
    except UnicodeDecodeError:return False

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

class NamedListPlacementTests(unittest.TestCase):
    """Where the named group's container sits comes from the count getter's instruction; whether it is a list or
    (AE 26.5) a std::map comes from LookupStream's exported type."""
    def resolver(self,count_instruction,tree=False):
        import tempfile
        from pathlib import Path
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);(Path(folder.name)/'U.dll').write_bytes(b'u')
        fields=dict(name_get=1,match_get=2,category_get=3,parent_get=4,stream_match_get=5,count_get=0x500,display_get=6,group_vector=0x220,
                    name_offset=16,match_offset=64,category_offset=96,parent_offset=32,guid_offset=8)
        r=Resolver.__new__(Resolver)
        r.getters=lambda:[{'field':k,'value':v} for k,v in fields.items()];r.span=lambda module,rva,size,code=False:count_instruction.ljust(size,b'\xcc')
        r.exe=Path(folder.name)/'AfterFX.exe';r.modules={'FLT.dll':{'sha256':'f'},'TDB.dll':{'sha256':'t'}};r.version='26.5.0.89'
        r.manager=lambda:0x100;r.primary_vtable=lambda name:0x200;r.match_pool=lambda:0x300;r.display_cache=lambda entry:0x88
        r.named_container=lambda:tree;r.tree_evidence=lambda named_map:None
        return r
    def test_list_right_after_the_vector_as_up_to_26_0(self):
        profile=self.resolver(bytes.fromhex('8b8130020000c3')).complete_static('')
        self.assertEqual((profile.named_map,profile.named_tree),(0x228,False))
    def test_list_further_down_is_accepted(self):
        profile=self.resolver(bytes.fromhex('8b8140020000c3')).complete_static('')
        self.assertEqual((profile.named_map,profile.named_vector,profile.count_bytes),(0x238,False,bytes.fromhex('8b8140020000c3')))
    def test_implausible_places_are_refused(self):
        for raw in ('8b8124020000c3','8b8128020000c3','8b8110090000c3'):   # misaligned, inside the vector, too far
            with self.assertRaisesRegex(ValueError,'命名参数映射位置不合理'):self.resolver(bytes.fromhex(raw)).complete_static('')
    def test_26_5_tree_shares_the_offset_of_the_other_class_vector(self):
        profile=self.resolver(bytes.fromhex('8b8128020000c3'),tree=True).complete_static('')
        self.assertEqual((profile.named_map,profile.group_vector,profile.named_tree,profile.named_vector),(0x220,0x220,True,False))
        for raw in ('8b8124020000c3','8b8140000000c3','8b8110090000c3'):   # misaligned, inside the base object, too far
            with self.assertRaisesRegex(ValueError,'命名参数映射位置不合理'):self.resolver(bytes.fromhex(raw),tree=True).complete_static('')

class NamedContainerTests(unittest.TestCase):
    """26.5's container type is read from LookupStream's own export names and instructions."""
    LOOKUP='?LookupStream@TDB_NamedStreamGroup@@AEBA?AV?$_%s_const_iterator@V?$_%s_val@@Z'
    def resolver(self,names,code=b''):
        import capstone
        r=Resolver.__new__(Resolver);r.dis=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);r.dis.detail=True
        symbols=[SimpleNamespace(name=n.encode(),forwarder=None,address=0x1000) for n in names]
        r.module=lambda name:{'pe':SimpleNamespace(DIRECTORY_ENTRY_EXPORT=SimpleNamespace(symbols=symbols))}
        r.instructions=lambda module,rva,size=256:list(r.dis.disasm(code,rva))
        return r
    def test_export_names_decide_list_or_tree(self):
        self.assertTrue(self.resolver([self.LOOKUP%('Tree','Tree')]).named_container())
        self.assertFalse(self.resolver([self.LOOKUP%('List','List')]).named_container())
        for names in ([self.LOOKUP%('Tree','Tree'),self.LOOKUP%('List','List')],[],['?LookupStream@TDB_NamedStreamGroup@@AEBAXZ']):
            with self.assertRaisesRegex(ValueError,'尚未识别命名参数容器'):self.resolver(names).named_container()
    def test_tree_needs_head_and_node_fields_in_lookup(self):
        # 26.5: mov rdi,[rbp+0x220]; cmp byte [rsi+0x19],0; lea rcx,[rsi+0x20]; mov rsi,[rdi+0x30]; ret
        code=bytes.fromhex('488bbd20020000'+'807e1900'+'488d4e20'+'488b7730'+'c3')
        self.resolver([self.LOOKUP%('Tree','Tree')],code).tree_evidence(0x220)
        with self.assertRaisesRegex(ValueError,'命名参数树结构不匹配'):self.resolver([self.LOOKUP%('Tree','Tree')],code).tree_evidence(0x228)
        with self.assertRaisesRegex(ValueError,'命名参数树结构不匹配'):self.resolver([self.LOOKUP%('Tree','Tree')],code[:11]+code[15:]).tree_evidence(0x220)

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
    def add_unnamed(self,at=0x400200,name='渐变色调',key=b''):
        # An effect AE lists with matchName '': empty in memory, or bytes this build cannot read.
        data=bytearray(160);struct.pack_into('<Q',data,0,0x100200);raw=name.encode();data[16:16+len(raw)]=raw;data[64:64+len(key)]=key;data[96:100]=b'Test';self.memory[at]=bytes(data)
        self.memory[0x300000]+=struct.pack('<QQ',at,0);self.memory[0x200020]=struct.pack('<QQ',0x300000,0x300000+16*(len(self.memory[0x300000])//16))
        self.host.append({'matchName':'','displayName':name,'category':'Test'})
    def test_effects_without_an_internal_name_are_counted_not_compared(self):
        self.add_unnamed();self.add_unnamed(0x400300,'聚光灯');self.add_unnamed(0x400400,'稳定器')
        self.assertEqual(registry_offset(self.process,self.profile,self.host),32)
    def test_unreadable_internal_names_pair_with_what_ae_reports_as_empty(self):
        # Memory holds bytes AE 2021 could not read (it reported ''): paired with AE's nameless rows by exact name.
        gbk='渐变'.encode('gbk')+b'\xff'
        self.add_unnamed(key=gbk);self.add_unnamed(0x400300,'聚光灯',key=b'\xfe\xfe')
        self.assertEqual(registry_offset(self.process,self.profile,self.host),32)
    def test_unnamed_pairing_is_exact(self):
        from adapter_resolver import CatalogMismatch
        self.add_unnamed(key=b'\xff\xfe');self.host[-1]['displayName']='别的名字'
        with self.assertRaises(CatalogMismatch) as caught:registry_offset(self.process,self.profile,self.host)
        utf8=caught.exception.details['candidates'][0]['utf8']
        self.assertEqual((utf8['unnamedInMemory'],[e['field'] for e in utf8['examples']]),(1,['unnamed','unnamedMissing']))
        self.assertEqual(utf8['examples'][0]['matchNameHex'],'fffe')
    def test_internal_names_read_in_the_system_code_page_as_ae_2021_does(self):
        # AE 2021 (18.0): names in UTF-8, internal names in the system code page -- the UTF-8 Chinese internal name
        # of a localized plugin comes back from scripts as GBK mojibake, and as '' where GBK cannot decode it.
        from adapter_resolver import registry_layout
        key='B双色'.encode('utf8');self.rename(0x400100,key=key);self.host[1]['matchName']=key.decode('mbcs')
        bad=next(t.encode('utf8') for t in ('渐变色调','聚光灯','稳定器','选择色调','油','画中画') if not _decodes(t.encode('utf8'),'mbcs'))
        self.add_unnamed(key=b'NWBL '+bad)
        self.assertEqual(registry_layout(self.process,self.profile,self.host),{'offset':32,'matchEncoding':'mbcs'})
    def test_ordinary_catalogs_keep_one_encoding(self):
        from adapter_resolver import registry_layout
        self.assertEqual(registry_layout(self.process,self.profile,self.host),{'offset':32,'matchEncoding':''})
    def test_unnamed_entries_must_match_in_number(self):
        from adapter_resolver import CatalogMismatch
        self.add_unnamed();self.host.append({'matchName':'','displayName':'聚光灯','category':'Test'})
        with self.assertRaises(CatalogMismatch) as caught:registry_offset(self.process,self.profile,self.host)
        self.assertEqual((caught.exception.details['unnamedCount'],caught.exception.details['unnamed']),(2,['渐变色调','聚光灯']))
        self.assertIn('AE 报告 4 个效果',str(caught.exception))
    def test_too_few_effects_is_a_diagnosed_mismatch(self):
        from adapter_resolver import CatalogMismatch
        with self.assertRaisesRegex(CatalogMismatch,'效果数量异常（1 个）'):registry_offset(self.process,self.profile,self.host[:1])

for _name in list(fixtures.AnalysisTests.__dict__):
    if _name.startswith('test_'):setattr(RegistryOffsetTests,_name,None)
