from unittest.mock import patch
from PySide6.QtCore import Qt
from test_gui import GuiCase
import app_live
from app_live import CHECK,ORIGINAL,CANDIDATE,CURRENT,KEY,STATUS
from ae_profiles import PROFILES

TESTED='26.5.0.89'

class CategoryGui(GuiCase):
    def live(self,writable=True):
        build={'version':TESTED,'profileSha256':PROFILES[TESTED].flt_sha} if writable else {'version':'26.0.0.67','profileSha256':'0'*64}
        self.window.live={'pid':123,'creationTime':456,'image':str(self.window.space.exe),**build,
                          'effects':[{'matchName':'S_Glow','displayName':'S_Glow','category':'Sapphire Lighting'},{'matchName':'tc Shine','displayName':'Shine','category':'RG Blurs and Glows'}]}
        self.window.update_names()
    def immediate(self,work,done,*rest):done(work(lambda text:None))
    def header(self,group):return next(r for r in self.window.group_rows() if self.window.group_of(r)==group)
    def tick(self,group):
        w=self.window;w.table.item(self.header(group),CHECK).setCheckState(Qt.CheckState.Checked)
    def menu(self,group):return {a.text():a for a in self.window.group_menu(self.header(group)).actions()}

class GroupRows(CategoryGui):
    def test_one_folded_header_per_category_above_its_effects(self):
        w=self.window;t=self.table
        self.assertEqual([w.group_of(r) for r in w.group_rows()],['Keying','RG Blurs and Glows','Sapphire Lighting'])
        self.assertEqual(len(w.effect_rows()),4);self.assertTrue(all(t.isRowHidden(r) for r in w.effect_rows()))
        self.assertEqual(t.item(self.header('Sapphire Lighting'),KEY).text(),'2 个效果')
        # Every row of a group follows its header, whichever column sorts and in either direction.
        for column,order in ((ORIGINAL,Qt.SortOrder.DescendingOrder),(KEY,Qt.SortOrder.AscendingOrder),(CHECK,Qt.SortOrder.DescendingOrder)):
            t.sortItems(column,order);head=None
            for row in range(t.rowCount()):
                if w.is_group(row):head=w.group_of(row)
                else:self.assertEqual(w.group_of(row),head)
    def test_clicking_a_header_opens_and_folds_its_group_only(self):
        w=self.window;t=self.table;row=self.header('Sapphire Lighting')
        w.table_clicked(row,ORIGINAL)
        self.assertEqual({t.item(r,KEY).text() for r in w.effect_rows() if not t.isRowHidden(r)},{'S_Glow'})
        w.fold_click=(None,0.0);w.table_clicked(row,STATUS);self.assertFalse(t.isRowHidden(self.rows_for('S_Glow')[0]))   # other cells only select
        w.table_clicked(row,ORIGINAL);self.assertTrue(all(t.isRowHidden(r) for r in w.effect_rows()))
        # A double click is two clicks: the second does not fold the group straight back.
        w.fold_click=(None,0.0);w.table_clicked(row,ORIGINAL);w.table_clicked(row,ORIGINAL);self.assertFalse(t.isRowHidden(self.rows_for('S_Glow')[0]))
    def test_searching_opens_matching_groups_and_finds_categories_by_chinese_name(self):
        w=self.window;t=self.table
        w.filter.setText('shine');self.assertFalse(t.isRowHidden(self.rows_for('tc Shine')[0]));self.assertTrue(t.isRowHidden(self.header('Keying')))
        w.filter.clear();self.assertTrue(t.isRowHidden(self.rows_for('tc Shine')[0]))
        t.item(self.header('Keying'),CANDIDATE).setText('抠像');self.assertEqual(w.category_pending,{'Keying':'抠像'})
        self.assertEqual(app_live.CandidateStore(w.space.folder/'category-translations.json').values,{'Keying':'抠像'})
        w.filter.setText('抠像');self.assertEqual({t.item(r,KEY).text() for r in w.visible_rows()},{'Atom Key'})

