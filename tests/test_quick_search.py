import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import json,shutil,subprocess,unittest
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtGui import QKeySequence
import app_live,parameter_names
import quick_search as qs
from quick_search import Entry,build_entries,initials,pinyin_hit,search,virtual_key
from shortcuts import Keymap,problem,sequence
from test_gui import GuiCase,plugin

class PinyinTests(unittest.TestCase):
    def test_initials_of_common_effect_names(self):
        for name,expected in (('高斯模糊','gsmh'),('色相/饱和度','sxbhd'),('径向擦除','jxcc'),('湍流置换','tlzh'),('CC 球体','cqt'),('Gaussian Blur','gb')):
            self.assertEqual(initials(name),expected,name)
    def test_second_level_characters_and_polyphones(self):
        self.assertEqual(initials('渲染'),'xr');self.assertEqual(initials('阈值'),'yz')
        self.assertEqual(pinyin_hit('重复边缘像素','cfby'),2);self.assertEqual(pinyin_hit('重复边缘像素','zfby'),2)   # 重 is zhong or chong
        self.assertEqual(pinyin_hit('高斯模糊','smh'),1);self.assertEqual(pinyin_hit('高斯模糊','hm'),0)
    def test_characters_without_an_initial_are_skipped(self):
        self.assertEqual(initials('模糊™'),'mh')

def entry(key,english,chinese='',category='',extra=''):return Entry(key,english,chinese,category,extra)

class SearchTests(unittest.TestCase):
    entries=[entry('ADBE Gaussian Blur 2','Gaussian Blur','高斯模糊','模糊和锐化'),entry('ADBE Fast Box Blur','Fast Box Blur','快速方框模糊','模糊和锐化'),
             entry('ADBE Glo2','Glow','发光','风格化'),entry('S_Glow','S_Glow','','Sapphire Lighting'),entry('ADBE Box Blur2','Box Blur','','模糊和锐化')]
    def titles(self,query):return [e.title for e in search(self.entries,query)]
    def test_chinese_english_and_initials(self):
        self.assertEqual(self.titles('高斯')[0],'高斯模糊');self.assertEqual(self.titles('gsmh'),['高斯模糊'])
        self.assertEqual(self.titles('gaus')[0],'高斯模糊');self.assertEqual(self.titles('glow')[0],'发光')
        self.assertEqual(self.titles('fg')[0],'发光')
        self.assertIn('Box Blur',self.titles('box'));self.assertEqual(self.titles('模糊')[0],'高斯模糊')
    def test_names_starting_with_the_query_come_first_in_name_order(self):
        entries=[entry('b','BCC Rain','BCC 雨'),entry('z','CC Snow','CC 雪'),entry('a','CC Rain','CC 雨'),entry('m','CC Ball','CC 球')]+[entry(f'x{i}',f'CC Extra {i}',f'CC 额外{i:02}') for i in range(20)]
        found=[e.title for e in search(entries,'cc',limit=qs.MAX_RESULTS)]
        self.assertEqual(len(found),24);self.assertEqual(found[-1],'BCC 雨')       # every match; the one only containing CC last
        self.assertEqual(found[:23],sorted(found[:23],key=str.casefold))
    def test_internal_name_and_category_still_match(self):
        self.assertIn('S_Glow',self.titles('sapphire'));self.assertEqual(self.titles('glo2'),['发光'])
    def test_nothing_for_an_empty_or_unknown_query(self):
        self.assertEqual(search(self.entries,'   '),search(self.entries,'')[:12]);self.assertEqual(self.titles('zzzzqq'),[])

class EntryTests(unittest.TestCase):
    plugins=[plugin('Gaussian Blur','ADBE Gaussian Blur 2','Blur & Sharpen','a.aex'),plugin('Glow','ADBE Glo2','Stylize','b.aex'),plugin('Shine','tc Shine','RG','c.aex')]
    def test_without_ae_every_plugin_with_its_candidate(self):
        found=build_entries(self.plugins,{'ADBE Glo2':'发光'})
        self.assertEqual([(e.match_name,e.chinese) for e in found],[('ADBE Gaussian Blur 2',''),('ADBE Glo2','发光'),('tc Shine','')])
    def test_connected_lists_only_loaded_effects_and_prefers_ae_names(self):
        live=[{'matchName':'ADBE Gaussian Blur 2','displayName':'高斯模糊'},{'matchName':'ADBE Glo2','displayName':'Glow'},{'matchName':'ADBE Extra','displayName':'额外'}]
        found={e.match_name:e for e in build_entries(self.plugins,{'ADBE Gaussian Blur 2':'高斯模糊 2','ADBE Glo2':'发光'},live)}
        self.assertNotIn('tc Shine',found)
        self.assertEqual(found['ADBE Gaussian Blur 2'].chinese,'高斯模糊');self.assertEqual(found['ADBE Gaussian Blur 2'].extra,'高斯模糊 2')
        self.assertEqual(found['ADBE Glo2'].chinese,'发光')     # not applied yet: the candidate
        self.assertEqual(found['ADBE Extra'].chinese,'额外')

