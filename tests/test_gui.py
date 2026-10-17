import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QItemSelectionModel
from PySide6.QtWidgets import QApplication
import app_live
import live_names
import effect_instances
import settings_store
from core import Plugin
from app_live import CHECK,CANDIDATE,CURRENT,KEY
from PySide6.QtCore import QPoint,QRect

def plugin(name,key,category,path):return Plugin(path,name,key,category,'PiPL/16000',1033)

class GuiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        # GUI mapping tests must not dispatch scheduled scan/connect workers to
        # the real host when another Qt test processes the event queue.
        timer=patch.object(app_live.QTimer,'singleShot');timer.start();self.addCleanup(timer.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        for module in (app_live,settings_store,live_names,effect_instances):
            handle=patch.object(module,'data_root',lambda:root);handle.start();self.addCleanup(handle.stop)
        # No real AE installation leaks in: one fallback version holds every plugin.
        for name,value in (('installations',lambda:self.installs()),('shared_roots',lambda:[]),('file_version',lambda exe:self.versions[Path(exe).parent.parent.name])):
            handle=patch.object(app_live,name,value);handle.start();self.addCleanup(handle.stop)
        # The background extension's folders (APPDATA) are the test's own, never the real CEP install.
        handle=patch.object(app_live.cep_bridge,'_appdata',lambda:root/'appdata');handle.start();self.addCleanup(handle.stop)
        # Nor Adobe's real registry settings (the extension debug mode).
        for name,value in (('_registry_get',lambda version:None),('_registry_set',lambda version,value:None)):
            handle=patch.object(app_live.cep_bridge,name,value);handle.start();self.addCleanup(handle.stop)
        self.window=app_live.Window();self.addCleanup(self.window.deleteLater)
        # Same effect installed for two AE versions gives two rows with one identity.
        self.window.show_scan(([plugin('S_Glow','S_Glow','Sapphire Lighting','a/S_Glow.aex'),plugin('Shine','tc Shine','RG Blurs and Glows','b/Shine.aex'),
                                plugin('Atom','Atom Key','Keying','c/Atom.aex'),plugin('S_Glow','S_Glow','Sapphire Lighting','d/S_Glow.aex')],[]))
        self.table=self.window.table
    versions={'AE':'26.0.0.67'}
    def installs(self):
        return [{'name':'Test AE','exe':str(Path(self.temp.name)/'AE'/'Support Files'/'AfterFX.exe'),'plugins':str(Path(self.temp.name)/'AE'/'Support Files'/'Plug-ins')}]
    def rows_for(self,key):return [r for r in range(self.table.rowCount()) if self.table.item(r,KEY).text()==key]
    def select(self,*rows):
        model=self.table.selectionModel();model.clearSelection()
        for row in rows:model.select(self.table.model().index(row,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)

class GuiMappingTests(GuiCase):
    """Rows are matched to effects by internal identity, whatever the table sort order."""
    def test_selection_follows_identity_after_sorting(self):
        for column,order in ((0,Qt.SortOrder.DescendingOrder),(4,Qt.SortOrder.AscendingOrder),(3,Qt.SortOrder.DescendingOrder)):
            self.table.sortItems(column,order)
            row=self.rows_for('tc Shine')[0];self.select(row);self.window.name_edit.setText('闪耀')
            self.assertEqual(self.window.translations_selected(),{'tc Shine':'闪耀'})
            self.assertEqual(self.window.pending['tc Shine'],'闪耀')
            self.assertEqual(self.table.item(self.rows_for('tc Shine')[0],CANDIDATE).text(),'闪耀')
    def test_edit_after_sort_saves_under_correct_identity(self):
        self.table.sortItems(0,Qt.SortOrder.DescendingOrder)
        self.table.item(self.rows_for('Atom Key')[0],CANDIDATE).setText('原子抠像')
        self.assertEqual(self.window.pending['Atom Key'],'原子抠像');self.assertNotIn('原子抠像',(self.window.pending.get('S_Glow'),self.window.pending.get('tc Shine')))
        self.assertEqual(app_live.CandidateStore(self.window.map_path).values['Atom Key'],'原子抠像')
    def test_editing_the_sorted_candidate_column_keeps_identity(self):
        self.table.sortItems(CANDIDATE,Qt.SortOrder.AscendingOrder)
        for key,name in (('Atom Key','原子抠像'),('tc Shine','啊闪耀'),('Atom Key','中原子')):
            self.table.item(self.rows_for(key)[0],CANDIDATE).setText(name)
            self.assertEqual(self.window.pending[key],name)
        self.assertEqual({self.table.item(r,KEY).text():self.table.item(r,CANDIDATE).text() for r in range(self.table.rowCount()) if self.table.item(r,CANDIDATE).text()},{'Atom Key':'中原子','tc Shine':'啊闪耀','S_Glow':'辉光'})
    def test_translation_result_updates_every_row_of_the_identity(self):
        self.table.sortItems(CANDIDATE,Qt.SortOrder.AscendingOrder)
        self.window.translated(({'S_Glow':'辉光','tc Shine':'闪耀'},''))
        self.assertEqual({self.table.item(r,CANDIDATE).text() for r in self.rows_for('S_Glow')},{'辉光'})
        self.assertEqual(self.table.item(self.rows_for('tc Shine')[0],CANDIDATE).text(),'闪耀')
        self.assertEqual(self.table.item(self.rows_for('Atom Key')[0],CANDIDATE).text(),'')
    def test_partial_translation_keeps_finished_batches_and_reports_failure(self):
        self.window.translated(({'S_Glow':'辉光'},'DeepSeek 请求失败（HTTP 500），请检查密钥、余额和模型配置；已完成 1/2 项'))
        self.assertEqual(self.table.item(self.rows_for('S_Glow')[0],CANDIDATE).text(),'辉光')
        self.assertEqual(app_live.CandidateStore(self.window.map_path).values.get('S_Glow'),'辉光')
        self.assertIn('已完成部分的候选已保存',self.window.log.toPlainText());self.assertTrue(self.window.toast.text().startswith('操作未完成'))
    def test_table_selects_one_row_and_acts_on_it_only(self):
        self.assertEqual(self.table.selectionMode(),self.table.SelectionMode.SingleSelection)
        self.window.translated(({'tc Shine':'闪耀'},''));self.table.selectRow(self.rows_for('S_Glow')[0]);self.table.selectRow(self.rows_for('tc Shine')[0])
        self.assertEqual(len(self.window.selected_rows()),1);self.assertEqual(self.window.translations_selected(),{'tc Shine':'闪耀'})
    def test_nothing_selected_asks_for_a_row(self):
        self.table.clearSelection()
        with self.assertRaises(ValueError):self.window.translations_selected()
    def test_live_names_follow_identity_after_sorting(self):
        self.table.sortItems(0,Qt.SortOrder.DescendingOrder)
        self.window.show_live({'pid':1,'effects':[{'matchName':'S_Glow','displayName':'辉光','category':'c'},{'matchName':'tc Shine','displayName':'Shine','category':'c'}]})
        self.assertEqual({self.table.item(r,CURRENT).text() for r in self.rows_for('S_Glow')},{'辉光'})
        self.assertEqual(self.table.item(self.rows_for('Atom Key')[0],CURRENT).text(),'当前 AE 未加载')

class SchemeTests(GuiCase):
    """Checked effects and their candidates form the translation scheme."""
    def check(self,key,state=True):
        self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
    def test_checkbox_is_shared_by_duplicate_rows_and_persisted(self):
        self.table.sortItems(KEY,Qt.SortOrder.DescendingOrder);self.check('S_Glow')
        self.assertEqual({self.table.item(r,CHECK).checkState() for r in self.rows_for('S_Glow')},{Qt.CheckState.Checked})
        self.assertEqual(app_live.CandidateStore(self.window.enabled_store.path,keys=True).values,{'S_Glow'})
        self.check('S_Glow',False);self.assertEqual(self.window.enabled,set())
    def test_check_visible_only_touches_filtered_rows(self):
        self.window.filter.setText('Sapphire');self.window.check_visible(True)
        self.assertEqual(self.window.enabled,{'S_Glow'})
        self.window.filter.setText('');self.window.check_visible(True)
        self.assertEqual(self.window.enabled,{'S_Glow','tc Shine','Atom Key'})
        self.window.check_visible(False);self.assertEqual(self.window.enabled,set())
    def test_shift_click_checks_the_visible_run(self):
        from PySide6.QtWidgets import QApplication
        self.table.sortItems(KEY,Qt.SortOrder.AscendingOrder);self.window.filter.setText('S')
        visible=self.window.visible_rows();self.table.item(visible[0],CHECK).setCheckState(Qt.CheckState.Checked)
        with patch.object(QApplication,'keyboardModifiers',return_value=Qt.KeyboardModifier.ShiftModifier),patch.object(self.window,'save_enabled',wraps=self.window.save_enabled) as save:
            self.table.item(visible[-1],CHECK).setCheckState(Qt.CheckState.Checked)
        save.assert_called_once();self.assertEqual(self.window.enabled,{self.table.item(r,KEY).text() for r in visible})
        self.assertNotIn('Atom Key',self.window.enabled)
    def test_header_box_reflects_and_toggles_visible_rows(self):
        header=self.window.check_header;self.assertEqual(header.state,Qt.CheckState.Unchecked)
        self.check('S_Glow');self.assertEqual(header.state,Qt.CheckState.PartiallyChecked)
        self.window.toggle_visible();self.assertEqual(self.window.enabled,{'S_Glow','tc Shine','Atom Key'});self.assertEqual(header.state,Qt.CheckState.Checked)
        self.window.filter.setText('Atom');self.window.toggle_visible();self.assertEqual(self.window.enabled,{'S_Glow','tc Shine'})
        self.window.filter.setText('');self.assertEqual(header.state,Qt.CheckState.PartiallyChecked)
    def test_restore_checked_and_uncheck_all(self):
        self.check('S_Glow');self.check('tc Shine');sent=[]
        with patch.object(self.window,'work',lambda work,done:sent.append(work)),patch.object(self.window,'restore_effect_names',lambda keys:sent.append(keys) or {}):
            self.window.restore_checked_action.trigger();sent[0](lambda text:None)
        self.assertEqual(sent[1],['S_Glow','tc Shine'])
        self.window.filter.setText('Atom');self.window.uncheck_all_action.trigger();self.assertEqual(self.window.enabled,set())
    def test_retranslate_includes_checked_effects_that_have_candidates(self):
        self.check('S_Glow');self.check('Atom Key');sent=[]
        with patch.object(self.window,'translate_rows',lambda rows:sent.append({self.table.item(r,KEY).text() for r in rows})):
            self.window.translate_checked_action.trigger();self.window.retranslate_checked_action.trigger()
        self.assertEqual(sent,[{'Atom Key'},{'S_Glow','Atom Key'}])
    def test_scheme_round_trip_and_load_replaces_state(self):
        self.window.translated(({'tc Shine':'闪耀'},''));self.check('tc Shine')
        path=Path(self.temp.name)/'方案.json';app_live.write_json(path,app_live.dump_scheme(self.window.pending,self.window.enabled))
        self.assertEqual(app_live.read_scheme(path),({'S_Glow':'辉光','tc Shine':'闪耀'},{'tc Shine'}))
        self.window.load_scheme({'Atom Key':'原子抠像'},{'Atom Key'},'方案.json')
        self.assertEqual((dict(self.window.pending),self.window.enabled),({'Atom Key':'原子抠像'},{'Atom Key'}))
        self.assertEqual(self.table.item(self.rows_for('tc Shine')[0],CANDIDATE).text(),'')
        self.assertEqual(self.table.item(self.rows_for('Atom Key')[0],CHECK).checkState(),Qt.CheckState.Checked)
    def test_foreign_or_damaged_scheme_files_are_rejected(self):
        path=Path(self.temp.name)/'x.json'
        for content in ('[]','{"format":"other","schemaVersion":1,"effects":{}}','{"format":"ae-localizer-scheme","schemaVersion":1,"effects":{"S_Glow":{"name":5}}}','{broken'):
            path.write_text(content,encoding='utf8')
            with self.assertRaises(ValueError):app_live.read_scheme(path)
    def test_apply_scheme_sends_only_checked_effects_with_candidates(self):
        self.window.translated(({'tc Shine':'闪耀'},''));self.check('S_Glow');self.check('tc Shine');self.check('Atom Key')
        sent={}
        def fake_work(work,callback):sent['names']=work
        def fake_apply(names,skip_missing):sent['apply']=(names,skip_missing);return {'applied':names}
        with patch.object(self.window,'work',fake_work),patch.object(self.window.session,'apply',fake_apply),patch.object(self.window.instance_session,'apply',return_value={'changed':0,'customSkipped':0}),patch.object(app_live,'read_live',lambda **kw:None):
            self.window.apply_scheme();sent['names'](lambda text:None)
        self.assertEqual(sent['apply'],({'S_Glow':'辉光','tc Shine':'闪耀'},True))
        self.assertIn('1 个勾选的效果还没有中文候选',self.window.log.toPlainText())
    def test_legacy_scheme_without_title_is_named_after_its_file(self):
        folder=self.window.scheme_folder;app_live.write_json(folder/'旧方案.json',app_live.dump_scheme({'S_Glow':'辉光'},{'S_Glow'}))
        self.window.refresh_schemes(None)
        self.assertEqual([s.title for s in self.window.schemes],['旧方案'])
        self.assertEqual(self.window.schemes[0].names,{'S_Glow':'辉光'})

class SchemeLibraryTests(GuiCase):
    """Saved schemes are listed in the drop-down and managed without file dialogs."""
    def name(self,*titles):
        it=iter(titles);return patch.object(app_live,'ask_text',lambda *a,**k:(next(it),True))
    def answer(self,yes):
        return patch.object(app_live,'confirm',return_value=yes)
    def check(self,key,state=True):
        self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
    def items(self):return [a.data() for a in self.window.scheme_menu().actions() if a.data()]
    def scheme(self,title):return next(s for s in self.window.schemes if s.title==title)
    def shown(self):return (self.window.scheme_switcher.title,self.window.scheme_switcher.dirty)
    def test_library_lists_newest_first_and_skips_foreign_files(self):
        folder=Path(self.temp.name)/'lib';folder.mkdir()
        app_live.write_json(folder/'a.json',app_live.dump_scheme({},{'S_Glow'},'早',app_live.datetime(2026,1,1)))
        app_live.write_json(folder/'b.json',app_live.dump_scheme({},set(),'晚',app_live.datetime(2026,5,1)))
        (folder/'c.json').write_text('{broken',encoding='utf8');(folder/'d.json.12.tmp').write_text('{}',encoding='utf8')
        schemes,skipped=app_live.list_schemes(folder)
        self.assertEqual(([s.title for s in schemes],skipped),(['晚','早'],1))
    def test_titles_become_safe_file_names(self):
        folder=Path('x')
        self.assertEqual(app_live.scheme_file(folder,'a/b:c*?').name,'a_b_c__.json')
        self.assertEqual(app_live.scheme_file(folder,'CON').name,'_CON.json')
        self.assertEqual(app_live.clean_title('  常用   滤镜 '),'常用 滤镜')
        for bad in ('   ','字'*61):
            with self.assertRaises(ValueError):app_live.clean_title(bad)
    def test_save_as_shows_scheme_and_tracks_unsaved_changes(self):
        self.check('S_Glow')
        with self.name('常用滤镜'):self.window.save_scheme_as()
        self.assertEqual(self.shown(),('常用滤镜',False));self.assertEqual(self.items(),['常用滤镜'])
        self.assertFalse(self.window.save_scheme_action.isEnabled());self.assertTrue(self.window.scheme_more.isEnabled())
        self.check('tc Shine')
        self.assertEqual(self.shown(),('常用滤镜',True));self.assertTrue(self.window.save_scheme_action.isEnabled())
        self.window.save_scheme()
        self.assertEqual(self.shown(),('常用滤镜',False));self.assertFalse(self.window.save_scheme_action.isEnabled())
        self.assertEqual(app_live.read_scheme(self.window.scheme_folder/'常用滤镜.json')[1],{'S_Glow','tc Shine'})
    def test_duplicate_names_are_rejected(self):
        with self.name('方案','方案'):
            self.window.save_scheme_as();self.window.save_scheme_as()
        self.assertEqual(len(self.window.schemes),1);self.assertIn('已经有名为“方案”的方案',self.window.status.text())
    def test_switching_loads_scheme_and_asks_before_losing_changes(self):
        self.check('S_Glow')
        with self.name('甲'):self.window.save_scheme_as()
        self.window.check_visible(False);self.window.translated(({'Atom Key':'原子抠像'},''));self.check('Atom Key')
        with self.name('乙'):self.window.save_scheme_as()
        first=self.scheme('甲')
        with self.answer(True) as asked:self.window.choose_scheme(first)
        asked.assert_not_called();self.assertEqual(self.window.enabled,{'S_Glow'});self.assertEqual(self.window.active.title,'甲')
        self.assertEqual(self.table.item(self.rows_for('S_Glow')[0],CHECK).checkState(),Qt.CheckState.Checked)
        self.check('tc Shine');second=self.scheme('乙')
        with self.answer(False) as asked:self.window.choose_scheme(second)
        asked.assert_called_once();self.assertEqual(self.window.enabled,{'S_Glow','tc Shine'})
        self.assertEqual(self.shown(),('甲',True))
        with self.answer(True):self.window.choose_scheme(second)
        self.assertEqual(self.window.enabled,{'Atom Key'});self.assertEqual(self.window.pending['Atom Key'],'原子抠像')
    def test_rename_keeps_saved_content_and_delete_keeps_table(self):
        self.check('S_Glow')
        with self.name('旧名'):self.window.save_scheme_as()
        self.check('tc Shine')
        with self.name('新名'):self.window.rename_scheme()
        self.assertEqual([s.title for s in self.window.schemes],['新名']);self.assertFalse((self.window.scheme_folder/'旧名.json').exists())
        self.assertEqual(app_live.read_scheme(self.window.scheme_folder/'新名.json')[1],{'S_Glow'})
        self.assertEqual(self.window.enabled,{'S_Glow','tc Shine'})
        with self.answer(True):self.window.delete_scheme()
        self.assertEqual((self.window.schemes,self.window.active),([],None));self.assertEqual(self.window.enabled,{'S_Glow','tc Shine'})
        self.assertEqual((self.items(),self.shown()),([],('未使用方案',False)));self.assertEqual(self.window.detail_stack.currentIndex(),0)
    def test_active_scheme_is_remembered_but_not_reloaded(self):
        self.check('S_Glow')
        with self.name('常用'):self.window.save_scheme_as()
        self.check('tc Shine');window=app_live.Window();self.addCleanup(window.deleteLater)
        self.assertEqual(window.active.title,'常用');self.assertEqual(window.enabled,{'S_Glow','tc Shine'})
    def test_import_copies_into_library_with_unique_name_and_loads(self):
        with self.name('共享'):self.window.save_scheme_as()
        outside=Path(self.temp.name)/'外部.json';app_live.write_json(outside,app_live.dump_scheme({'Atom Key':'原子抠像'},{'Atom Key'},'共享'))
        with patch.object(app_live.QFileDialog,'getOpenFileName',return_value=(str(outside),'')),self.answer(True):self.window.import_scheme()
        self.assertEqual(sorted(s.title for s in self.window.schemes),['共享','共享 (2)'])
        self.assertEqual(self.window.active.title,'共享 (2)');self.assertEqual(self.window.enabled,{'Atom Key'})
    def test_export_writes_the_saved_scheme_or_saves_first(self):
        self.check('S_Glow')
        with self.name('导出用'):self.window.save_scheme_as()
        self.check('tc Shine');target=Path(self.temp.name)/'导出.json'
        with patch.object(app_live.QFileDialog,'getSaveFileName',return_value=(str(target),'')):
            with self.answer(False):self.window.export_scheme()
            self.assertEqual(app_live.open_scheme(target).enabled,{'S_Glow'});self.assertEqual(app_live.open_scheme(target).title,'导出用')
            with self.answer(True):self.window.export_scheme()
        self.assertEqual(app_live.open_scheme(target).enabled,{'S_Glow','tc Shine'});self.assertFalse(self.window.scheme_dirty())
        self.assertIsNotNone(app_live.open_scheme(target).parameters)

class VersionTests(GuiCase):
    """Each installed AE version shows only its own plugins and keeps its own choices."""
    versions={'Adobe After Effects 2026':'26.0.0.67','Adobe After Effects 2021':'18.2.1.9'}
    def installs(self):
        base=Path(self.temp.name)/'AE';result=[]
        for name in self.versions:
            plugins=base/name/'Support Files'/'Plug-ins';plugins.mkdir(parents=True,exist_ok=True)
            result.append({'name':name,'exe':str(base/name/'Support Files'/'AfterFX.exe'),'plugins':str(plugins)})
        return result
    def setUp(self):
        super().setUp();w=self.window;base=Path(self.temp.name)/'AE'
        self.v26,self.v21=w.spaces
        w.show_scan(([plugin('S_Glow','S_Glow','Sapphire',str(base/'Adobe After Effects 2026'/'Support Files'/'Plug-ins'/'S_Glow.aex')),
                      plugin('Old','Old Effect','Legacy',str(base/'Adobe After Effects 2021'/'Support Files'/'Plug-ins'/'Old.aex')),
                      plugin('Shine','tc Shine','RG',str(Path(self.temp.name)/'MediaCore'/'Shine.aex'))],[]))
    def keys(self):return {self.table.item(r,KEY).text() for r in range(self.table.rowCount())}
    def items(self):return [self.window.version_box.itemText(i) for i in range(self.window.version_box.count())]
    def test_versions_are_detected_and_the_supported_build_is_live(self):
        self.assertEqual((self.v26.live,self.v21.live),(True,False));self.assertIs(self.window.space,self.v26)
        self.assertEqual(self.items(),['AE 2026 · 26.0.0.67 · 2 个','AE 2021 · 18.2.1.9 · 2 个（仅翻译）'])
        self.assertEqual(self.v26.map_path,self.window.map_path);self.assertEqual(self.v26.map_path.parent,Path(self.temp.name))
    def test_each_version_shows_its_own_and_common_plugins(self):
        self.assertEqual(self.keys(),{'S_Glow','tc Shine'})
        self.window.choose_version(1)
        self.assertEqual(self.keys(),{'Old Effect','tc Shine'});self.assertIn('AE 2021：2 条名称资源（含公共目录 1 条）',self.window.summary.text())
        self.assertIn('只能扫描、翻译和保存方案',self.window.note.text())
    def test_candidates_and_checks_are_kept_per_version(self):
        self.window.choose_version(1)
        self.table.item(self.rows_for('tc Shine')[0],CANDIDATE).setText('旧闪耀');self.table.item(self.rows_for('tc Shine')[0],CHECK).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(app_live.CandidateStore(self.v21.map_path).values,{'tc Shine':'旧闪耀'})
        self.window.choose_version(0)
        self.assertEqual(self.table.item(self.rows_for('tc Shine')[0],CANDIDATE).text(),'');self.assertNotIn('tc Shine',self.window.enabled)
        self.window.choose_version(1)
        self.assertEqual(self.window.pending,{'tc Shine':'旧闪耀'});self.assertEqual(self.window.enabled,{'tc Shine'})
    def test_unsupported_version_cannot_rename_live(self):
        self.window.show_live({'pid':1,'effects':[{'matchName':'tc Shine','displayName':'Shine','category':'c'}]});self.window.update_buttons();self.select(0)
        self.assertTrue(self.window.apply_btn.isEnabled())
        self.window.choose_version(1);self.select(0)
        for widget in (self.window.apply_btn,self.window.restore_btn,self.window.restore_all_action,self.window.refresh_btn,self.window.parameters_btn,self.window.parameter_page.apply_btn):
            self.assertFalse(widget.isEnabled())
        self.assertEqual(self.table.item(self.rows_for('tc Shine')[0],CURRENT).text(),'仅翻译 · 不支持实时改名')
        with patch.object(self.window,'work',side_effect=AssertionError('must not apply')):self.window.apply_scheme()
        self.assertIn('不支持实时改名',self.window.status.text())
        self.window.choose_version(0);self.assertEqual(self.table.item(self.rows_for('tc Shine')[0],CURRENT).text(),'Shine')
    def test_schemes_are_kept_per_version_and_version_is_remembered(self):
        self.window.choose_version(1);self.table.item(self.rows_for('Old Effect')[0],CHECK).setCheckState(Qt.CheckState.Checked)
        with patch.object(app_live,'ask_text',return_value=('旧版方案',True)):self.window.save_scheme_as()
        self.assertTrue((self.v21.scheme_folder/'旧版方案.json').exists())
        self.window.choose_version(0);self.assertEqual(self.window.schemes,[])
        self.window.choose_version(1);self.assertEqual(self.window.active.title,'旧版方案')
        window=app_live.Window();self.addCleanup(window.deleteLater)
        self.assertEqual(window.space.key,'Adobe After Effects 2021');self.assertEqual(window.active.title,'旧版方案')

    def test_table_lists_versions_and_switches(self):
        w=self.window;table=w.version_table
        self.assertEqual([table.item(r,0).text() for r in range(table.rowCount())],['AE 2026（当前）','AE 2021'])
        self.assertEqual([table.item(r,3).text() for r in range(table.rowCount())],['已适配','仅翻译'])
        table.selectRow(1);w.use_version_btn.click();self.assertIs(w.space,self.v21);self.assertEqual(table.item(1,0).text(),'AE 2021（当前）')
        self.assertTrue(w.note.isVisibleTo(w));self.assertEqual(w.pill.property('state'),'warn')

class ToastTests(GuiCase):
    """Status notices float briefly instead of staying on screen."""
    def test_there_is_no_permanent_badge(self):
        self.assertFalse(hasattr(self.window,'badge'));self.assertFalse(self.window.toast.isVisibleTo(self.window))
    def test_error_toast_is_red_longer_and_trimmed(self):
        self.window.error('x'*200)
        toast=self.window.toast
        self.assertTrue(toast.isVisibleTo(self.window));self.assertTrue(toast.property('error'))
        self.assertEqual(toast.full,'操作未完成：'+'x'*200);self.assertIn('x'*200,toast.toolTip())
        self.assertTrue(toast.text().startswith('操作未完成：'));self.assertTrue(toast.text().endswith('…'))
        self.assertLessEqual(toast.width(),self.window.toast_area().width())
        self.assertEqual(toast.timer.interval(),7000);self.assertTrue(toast.timer.isActive())
        self.assertEqual(self.window.status.text(),'x'*200);self.assertIn('x'*200,self.window.log.toPlainText())
    def test_success_toast_hides_itself_or_on_click(self):
        self.window.applied(({'applied':{'S_Glow':'辉光'}},None,'无 AE'))
        toast=self.window.toast
        self.assertEqual(toast.text(),'实时更名已生效：已应用 1 项');self.assertFalse(toast.property('error'));self.assertEqual(toast.timer.interval(),3500)
        self.assertIn('transparent',self.window.status.styleSheet())
        toast.timer.timeout.emit();self.assertFalse(toast.isVisibleTo(self.window));self.assertEqual(self.window.status.styleSheet(),'')
        self.window.error('失败');toast.mousePressEvent(None)
        self.assertFalse(toast.isVisibleTo(self.window));self.assertFalse(toast.timer.isActive())
    def test_unsupported_version_has_its_own_title(self):
        self.window.error('检测到 AE 2019。'+app_live.UNSUPPORTED)
        self.assertTrue(self.window.toast.text().startswith('此 AE 版本不支持：'))
    def test_toast_covers_no_control_on_any_page(self):
        w=self.window;w.resize(1200,720);w.show();self.addCleanup(w.hide);self.app.processEvents()
        w.show_live({'pid':1,'effects':[]});w.update_buttons()
        from PySide6.QtWidgets import QAbstractButton,QComboBox,QLineEdit,QAbstractItemView
        for index in range(w.pages.count()):
            w.go(index);self.app.processEvents();w.toast.notify('实时更名已生效','已应用 12 项');self.app.processEvents()
            box=w.toast.geometry();self.assertLessEqual(w.height()-box.bottom(),10)
            for widget in [x for kind in (QAbstractButton,QComboBox,QLineEdit,QAbstractItemView) for x in w.findChildren(kind)]:
                if widget is w.toast or not widget.isVisible():continue
                rect=QRect(widget.mapTo(w,QPoint(0,0)),widget.size())
                self.assertFalse(rect.intersects(box),(index,type(widget).__name__,getattr(widget,'text',lambda:'')()))

class SchemeActionTests(GuiCase):
    def test_applied_summary_counts_categories(self):
        self.window.applied(({'applied':{'S_Glow':'辉光','tc Shine':'闪耀'},'unchanged':['tc Shine'],'skipped':['Missing']},None,'无 AE'))
        log=self.window.log.toPlainText()
        self.assertIn('已应用 1 项：S_Glow → 辉光',log);self.assertIn('1 项已经是目标名称',log);self.assertIn('1 项当前 AE 未加载',log)

class SupportedBuildGuiTests(GuiCase):
    versions={'Adobe After Effects 2026':'26.0.0.67','Adobe After Effects 2025':'25.3.0.71','Adobe After Effects 2021':'18.2.1.8'}
    installs=VersionTests.installs
    def test_each_verified_build_enables_native_actions_and_binds_its_path(self):
        for index,space in enumerate(self.window.spaces):
            self.window.choose_version(index)
            self.window.show_live({'pid':1,'image':str(space.exe),'effects':[]});self.window.update_buttons();self.select(0)
            self.assertTrue(space.live)
            for button in (self.window.apply_btn,self.window.restore_btn,self.window.parameters_btn,self.window.refresh_btn):self.assertTrue(button.isEnabled())
            self.assertEqual(self.window.session.expected_image,space.exe);self.assertEqual(self.window.parameter_session.expected_image,space.exe)
    def test_switch_to_another_supported_install_discards_old_live_catalog(self):
        first=self.window.spaces[0]
        self.window.show_live({'pid':1,'image':str(first.exe),'effects':[]})
        self.window.choose_version(1);self.assertIsNone(self.window.live);self.assertFalse(self.window.apply_btn.isEnabled())
        self.assertEqual(self.window.session.expected_image,self.window.space.exe)
    def test_only_26_keeps_legacy_data_root(self):
        for space in self.window.spaces:
            self.assertEqual(space.folder==Path(self.temp.name),space.version=='26.0.0.67')
    def test_parameter_candidates_and_checks_are_isolated_by_build(self):
        key=app_live.identity('S_Glow','S_Glow-0050')
        self.window.parameter_pending[key]='亮度';self.window.parameter_enabled.add(key);self.window.save_parameters()
        self.window.choose_version(1);self.assertNotIn(key,self.window.parameter_pending);self.assertNotIn(key,self.window.parameter_enabled)
        self.window.parameter_pending[key]='光亮度';self.window.save_parameters();self.window.choose_version(0)
        self.assertEqual(self.window.parameter_pending[key],'亮度');self.assertIn(key,self.window.parameter_enabled)

class NavigationTests(GuiCase):
    """Pages, filters, the detail card and the second- and third-level menus."""
    def check(self,key):self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked)
    def shown(self):return {self.table.item(r,KEY).text() for r in self.window.visible_rows()}
    def test_navigation_switches_pages_and_counts_items(self):
        w=self.window
        for index in range(w.pages.count()):
            w.go(index);self.assertEqual(w.pages.currentIndex(),index);self.assertTrue(w.nav_buttons[index].isChecked())
        self.assertEqual(w.nav_buttons[app_live.EFFECTS].count.text(),'4');self.assertEqual(w.nav_buttons[app_live.VERSIONS].count.text(),'1')
    def test_errors_mark_the_log_page_until_it_is_opened(self):
        w=self.window;w.go(app_live.EFFECTS);w.error('一');w.error('二')
        self.assertEqual(w.nav_buttons[app_live.SETTINGS].count.text(),'2');self.assertTrue(w.nav_buttons[app_live.SETTINGS].count.property('alert'))
        w.go(app_live.SETTINGS);self.assertEqual(w.nav_buttons[app_live.SETTINGS].count.text(),'')
    def test_shared_names_are_applied_and_only_noted(self):
        w=self.window;operation={'applied':{'S_Glow':'三色调'},'unchanged':[],'skipped':[],'sharedNames':['“三色调”（S_TriTone / 三色调）']}
        with patch.object(w,'show_reread'),patch.object(w.toast,'notify') as toast:w.applied((operation,None,None))
        self.assertEqual(toast.call_args_list[0].args[0],'实时更名已生效');self.assertFalse(any(c.kwargs.get('error') for c in toast.call_args_list))
        self.assertIn('提示：1 组中文名称与其他效果相同',w.log.toPlainText());self.assertIn('S_TriTone / 三色调',w.log.toPlainText())
    def test_state_chips_filter_rows_and_show_counts(self):
        w=self.window;self.check('tc Shine')
        w.chips['checked'].click();self.assertEqual(self.shown(),{'tc Shine'})
        self.assertNotIn('todo',w.chips)
        w.show_live({'pid':1,'effects':[{'matchName':'S_Glow','displayName':'辉光','category':'c'}]})
        w.chips['applied'].click();self.assertEqual(self.shown(),{'S_Glow'})
        w.chips['missing'].click();self.assertEqual(self.shown(),{'tc Shine','Atom Key'})
        self.assertEqual([w.chips[k].text() for k,_ in app_live.ROW_FILTERS],['全部 4','已勾选 1','已应用 2','未加载 2'])
        w.filter.setText('atom');self.assertEqual(self.shown(),{'Atom Key'})
        w.chips['all'].click();w.filter.clear();self.assertEqual(len(w.visible_rows()),4)
    def test_detail_card_follows_selection(self):
        w=self.window;w.show_live({'pid':1,'effects':[]});w.update_buttons()
        self.assertFalse(w.apply_btn.isEnabled());self.assertFalse(w.translate_action.isEnabled())
        self.select(self.rows_for('tc Shine')[0])
        self.assertEqual(w.selected_label.text(),'Shine');self.assertIn('内部标识：tc Shine',w.detail_info.text())
        self.assertTrue(w.apply_btn.isEnabled());self.assertTrue(w.name_edit.isEnabled());self.assertTrue(w.parameters_btn.isEnabled())
        self.table.clearSelection();self.assertEqual(w.selected_label.text(),'在左侧表格中选择效果');self.assertIn('批量操作请勾选',w.detail_info.text())
        self.assertFalse(w.name_edit.isEnabled());self.assertFalse(w.parameters_btn.isEnabled())
    def test_row_menu_acts_on_the_row_under_the_pointer(self):
        w=self.window;row=self.rows_for('Atom Key')[0];menu=w.row_menu(row);self.addCleanup(menu.deleteLater)
        self.assertEqual(w.selected_rows(),[row]);toggle=next(a for a in menu.actions() if a.text()=='勾选此效果')
        toggle.trigger();self.assertEqual(w.enabled,{'Atom Key'})
        w.copy_column(KEY);self.assertEqual(QApplication.clipboard().text(),'Atom Key')
    def test_translate_checked_only_sends_checked_effects_without_candidates(self):
        w=self.window;self.check('S_Glow');self.check('Atom Key');sent=[]
        with patch.object(w,'translate_rows',lambda rows:sent.append({self.table.item(r,KEY).text() for r in rows})):w.translate_checked_action.trigger()
        self.assertEqual(sent,[{'Atom Key'}])
    def submenu(self,tool,title):return next(a.menu() for a in tool.menu().actions() if a.text()==title)
    def test_menus_nest_actions_by_level(self):
        w=self.window
        self.assertNotIn('应用到 AE',[a.text() for a in w.more_tool.menu().actions()])
        translate=self.submenu(w.more_tool,'翻译').actions();self.assertIn(w.translate_checked_action,translate);self.assertIn(w.retranslate_checked_action,translate)
        self.assertIn(w.refresh_action,w.more_tool.menu().actions())
        restore=w.restore_tool.menu().actions();self.assertIn(w.restore_checked_action,restore);self.assertIn(w.restore_all_action,restore);self.assertNotIn(w.restore_selected_action,restore)
        self.assertEqual([a.text() for a in w.check_tool.menu().actions() if a.text()],['勾选当前筛选结果','取消勾选当前筛选结果','取消全部勾选'])
        self.assertIn(w.manage_action,w.scheme_menu().actions())
        w.manage_action.trigger();self.assertEqual(w.pages.currentIndex(),app_live.SCHEMES)
    def test_effect_menus_no_longer_carry_parameter_options(self):
        w=self.window;texts=[a.text() for a in self.submenu(w.more_tool,'翻译').actions()]
        self.assertFalse(any('参数' in text for text in texts));self.assertFalse(hasattr(w,'include_parameters'))
    def test_effect_translation_sends_only_effect_names(self):
        w=self.window;w.settings.key='test-only';sent=[]
        def fake(items,*args,**kwargs):sent.extend(items);return {item['id']:'译名' for item in items}
        with patch.object(app_live,'translate_names',fake),patch.object(w,'work',lambda work,done:done(work(lambda text:None))):
            w.translate_rows(self.rows_for('tc Shine'))
        self.assertEqual([item['id'] for item in sent],['tc Shine']);self.assertEqual(w.pending['tc Shine'],'译名')
    def test_long_status_does_not_widen_the_window(self):
        width=self.window.minimumSizeHint().width();self.window.error('很长的错误'*80)
        self.assertEqual(self.window.minimumSizeHint().width(),width)

class SchemePageTests(GuiCase):
    """The schemes page previews any saved scheme and manages it without making it active."""
    def save(self,title,*keys):
        for key in keys:self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked)
        with patch.object(app_live,'ask_text',return_value=(title,True)):self.window.save_scheme_as()
    def pick(self,title):self.window.scheme_list.setCurrentRow([s.title for s in self.window.schemes].index(title))
    def menu_item(self,text):return next(a for a in self.window.list_menu().actions() if a.text()==text)
    def test_preview_shows_a_scheme_that_is_not_active(self):
        self.save('甲','S_Glow');self.window.check_visible(False);self.save('乙','Atom Key')
        self.pick('甲');w=self.window
        self.assertEqual(w.active.title,'乙');self.assertEqual(w.preview_title.text(),'甲')
        self.assertEqual([w.preview_table.item(r,1).text() for r in range(w.preview_table.rowCount()) if not w.preview_table.isRowHidden(r)],['S_Glow'])
        self.assertTrue(w.load_scheme_btn.isEnabled())
        with patch.object(app_live,'confirm',return_value=True):w.load_scheme_btn.click()
        self.assertEqual(w.active.title,'甲');self.assertEqual(w.enabled,{'S_Glow'})
    def test_rename_and_delete_from_page_keep_the_active_scheme(self):
        self.save('甲','S_Glow');self.save('乙','Atom Key');w=self.window
        self.pick('甲')
        with patch.object(app_live,'ask_text',return_value=('丙',True)):self.menu_item('重命名…').trigger()
        self.assertEqual(w.active.title,'乙');self.assertEqual(sorted(s.title for s in w.schemes),['丙','乙'])
        self.pick('丙')
        with patch.object(app_live,'confirm',return_value=True):self.menu_item('删除…').trigger()
        self.assertEqual([s.title for s in w.schemes],['乙']);self.assertEqual(w.active.title,'乙')
    def test_overwrite_writes_the_table_into_the_chosen_scheme(self):
        self.save('甲','S_Glow');self.save('乙','Atom Key');w=self.window
        self.pick('甲')
        with patch.object(app_live,'confirm',return_value=True):self.menu_item('用当前表格覆盖…').trigger()
        self.assertEqual(app_live.read_scheme(w.scheme_folder/'甲.json')[1],{'S_Glow','Atom Key'});self.assertEqual(w.active.title,'甲')