class GroupChecks(CategoryGui):
    def test_the_header_box_checks_its_effects_and_ticks_the_category(self):
        w=self.window;self.tick('Sapphire Lighting')
        self.assertEqual(w.enabled,{'S_Glow'});self.assertEqual(w.checked_groups(),['Sapphire Lighting'])
        self.assertEqual(app_live.CandidateStore(w.space.folder/'category-enabled.json',keys=True).values,{'Sapphire Lighting'})
        # Unchecking one effect leaves the box partly checked, and the category is no longer taken along.
        w.check_keys({'S_Glow'},False);self.assertEqual(w.checked_groups(),[])
        self.assertEqual(self.table.item(self.header('Sapphire Lighting'),CHECK).checkState(),Qt.CheckState.Unchecked)
    def test_checking_every_effect_by_hand_does_not_take_the_category_along(self):
        w=self.window;w.check_keys({'Atom Key'},True)
        self.assertEqual(self.table.item(self.header('Keying'),CHECK).checkState(),Qt.CheckState.PartiallyChecked);self.assertEqual(w.checked_groups(),[])
    def test_checking_the_filtered_results_ticks_their_categories_and_uncheck_all_clears_them(self):
        w=self.window;w.check_visible(True);self.assertEqual(set(w.checked_groups()),{'Keying','RG Blurs and Glows','Sapphire Lighting'})
        w.uncheck_all_action.trigger();self.assertEqual((w.enabled,w.ticked),(set(),set()))

class CategoryWrites(CategoryGui):
    def test_actions_need_the_tested_build_a_connection_and_idle(self):
        w=self.window;title='应用此分类名到 AE'
        self.table.item(self.header('Sapphire Lighting'),CANDIDATE).setText('蓝宝石灯光')
        self.assertFalse(self.menu('Sapphire Lighting')[title].isEnabled())
        self.live(writable=False);actions=self.menu('Sapphire Lighting')
        self.assertFalse(actions[title].isEnabled());self.assertIn('分类名写入需要当前 AE 构建与模块通过适配核验',actions)
        self.live();self.assertTrue(self.menu('Sapphire Lighting')[title].isEnabled())
        with patch.object(w,'busy',return_value=True):self.assertFalse(self.menu('Sapphire Lighting')[title].isEnabled())
        self.assertEqual(self.table.item(self.header('Sapphire Lighting'),STATUS).text(),'待应用')
    def test_applying_checked_items_writes_ticked_categories_as_whole_ae_groups(self):
        self.live();w=self.window;self.table.item(self.header('Sapphire Lighting'),CANDIDATE).setText('蓝宝石灯光');self.tick('Sapphire Lighting')
        group={'category':'Sapphire Lighting','keys':['S_Glow','S_Other'],'supported':True,'version':TESTED}
        with patch.object(w,'work',side_effect=self.immediate),patch.object(w,'apply_names',return_value=({'applied':{}},w.live,'')) as names,\
             patch.object(w.category_session,'group',return_value=group),patch.object(w.category_session,'apply_group',return_value={'applied':{'S_Glow':'蓝宝石灯光','S_Other':'蓝宝石灯光'}}) as apply,\
             patch.object(app_live,'with_live',side_effect=lambda value,image:(value,w.live,'')),patch.object(app_live,'refresh_effects') as refresh:
            w.apply_target()
        apply.assert_called_once_with('S_Glow','蓝宝石灯光','Sapphire Lighting',['S_Glow','S_Other']);refresh.assert_not_called()
        self.assertEqual(names.call_args.args[0],{'S_Glow':'辉光'});self.assertTrue(w.needs_refresh)
        self.assertIn('分类名称：Sapphire Lighting→蓝宝石灯光',w.log.toPlainText())
    def test_a_failed_category_is_reported_and_does_not_stop_the_others(self):
        self.live();w=self.window
        for group,value in (('Sapphire Lighting','蓝宝石灯光'),('RG Blurs and Glows','RG 模糊与辉光')):self.table.item(self.header(group),CANDIDATE).setText(value)
        def apply(key,value,category,keys):
            if key=='S_Glow':raise RuntimeError('分类已被外部修改')
            return {'applied':{key:value}}
        with patch.object(w,'work',side_effect=self.immediate),patch.object(w.category_session,'group',side_effect=lambda key:{'category':{'S_Glow':'Sapphire Lighting','tc Shine':'RG Blurs and Glows'}[key],'keys':[key]}),\
             patch.object(w.category_session,'apply_group',side_effect=apply),patch.object(app_live,'with_live',side_effect=lambda value,image:(value,w.live,'')):
            w.apply_categories(['Sapphire Lighting','RG Blurs and Glows'])
        log=w.log.toPlainText();self.assertIn('分类名称未应用：Sapphire Lighting：分类已被外部修改',log);self.assertIn('RG Blurs and Glows→RG 模糊与辉光',log)
    def test_an_unverified_build_keeps_category_names_as_candidates(self):
        self.live(writable=False);w=self.window;self.table.item(self.header('Keying'),CANDIDATE).setText('抠像');self.tick('Keying');w.pending['Atom Key']='原子'
        with patch.object(w,'work',side_effect=self.immediate),patch.object(w,'apply_names',return_value=({'applied':{}},w.live,'')),patch.object(w.category_session,'apply_group') as apply:
            w.apply_target()
        apply.assert_not_called();self.assertIn('分类名只保存为候选',w.log.toPlainText())
        self.assertEqual(self.table.item(self.header('Keying'),STATUS).text(),'仅翻译')
    def test_restoring_checked_items_restores_ticked_categories_when_any_were_written(self):
        self.live();w=self.window;self.tick('Sapphire Lighting')
        with patch.object(w,'work',side_effect=self.immediate),patch.object(w,'restore_effect_names',return_value={'restored':{}}),\
             patch.object(w.category_session,'journal',return_value={'entries':{'S_Glow':{}}}),patch.object(w.category_session,'group',return_value={'category':'蓝宝石灯光','keys':['S_Glow','S_Other']}),\
             patch.object(w.category_session,'restore',return_value={'restored':{'S_Glow':'Sapphire Lighting'}}) as restore,patch.object(app_live,'with_live',side_effect=lambda value,image:(value,w.live,'')):
            w.restore_target()
        restore.assert_called_once_with({'S_Glow','S_Other'});self.assertTrue(w.needs_refresh)
    def test_restore_all_includes_categories_without_automatic_refresh(self):
        w=self.window
        with patch.object(w,'restore_effect_names',return_value={'restored':{}}),patch.object(w.parameter_session,'restore',return_value={'restored':0}),patch.object(w.category_session,'restore',return_value={'restored':{'S_Glow':'Lights'}}) as restore,patch.object(app_live,'refresh_effects') as refresh:
            result=w.restore_names(None)
        restore.assert_called_once();refresh.assert_not_called();self.assertEqual(result['categoryResult']['restored'],{'S_Glow':'Lights'})
    def test_header_shows_the_ae_category_and_applied_state(self):
        self.live();w=self.window;row=self.header('Sapphire Lighting')
        self.assertEqual(self.table.item(row,CURRENT).text(),'Sapphire Lighting');self.assertEqual(self.table.item(self.header('Keying'),CURRENT).text(),app_live.MISSING)
        self.table.item(row,CANDIDATE).setText('蓝宝石灯光')
        w.live['effects'][0]['category']='蓝宝石灯光';w.update_names();self.assertEqual(self.table.item(row,STATUS).text(),'已应用')