class DisplayTests(GuiCase):
    def test_rows_show_only_the_name_and_categories_are_readable(self):
        from core import readable_category
        self.assertEqual(readable_category('$$$/AE/Effect/Category/3DChannel=3D Channel'),'3D Channel');self.assertEqual(readable_category('模拟'),'模拟')
        box=qs.QuickSearch([Entry('CC Rain','CC Rain','CC Rain (CC 雨)','Simulation'),Entry('EXtractoR','EXtractoR','','3D Channel')])
        box.edit.setText('c');self.assertEqual(box.list.item(0).text().strip(),'CC Rain (CC 雨)')
        box.edit.setText('extractor');self.assertEqual(box.list.item(0).text().strip(),'EXtractoR')
        box.edit.setText('=');self.assertEqual(box.list.count(),0)     # the raw category keys no longer match
        found=build_entries([plugin('EXtractoR','EXtractoR','$$$/AE/Effect/Category/3DChannel=3D Channel','a.aex')],{})
        self.assertEqual(found[0].category,'3D Channel')

class SizeTests(GuiCase):
    def settle(self):
        import time
        end=time.time()+.3
        while time.time()<end:self.app.processEvents()
    def test_the_box_is_compact_again_after_an_add_esc_or_clicking_away(self):
        from PySide6.QtCore import QPoint
        entries=[Entry(f'm{i}',f'Blur {i}',f'模糊{i}','') for i in range(20)]
        box=qs.QuickSearch();box.open(entries,0,QPoint(50,50));self.settle();compact=box.height()
        box.edit.setText('mh');self.settle();self.assertGreater(box.height(),compact+200)   # results make it taller (8 rows at most)
        tall=box.height();box.edit.setText('mhh mh mh');self.settle();self.assertLessEqual(box.height(),tall)
        for leave in (lambda:(box.choose(0),box.finished()),box.dismiss,box.hide):
            box.open(entries,0,QPoint(50,50));box.edit.setText('mh');self.settle()
            leave();box.open(entries,0,QPoint(50,50));self.settle()
            self.assertEqual(box.height(),compact)
        box.close()
    def test_it_appears_at_its_final_size_without_a_resize_after_showing(self):
        from PySide6.QtCore import QEvent,QObject,QPoint
        entries=[Entry(f'm{i}',f'Blur {i}',f'模糊{i}','') for i in range(20)]
        box=qs.QuickSearch();box.open(entries,0,QPoint(50,50));self.settle();compact=box.height()
        box.edit.setText('mh');self.settle();box.choose(0);box.finished();self.settle()
        seen=[]
        class Spy(QObject):
            def eventFilter(self,obj,event):
                if obj is box and event.type() in (QEvent.Type.Show,QEvent.Type.Resize):seen.append((event.type(),box.height()))
                return False
        spy=Spy();box.installEventFilter(spy);box.open(entries,0,QPoint(50,50));self.settle()
        shown=[i for i,(kind,_) in enumerate(seen) if kind==QEvent.Type.Show]
        self.assertEqual(seen[shown[0]][1],compact);self.assertEqual([h for kind,h in seen[shown[0]:] if kind==QEvent.Type.Resize],[])
        box.close()
    def test_it_becomes_visible_only_after_it_is_painted(self):
        from PySide6.QtCore import QPoint
        box=qs.QuickSearch();steps=[];later=[]
        with patch.object(box,'setWindowOpacity',lambda v:steps.append(('opacity',v))),patch.object(box,'repaint',lambda:steps.append('paint')),             patch.object(qs.QTimer,'singleShot',lambda ms,fn:later.append(fn)):
            box.open([Entry('a','A','甲','')],0,QPoint(50,50));steps.append('opened')
            for fn in later:fn()     # the next turn of the event loop
        self.assertEqual(steps[0],('opacity',0.0));self.assertLess(steps.index('paint'),steps.index(('opacity',1.0)))
        self.assertLess(steps.index('opened'),steps.index(('opacity',1.0)))   # revealed on the next turn of the event loop
        box.close()
    def test_enter_changes_nothing_on_screen_unless_the_add_is_slow(self):
        from PySide6.QtCore import QPoint
        box=qs.QuickSearch();later=[]
        with patch.object(qs.QTimer,'singleShot',lambda ms,fn:later.append((ms,fn))):
            box.open([Entry('a','A','甲','')],0,QPoint(50,50));later.clear()
            box.edit.setText('甲');self.settle();before=(box.height(),box.note.text(),box.edit.isEnabled())
            box.choose(0);self.settle()
            self.assertEqual((box.height(),box.note.text(),box.edit.isEnabled()),before);self.assertTrue(box.edit.isReadOnly())
            [(ms,slow)]=later;self.assertEqual(ms,400);slow();self.assertIn('正在添加',box.note.text())   # still busy after 0.4 s
            box.finished();self.assertFalse(box.edit.isReadOnly())
        box.close()
    def test_a_short_query_lists_every_match_and_scrolls(self):
        from PySide6.QtCore import QPoint
        entries=[Entry(f'CC {i}',f'CC Effect {i}',f'CC 效果{i}','') for i in range(60)]
        box=qs.QuickSearch();box.open(entries,0,QPoint(50,50));box.edit.setText('cc');self.settle()
        self.assertEqual(box.list.count(),60);self.assertEqual(box.list.height(),8*qs.ROW+4)    # all of them, 8 rows visible
        for _ in range(59):box.eventFilter(box.edit,__import__('PySide6.QtGui',fromlist=['QKeyEvent']).QKeyEvent(__import__('PySide6.QtCore',fromlist=['QEvent']).QEvent.Type.KeyPress,__import__('PySide6.QtCore',fromlist=['Qt']).Qt.Key.Key_Down,__import__('PySide6.QtCore',fromlist=['Qt']).Qt.KeyboardModifier.NoModifier))
        self.assertEqual(box.list.currentRow(),59)
        box.close()

