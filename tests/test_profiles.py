from dataclasses import replace
import hashlib,struct,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ae_profiles,live_names,parameter_names

class Memory:
    def __init__(self):self.blocks={};self.pid=1;self.creation_time=2;self.image='AfterFX.exe';self.writes=[]
    def read(self,address,size):
        for start,raw in self.blocks.items():
            if start<=address and address+size<=start+len(raw):return raw[address-start:address-start+size]
        raise RuntimeError('Out of fixture bounds')
    def u64(self,address):return struct.unpack('<Q',self.read(address,8))[0]

class ProfileTests(unittest.TestCase):
    def test_unregistered_build_never_reuses_nearby_layout(self):
        for version in ('18.2.1.9','25.3.0.72','26.0.0.68','27.0.0.1'):
            self.assertFalse(ae_profiles.is_supported(version))
            with self.assertRaises(RuntimeError):ae_profiles.get_profile(version)
    def test_registry_selects_version_specific_vector_and_encoding(self):
        for version in ('18.2.1.8','25.3.0.71','26.0.0.67'):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as temp:
                file=Path(temp)/'FLT.dll';file.write_bytes(b'fixture');profile=replace(ae_profiles.PROFILES[version],flt_sha=hashlib.sha256(b'fixture').hexdigest())
                p=Memory();base=0x10000000;manager=0x20000000;vector=0x30000000;obj=0x40000000
                p.modules=lambda:[{'name':'FLT.dll','base':base,'path':str(file)}]
                p.blocks[base+profile.manager]=struct.pack('<Q',manager)
                p.blocks[manager+profile.registry_vector]=struct.pack('<QQ',vector,vector+16)
                p.blocks[vector]=struct.pack('<QQ',obj,0)
                for rva,raw in ((profile.name_get,bytes.fromhex('488d4110c3')),(profile.match_get,bytes.fromhex('488d4140c3')),(profile.category_get,bytes.fromhex('488d4160c3'))):p.blocks[base+rva]=raw
                record=bytearray(160);struct.pack_into('<Q',record,0,base+profile.fc_vtable)
                record[16:64]=live_names.encode_name('辉光',profile.encoding);record[64:96]=b'S_Glow\0'.ljust(32,b'\0');record[96:160]='灯光'.encode(profile.encoding).ljust(64,b'\0');p.blocks[obj]=bytes(record)
                with patch.dict(ae_profiles.PROFILES,{version:profile}),patch.object(live_names,'file_version',return_value=version):
                    registry=live_names.Registry(p);rows=registry.records();self.assertEqual(rows[0]['displayName'],'辉光');self.assertEqual(rows[0]['category'],'灯光')
                    # A Chinese internal name and GBK names a plugin shipped never stop the read.
                    odd=bytearray(record);gbk='键控与混合'.encode('gbk')
                    odd[16:64]=(gbk+b'\0').ljust(48,b'\0');odd[64:96]=('S_中文'.encode(profile.encoding)+b'\0').ljust(32,b'\0');p.blocks[obj]=bytes(odd)
                    row=registry.records()[0];self.assertEqual(row['matchName'],'S_中文')
                    if profile.encoding=='utf8':self.assertEqual(row['displayName'],gbk.decode('latin-1'))   # as AE shows them
                    else:self.assertIsInstance(row['displayName'],str)   # readable or not depends on the system code page
                    p.blocks[obj]=bytes(160)
                    with self.assertRaisesRegex(RuntimeError,'对象类型不符'):registry.records()
                self.assertEqual(p.writes,[])
    def test_module_hash_mismatch_refuses_before_memory_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'FLT.dll';file.write_bytes(b'wrong')
            p=Memory();p.modules=lambda:[{'name':'FLT.dll','base':1,'path':str(file)}]
            with patch.object(live_names,'file_version',return_value='18.2.1.8'):
                with self.assertRaisesRegex(RuntimeError,'FLT.dll'):live_names.Registry(p)
            self.assertEqual(p.writes,[])
    def test_legacy_names_round_trip_and_unrepresentable_names_refused(self):
        raw=live_names.encode_name('蓝宝石辉光','mbcs');self.assertEqual(raw.split(b'\0')[0].decode('mbcs'),'蓝宝石辉光')
        self.assertEqual(live_names._name_bytes(raw.hex(),'mbcs'),raw)
        with self.assertRaises(ValueError):live_names.encode_name('😀','mbcs')
    def test_parameter_children_select_old_vector_or_new_map(self):
        for version in ('18.2.1.8','25.3.0.71','26.0.0.67'):
            p=Memory();catalog=parameter_names.ParameterCatalog.__new__(parameter_names.ParameterCatalog);catalog.p=p;catalog.profile=ae_profiles.PROFILES[version]
            root=1000;address=3000;guid=b'g'*16
            if catalog.profile.named_vector:
                p.blocks[root+catalog.profile.group_vector]=struct.pack('<QQ',2000,2008);p.blocks[2000]=struct.pack('<Q',address);p.blocks[address+8]=guid
            else:
                p.blocks[root+0x228]=struct.pack('<Q',2000);p.blocks[2000]=struct.pack('<Q',2100)
                p.blocks[2100]=struct.pack('<QQ',2000,0)+guid+struct.pack('<Q',address)
            self.assertEqual(list(catalog.children(root)),[(address,guid)])
    def test_invalid_old_child_vector_is_rejected(self):
        catalog=parameter_names.ParameterCatalog.__new__(parameter_names.ParameterCatalog);catalog.p=Memory();catalog.profile=ae_profiles.PROFILES['18.2.1.8']
        catalog.p.blocks[1000+catalog.profile.group_vector]=struct.pack('<QQ',2000,1992)
        with self.assertRaises(RuntimeError):list(catalog.children(1000))
    def tree(self,size=3,head_nil=1):
        """AE 26.5's named group: a std::map at +0x220 holding A < B < C (B the root), plus its size."""
        p=Memory();head,a,b,c=5000,5100,5200,5300;guid=lambda n:n.encode()*16
        node=lambda left,parent,right,nil,key,value:struct.pack('<QQQBB6x16sQ',left,parent,right,nil,nil,key,value)
        p.blocks[head]=node(a,b,c,head_nil,b'\0'*16,0)
        p.blocks[b]=node(a,head,c,0,guid('B'),3200);p.blocks[a]=node(head,b,head,0,guid('A'),3100);p.blocks[c]=node(head,b,head,0,guid('C'),3300)
        p.blocks[1000+0x220]=struct.pack('<QQ',head,size)
        catalog=parameter_names.ParameterCatalog.__new__(parameter_names.ParameterCatalog);catalog.p=p
        catalog.profile=replace(ae_profiles.PROFILES['26.0.0.67'],named_map=0x220,named_tree=True);return catalog,p,(a,b,c)
    def test_parameter_children_walk_the_26_5_tree_in_order(self):
        catalog,_,_=self.tree()
        self.assertEqual(list(catalog.children(1000)),[(3100,b'A'*16),(3200,b'B'*16),(3300,b'C'*16)])
    def test_tree_that_does_not_add_up_or_loops_is_refused(self):
        catalog,_,_=self.tree(size=2)
        with self.assertRaisesRegex(RuntimeError,'参数树结构不符'):list(catalog.children(1000))
        catalog,_,_=self.tree(head_nil=0)
        with self.assertRaisesRegex(RuntimeError,'参数树结构不符'):list(catalog.children(1000))
        catalog,p,(a,b,c)=self.tree();raw=bytearray(p.blocks[c]);struct.pack_into('<Q',raw,16,b);p.blocks[c]=bytes(raw)   # C's right points back up
        with self.assertRaisesRegex(RuntimeError,'循环'):list(catalog.children(1000))
    def test_native_layout_names_the_container_kind(self):
        profiles=ae_profiles.PROFILES
        self.assertEqual(parameter_names.native_layout(profiles['26.0.0.67']).split(',')[2:4],['228','0'])
        self.assertEqual(parameter_names.native_layout(profiles['18.2.1.8']).split(',')[3],'1')
        tree=replace(profiles['26.0.0.67'],named_map=0x220,named_tree=True)
        self.assertEqual(parameter_names.native_layout(tree).split(',')[1:4],['220','220','2'])
    def test_native_and_python_adapters_pin_identical_hash_pairs(self):
        source=(Path(__file__).parents[1]/'native'/'ae_parameters.c').read_text(encoding='utf8')
        for p in ae_profiles.PROFILES.values():
            if p.native_dynamic:continue   # v9 receives these hashes in configure and checks them itself
            self.assertIn('"'+p.flt_sha+'","'+p.tdb_sha+'"',source)
    def test_built_in_26_5_uses_the_dynamic_helper_with_its_tree_layout(self):
        p=ae_profiles.PROFILES['26.5.0.89']
        self.assertTrue(p.enabled and p.native_dynamic and p.named_tree and len(p.codec_sha)==64)
        self.assertEqual(parameter_names.native_layout(p).split(',')[1:4],['220','220','2'])
        self.assertTrue(ae_profiles.is_supported('26.5.0.89'))