class CategoryTranslation(CategoryGui):
    def test_translating_checked_items_sends_ticked_categories_with_their_own_prompt(self):
        w=self.window;self.tick('RG Blurs and Glows');calls=[]
        def fake(items,*args,**kwargs):
            calls.append((items,kwargs.get('system_prompt')));return {item['id']:'译'+item['id'][:2] for item in items}
        with patch.object(w.settings,'ready',return_value=True),patch.object(w,'work',side_effect=self.immediate),patch.object(app_live,'translate_names',side_effect=fake):
            w.translate_target()
        (effects,plain),(categories,prompt)=calls
        self.assertEqual([i['id'] for i in effects],['tc Shine']);self.assertIsNone(plain)
        self.assertEqual(categories,[{'id':'RG Blurs and Glows','sourceName':'RG Blurs and Glows','category':'该分类下的效果：Shine'}]);self.assertIs(prompt,app_live.CATEGORY_PROMPT)
        self.assertEqual(w.category_pending,{'RG Blurs and Glows':'译RG'});self.assertEqual(self.table.item(self.header('RG Blurs and Glows'),CANDIDATE).text(),'译RG')
    def test_a_category_can_be_translated_from_its_menu(self):
        w=self.window
        with patch.object(w.settings,'ready',return_value=True),patch.object(w,'work',side_effect=self.immediate),patch.object(app_live,'translate_names',return_value={'Keying':'抠像'}) as fake:
            self.menu('Keying')['AI 翻译此分类名'].trigger()
        fake.assert_called_once();self.assertEqual(w.category_pending,{'Keying':'抠像'})
    def test_a_failed_category_batch_keeps_the_finished_effect_names(self):
        w=self.window;self.tick('Keying')
        def fake(items,*args,**kwargs):
            if kwargs.get('system_prompt'):raise app_live.TranslationError('服务失败',{},1)
            return {'Atom Key':'原子抠像'}
        with patch.object(w.settings,'ready',return_value=True),patch.object(w,'work',side_effect=self.immediate),patch.object(app_live,'translate_names',side_effect=fake),patch.object(w,'error') as error:
            w.translate_target()
        self.assertEqual(w.pending['Atom Key'],'原子抠像');self.assertEqual(w.category_pending,{});error.assert_called_once()