class HotkeyTests(unittest.TestCase):
    def test_virtual_keys(self):
        self.assertEqual(virtual_key(sequence('Shift+Space')),(qs.MOD_SHIFT|qs.MOD_NOREPEAT,0x20))
        self.assertEqual(virtual_key(sequence('Ctrl+Alt+K')),(qs.MOD_CONTROL|qs.MOD_ALT|qs.MOD_NOREPEAT,ord('K')))
        self.assertEqual(virtual_key(sequence('F9')),(qs.MOD_NOREPEAT,0x78));self.assertIsNone(virtual_key(QKeySequence()))
    def test_only_the_quick_search_key_may_be_shift_space(self):
        self.assertEqual(problem('quick_search',sequence('Shift+Space')),'');self.assertEqual(problem('quick_search',sequence('Ctrl+Space')),'')
        self.assertIn('Ctrl',problem('quick_search',sequence('Shift+A')));self.assertIn('Ctrl',problem('quick_search',sequence('Space')))
        self.assertIn('Ctrl',problem('translate',sequence('Shift+Space')))
    def test_inactive_hotkey_never_registers_and_follows_the_keymap(self):
        hotkey=qs.AeHotkey(active=False)
        with patch.object(qs,'foreground',side_effect=AssertionError('no window lookups offscreen')):
            keymap=Keymap(Path(os.devnull));keymap.bind('quick_search',hotkey)
            self.assertEqual(hotkey.key,(qs.MOD_CONTROL|qs.MOD_NOREPEAT,0x20));self.assertFalse(hotkey.registered)
            keymap.enabled=False;keymap.apply();self.assertIsNone(hotkey.key)

    def test_a_key_held_by_another_program_is_reported_once(self):
        hotkey=qs.AeHotkey(active=False);said=[];hotkey.unavailable.connect(said.append)
        hotkey.setKeys([sequence('Ctrl+Space')]);hotkey.active=True
        with patch.object(qs,'foreground',lambda:5),patch.object(qs,'window_image',lambda hwnd:'afterfx.exe'),patch.object(qs.ctypes.windll.user32,'RegisterHotKey',lambda *a:0):
            hotkey.follow();hotkey.follow()
        self.assertFalse(hotkey.registered);self.assertEqual(len(said),1);self.assertIn('Space',said[0])

class AddEffectTests(unittest.TestCase):
    def test_add_effect_needs_no_helper_and_reports_ae_errors(self):
        class Process:
            pid=1;image='AfterFX.exe'
            def __enter__(self):return self
            def __exit__(self,*a):pass
        calls=[]
        def run(p,operations,timeout=25,action='labels',**kw):calls.append((operations,action));return {'changed':2,'skipped':1,'requestId':'x','action':action}
        with patch.object(parameter_names,'current_process',lambda **kw:Process()),patch.object(parameter_names,'run_native',run),patch('cep_bridge.connected',lambda:False):
            result=parameter_names.add_effect('ADBE Gaussian Blur 2')
            self.assertEqual((result['changed'],result['skipped'],result['route']),(2,1,'relay'));self.assertEqual(calls,[(['ADBE Gaussian Blur 2'],'addEffect')])
        with patch.object(parameter_names,'current_process',lambda **kw:Process()),patch.object(parameter_names,'run_native',lambda *a,**k:{'error':'Error: 请先在合成中选中要添加效果的图层'}),patch('cep_bridge.connected',lambda:False):
            with self.assertRaisesRegex(RuntimeError,'^请先在合成中选中'):parameter_names.add_effect('ADBE Gaussian Blur 2')
        with self.assertRaises(ValueError):parameter_names.add_effect('')
    def test_the_hotkey_window_gives_the_process_without_a_listing(self):
        import win_process
        seen=[]
        def run(p,operations,timeout=25,action='labels',**kw):seen.append((p.pid,p.image));return {'changed':1}
        with patch.object(win_process,'window_process',lambda hwnd:(42,r'A:\AE\Support Files\AfterFX.exe')),patch('host_identity.query',lambda pid:(r'A:\AE\Support Files\AfterFX.exe',4)),patch.object(parameter_names,'run_native',run),\
             patch.object(parameter_names,'current_process',side_effect=AssertionError('no listing')),patch('cep_bridge.connected',lambda:False):
            self.assertEqual(parameter_names.add_effect('ADBE Glo2',ae_window=7)['changed'],1)
        self.assertEqual(seen,[(42,r'A:\AE\Support Files\AfterFX.exe')])
        with patch.object(win_process,'window_process',lambda hwnd:(43,r'C:\Windows\notepad.exe')):
            with self.assertRaisesRegex(RuntimeError,'不是 AE'):parameter_names.add_effect('ADBE Glo2',ae_window=7)
    def test_timing_record_keeps_numbers_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'t.jsonl'
            for _ in range(205):qs.record_timing(path,.3456,'relay',2,True)
            lines=path.read_text(encoding='utf8').splitlines();self.assertEqual(len(lines),200)
            self.assertEqual({k:v for k,v in json.loads(lines[-1]).items() if k!='time'},{'ms':346,'route':'relay','layers':2,'ok':True})

