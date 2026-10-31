import json,tempfile,unittest
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
import effect_menu_names as M
import test_effect_categories as fixtures

def row(text,path=(4,1,0),command=101,submenu=0,type=0):return dict(text=text,path=list(path),id=command,submenu=submenu,type=type,menu=20,index=path[-1])

class FakeMenus:
    rows=[];calls=[];path=None;fail=False
    def __init__(self,hwnd):pass
    def snapshot(self):return [dict(r) for r in self.rows]
    def put(self,item,text):
        if not self.path.exists():raise AssertionError('journal missing')
        if self.fail:raise RuntimeError('menu rebuilt')
        target=next(r for r in self.rows if r['path']==item['path'])
        if target['text']!=item['text']:raise RuntimeError('changed')
        target['text']=text;self.calls.append(text)

class MenuNames(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.p=fixtures.Memory()
        self.nj={'pid':123,'creationTime':456,'entries':{'Key0':{'originalName':'Old0'}}};self.cj={'entries':{}}
        self.session=M.MenuNameSession(SimpleNamespace(journal=lambda:self.nj),SimpleNamespace(journal=lambda:self.cj));self.session.path=Path(self.tmp.name)/'menus.json'
        FakeMenus.rows=[row('Old0\tCtrl+X'),row('Lights',(4,1),-1,30)];FakeMenus.calls=[];FakeMenus.path=self.session.path;FakeMenus.fail=False
        for key,value in (('_process_lock',lambda:nullcontext()),('current_process',lambda **kw:self.p),('Registry',fixtures.Registry),('Menus',FakeMenus),('main_window',lambda pid:1)):
            p=patch.object(M,key,value);p.start();self.addCleanup(p.stop)
    def sync(self):return self.session.sync(123,456,'AfterFX.exe')
    def test_effect_sync_preserves_command_shortcut_and_restores(self):
        r=self.sync();self.assertEqual(r['updated'],1);self.assertEqual(FakeMenus.rows[0]['text'],'Effect0\tCtrl+X');self.assertEqual(FakeMenus.rows[0]['id'],101)
        self.nj['entries']={};self.p.blocks[4096][16:64]=fixtures.E.encode_name('Old0')
        self.sync();self.assertEqual(FakeMenus.rows[0]['text'],'Old0\tCtrl+X');self.assertEqual(json.loads(self.session.path.read_text())['entries'],{})
    def test_category_caption_and_restore(self):
        self.nj['entries']={};self.cj={'pid':123,'creationTime':456,'entries':{k:{'originalName':'Lights'} for k in ('Key0','Key1')}}
        for base in (4096,8192):self.p.blocks[base][96:160]='灯光'.encode().ljust(64,b'\0')
        self.sync();self.assertEqual(FakeMenus.rows[1]['text'],'灯光')
        self.cj['entries']={}
        for base in (4096,8192):self.p.blocks[base][96:160]=b'Lights'.ljust(64,b'\0')
        self.sync();self.assertEqual(FakeMenus.rows[1]['text'],'Lights')
    def test_wrong_host_never_sets_caption(self):
        self.p.creation_time+=1
        with self.assertRaisesRegex(RuntimeError,'宿主'):self.sync()
        self.assertEqual(FakeMenus.calls,[])
    def test_same_version_other_module_does_not_sync(self):
        class Other(fixtures.Registry):
            def __init__(self,p):super().__init__(p);self.profile.flt_sha='0'*64
        with patch.object(M,'Registry',Other):self.assertFalse(self.sync()['supported'])
        self.assertEqual(FakeMenus.calls,[])
    def test_ambiguous_effect_does_not_write(self):
        FakeMenus.rows.append(row('Old0',(4,2,0),102))
        result=self.sync();self.assertEqual(result['skipped'][0]['reason'],'ambiguous_menu')
        self.assertEqual(FakeMenus.calls,[])
    def test_repeat_last_effect_command_is_not_renamed(self):
        recent=row('Old0\tCtrl+Alt+Shift+E',(4,1),77);FakeMenus.rows.append(recent)
        self.sync();self.assertEqual(recent['text'],'Old0\tCtrl+Alt+Shift+E');self.assertEqual(FakeMenus.rows[0]['text'],'Effect0\tCtrl+X')
    def test_ownerdraw_and_separator_are_not_targets(self):
        for flag in (0x100,0x800):
            FakeMenus.rows[0]['type']=flag
            self.assertEqual(len(self.sync()['skipped']),1)
        self.assertEqual(FakeMenus.calls,[])
    def test_failed_write_keeps_record_for_retry(self):
        FakeMenus.fail=True
        with self.assertRaisesRegex(RuntimeError,'rebuilt'):self.sync()
        self.assertTrue(json.loads(self.session.path.read_text())['entries'])
        FakeMenus.fail=False;self.sync();self.assertEqual(FakeMenus.rows[0]['text'],'Effect0\tCtrl+X')
    def test_external_caption_conflict_is_not_overwritten(self):
        self.sync();FakeMenus.rows[0]['text']='External'
        self.assertEqual(len(self.sync()['skipped']),1)
        self.assertEqual(FakeMenus.rows[0]['text'],'External')
    def test_corrupt_record_is_preserved(self):
        self.session.path.write_text('{bad')
        with self.assertRaisesRegex(RuntimeError,'记录损坏'):self.sync()
        self.assertEqual(self.session.path.read_text(),'{bad');self.assertEqual(FakeMenus.calls,[])
    def test_missing_effect_is_not_reassociated_by_similar_name(self):
        self.nj['entries']={'Missing':{'originalName':'Old0'}}
        self.assertEqual(self.sync()['skipped'][0]['reason'],'effect_not_loaded')
        self.assertEqual(FakeMenus.calls,[])
    def test_missing_first_item_does_not_abort_other_items(self):
        self.nj['entries']={'Missing':{'originalName':'Hidden'},'Key0':{'originalName':'Old0'}}
        result=self.sync();self.assertEqual(result['updated'],1);self.assertEqual(len(result['skipped']),1)
        self.assertEqual(FakeMenus.rows[0]['text'],'Effect0\tCtrl+X')
    def test_ambiguous_first_item_does_not_abort_unique_item(self):
        FakeMenus.rows.extend([row('Old0',(4,2,0),102),row('Old1',(4,3,0),103)])
        self.nj['entries']['Key1']={'originalName':'Old1'}
        result=self.sync();self.assertEqual(result['updated'],1);self.assertEqual(result['skipped'][0]['key'],'Key0')
        self.assertEqual(FakeMenus.rows[-1]['text'],'Effect1')
    def aliases(self,same=True):
        self.nj['entries']['Key1']={'originalName':'Old0'}
        if same:self.p.blocks[8192][16:64]=fixtures.E.encode_name('Effect0')
    def test_same_alias_translation_writes_once_and_restores_once(self):
        self.aliases();result=self.sync();self.assertEqual(result['updated'],1);self.assertEqual(result['matched'],2)
        self.assertEqual(len(json.loads(self.session.path.read_text())['entries']),2)
        self.nj['entries']={}
        for at in (4096,8192):self.p.blocks[at][16:64]=fixtures.E.encode_name('Old0')
        self.assertEqual(self.sync()['updated'],1);self.assertEqual(json.loads(self.session.path.read_text())['entries'],{})
    def test_conflicting_aliases_skipped_without_guessing(self):
        self.aliases(False);result=self.sync();self.assertEqual(result['updated'],0);self.assertEqual(len(result['skipped']),2)
        self.assertTrue(all(x['reason']=='alias_translation_conflict' for x in result['skipped']));self.assertEqual(FakeMenus.calls,[])
    def test_partial_alias_restore_keeps_record_until_consensus(self):
        self.aliases();self.sync();self.nj['entries'].pop('Key0');self.p.blocks[4096][16:64]=fixtures.E.encode_name('Old0')
        result=self.sync();self.assertEqual(result['updated'],0);self.assertEqual(len(result['skipped']),2)
        self.assertEqual(FakeMenus.rows[0]['text'],'Effect0\tCtrl+X')
        self.nj['entries']={};self.p.blocks[8192][16:64]=fixtures.E.encode_name('Old0')
        self.sync();self.assertEqual(json.loads(self.session.path.read_text())['entries'],{})
    def test_already_translated_menu_is_tracked_for_later_restore(self):
        FakeMenus.rows[0]['text']='Effect0\tCtrl+X';self.assertEqual(self.sync()['updated'],0)
        self.assertTrue(json.loads(self.session.path.read_text())['entries'])
        self.nj['entries']={};self.p.blocks[4096][16:64]=fixtures.E.encode_name('Old0');self.sync()
        self.assertEqual(FakeMenus.rows[0]['text'],'Old0\tCtrl+X')
    def test_physical_target_conflict_is_skipped(self):
        self.nj['entries']={'Key0':{'originalName':'X&Y'},'Key1':{'originalName':'X&&Y'}};FakeMenus.rows[0]['text']='X&&Y'
        result=self.sync();self.assertEqual(result['updated'],0);self.assertEqual(len(result['skipped']),2)
        self.assertTrue(all(x['reason']=='menu_target_conflict' for x in result['skipped']))
    def test_large_batch_with_hidden_entry_still_updates_visible_items(self):
        class Many(fixtures.Registry):
            def records(self):return [dict(matchName='K'+str(i),displayName='译'+str(i)) for i in range(1000)]
        self.nj['entries']={'K'+str(i):{'originalName':'Original'+str(i)} for i in range(1000)}
        FakeMenus.rows=[row('Original'+str(i),(4,2,i),100+i) for i in range(1,1000)]
        with patch.object(M,'Registry',Many):result=self.sync()
        self.assertEqual(result['updated'],999);self.assertEqual(len(result['skipped']),1)
    def test_timeout_before_or_after_journal_does_not_write(self):
        with self.assertRaises(TimeoutError):self.session.sync(123,456,'AfterFX.exe',deadline=0)
        self.assertEqual(FakeMenus.calls,[])
        expired=[];save=M._save
        def persist(path,value):save(path,value);expired.append(True)
        with patch.object(M.time,'monotonic',side_effect=lambda:11 if expired else 0),patch.object(M,'_save',side_effect=persist),self.assertRaises(TimeoutError):self.session.sync(123,456,'AfterFX.exe',deadline=10)
        self.assertEqual(FakeMenus.calls,[])

if __name__=='__main__':unittest.main()