class MenuSync(CategoryGui):
    def test_only_explicit_refresh_synchronizes_menus(self):
        self.live();w=self.window
        with patch.object(w,'work',side_effect=self.immediate),patch.object(app_live,'refresh_effects',return_value={'nativePaletteRefreshed':True}),patch.object(w.session,'journal',return_value={'entries':{'S_Glow':{}}}),patch.object(w.menu_name_session,'sync',return_value={'updated':1,'supported':True}) as sync:
            w.refresh_native()
        self.assertEqual(sync.call_args.args,(123,456,str(w.space.exe)));self.assertIn('deadline',sync.call_args.kwargs);self.assertIn('同步 1',w.status.text())
    def test_menu_failure_is_visible_without_reporting_list_failure(self):
        self.live();w=self.window
        with patch.object(w,'work',side_effect=self.immediate),patch.object(app_live,'refresh_effects',return_value={'nativePaletteRefreshed':True}),patch.object(w.session,'journal',return_value={'entries':{'S_Glow':{}}}),patch.object(w.menu_name_session,'sync',side_effect=RuntimeError('菜单目标变化')):
            w.refresh_native()
        self.assertIn('菜单目标变化',w.status.text());self.assertIn('列表已刷新',w.status.text())

class CategoryDetail(CategoryGui):
    def select_header(self,group):
        self.table.clearSelection();self.table.selectRow(self.header(group))
    def test_a_selected_category_fills_the_detail_panel_and_takes_the_typed_name(self):
        self.live();w=self.window;self.select_header('Sapphire Lighting')
        self.assertEqual(w.selected_rows(),[]);self.assertEqual(w.selected_group(),'Sapphire Lighting')
        self.assertEqual(w.detail_eyebrow.text(),'效果分类');self.assertEqual(w.selected_label.text(),'Sapphire Lighting');self.assertIn('2 个效果',w.detail_info.text())
        self.assertTrue(w.name_edit.isEnabled());self.assertIn('分类',w.name_edit.placeholderText());self.assertFalse(w.bilingual_box.isHidden());self.assertIn('分类名始终只写中文',w.path_label.text());self.assertEqual(w.target_note.text(),'作用于所选分类；勾选效果后改为作用于勾选项')
        w.name_edit.setText('蓝宝石灯光');w.commit_name()
        self.assertEqual(w.category_pending,{'Sapphire Lighting':'蓝宝石灯光'});self.assertEqual(self.table.item(self.header('Sapphire Lighting'),CANDIDATE).text(),'蓝宝石灯光')
        self.assertTrue(w.apply_btn.isEnabled());self.assertTrue(w.restore_btn.isEnabled());self.assertTrue(w.translate_btn.isEnabled())
    def test_the_detail_buttons_act_on_the_selected_category(self):
        self.live();w=self.window;self.select_header('Sapphire Lighting');w.name_edit.setText('蓝宝石灯光')
        with patch.object(w,'apply_categories') as apply,patch.object(w,'restore_categories') as restore,patch.object(w,'translate_rows') as translate,patch.object(w,'apply_names') as names:
            w.apply_target();w.restore_target();w.translate_target()
        apply.assert_called_once_with(['Sapphire Lighting']);restore.assert_called_once_with(['Sapphire Lighting']);translate.assert_called_once_with([],['Sapphire Lighting']);names.assert_not_called()
        self.assertEqual(w.category_pending,{'Sapphire Lighting':'蓝宝石灯光'})
    def test_an_unverified_build_keeps_the_typed_name_and_says_why(self):
        self.live(writable=False);w=self.window;self.select_header('Keying');w.name_edit.setText('抠像');w.commit_name()
        self.assertFalse(w.apply_btn.isEnabled());self.assertIn('适配核验',w.path_label.text())
        with patch.object(w,'error') as error,patch.object(w,'work') as work:w.apply_selected()
        error.assert_called_once();work.assert_not_called();self.assertEqual(w.category_pending,{'Keying':'抠像'})
    def test_restore_menu_has_a_categories_only_entry(self):
        w=self.window;self.live();w.update_buttons()
        self.assertIn(w.restore_categories_action,w.restore_tool.menu().actions())
        with patch.object(w,'work',side_effect=self.immediate),patch.object(w.category_session,'restore',return_value={'restored':{'S_Glow':'Sapphire Lighting'}}) as restore,\
             patch.object(w,'restore_effect_names') as names,patch.object(w.parameter_session,'restore') as parameters,patch.object(app_live,'with_live',side_effect=lambda value,image:(value,w.live,'')):
            w.restore_categories_action.trigger()
        restore.assert_called_once_with();names.assert_not_called();parameters.assert_not_called();self.assertIn('已恢复 1 个效果分类',w.status.text())