RUNNER=r'''
const fs=require('fs'),vm=require('vm');let source=fs.readFileSync(0,'utf8'),files={};
const setup=JSON.parse(process.argv[1]);
function File(path){this.fsName=path;this.parent={fsName:'.'};this.exists=path==='permit';this.open=()=>true;this.write=text=>files[path]=text;this.close=()=>{};}
function CompItem(){}
const added=[],undo=[],titles=[];
function layer(i,ok){return {index:i,property:name=>name==='ADBE Effect Parade'?{canAddProperty:m=>ok,addProperty:m=>{added.push([i,m]);const fx={name:'CC Drizzle'};titles.push(fx);return fx;}}:null};}
const comp=new CompItem();comp.selectedLayers=setup.layers.map((ok,i)=>layer(i+1,ok));
const app={version:'26.5x89',beginUndoGroup:n=>undo.push('begin '+n),endUndoGroup:()=>undo.push('end'),
  project:{file:null,numItems:0,renderQueue:{rendering:false},activeItem:setup.comp?comp:null}};
const request={action:setup.action||'addEffect',hostVersion:setup.action?'wrong':null,permit:'permit',output:'output',expires:Date.now()+100000,helper:'',operations:setup.title?['ADBE Gaussian Blur 2',setup.title]:['ADBE Gaussian Blur 2']};
request.targetHost={pid:3,creationTime:'0000000000000004',image:'C:/AE/AfterFX.exe'};request.identityHelper='identity';
vm.runInNewContext(source.replace('@@REQUEST@@',JSON.stringify(request)),{app,File,CompItem,JSON:setup.noJSON?undefined:JSON,ExternalObject:function(){return {identity:()=>JSON.stringify(request.targetHost)};}},{timeout:3000});
process.stdout.write(JSON.stringify({added,undo,titles:titles.map(t=>t.name),result:JSON.parse(files.output)}));
'''

@unittest.skipUnless(shutil.which('node'),'Node is required for isolated JSX tests')
class BridgeScriptTests(unittest.TestCase):
    def run_script(self,**setup):
        source=(Path(__file__).resolve().parents[1]/'scripts'/'parameter_bridge.jsx').read_text(encoding='utf8')
        out=subprocess.run([shutil.which('node'),'-e',RUNNER,json.dumps(setup)],input=source,text=True,encoding='utf8',capture_output=True,check=True)
        return json.loads(out.stdout)
    def test_adds_to_every_selected_layer_that_takes_it_in_one_undo_step(self):
        data=self.run_script(comp=True,layers=[True,False,True])
        self.assertEqual(data['added'],[[1,'ADBE Gaussian Blur 2'],[3,'ADBE Gaussian Blur 2']]);self.assertEqual(data['undo'],['begin 添加效果','end'])
        self.assertEqual((data['result']['changed'],data['result']['skipped']),(2,1));self.assertNotIn('error',data['result'])
    def test_no_composition_or_no_selected_layer_is_said_plainly(self):
        self.assertIn('打开一个合成',self.run_script(comp=False,layers=[])['result']['error'])
        data=self.run_script(comp=True,layers=[]);self.assertIn('选中要添加效果的图层',data['result']['error']);self.assertEqual(data['added'],[])
        self.assertIn('不能添加这个效果',self.run_script(comp=True,layers=[False])['result']['error'])
    def test_old_engine_without_global_json_can_add_safely(self):
        data=self.run_script(comp=True,layers=[True],noJSON=True)
        self.assertEqual(data['result']['changed'],1);self.assertNotIn('error',data['result'])
    def test_a_new_effect_gets_the_name_the_effects_list_shows(self):
        self.assertEqual(self.run_script(comp=True,layers=[True,True],title='CC Drizzle (CC 细雨)')['titles'],['CC Drizzle (CC 细雨)']*2)
        self.assertEqual(self.run_script(comp=True,layers=[True])['titles'],['CC Drizzle'])
    def test_a_missing_build_check_is_only_allowed_for_adding_effects(self):
        self.assertIn('AE 构建不匹配',self.run_script(comp=True,layers=[True],action='selection')['result']['error'])

