import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from core import resources,inspect_plugin,pipl_properties,pascal_string,file_hash
from runtime import rename_gate,file_version,native_probe


def pipl(name="Glow",match="vendor:Glow",category="灯光"):
    properties=[]
    for key,text in (("name",name),("eMNA",match),("catg",category)):
        raw=text.encode("utf-8");payload=bytes([len(raw)])+raw
        properties.append(struct.pack("<4s4sII",b"MIB8",key[::-1].encode(),0,len(payload))+payload+b"\0"*((-len(payload))%4))
    return struct.pack("<HII",1,0,len(properties))+b"".join(properties)


def pe_fixture(is64=True):
    data=bytearray(2048);data[:2]=b"MZ";struct.pack_into("<I",data,60,0x80)
    data[0x80:0x84]=b"PE\0\0";struct.pack_into("<H",data,0x86,1)
    optlen=240 if is64 else 224;struct.pack_into("<H",data,0x94,optlen)
    opt=0x98;struct.pack_into("<H",data,opt,0x20b if is64 else 0x10b)
    struct.pack_into("<II",data,opt+(112 if is64 else 96)+16,0x1000,0x400)
    section=opt+optlen;data[section:section+8]=b".rsrc\0\0\0";struct.pack_into("<IIII",data,section+8,0x400,0x1000,0x400,0x200)
    root=0x200
    for relative,named,ids in ((0,1,0),(0x20,0,1),(0x40,0,1)):
        struct.pack_into("<IIHHHH",data,root+relative,0,0,0,0,named,ids)
    struct.pack_into("<II",data,root+16,0x80000060,0x80000020)
    struct.pack_into("<II",data,root+0x30,16000,0x80000040)
    struct.pack_into("<II",data,root+0x50,1033,0x80)
    struct.pack_into("<H",data,root+0x60,4);data[root+0x62:root+0x6a]="PIPL".encode("utf-16le")
    payload=pipl();struct.pack_into("<IIII",data,root+0x80,0x1100,len(payload),0,0);data[root+0x100:root+0x100+len(payload)]=payload
    return bytes(data)


class ResourceTests(unittest.TestCase):
    def test_realistic_pe32_and_pe64_keep_display_and_match_distinct(self):
        for is64 in (False,True):
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/"中文插件.aex";path.write_bytes(pe_fixture(is64));before=file_hash(path)
                effects=inspect_plugin(path)
                self.assertEqual(len(effects),1)
                self.assertEqual(effects[0].display_name,"Glow")
                self.assertEqual(effects[0].match_name,"vendor:Glow")
                self.assertEqual(effects[0].category,"灯光")
                self.assertEqual(effects[0].language,1033)
                self.assertEqual(file_hash(path),before)

    def test_malformed_pe_offsets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"broken.aex";path.write_bytes(b"MZ"+bytes(60))
            with self.assertRaises(ValueError):resources(path)

    def test_recursive_resource_directory_is_rejected(self):
        data=bytearray(pe_fixture());struct.pack_into("<II",data,0x210,0x80000060,0x80000000)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"cycle.aex";path.write_bytes(data)
            with self.assertRaises(ValueError):resources(path)

    def test_invalid_pipl_length_is_rejected(self):
        payload=bytearray(pipl());struct.pack_into("<I",payload,22,0x7fffffff)
        with self.assertRaises(ValueError):pipl_properties(payload)

    def test_observed_continuum_inclusive_length_quirk(self):
        self.assertEqual(pascal_string(b"\x08Glow\0\0\0"),"Glow")
        with self.assertRaises(ValueError):pascal_string(b"\xffabc")

    def test_runtime_writer_remains_gated_for_unverified_catalog_change(self):
        self.assertFalse(rename_gate(None)[0])
        self.assertFalse(rename_gate({"hostChangeObserved":True})[0])

    def test_native_module_is_read_only(self):
        result=native_probe()
        self.assertEqual(result["mode"],"read_only")
        self.assertEqual(result["memoryWrites"],0)
        self.assertFalse(result["runtimeRenameEnabled"])

    def test_installed_ae_version_is_read_from_binary(self):
        # Optional real-host check: point AE_LOCALIZER_TEST_AE at an AE 2026 (26.0.0.67) AfterFX.exe to run it.
        path=Path(os.environ.get("AE_LOCALIZER_TEST_AE") or "missing")
        if not path.is_file():self.skipTest("Set AE_LOCALIZER_TEST_AE to an AE 26.0.0.67 AfterFX.exe")
        self.assertEqual(file_version(path),"26.0.0.67")


if __name__=="__main__":unittest.main()