class CategoriesOnly(CategoryGui):
    def test_categories_alone_leave_the_effects_and_still_drive_the_checked_actions(self):
        self.live();w=self.window;w.categories_only_action.trigger();self.assertTrue(w.categories_only)
        self.assertTrue(app_live.ui_prefs().get('categoriesOnly'))
        self.tick('Sapphire Lighting')
        self.assertEqual(w.enabled,set());self.assertEqual(w.checked_groups(),['Sapphire Lighting']);self.assertEqual(w.target_mode(),'checked')
        self.assertEqual(self.table.item(self.header('Sapphire Lighting'),CHECK).checkState(),Qt.CheckState.Checked)
        # Effects are still checked one by one, and unchecking one keeps the category selected.
        w.check_keys({'S_Glow'},True);w.check_keys({'S_Glow'},False);self.assertEqual(w.checked_groups(),['Sapphire Lighting'])
        self.table.item(self.header('Sapphire Lighting'),CANDIDATE).setText('蓝宝石灯光')
        with patch.object(w,'apply_names') as names,patch.object(w,'work',side_effect=self.immediate),patch.object(w.category_session,'group',return_value={'category':'Sapphire Lighting','keys':['S_Glow']}),\
             patch.object(w.category_session,'apply_group',return_value={'applied':{'S_Glow':'蓝宝石灯光'}}) as apply,patch.object(app_live,'with_live',side_effect=lambda value,image:(value,w.live,'')):
            w.apply_target()
        names.assert_not_called();apply.assert_called_once()
    def test_sweeping_over_a_category_box_ticks_only_the_category(self):
        w=self.window;w.set_categories_only(True);row=self.header('Keying')
        w.paint_checks([row],True);w.finish_checks([row],True)
        self.assertEqual((w.enabled,w.checked_groups()),(set(),['Keying']));self.assertIn('1 个分类名',w.status.text())
        self.assertEqual(self.menu('Keying')['取消勾选此分类名'].text(),'取消勾选此分类名')
    def test_the_default_mode_unticks_a_category_when_one_of_its_effects_is_unchecked(self):
        w=self.window;self.tick('Sapphire Lighting');self.assertEqual(w.checked_groups(),['Sapphire Lighting'])
        self.table.item(self.rows_for('S_Glow')[0],CHECK).setCheckState(Qt.CheckState.Unchecked);self.assertEqual(w.checked_groups(),[])

class Thanks(CategoryGui):
    def test_the_acknowledgement_names_the_panel_helper_and_scrolls_inside_its_card(self):
        w=self.window;self.assertIn('白嫖',w.thanks.text());self.assertIn('面板与脚本',w.thanks.text())
        self.assertTrue(w.thanks_scroll.isAncestorOf(w.thanks));self.assertTrue(w.thanks_scroll.isAncestorOf(w.limits))