class WindowTests(GuiCase):
    def test_box_lists_the_table_and_reports_the_result(self):
        window=self.window;window.pending['tc Shine']='闪耀'
        window.open_quick_search(0);self.assertTrue(window.quick.isVisible())
        window.quick.edit.setText('sy');self.assertEqual(window.quick.list.item(0).data(256).match_name,'tc Shine')
        with patch.object(window,'quick_add',lambda entry:None):
            window.quick.chosen.disconnect();window.quick.chosen.connect(window.quick_add)
            window.quick.choose(0);self.assertTrue(window.quick.busy)
        found=window.quick.list.item(0).data(256)
        window.quick_added(found,'请先在合成中选中要添加效果的图层',0.4)
        self.assertTrue(window.quick.isVisible());self.assertFalse(window.quick.busy);self.assertIn('选中',window.quick.note.text())
        window.quick_added(found,{'changed':1,'skipped':0,'route':'relay'},0.4)
        self.assertFalse(window.quick.isVisible());self.assertIn('快速添加“闪耀”',window.log.toPlainText());self.assertIn('0.40 秒',window.log.toPlainText())

class ParameterAutoTests(GuiCase):
    def entry(self):return Entry('tc Shine','Shine','闪耀','RG')
    def test_saved_checked_translations_are_written_cache_first(self):
        window=self.window;window.space.live=True
        k1,k2,k3=parameter_names.identity('tc Shine','p1'),parameter_names.identity('tc Shine','p2'),parameter_names.identity('Other','p1')
        window.parameter_pending.update({k1:'强度',k2:'半径',k3:'别的'});window.parameter_enabled.update({k1,k3})
        self.assertEqual(window.quick_parameter_names('tc Shine'),{k1:'强度'})
        calls=[]
        def apply(names,fast=False,rescan=False,progress=None,skip_missing=False):
            calls.append((dict(names),fast,rescan))
            if fast:raise parameter_names.FastMiss('miss')
            return {'changed':2,'parameters':1}
        threads=[]
        with patch.object(window.parameter_session,'apply',apply),patch('threading.Thread',lambda target,**kw:threads.append(target) or type('T',(),{'start':lambda self:None,'join':lambda self,**kw:None})()):
            window.quick_parameters(self.entry());threads.pop()()
        # Cache, then a fresh heap-only scan (still a guarded fast write), then the full scan.
        self.assertEqual(calls,[({k1:'强度'},True,False),({k1:'强度'},True,True),({k1:'强度'},False,False)])
        self.assertIn('完整扫描',window.log.toPlainText())
    def run_quick(self,apply):
        window=self.window;window.space.live=True;key=parameter_names.identity('tc Shine','p1')
        window.parameter_pending[key]='强度';window.parameter_enabled.add(key);threads=[]
        with patch.object(window.parameter_session,'apply',apply),patch('threading.Thread',lambda target,**kw:threads.append(target) or type('T',(),{'start':lambda self:None,'join':lambda self,**kw:None})()):
            window.quick_parameters(self.entry());threads.pop()()
        return window
    def test_a_miss_retries_with_a_heap_scan_and_schedules_the_full_pass(self):
        calls=[]
        def apply(names,fast=False,rescan=False,progress=None,skip_missing=False):
            calls.append((fast,rescan))
            if fast and not rescan:raise parameter_names.FastMiss('miss')
            return {'changed':1,'parameters':1,'scanned':True,'scanTarget':'heap'}
        window=self.run_quick(apply)
        self.assertEqual(calls,[(True,False),(True,True)]);self.assertIn('默认堆扫描',window.log.toPlainText());self.assertTrue(window.quick_full.isActive())
    def test_a_miss_that_already_scanned_goes_straight_to_the_full_scan(self):
        calls=[]
        def apply(names,fast=False,rescan=False,progress=None,skip_missing=False):
            calls.append((fast,rescan))
            if fast:raise parameter_names.FastMiss('miss',scanned=True)
            return {'changed':1,'parameters':1}
        window=self.run_quick(apply)
        self.assertEqual(calls,[(True,False),(False,False)]);self.assertIn('完整扫描',window.log.toPlainText());self.assertFalse(window.quick_full.isActive())
    def test_first_write_scanning_the_heap_is_reported_as_such(self):
        window=self.run_quick(lambda names,**kw:{'changed':1,'parameters':1,'scanned':True,'scanTarget':'heap'})
        self.assertIn('默认堆扫描',window.log.toPlainText());self.assertTrue(window.quick_full.isActive())
    def test_cache_writes_schedule_one_full_pass_and_missing_translations_only_say_so(self):
        window=self.window;window.space.live=True
        window.quick_parameters(self.entry());self.assertIn('参数还没翻译',window.log.toPlainText())
        key=parameter_names.identity('tc Shine','p1');window.parameter_pending[key]='强度';window.parameter_enabled.add(key)
        window.quick_params_done(self.entry(),{'changed':1,'parameters':1},0.5,'cache')
        self.assertTrue(window.quick_full.isActive());self.assertEqual(window.quick_full_keys,{key:'强度'})
        calls=[];threads=[]
        with patch.object(window.parameter_session,'apply',lambda names,**kw:calls.append(kw) or {'changed':1}),patch('threading.Thread',lambda target,**kw:threads.append(target) or type('T',(),{'start':lambda self:None,'join':lambda self,**kw:None})()):
            window.quick_full_pass();threads.pop()()
        self.assertEqual(calls,[{'rescan':True,'skip_missing':True}]);self.assertEqual(window.quick_full_keys,{})