class ApiDialogTests(GuiCase):
    """The translation-model dialog keeps each service's entries and can test the chosen one."""
    def test_switching_services_keeps_what_was_typed(self):
        dialog=app_live.ApiDialog(self.window.settings,self.window);self.addCleanup(dialog.deleteLater)
        dialog.key.setText('deepseek-test-only')
        dialog.services.setCurrentIndex(dialog.services.findData('anthropic'))
        self.assertEqual(dialog.base.text(),'https://api.anthropic.com/v1');self.assertEqual(dialog.model.currentText(),'claude-haiku-5-5');self.assertEqual(dialog.key.text(),'')
        dialog.key.setText('claude-test-only');dialog.model.setCurrentText('claude-sonnet-5-5')
        dialog.services.setCurrentIndex(dialog.services.findData('ollama'));self.assertFalse(dialog.key.isEnabled())
        dialog.services.setCurrentIndex(dialog.services.findData('deepseek'));self.assertEqual(dialog.key.text(),'deepseek-test-only')
        dialog.services.setCurrentIndex(dialog.services.findData('anthropic'));dialog.save()
        settings=self.window.settings
        self.assertEqual((settings.service,settings.key,settings.model),('anthropic','claude-test-only','claude-sonnet-5-5'))
        self.assertEqual(settings.profiles['deepseek']['key'],'deepseek-test-only')
        self.window.update_api_info();self.assertIn('服务商：Anthropic Claude',self.window.api_info.text())
    def test_connection_test_uses_the_shown_service(self):
        dialog=app_live.ApiDialog(self.window.settings,self.window);self.addCleanup(dialog.deleteLater)
        dialog.services.setCurrentIndex(dialog.services.findData('ollama'));calls=[]
        def fake(items,key,base,model,**kwargs):calls.append((base,model,kwargs['service']));return {'test':'高斯模糊'}
        with patch.object(app_live,'translate_names',fake):
            dialog.test();self.assertTrue(dialog.job.wait(3000));self.app.processEvents()
        self.assertEqual(calls,[('http://localhost:11434/v1','qwen2.5:7b','ollama')]);self.assertIn('高斯模糊',dialog.error.text())
        self.assertEqual(self.window.settings.service,'deepseek')
    def test_translation_asks_for_settings_until_service_is_ready(self):
        self.select(0)
        with patch.object(self.window,'configure_api',return_value=False) as asked,patch.object(self.window,'work',side_effect=AssertionError('must not translate')):
            self.window.translate_selected()
        asked.assert_called_once()

if __name__=='__main__':unittest.main()
