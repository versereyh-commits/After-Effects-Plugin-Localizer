import struct,unittest
from types import SimpleNamespace
from unittest.mock import patch
import live_names as L

class CatalogReadTests(unittest.TestCase):
 def session_registry(self):
  from unittest.mock import Mock
  r=L.Registry.__new__(L.Registry);r.base=0x800;r.manager=0x5000
  r.profile=SimpleNamespace(name_base=16,manager=0x100,name_get=1,match_get=2,category_get=3)
  r.modules=[{'name':'FLT.dll','base':0x800,'path':'FLT.dll'}]
  r.p=SimpleNamespace(ensure_alive=Mock(),modules=lambda:list(r.modules),u64=lambda at:r.manager,read=lambda at,n:{0x801:b'\x48\x8d\x41\x10\xc3',0x802:b'\x48\x8d\x41\x40\xc3',0x803:b'\x48\x8d\x41\x60\xc3'}[at])
  return r
 def test_batch_light_checks_revalidate_modules_manager_and_getters(self):
  r=self.session_registry();r.check_session();r.p.ensure_alive.assert_called_once()
  r.p.modules=lambda:[{'name':'FLT.dll','base':0x900,'path':'FLT.dll'}]
  with self.assertRaisesRegex(RuntimeError,'模块'):r.check_session()
 def test_batch_light_checks_stop_on_changed_manager_or_getter(self):
  r=self.session_registry();r.p.u64=lambda at:0x6000
  with self.assertRaisesRegex(RuntimeError,'管理器'):r.check_session()
  r=self.session_registry();r.p.read=lambda *a:b'bad!!'
  with self.assertRaisesRegex(RuntimeError,'接口'):r.check_session()
 def make(self):
  data=bytearray(352)
  for i in range(2):
   at=i*176;struct.pack_into('<Q',data,at,0x900);data[at+16:at+64]=('Effect'+str(i)).encode().ljust(48,b'\0');data[at+64:at+96]=('Key'+str(i)).encode().ljust(32,b'\0');data[at+96:at+160]=b'Lights'.ljust(64,b'\0')
  calls=[]
  def read(at,n):calls.append((at,n));return bytes(data[at-4096:at-4096+n])
  r=L.Registry.__new__(L.Registry);r.p=SimpleNamespace(read=read);r.profile=SimpleNamespace(name_base=16,fc_vtable=0x100,registry_vector=0,encoding='utf8');r.base=0x800;r.manager=0
  return r,data,calls
 def test_contiguous_reads_are_coalesced_and_latest_name_is_returned(self):
  r,data,calls=self.make()
  with patch.object(L,'catalog_addresses',return_value=[4096,4272]):
   self.assertEqual(len(r.records()),2);self.assertEqual(len(calls),1)
   data[16:64]='中文'.encode().ljust(48,b'\0');self.assertEqual(r.target('Key0')['displayName'],'中文');self.assertEqual(len(calls),2)
 def test_changed_type_is_rejected_even_after_cache_hit(self):
  r,data,calls=self.make()
  with patch.object(L,'catalog_addresses',return_value=[4096,4272]):
   r.records();struct.pack_into('<Q',data,0,0x999)
   with self.assertRaisesRegex(RuntimeError,'类型'):r.records()
 def test_gap_read_failure_falls_back_to_individual_fresh_reads(self):
  r,data,calls=self.make();read=r.p.read
  def sparse(at,n):
   if n>160:raise OSError('gap unreadable')
   return read(at,n)
  r.p.read=sparse
  with patch.object(L,'catalog_addresses',return_value=[4096,4272]):self.assertEqual(len(r.records()),2)
  self.assertEqual(len(calls),2)
 def test_returned_rows_cannot_poison_parse_cache(self):
  r,data,calls=self.make()
  with patch.object(L,'catalog_addresses',return_value=[4096,4272]):
   r.records()[0]['matchName']='Wrong';self.assertEqual(r.target('Key0')['matchName'],'Key0')
 def test_directory_rebuild_uses_new_addresses_and_discards_old_cache(self):
  r,data,calls=self.make()
  with patch.object(L,'catalog_addresses',return_value=[4096,4272]):r.records()
  with patch.object(L,'catalog_addresses',return_value=[4272]):self.assertEqual([x['matchName'] for x in r.records()],['Key1'])
  self.assertNotIn(4096,r._record_cache)
 def test_failed_reads_never_return_cached_rows(self):
  r,data,calls=self.make()
  with patch.object(L,'catalog_addresses',return_value=[4096,4272]):
   r.records();r.p.read=lambda *a:(_ for _ in ()).throw(RuntimeError('host exited'))
   with self.assertRaisesRegex(RuntimeError,'exited'):r.records()

if __name__=='__main__':unittest.main()