class LayerParameterTests(GuiCase):
    """Shift+Space over AE: every parameter of the selected layers' effects in Chinese."""
    def test_key_defaults_to_ctrl_shift_space_and_has_its_own_hotkey_id(self):
        import ctypes
        from ctypes import wintypes as W
        from unittest.mock import Mock
        self.assertEqual(self.window.keymap.keys['layer_parameters'],'Ctrl+Shift+Space');self.assertEqual(problem('layer_parameters',sequence('Ctrl+Shift+Space')),'')
        self.assertIsNone(self.window.keymap.conflict('layer_parameters',sequence('Ctrl+Shift+Space')))
        self.assertNotEqual(self.window.layer_key.id,self.window.quick_key.id)
        owner=type('O',(),{'id':qs.HOTKEY_ID+1,'fire':Mock()})();flt=qs._HotkeyFilter(owner);msg=W.MSG();msg.message=qs.WM_HOTKEY
        msg.wParam=qs.HOTKEY_ID;self.assertEqual(flt.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(msg)),(False,0));owner.fire.assert_not_called()
        msg.wParam=qs.HOTKEY_ID+1;self.assertEqual(flt.nativeEventFilter(b'windows_generic_MSG',ctypes.addressof(msg)),(True,0));owner.fire.assert_called_once()
    def test_parameter_page_names_the_key_and_follows_changes(self):
        note=self.window.parameter_page.key_note;self.assertFalse(note.isHidden());self.assertIn('Ctrl+Shift+Space'.replace('Space',''),note.text().replace('Space','').replace('空格',''))
        self.assertIn('选中图层',note.text())
        self.window.keymap.assign('layer_parameters',sequence('Ctrl+Alt+P'));self.assertIn('Ctrl+Alt+P',note.text())
        self.window.keymap.assign('layer_parameters',sequence(''));self.assertTrue(note.isHidden())
    def run_layer(self,report,apply_result=None,ready=True,translate=None):
        window=self.window;window.space.live=True;threads=[];applied=[]
        def apply(names,**kw):applied.append((dict(names),kw));return dict(apply_result or {'changed':2,'parameters':len(names)})
        with patch.object(parameter_names,'read_parameters',return_value=report),patch.object(window.parameter_session,'apply',apply),             patch.object(window.settings,'ready',return_value=ready),patch.object(app_live,'translate_names',translate or (lambda items,*a,**k:{i['id']:'译'+i['sourceName'] for i in items})),             patch.object(window,'save_parameters'),patch('threading.Thread',lambda target,**kw:threads.append(target) or type('T',(),{'start':lambda self:None,'join':lambda self,**kw:None})()):
            window.layer_parameters();self.assertTrue(window.layer_running)
            window.layer_parameters();self.assertEqual(len(threads),1)   # a second press while running starts nothing
            threads.pop()()
        self.assertFalse(window.layer_running);return applied
    def test_known_borrowed_and_new_translations_are_written_heap_first(self):
        window=self.window;k1,k2,k3=parameter_names.identity('fxA','p1'),parameter_names.identity('fxB','p2'),parameter_names.identity('fxB','p3')
        window.parameter_pending[k1]='强度';window.parameter_enabled.add(k1)
        rows=[{'id':k,'name':n,'effectMatchName':parameter_names.split_identity(k)[0]} for k,n in ((k1,'Amount'),(k2,'Radius'),(k3,'Angle'))]
        applied=self.run_layer({'parameters':rows},{'changed':3,'parameters':3,'heapOnly':True})
        names,kw=applied[0];self.assertEqual(names,{k1:'强度',k2:'译Radius',k3:'译Angle'});self.assertEqual(kw,{'full':False,'skip_missing':True})
        self.assertEqual(window.parameter_pending[k2],'译Radius');self.assertIn(k3,window.parameter_enabled)
        self.assertIn('选中图层的参数已改成中文',window.log.toPlainText());self.assertTrue(window.completion.isActive())
    def test_without_a_translation_service_only_known_names_are_written(self):
        window=self.window;k1,k2=parameter_names.identity('fxA','p1'),parameter_names.identity('fxA','p2')
        window.parameter_pending[k1]='强度';window.parameter_enabled.add(k1)
        applied=self.run_layer({'parameters':[{'id':k1,'name':'Amount','effectMatchName':'fxA'},{'id':k2,'name':'Size','effectMatchName':'fxA'}]},ready=False)
        self.assertEqual(applied[0][0],{k1:'强度'});self.assertIn('翻译服务未设置',window.log.toPlainText());self.assertFalse(window.completion.isActive())
    def test_no_selection_says_what_to_do_and_writes_nothing(self):
        applied=self.run_layer({'parameters':[],'emptyReason':'noSelectedLayers'})
        self.assertEqual(applied,[]);self.assertIn('请先在 AE 的合成里选中图层',self.window.log.toPlainText())
    def test_not_connected_does_nothing(self):
        self.window.space.live=False
        with patch('threading.Thread') as thread:self.window.layer_parameters()
        thread.assert_not_called();self.assertFalse(self.window.layer_running)

