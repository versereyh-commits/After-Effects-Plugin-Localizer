"""Module verification is done once per AE process (0.7.9), never across restarts or file changes."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import hashlib,tempfile,unittest
from unittest.mock import Mock,patch
import live_names,parameter_names
from ae_profiles import PROFILES

class DigestTests(unittest.TestCase):
    def setUp(self):
        h=patch.dict(live_names._digests,{},clear=True);h.start();self.addCleanup(h.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.file=Path(self.temp.name)/'FLT.dll';self.file.write_bytes(b'module')
    def digest(self,p):
        with patch.object(Path,'read_bytes',autospec=True,side_effect=lambda path:b'module' if path.exists() else b'') as read:
            value=live_names.module_digest(p,str(self.file));return value,read.call_count
    def test_same_process_hashes_once(self):
        p=SimpleNamespace(pid=1,creation_time=2)
        self.assertEqual(self.digest(p),(hashlib.sha256(b'module').hexdigest(),1));self.assertEqual(self.digest(p)[1],0)
    def test_restarted_host_hashes_again(self):
        self.digest(SimpleNamespace(pid=1,creation_time=2));self.assertEqual(self.digest(SimpleNamespace(pid=1,creation_time=3))[1],1)
    def test_replaced_file_hashes_again(self):
        p=SimpleNamespace(pid=1,creation_time=2);live_names.module_digest(p,str(self.file))
        self.file.write_bytes(b'changed module');self.assertEqual(live_names.module_digest(p,str(self.file)),hashlib.sha256(b'changed module').hexdigest())

class CodecTests(unittest.TestCase):
    def setUp(self):
        h=patch.dict(live_names._codecs,{},clear=True);h.start();self.addCleanup(h.stop)
        self.profile=replace(PROFILES['26.0.0.67'],version='99.0.0.1',native_dynamic=True,codec_sha='c'*64,encoding='utf8')
        getters={self.profile.name_get:bytes.fromhex('488d4110c3'),self.profile.match_get:bytes.fromhex('488d4140c3'),self.profile.category_get:bytes.fromhex('488d4160c3')}
        digests={'FLT.dll':self.profile.flt_sha,'TDB.dll':self.profile.tdb_sha,'U.dll':self.profile.codec_sha}
        for target,value in (('get_profile',lambda version:self.profile),('file_version',lambda path:'99.0.0.1'),('module_digest',lambda p,path:digests[path])):
            h=patch.object(live_names,target,value);h.start();self.addCleanup(h.stop)
        self.host=lambda created:SimpleNamespace(pid=5,creation_time=created,image='AfterFX.exe',modules=lambda:[{'name':n,'base':0x1000,'path':n} for n in digests],read=lambda a,s:getters[a-0x1000],u64=lambda a:0)
    def test_encoding_is_asked_once_per_ae_process(self):
        with patch.object(parameter_names,'run_native',return_value={'codec':1}) as native:
            live_names.Registry(self.host(6));live_names.Registry(self.host(6));self.assertEqual(native.call_count,1)
            live_names.Registry(self.host(7));self.assertEqual(native.call_count,2)
    def test_wrong_or_failed_encoding_still_stops(self):
        with patch.object(parameter_names,'run_native',return_value={'codec':2}):
            with self.assertRaisesRegex(RuntimeError,'编码'):live_names.Registry(self.host(6))
            with self.assertRaisesRegex(RuntimeError,'编码'):live_names.Registry(self.host(6))
        with patch.object(parameter_names,'run_native',return_value={'error':'x'}):
            with self.assertRaisesRegex(RuntimeError,'编码'):live_names.Registry(self.host(8))
        self.assertNotIn((5,8,self.profile.flt_sha,self.profile.tdb_sha,self.profile.codec_sha),live_names._codecs)

if __name__=='__main__':unittest.main()