class FileVariantTests(unittest.TestCase):
    """Same version number, different FLT.dll (patched, slimmed or repackaged AE): the built-in profile must not
    block this machine's own validated one, and an unvalidated variant is sent to automatic adaptation."""
    VERSION='26.0.0.67'
    def setUp(self):
        self.built=ae_profiles.PROFILES[self.VERSION];self.local=replace(self.built,flt_sha='a'*64,tdb_sha='b'*64,manager=self.built.manager+8)
        h=patch.dict(ae_profiles.LOCAL_PROFILES,{},clear=True);h.start();self.addCleanup(h.stop)
        h=patch.dict(live_names._digests,{},clear=True);h.start();self.addCleanup(h.stop)
    def test_the_running_file_picks_the_profile(self):
        self.assertFalse(ae_profiles.ambiguous(self.VERSION))
        self.assertIs(ae_profiles.get_profile(self.VERSION,'a'*64),self.built)   # no validation here: built-in, whose check stops
        self.assertFalse(ae_profiles.is_supported(self.VERSION,'a'*64));self.assertTrue(ae_profiles.is_supported(self.VERSION,self.built.flt_sha))
        ae_profiles.LOCAL_PROFILES[self.VERSION]=self.local
        self.assertTrue(ae_profiles.ambiguous(self.VERSION))
        self.assertIs(ae_profiles.get_profile(self.VERSION,'a'*64),self.local);self.assertIs(ae_profiles.get_profile(self.VERSION,self.built.flt_sha),self.built)
        self.assertTrue(ae_profiles.is_supported(self.VERSION,'a'*64));self.assertIn(self.VERSION+'（本机验证）',ae_profiles.supported_text())
    def host(self,digest):
        getters={self.local.name_get:bytes.fromhex('488d4110c3'),self.local.match_get:bytes.fromhex('488d4140c3'),self.local.category_get:bytes.fromhex('488d4160c3')}
        p=SimpleNamespace(pid=5,creation_time=6,image='AfterFX.exe',modules=lambda:[{'name':'FLT.dll','base':0x1000,'path':'FLT.dll'}],read=lambda a,s:getters[a-0x1000],u64=lambda a:0)
        return p,patch.object(live_names,'module_digest',lambda p,path:digest)
    def test_unvalidated_variant_explains_and_points_to_adaptation(self):
        p,digest=self.host('a'*64)
        with digest,patch.object(live_names,'file_version',return_value=self.VERSION):
            with self.assertRaisesRegex(RuntimeError,'FLT.dll.*自动识别与适配'):live_names.Registry(p)
    def test_validated_variant_is_used_for_reads_and_the_helper(self):
        ae_profiles.LOCAL_PROFILES[self.VERSION]=self.local;p,digest=self.host('a'*64)
        with digest,patch.object(live_names,'file_version',return_value=self.VERSION):
            self.assertIs(live_names.Registry(p).profile,self.local);self.assertIs(live_names.profile_for(p),self.local)
    def test_certificate_for_a_variant_of_a_built_in_version_is_registered(self):
        import local_adapters
        for stored,registered in ((self.local,True),(replace(self.built,manager=self.built.manager),False)):
            ae_profiles.LOCAL_PROFILES.clear()
            resolver=SimpleNamespace(modules={'FLT.dll':{'sha256':stored.flt_sha},'TDB.dll':{'sha256':stored.tdb_sha}},version=stored.version,complete_static=lambda host,s=stored:replace(s,registry_vector=0),close=lambda:None)
            record={'schemaVersion':1,'profile':local_adapters.profile_data(replace(stored,enabled=True,native_dynamic=True)),'checks':{k:True for k in local_adapters.AUTOMATED},'native':{k:True for k in local_adapters.NATIVE},'pendingRestores':0,'codepage':0}
            with tempfile.TemporaryDirectory() as temp,patch.object(local_adapters,'Resolver',return_value=resolver),patch.object(local_adapters,'_crypt',side_effect=lambda data,decrypt=False:data):
                import base64,json
                path=Path(temp)/(stored.flt_sha+'-'+stored.tdb_sha+'.json')
                path.write_text(json.dumps({'schemaVersion':1,'kind':'local-validated-adapter','protected':base64.b64encode(json.dumps(record).encode()).decode()}))
                resolver.complete_static=lambda host,s=stored:replace(s,registry_vector=0,native_dynamic=True,enabled=False)
                local_adapters.load_validated(Path(temp)/'AfterFX.exe',temp)
            self.assertEqual(self.VERSION in ae_profiles.LOCAL_PROFILES,registered)
    def test_version_page_sends_an_unvalidated_variant_to_adaptation(self):
        import ae_paths,app_live
        with tempfile.TemporaryDirectory() as temp:
            support=Path(temp)/'AE'/'Support Files';support.mkdir(parents=True);(support/'AfterFX.exe').write_bytes(b'MZ');(support/'FLT.dll').write_bytes(b'patched')
            install=[{'name':'Adobe After Effects 2026','exe':str(support/'AfterFX.exe'),'plugins':str(Path(temp)/'AE'/'Plug-ins')}]
            self.assertFalse(app_live.discover_spaces(install,lambda path:self.VERSION,Path(temp))[0].live)
            digest=hashlib.sha256(b'patched').hexdigest();ae_profiles.LOCAL_PROFILES[self.VERSION]=replace(self.local,flt_sha=digest)
            self.assertTrue(app_live.discover_spaces(install,lambda path:self.VERSION,Path(temp))[0].live)
            self.assertTrue(ae_paths.compatibility({**install[0],'sources':[]},lambda path:self.VERSION)['modules']['FLT.dll']['matchesAdapter'])

if __name__=='__main__':unittest.main()