class TrayTests(GuiCase):
    def test_autostart_writes_and_removes_only_its_own_value(self):
        import autostart,sys
        store={}
        with patch.object(autostart,'_registry_get',lambda:store.get('v')),patch.object(autostart,'_registry_set',lambda value:store.pop('v',None) if value is None else store.__setitem__('v',value)):
            with patch.object(sys,'frozen',False,create=True):
                self.assertFalse(autostart.available());self.assertFalse(autostart.enabled())
                with self.assertRaises(RuntimeError):autostart.set_enabled(True)
            with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',r'C:\Program Files\Apps\AE汉化工具.exe'):
                autostart.set_enabled(True);self.assertEqual(store['v'],r'"C:\Program Files\Apps\AE汉化工具.exe" --minimized');self.assertTrue(autostart.enabled())
                store['v']=r'"D:\Other\old\AE汉化工具.exe" --minimized';self.assertFalse(autostart.enabled())   # another copy does not count
                autostart.set_enabled(False);self.assertEqual(store,{})
    def test_closing_hides_to_the_tray_until_quit_is_chosen(self):
        window=self.window
        class Tray:
            shown=True;messages=[]
            def isVisible(self):return self.shown
            def showMessage(self,*a):self.messages.append(a)
            def hide(self):self.shown=False
            def setVisible(self,on):self.shown=on
        window.tray=tray=Tray();window.show()
        window.close();self.assertFalse(window.isVisible());self.assertEqual(len(tray.messages),1)
        window.show();window.close();self.assertEqual(len(tray.messages),1)   # said once
        window.set_tray_on_close(False);self.assertFalse(tray.shown);self.assertFalse(window.tray_on_close())
    def test_installing_an_update_really_quits(self):
        window=self.window
        window.tray=type('Tray',(),{'isVisible':lambda self:True,'showMessage':lambda self,*a:None,'hide':lambda self:None})()
        launched=[]
        with patch.object(window,'launch_installer',lambda:launched.append(1)):
            window.show();window.pending_installer=None;window.quitting=True;window.pending_installer='x.exe';window.close()
        self.assertEqual(launched,[1])

class FollowUpTests(GuiCase):
    def test_another_versions_checked_translations_are_borrowed(self):
        import json
        window=self.window;own=parameter_names.identity('tc Shine','p1');other=parameter_names.identity('tc Shine','p2')
        window.parameter_pending[own]='强度';window.parameter_enabled.add(own)
        folder=Path(self.temp.name)/'other';folder.mkdir()
        (folder/'parameter-translations.json').write_text(json.dumps({own:'别的强度',other:'半径',parameter_names.identity('X','p'):'无关'}),encoding='utf8')
        (folder/'parameter-enabled.json').write_text(json.dumps([own,other]),encoding='utf8')
        spare=type('Space',(),{'folder':folder,'version':'25.3'})()
        with patch.object(window,'spaces',[window.space,spare]):
            self.assertEqual(window.quick_parameter_names('tc Shine'),{own:'强度',other:'半径'})   # this version's own wins
    def test_hidden_window_says_it_in_the_tray(self):
        window=self.window;said=[]
        window.tray=type('Tray',(),{'isVisible':lambda self:True,'showMessage':lambda self,*a:said.append(a[:2])})()
        window.hide();window.quick_notice('参数未汉化','说明');self.assertEqual(said,[('参数未汉化','说明')])
    def test_missing_parameters_are_left_out_only_when_asked(self):
        from types import SimpleNamespace
        from contextlib import nullcontext
        key=parameter_names.identity('E','p');gone=parameter_names.identity('E','q')
        rows=[dict(id=key,effectMatchName='E',matchName='p',name='Size',pfName='Size',address=1,guid='ab'*16,parent=9)]
        session=parameter_names.ParameterSession();session.path=Path(self.temp.name)/'ps.json'
        with patch.object(parameter_names,'ParameterCatalog',lambda p:SimpleNamespace(rows=lambda **kw:[dict(r) for r in rows])),             patch.object(parameter_names,'_host_names',lambda p,strict=True:{key:[rows[0]['name']]}),patch.object(parameter_names,'_process_lock',lambda:nullcontext()),             patch.object(session,'process',lambda:nullcontext(SimpleNamespace(pid=1,creation_time=2))),             patch.object(parameter_names,'run_native',lambda p,ops:(rows[0].update(name=ops[0]['after']),{'changed':len(ops)})[1]):
            with self.assertRaises(ValueError):session.apply({key:'大小',gone:'别的'})
            self.assertEqual(session.apply({key:'大小',gone:'别的'},skip_missing=True)['parameters'],1)
    def test_the_add_carries_the_name_ae_shows(self):
        entry=Entry('CC Drizzle','CC Drizzle','CC Drizzle (CC 细雨)','模拟','','CC Drizzle (CC 细雨)');window=self.window;seen=[]
        import threading as t
        with patch.object(parameter_names,'add_effect',lambda m,**kw:seen.append(kw['title']) or {'changed':1}),patch.object(window,'quick_parameters',lambda e:None),             patch('threading.Thread',lambda target,**kw:type('T',(),{'start':lambda self:target(),'join':lambda self,**kw:None})()):
            window.quick_add(entry)
        self.assertEqual(seen,['CC Drizzle (CC 细雨)'])

