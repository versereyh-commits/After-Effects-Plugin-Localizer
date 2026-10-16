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
                    p.blocks[obj]=bytes(160)
                    with self.assertRaisesRegex(RuntimeError,'对象类型不符'):registry.records()
                self.assertEqual(p.writes,[])
    def test_module_hash_mismatch_refuses_before_memory_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            file=Path(temp)/'FLT.dll';file.write_bytes(b'wrong')
            p=Memory();p.modules=lambda:[{'name':'FLT.dll','base':1,'path':str(file)}]
            with patch.object(live_names,'file_version',return_value='18.2.1.8'):
                with self.assertRaisesRegex(RuntimeError,'哈希'):live_names.Registry(p)
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
    def test_native_and_python_adapters_pin_identical_hash_pairs(self):
        source=(Path(__file__).parents[1]/'native'/'ae_parameters.c').read_text(encoding='utf8')
        for p in ae_profiles.PROFILES.values():
            self.assertIn('"'+p.flt_sha+'","'+p.tdb_sha+'"',source)

if __name__=='__main__':unittest.main()
