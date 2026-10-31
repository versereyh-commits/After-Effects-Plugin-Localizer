from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from test_gui import GuiCase
import panel_localizer as P

class PanelGuiTests(GuiCase):
    def setUp(self):
        super().setUp();self.page=self.window.panel_page;self.root=Path(self.temp.name)/'sample';self.root.mkdir()
        self.script=self.root/'sample.jsx';self.raw=b'var w=new Window("palette","Title");var b=w.add("button",null,"Apply");';self.script.write_bytes(self.raw)
        self.page.found(([P.inspect(self.script)],[]))
    def test_page_appended_preserves_existing_indices(self):
        self.assertEqual(self.window.pages.count(),7);self.assertEqual(self.window.pages.widget(6),self.page)
        self.window.go(6);self.assertEqual(self.window.pages.currentIndex(),6)
    def test_no_automatic_read_or_write_on_selection(self):
        self.assertIsNone(self.page.report);self.assertEqual(self.script.read_bytes(),self.raw);self.assertFalse(self.page.apply_btn.isEnabled())
    def test_edit_saves_local_dictionary_only(self):
        self.page.extracted(self.page.localizer().extract());self.page.table.item(0,2).setText('标题')
        self.assertEqual(self.page.localizer().dictionary(),{'Title':'标题'});self.assertEqual(self.script.read_bytes(),self.raw)
    def switch_away_and_back(self):
        from panel_page import KEY
        original=self.page.target().key
        other=self.root/'other.jsx';other.write_text('var w=new Window("palette","Other");',encoding='utf-8')
        self.page.found(([P.inspect(self.script),P.inspect(other)],[]),select=P.inspect(other).key)
        self.assertIsNone(self.page.report)
        for i in range(self.page.target_list.count()):
            if self.page.target_list.item(i).data(KEY)==original:
                self.page.target_list.setCurrentRow(i);break
    def test_return_to_script_restores_valid_report_without_scan(self):
        self.page.extracted(self.page.localizer().extract());self.page.table.item(0,2).setText('标题')
        with patch.object(P.Localizer,'_extract',side_effect=AssertionError('must not scan')):
            self.switch_away_and_back()
        self.assertIsNotNone(self.page.report);self.assertEqual(self.page.report['readMode'],'cache')
        self.assertEqual(self.page.table.item(0,2).text(),'标题');self.assertEqual(self.script.read_bytes(),self.raw)
    def test_return_to_changed_script_requires_explicit_read(self):
        self.page.extracted(self.page.localizer().extract());self.script.write_bytes(self.raw+b'\n// changed')
        with patch.object(P.Localizer,'_extract',side_effect=AssertionError('must not scan')):
            self.switch_away_and_back()
        self.assertIsNone(self.page.report);self.assertEqual(self.page.table.rowCount(),0)
    def test_return_after_cache_eviction_requires_explicit_read(self):
        import panel_cache
        self.page.extracted(self.page.localizer().extract());panel_cache.clear(self.page.target().key)
        with patch.object(P.Localizer,'_extract',side_effect=AssertionError('must not scan')):
            self.switch_away_and_back()
        self.assertIsNone(self.page.report)
    def test_busy_disables_apply_and_extraction(self):
        self.page.extracted(self.page.localizer().extract())
        with patch.object(self.window,'busy',return_value=True):self.page.update_controls();self.assertFalse(self.page.apply_btn.isEnabled());self.assertFalse(self.page.extract_btn.isEnabled())
    def test_fill_retains_unchecked_rows(self):
        self.page.extracted(self.page.localizer().extract());self.page.table.item(0,0).setCheckState(Qt.CheckState.Unchecked);self.page.fill()
        self.assertNotIn('Title',self.page.checked())
    def test_invalid_edit_reverts_and_preserves_dictionary(self):
        self.page.extracted(self.page.localizer().extract())
        with patch.object(self.window,'error') as error:self.page.table.item(0,2).setText('bad\0');error.assert_called_once()
        self.assertEqual(self.page.words,{});self.assertEqual(self.page.table.item(0,2).text(),'')
    def test_cancel_discards_late_result(self):
        captured={}
        def start(work,result,failure):captured['result']=result
        with patch.object(self.window,'work',side_effect=start):self.page.extract()
        self.page.cancelled.set();captured['result'](self.page.localizer().extract());self.assertIsNone(self.page.report)
    def test_unsupported_jsxbin_disabled(self):
        p=self.root/'encoded.jsxbin';p.write_text('@JSXBIN@');self.page.found(([P.inspect(p)],[]));self.assertFalse(self.page.extract_btn.isEnabled())
    def cep(self,name,identifier):
        folder=self.root/name;(folder/'CSXS').mkdir(parents=True)
        (folder/'CSXS/manifest.xml').write_text(f'<ExtensionManifest ExtensionBundleId="{identifier}" ExtensionBundleName="{name}"><MainPath>./index.html</MainPath></ExtensionManifest>',encoding='utf8')
        (folder/'index.html').write_text('<body><button title="Run it">Run</button></body>',encoding='utf8');return P.inspect(folder)
    def listed(self):return [self.page.target_list.item(i).text() for i in range(self.page.target_list.count()) if not self.page.target_list.item(i).isHidden()]
    def test_dictionary_files_are_gone(self):
        self.assertFalse(hasattr(P,'import_dictionary') or hasattr(P,'export_dictionary'))
        self.assertFalse(hasattr(self.page,'import_btn') or hasattr(self.page,'export_btn'))
    def mode(self,key):self.page.type_box.setCurrentIndex(self.page.type_box.findData(key))
    def test_type_filters_and_visible_counts(self):
        spaces,targets=self.scoped_scripts();shared=self.cep('Shared','com.studio.shared');self.page.found((targets+[shared],[]))
        self.mode('current');self.assertEqual(sorted(self.listed()),['Same','Same']);self.assertEqual(self.page.visible_count(),2)
        self.mode('cep');self.assertEqual(self.listed(),['Shared']);self.assertIn('当前显示 1 / 共 5',self.page.list_hint.text())
        self.mode('manual');self.assertEqual(self.listed(),[])
        self.mode('applied');self.assertEqual(self.listed(),[])
    def test_manual_filter_and_nav_count(self):
        self.page.locate(str(self.script));self.mode('manual');self.assertEqual(self.listed(),['sample'])
        self.assertEqual(self.page.visible_count(),1)
        with patch.object(self.window,'update_nav') as nav:self.page.target_search.setText('missing');nav.assert_called()
        self.assertEqual(self.page.visible_count(),0)
    def test_no_checked_no_translate_or_apply(self):
        self.page.extracted(self.page.localizer().extract());self.page.paint_checks(range(self.page.table.rowCount()),False)
        self.assertFalse(self.page.translate_btn.isEnabled());self.assertFalse(self.page.apply_btn.isEnabled());self.assertIn('勾选',self.page.translate_btn.toolTip())
    def test_untranslated_and_translated_button_states(self):
        self.page.extracted(self.page.localizer().extract());self.assertFalse(self.page.apply_btn.isEnabled());self.assertTrue(self.page.translate_btn.isEnabled())
        for r in range(self.page.table.rowCount()):self.page.table.item(r,2).setText('中文'+str(r))
        self.assertTrue(self.page.apply_btn.isEnabled());self.assertTrue(self.page.translate_btn.isEnabled());self.assertIn('已有中文',self.page.translate_btn.toolTip())
    def test_invalid_saved_translation_disables_apply(self):
        self.page.extracted(self.page.localizer().extract());self.page.words['Title']='bad\0';self.page.update_controls();self.assertFalse(self.page.apply_btn.isEnabled())
    def test_forced_read_and_read_failure_diagnostic(self):
        captured={}
        def start(work,result,failure):captured.update(work=work,result=result,failure=failure)
        with patch.object(self.window,'work',side_effect=start):self.page.extract(force=True)
        with patch.object(P.Localizer,'extract',return_value={'ok':True}) as read:captured['work'](None);self.assertTrue(read.call_args.kwargs['force'])
        with patch.object(self.window,'error'):captured['failure']('unsupported syntax')
        self.assertEqual(self.page.read_error,'unsupported syntax');self.assertIsNone(self.page.report);self.assertTrue(self.page.diagnostics_btn.isEnabled())
    def test_history_and_diagnostic_windows_read_only(self):
        from panel_details import History,Diagnostics
        self.page.extracted(self.page.localizer().extract());self.page.localizer().apply(self.page.report,{'Title':'标题'})
        before=self.script.read_bytes();h=History(self.page,self.page.root/'panels');self.addCleanup(h.deleteLater)
        self.assertGreater(h.table.rowCount(),0);h.reload();self.assertEqual(self.script.read_bytes(),before)
        d=Diagnostics(self.page,self.page.report);self.addCleanup(d.deleteLater);self.assertEqual(self.script.read_bytes(),before)
    def test_history_open_is_limited_to_backup_folder(self):
        from panel_details import History
        self.page.extracted(self.page.localizer().extract());self.page.localizer().apply(self.page.report,{'Title':'标题'})
        h=History(self.page,self.page.root/'panels');self.addCleanup(h.deleteLater)
        with patch('panel_details.QDesktopServices.openUrl') as opened:
            h.open_backup();opened.assert_called_once();opened.reset_mock()
            h.rows[h.table.currentRow()]['backup']=str(self.root/'outside')
            h.open_backup();opened.assert_not_called()
    def scoped_scripts(self):
        from types import SimpleNamespace
        spaces=[];targets=[]
        for name in ('AE24','AE26'):
            folder=self.root/name/'Support Files';(folder/'Scripts/ScriptUI Panels').mkdir(parents=True)
            exe=folder/'AfterFX.exe';spaces.append(SimpleNamespace(exe=exe,label=name))
            for suffix in ('.jsx','.jsxbin'):
                p=folder/'Scripts/ScriptUI Panels'/('Same'+suffix);p.write_bytes(self.raw);targets.append(P.inspect(p))
        self.window.spaces=spaces;self.window.space=spaces[0];return spaces,targets
    def test_only_current_install_scripts_and_shared_cep(self):
        spaces,targets=self.scoped_scripts();shared=self.cep('Shared','com.studio.shared')
        self.page.found((targets+[shared],[]))
        from panel_page import KEY
        keys={self.page.target_list.item(i).data(KEY) for i in range(self.page.target_list.count()) if not self.page.target_list.item(i).isHidden()}
        self.assertEqual(keys,{targets[0].key,targets[1].key,shared.key})
        self.window.space=spaces[1];self.page.version_changed()
        keys={self.page.target_list.item(i).data(KEY) for i in range(self.page.target_list.count()) if not self.page.target_list.item(i).isHidden()}
        self.assertEqual(keys,{targets[2].key,targets[3].key,shared.key})
    def test_version_change_clears_terms_without_read_or_write(self):
        spaces,targets=self.scoped_scripts();self.page.found((targets,[]),select=targets[0].key);self.page.extracted(self.page.localizer().extract())
        self.page.table.item(0,2).setText('标题');dictionary=self.page.localizer().dictionary();self.page.reread=True
        self.window.space=spaces[1]
        with patch.object(P.Localizer,'extract') as read:self.page.version_changed();read.assert_not_called()
        self.assertIsNone(self.page.report);self.assertEqual(self.page.table.rowCount(),0);self.assertFalse(self.page.reread)
        self.assertEqual(P.Localizer(targets[0],self.page.root/'panels').dictionary(),dictionary)
        self.assertEqual(Path(targets[0].root,targets[0].entry).read_bytes(),self.raw)
    def test_same_version_label_does_not_merge_installations(self):
        spaces,targets=self.scoped_scripts();spaces[0].label=spaces[1].label='Same AE'
        self.page.found((targets,[]))
        from panel_page import KEY
        keys={self.page.target_list.item(i).data(KEY) for i in range(self.page.target_list.count()) if not self.page.target_list.item(i).isHidden()}
        self.assertEqual(keys,{targets[0].key,targets[1].key})
    def test_main_version_binding_notifies_panel_page(self):
        with patch.object(self.page,'version_changed') as changed:self.window.bind_space(self.window.space);changed.assert_called_once_with()
    def test_outside_manual_script_stays_shared(self):
        spaces,targets=self.scoped_scripts();self.page.found((targets+[P.inspect(self.script)],[]));self.assertIn('sample',self.listed())
        self.window.space=spaces[1];self.page.version_changed();self.assertIn('sample',self.listed())
    def test_adobe_extensions_hidden_until_asked(self):
        self.page.found(([P.inspect(self.script),self.cep('Start','com.adobe.ccx.start'),self.cep('Tool','com.studio.tool')],[]))
        self.assertEqual(sorted(self.listed()),['Tool','sample']);self.assertIn('隐藏了 1 个',self.page.list_hint.text())
        self.page.adobe_box.setChecked(True);self.assertEqual(sorted(self.listed()),['Start','Tool','sample'])
    def test_search_filters_targets_and_keeps_a_visible_selection(self):
        self.page.found(([P.inspect(self.script),self.cep('Tool','com.studio.tool')],[]))
        self.page.target_search.setText('studio');self.assertEqual(self.listed(),['Tool']);self.assertEqual(self.page.target().name,'Tool')
    def test_read_then_terms_view_and_counts(self):
        self.assertIs(self.page.stack.currentWidget(),self.page.empty);self.assertTrue(self.page.extract_btn.isVisible() or not self.page.isVisible())
        self.page.extracted(self.page.localizer().extract());self.assertIs(self.page.stack.currentWidget(),self.page.terms)
        self.page.table.item(0,2).setText('标题');self.assertIn('已有中文 1',self.page.counts.text())
        self.page.untranslated.setChecked(True);hidden=[self.page.table.item(i,1).text() for i in range(self.page.table.rowCount()) if self.page.table.isRowHidden(i)]
        self.assertEqual(hidden,['Title'])
    def test_header_toggles_only_listed_rows(self):
        self.page.extracted(self.page.localizer().extract());self.page.term_search.setText('Apply')
        self.page.toggle_visible();self.assertEqual(self.page.checked(),['Title'])
    def test_apply_marks_target_and_reads_again(self):
        self.page.extracted(self.page.localizer().extract());self.page.table.item(0,2).setText('标题')
        self.assertFalse(self.page.restore_btn.isEnabled())
        def start(work,result,failure):result(work(None))
        with patch.object(self.window,'work',side_effect=start),patch('panel_page.confirm',return_value=True),patch('panel_page.QTimer.singleShot') as later:
            self.page.apply();self.assertIn(b'\u6807\u9898',self.script.read_bytes())
            self.assertTrue(self.page.reread);self.page.finished();later.assert_called_once_with(0,self.page.extract)
        self.assertEqual(self.page.status[self.page.target().key],'applied');self.assertTrue(self.page.restore_btn.isEnabled())
        self.assertEqual(self.page.target_list.currentItem().data(Qt.ItemDataRole.UserRole)['tags'],[('已汉化',True)])
        with patch.object(self.window,'work',side_effect=start),patch('panel_page.confirm',return_value=True):self.page.restore()
        self.assertEqual(self.script.read_bytes(),self.raw);self.assertFalse(self.page.restore_btn.isEnabled())
    def test_manual_target_can_be_removed_from_list(self):
        extra=self.cep('Hand','com.studio.hand');self.page.locate(extra.root)
        self.assertEqual(self.page.target().name,'Hand');self.assertTrue(self.page.forget_action.isEnabled())
        self.page.forget();self.assertNotIn('Hand',self.listed());self.assertEqual(self.page.extra,[])
        self.assertEqual(P.document(self.page.root/'panels'/'locations.json'),[])
    def test_translate_reports_progress_as_one_line(self):
        self.page.extracted(self.page.localizer().extract());lines=[]
        def fake(items,*args,progress=None,**options):
            progress(len(items),len(items));return {item['id']:'中文'+item['id'] for item in items}
        def start(work,result,failure):result(work(lambda text:lines.append(text)))   # like Job.progress: Signal(str)
        with patch.object(self.window.settings,'ready',return_value=True),patch.object(self.window,'work',side_effect=start),patch('panel_page.translate_ui_strings',side_effect=fake):
            self.page.translate()
        self.assertEqual(len(lines),1);self.assertIn('2/2',lines[0]);self.assertEqual(sorted(self.page.words.values()),['中文0','中文1'])
    def seed_translations(self):
        self.page.extracted(self.page.localizer().extract())
        for r in range(self.page.table.rowCount()):self.page.table.item(r,2).setText('旧译文'+str(r))
        return dict(self.page.words)
    def run_translation(self,reply=None,error=None):
        def start(work,result,failure):result(work(lambda text:None))
        with patch.object(self.window.settings,'ready',return_value=True),patch.object(self.window,'work',side_effect=start),patch('panel_page.translate_ui_strings',return_value=reply,side_effect=error) as translate:
            self.page.translate()
        return translate
    def test_retranslate_only_checked_replaces_saved_translation(self):
        old=self.seed_translations();self.page.table.item(1,0).setCheckState(Qt.CheckState.Unchecked)
        call=self.run_translation({'0':'新标题'})
        self.assertEqual([r['sourceName'] for r in call.call_args.args[0]],['Title'])
        self.assertEqual(self.page.words,{'Title':'新标题','Apply':old['Apply']})
        self.assertEqual(self.page.localizer().dictionary(),self.page.words);self.assertEqual(self.script.read_bytes(),self.raw)
    def test_retranslation_partial_failure_preserves_remaining_old_translation(self):
        from llm_client import TranslationError
        old=self.seed_translations();self.run_translation(error=TranslationError('失败',{'0':'新标题'},2))
        self.assertEqual(self.page.words,{'Title':'新标题','Apply':old['Apply']})
    def test_retranslation_total_failure_preserves_old_translation(self):
        from llm_client import TranslationError
        old=self.seed_translations();self.run_translation(error=TranslationError('失败',{},2))
        self.assertEqual(self.page.words,old);self.assertEqual(self.page.localizer().dictionary(),old)
    def test_retranslation_invalid_reply_preserves_old_translation(self):
        old=self.seed_translations()
        with patch.object(self.window,'error') as error:self.run_translation({'0':'bad\0'});error.assert_called_once()
        self.assertEqual(self.page.words,old);self.assertEqual(self.page.localizer().dictionary(),old)