class AutoTranslateTests(GuiCase):
    def run_quick(self,window,ready=True,translate=None,read=None):
        applied=[];threads=[]
        def apply(names,**kw):applied.append(dict(names));return {'changed':len(names),'parameters':len(names)}
        with patch.object(window.settings,'ready',lambda:ready),patch.object(window.parameter_session,'apply',apply),             patch('parameter_names.read_parameters',read or (lambda **kw:{'parameters':[]})),patch.object(app_live,'translate_names',translate or (lambda *a,**k:{})),             patch('threading.Thread',lambda target,**kw:threads.append(target) or type('T',(),{'start':lambda self:None,'join':lambda self,**kw:None})()):
            window.quick_parameters(Entry('tc Shine','Shine','闪耀','RG'))
            if threads:threads.pop()()
        return applied
    def test_untranslated_parameters_are_translated_saved_checked_and_written(self):
        window=self.window;window.space.live=True
        k1,k2=parameter_names.identity('tc Shine','p1'),parameter_names.identity('tc Shine','p2')
        window.parameter_pending[k1]='强度';window.parameter_enabled.add(k1);sent=[]
        rows=[{'id':k1,'name':'Intensity','effectMatchName':'tc Shine'},{'id':k2,'name':'Radius','effectMatchName':'tc Shine'},{'id':parameter_names.identity('X','q'),'name':'Other','effectMatchName':'X'}]
        def translate(items,*a,**k):sent.extend(items);self.assertIs(k['system_prompt'],app_live.PARAMETER_PROMPT);return {k2:'半径'}
        applied=self.run_quick(window,translate=translate,read=lambda **kw:{'parameters':rows})
        self.assertEqual([i['id'] for i in sent],[k2]);self.assertEqual(applied,[{k1:'强度',k2:'半径'}])
        self.assertEqual(window.parameter_pending[k2],'半径');self.assertIn(k2,window.parameter_enabled);self.assertIn('自动翻译',window.log.toPlainText())
    def test_switched_off_or_no_service_only_says_so(self):
        window=self.window;window.space.live=True
        self.assertEqual(self.run_quick(window,ready=False),[]);self.assertIn('翻译服务未设置',window.log.toPlainText())
        window.save_ui_preference('quickTranslate',False);window.log.clear()
        self.assertEqual(self.run_quick(window),[]);self.assertIn('可到参数页翻译',window.log.toPlainText())
    def test_the_master_switch_turns_the_whole_step_off(self):
        window=self.window;window.space.live=True;k=parameter_names.identity('tc Shine','p1')
        window.parameter_pending[k]='强度';window.parameter_enabled.add(k)
        self.assertTrue(window.quick_params_box.isChecked());self.assertTrue(window.quick_translate_box.isEnabled())   # on by default
        window.quick_full_keys={k:'强度'};window.quick_full.start()
        window.quick_params_box.setChecked(False)
        self.assertFalse(window.quick_parameters_on());self.assertFalse(window.quick_translate_box.isEnabled());self.assertFalse(window.quick_full.isActive())
        read=[]
        self.assertEqual(self.run_quick(window,read=lambda **kw:read.append(1) or {'parameters':[]}),[]);self.assertEqual(read,[])
        self.assertNotIn('参数还没翻译',window.log.toPlainText())
        window.quick_params_box.setChecked(True);self.assertTrue(window.quick_translate_box.isEnabled())
        self.assertEqual(self.run_quick(window,ready=False),[{k:'强度'}])
    def test_a_failed_translation_is_reported(self):
        window=self.window;window.space.live=True;k=parameter_names.identity('tc Shine','p1')
        def translate(*a,**k):raise app_live.TranslationError('服务返回 401',{},1)
        self.run_quick(window,translate=translate,read=lambda **kw:{'parameters':[{'id':k,'name':'Intensity','effectMatchName':'tc Shine'}]})
        self.assertIn('401',window.log.toPlainText())

if __name__=='__main__':unittest.main()
