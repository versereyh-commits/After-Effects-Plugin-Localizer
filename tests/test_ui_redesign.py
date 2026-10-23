"""The 0.7 interface: the detail panel's target buttons, keyframe status, metric tiles, loaders and empty states."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu
from test_gui import GuiCase,VersionTests
import app_live,ui_kit
from app_live import CHECK,CANDIDATE,KEY,STATUS
from parameter_names import identity
import parameter_dialog
from parameter_dialog import H_EFFECT,H_NAME,H_CHINESE,H_TRASH

LIVE={'pid':1,'creationTime':5,'effects':[{'matchName':'S_Glow','displayName':'辉光','category':'c'},{'matchName':'tc Shine','displayName':'Shine','category':'c'}]}

class DetailTargetTests(GuiCase):
    """The detail panel's translate / apply / restore act on the checked effects, or on the selected row when none are checked."""
    def check(self,key):self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked)
    def keys(self,rows):return {self.table.item(r,KEY).text() for r in rows}
    def test_labels_and_state_follow_the_checks(self):
        w=self.window;w.show_live(LIVE);w.update_buttons()
        self.assertEqual([b.text() for b in (w.translate_btn,w.apply_btn,w.restore_btn)],['翻译所选项','应用所选项','恢复所选项'])
        self.assertFalse(any(b.isEnabled() for b in (w.translate_btn,w.apply_btn,w.restore_btn)));self.assertIn('选择一行',w.target_note.text())
        self.select(self.rows_for('Atom Key')[0]);self.assertTrue(all(b.isEnabled() for b in (w.translate_btn,w.apply_btn,w.restore_btn)))
        self.table.clearSelection();self.check('tc Shine')
        self.assertEqual([b.text() for b in (w.translate_btn,w.apply_btn,w.restore_btn)],['翻译勾选项','应用勾选项','恢复勾选项'])
        self.assertTrue(w.translate_btn.isEnabled());self.assertTrue(w.restore_btn.isEnabled());self.assertFalse(w.apply_btn.isEnabled())
        self.assertIn('1 个勾选项',w.target_note.text())
        w.translated(({'tc Shine':'闪耀'},''));self.assertTrue(w.apply_btn.isEnabled())
        self.select(self.rows_for('Atom Key')[0]);self.assertIn('所选行不参与',w.target_note.text())
    def test_without_checks_the_selected_row_is_used(self):
        w=self.window;w.show_live(LIVE);w.update_buttons();self.select(self.rows_for('Atom Key')[0])
        with patch.object(w,'translate_rows') as rows:w.translate_btn.click()
        self.assertEqual(self.keys(rows.call_args.args[0]),{'Atom Key'})
        w.name_edit.setText('原子抠像')
        with patch.object(w,'work') as work:w.apply_btn.click()
        self.assertEqual(w.writing,{'Atom Key'});work.assert_called_once();w.writing=set()
        with patch.object(w,'restore_selected') as restore:w.restore_btn.click()
        restore.assert_called_once()
    def test_with_checks_the_selected_row_takes_no_part(self):
        w=self.window;w.show_live(LIVE);w.update_buttons();self.check('tc Shine');self.select(self.rows_for('Atom Key')[0])
        with patch.object(w,'translate_rows') as rows:w.translate_btn.click()
        self.assertEqual(self.keys(rows.call_args.args[0]),{'tc Shine'})
        w.translated(({'tc Shine':'闪耀','Atom Key':'原子抠像'},''))
        with patch.object(w,'work') as work:w.apply_btn.click()
        self.assertEqual(w.writing,{'tc Shine'});work.assert_called_once();w.writing=set()
        with patch.object(w,'work') as work:w.restore_btn.click()
        self.assertIn('1 个勾选项',w.status.text());work.assert_called_once()
    def test_checks_work_without_a_selected_row(self):
        w=self.window;self.check('Atom Key');self.table.clearSelection();self.assertTrue(w.translate_btn.isEnabled())
        with patch.object(w,'translate_rows') as rows:w.translate_btn.click()
        self.assertEqual(self.keys(rows.call_args.args[0]),{'Atom Key'})
    def test_translating_checks_that_already_have_chinese_asks_first(self):
        w=self.window;self.check('S_Glow');self.check('Atom Key')
        for answer,expected in ((None,None),(0,{'S_Glow','Atom Key'}),(1,{'Atom Key'})):
            with patch.object(app_live,'choose',return_value=answer) as asked,patch.object(w,'translate_rows') as rows:w.translate_btn.click()
            choices=asked.call_args.args[3];self.assertEqual(choices,['全部重新翻译（2 项）','只翻译缺少的 1 项'])
            self.assertEqual(self.keys(rows.call_args.args[0]) if rows.called else None,expected)
        w.check_keys({'Atom Key'},False)
        with patch.object(app_live,'choose',return_value=0) as asked,patch.object(w,'translate_rows') as rows:w.translate_btn.click()
        self.assertEqual(asked.call_args.args[3],['全部重新翻译（1 项）']);self.assertEqual(self.keys(rows.call_args.args[0]),{'S_Glow'})
        w.check_keys({'S_Glow'},False);self.check('tc Shine')
        with patch.object(app_live,'choose',side_effect=AssertionError('nothing to ask')),patch.object(w,'translate_rows') as rows:w.translate_btn.click()
        rows.assert_called_once()
    def test_typed_name_is_kept_when_the_field_loses_focus(self):
        w=self.window;self.select(self.rows_for('Atom Key')[0]);w.name_edit.setText('原子抠像');w.name_edit.editingFinished.emit()
        self.assertEqual(w.pending['Atom Key'],'原子抠像');self.assertEqual(self.table.item(self.rows_for('Atom Key')[0],CANDIDATE).text(),'原子抠像')
    def test_refresh_button_stands_out_after_a_write_but_never_refreshes_by_itself(self):
        w=self.window;w.show_live(LIVE);w.update_buttons();self.assertFalse(w.refresh_btn.property('attention'))
        with patch.object(app_live,'refresh_effects',side_effect=AssertionError('must not refresh')):
            w.applied(({'applied':{'tc Shine':'闪耀'}},LIVE,''));w.update_buttons()
        self.assertTrue(w.needs_refresh);self.assertTrue(w.refresh_btn.property('attention'));self.assertIn('点此刷新',w.refresh_btn.toolTip())
        with patch.object(app_live,'refresh_effects',return_value={}),patch.object(w,'work',lambda work,done:done(work(lambda text:None))):w.refresh_native()
        self.assertFalse(w.needs_refresh);self.assertFalse(w.refresh_btn.property('attention'))
    def test_the_header_has_no_next_step_button_or_apply_menu(self):
        w=self.window;self.assertFalse(hasattr(w,'next_btn'));self.assertFalse(hasattr(w,'apply_scheme_action'))
    def test_switching_version_forgets_the_refresh_reminder(self):
        w=self.window;w.needs_refresh=True;w.bind_space(w.space);self.assertFalse(w.needs_refresh)

class StatusColumnTests(GuiCase):
    def status(self,key):return self.table.item(self.rows_for(key)[0],STATUS).text()
    def test_rows_show_keyframe_status(self):
        w=self.window
        self.assertEqual((self.status('S_Glow'),self.status('Atom Key')),('未连接','待翻译'))
        w.show_live(LIVE);w.translated(({'tc Shine':'闪耀'},''))
        self.assertEqual([self.status(k) for k in ('S_Glow','tc Shine','Atom Key')],['已应用','待应用','未加载'])
        self.table.item(self.rows_for('tc Shine')[0],CANDIDATE).setText('Shine');self.assertEqual(self.status('tc Shine'),'已应用')
    def test_status_is_shown_beside_the_check_column(self):
        self.assertEqual(self.table.horizontalHeader().visualIndex(STATUS),1)
    def test_writing_rows_are_marked_for_the_breathing_diamond(self):
        w=self.window;w.writing={'S_Glow'};index=self.table.model().index(self.rows_for('S_Glow')[0],STATUS)
        self.assertTrue(w.status_delegate.active(index));w.writing=set();self.assertFalse(w.status_delegate.active(index))

class LoaderTests(GuiCase):
    def test_loader_shows_verb_ignores_clicks_and_restores(self):
        button=ui_kit.LoaderButton('应用到 AE','primary');self.addCleanup(button.deleteLater)
        button.start('fill','写入中');self.assertEqual(button.text(),'写入中');self.assertTrue(button.loading())
        self.assertTrue(button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        button.set_label('应用 3 项');self.assertEqual(button.text(),'写入中')
        button.stop();self.assertEqual(button.text(),'应用 3 项');self.assertFalse(button.loading())
        self.assertFalse(button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
    def test_job_started_from_a_button_loads_until_it_finishes(self):
        w=self.window;w.show_live(LIVE);w.update_buttons();self.select(self.rows_for('S_Glow')[0])
        with patch.object(w,'apply_names',return_value=({'applied':{}},None,'')):
            w.apply_btn.click();self.assertTrue(w.apply_btn.loading());self.assertTrue(w.apply_btn.isEnabled());self.assertEqual(w.writing,{'S_Glow'})
            self.assertTrue(w.job.wait(3000));self.app.processEvents()
        self.assertFalse(w.apply_btn.loading());self.assertEqual(w.apply_btn.text(),'应用所选项');self.assertEqual(w.writing,set())
    def test_tests_run_without_motion(self):
        self.assertFalse(ui_kit.motion())

class TileTests(GuiCase):
    def test_tiles_count_and_filter(self):
        w=self.window;self.assertEqual(w.chips['all'].value,4);self.assertEqual(w.chips['missing'].text(),'未加载 0')
        w.chips['favorite'].click();self.assertEqual(w.visible_rows(),[])
    def test_tile_order_ends_with_token_usage(self):
        w=self.window;row=w.chips['all'].parentWidget().layout()
        self.assertEqual([row.itemAt(i).widget() for i in range(row.count())],[*(w.chips[k] for k,_ in app_live.ROW_FILTERS),w.usage_tile])
        # The user's order: no 已勾选 tile (only_checked in the check menu took over), 已收藏 after 未加载, with a star.
        self.assertEqual([k for k,_ in app_live.ROW_FILTERS],['all','applied','missing','favorite']);self.assertEqual(w.chips['favorite'].mark,'star')

class EmptyStateTests(GuiCase):
    def test_effects_table_explains_an_empty_version(self):
        w=self.window;self.assertEqual(w.table_stack.currentIndex(),1)
        w.show_scan(([],[]));self.assertEqual(w.table_stack.currentIndex(),0);self.assertIn('没有找到插件名称',w.table_empty.title.text())
    def test_parameter_page_invites_reading_then_shows_rows(self):
        panel=self.window.parameter_page;self.assertEqual(panel.stack.currentIndex(),0);self.assertFalse(panel.empty_read.isHidden())
        panel.loaded({'parameters':[{'id':'A::1','name':'Size','effectMatchName':'A','matchName':'1'}]});self.assertEqual(panel.stack.currentIndex(),1)

class UnsupportedEmptyTests(GuiCase):
    versions=VersionTests.versions;installs=VersionTests.installs
    def test_unverified_version_points_to_versions_page(self):
        w=self.window;w.choose_version(1);panel=w.parameter_page
        self.assertEqual(panel.stack.currentIndex(),0);self.assertIn('尚未适配',panel.empty.title.text());self.assertFalse(panel.empty_versions.isHidden())
        self.assertEqual(w.connect_btn.text(),'自动识别与适配…')
        panel.empty_versions.click();self.assertEqual(w.pages.currentIndex(),app_live.VERSIONS)
    def test_version_detail_follows_selection(self):
        w=self.window;w.version_table.selectRow(1)
        self.assertEqual(w.version_title.text(),'AE 2021');self.assertIn('仅翻译',w.version_state.text());self.assertEqual(w.use_version_btn.text(),'切换到此版本')
        w.version_table.selectRow(0);self.assertEqual(w.use_version_btn.text(),'当前使用中')

class ParameterStatusTests(GuiCase):
    def setUp(self):
        super().setUp();self.panel=self.window.parameter_page;self.window.parameter_pending.clear()
        self.rows=[{'id':identity('A','1'),'name':'大小','effectMatchName':'A','matchName':'1'},{'id':identity('A','2'),'name':'Width','effectMatchName':'A','matchName':'2'},{'id':identity('B','1'),'name':'Size','effectMatchName':'B','matchName':'1'}]
        self.window.parameter_pending.update({self.rows[0]['id']:'大小',self.rows[1]['id']:'宽度'});self.panel.loaded({'parameters':self.rows})
    def test_parameter_rows_show_status_and_tiles_filter(self):
        p=self.panel;self.assertEqual([p.table.item(i,5).text() for i in range(3)],['已应用','待应用','待翻译'])
        self.assertEqual(p.chips['applied'].value,1);p.chips['todo'].click()
        self.assertEqual([p.table.isRowHidden(i) for i in range(3)],[True,True,False])
        p.check(True);self.assertEqual(self.window.parameter_enabled,{self.rows[2]['id']})
    def test_editing_a_candidate_updates_its_status(self):
        self.panel.table.item(2,2).setText('尺寸');self.assertEqual(self.panel.table.item(2,5).text(),'待应用');self.assertEqual(self.panel.chips['todo'].value,0)

class NavUnfoldTests(GuiCase):
    """Choosing a page fades the old block where it is and unfolds the new one from its icon, instead of sliding."""
    def setUp(self):
        super().setUp();w=self.window;w.resize(1200,720);w.show();self.addCleanup(w.hide);self.app.processEvents();self.nav=w.nav
        for _ in range(3):self.app.processEvents()
        self.nav.select(self.nav.current,False)    # What showEvent schedules (QTimer.singleShot is stubbed in tests).
    def test_without_motion_the_block_jumps(self):
        w=self.window;w.go(app_live.SCHEMES)
        self.assertEqual(self.nav.indicator.geometry(),w.nav_buttons[app_live.SCHEMES].geometry());self.assertTrue(self.nav.ghost.isHidden())
        self.assertTrue(w.nav_buttons[app_live.SCHEMES].name.property('active'))
    def test_with_motion_old_fades_and_new_unfolds_from_the_left(self):
        w=self.window;nav=self.nav;old=w.nav_buttons[app_live.EFFECTS].geometry();row=w.nav_buttons[app_live.PARAMETERS].geometry()
        with patch.object(ui_kit,'motion',return_value=True):
            w.go(app_live.PARAMETERS)
            self.assertFalse(nav.ghost.isHidden());self.assertEqual(nav.ghost.geometry(),old)
            start=nav.indicator.geometry();self.assertEqual((start.x(),start.y(),start.height()),(row.x(),row.y(),row.height()));self.assertLess(start.width(),row.width())
            # Colours follow the blocks: the new item waits for its block, the old one stays white over its fading block.
            self.assertFalse(w.nav_buttons[app_live.PARAMETERS].name.property('active'));self.assertTrue(w.nav_buttons[app_live.EFFECTS].name.property('active'))
            nav.fade.setCurrentTime(100);self.assertFalse(w.nav_buttons[app_live.EFFECTS].name.property('active'))
            nav.grow.setCurrentTime(nav.grow.duration()//2);self.assertTrue(w.nav_buttons[app_live.PARAMETERS].name.property('active'))
            grow=nav.grow;w.go(app_live.SCHEMES)    # A second click cuts the first one short.
            self.assertIsNot(nav.grow,grow);self.assertTrue(w.nav_buttons[app_live.SCHEMES].isChecked())
            self.assertTrue(w.nav_buttons[app_live.PARAMETERS].name.property('active'))    # Still over the fading block.
            nav.grow.setCurrentTime(nav.grow.duration());nav.fade.setCurrentTime(nav.fade.duration());self.app.processEvents()
            self.assertFalse(w.nav_buttons[app_live.PARAMETERS].name.property('active'))
        self.assertEqual(nav.indicator.geometry(),w.nav_buttons[app_live.SCHEMES].geometry());self.assertTrue(nav.ghost.isHidden())
        self.assertTrue(w.nav_buttons[app_live.SCHEMES].name.property('active'))

class PreferenceTests(GuiCase):
    def test_motion_choice_is_saved_locally(self):
        self.window.save_motion(False)
        self.assertEqual(json.loads((Path(self.temp.name)/'ui.json').read_text(encoding='utf8')),{'motion':False})

class TooltipTests(GuiCase):
    def test_shortcut_becomes_a_key_cap(self):
        tip=ui_kit.Tooltip();self.addCleanup(tip.deleteLater)
        from PySide6.QtCore import QPoint
        tip.show_for(self.window.connect_btn,'核对 AE 路径、构建版本并读取实际效果目录（F5）',QPoint(100,100))
        self.assertEqual((tip.text.text(),tip.key.text()),('核对 AE 路径、构建版本并读取实际效果目录','F5'));self.assertTrue(tip.isVisible())
        tip.show_for(self.window.connect_btn,'没有快捷键的说明',QPoint(100,100));self.assertTrue(tip.key.isHidden())
    def test_table_cells_and_plain_widgets_supply_text(self):
        from PySide6.QtCore import QPoint,QEvent
        from PySide6.QtGui import QHelpEvent
        styler=ui_kit.PopupStyler(self.app);self.addCleanup(styler.tip.deleteLater)
        rect=self.table.visualItemRect(self.table.item(0,KEY));event=QHelpEvent(QEvent.Type.ToolTip,rect.center(),QPoint(0,0))
        text,_=styler.tooltip_for(self.table.viewport(),event);self.assertEqual(text,self.table.item(0,KEY).text())
        text,_=styler.tooltip_for(self.window.refresh_btn,QHelpEvent(QEvent.Type.ToolTip,QPoint(1,1),QPoint(0,0)));self.assertIn('Ctrl+R',text)

class TooltipDelayTests(GuiCase):
    def help(self,widget):
        from PySide6.QtCore import QPoint,QEvent
        from PySide6.QtGui import QHelpEvent
        return QHelpEvent(QEvent.Type.ToolTip,QPoint(2,2),widget.mapToGlobal(QPoint(2,2)))
    def test_tips_wait_for_a_pause_even_right_after_another_tip(self):
        from PySide6.QtCore import QEvent,QPoint
        styler=ui_kit.PopupStyler(self.app);self.addCleanup(styler.tip.deleteLater);w=self.window
        enter=QEvent(QEvent.Type.Enter);styler.eventFilter(w.refresh_btn,enter)
        self.assertTrue(styler.eventFilter(w.refresh_btn,self.help(w.refresh_btn)))
        self.assertFalse(styler.tip.isVisible());self.assertTrue(styler.timer.isActive());self.assertGreaterEqual(styler.timer.interval(),950)
        with patch.object(ui_kit.QApplication,'widgetAt',return_value=w.refresh_btn),patch.object(ui_kit.QCursor,'pos',return_value=QPoint(5,5)):styler.reveal()
        self.assertTrue(styler.tip.isVisible());self.assertIs(styler.tip.target,w.refresh_btn)
        styler.eventFilter(w.translate_btn,enter);styler.eventFilter(w.translate_btn,self.help(w.translate_btn))
        self.assertFalse(styler.tip.isVisible());self.assertGreaterEqual(styler.timer.interval(),950)
    def test_tip_is_dropped_when_the_pointer_moved_away(self):
        from PySide6.QtCore import QEvent,QPoint
        styler=ui_kit.PopupStyler(self.app);self.addCleanup(styler.tip.deleteLater);w=self.window
        styler.eventFilter(w.refresh_btn,self.help(w.refresh_btn))
        with patch.object(ui_kit.QApplication,'widgetAt',return_value=w.apply_btn),patch.object(ui_kit.QCursor,'pos',return_value=QPoint(5,5)):styler.reveal()
        self.assertFalse(styler.tip.isVisible())
        styler.eventFilter(w.refresh_btn,self.help(w.refresh_btn));styler.eventFilter(w.refresh_btn,QEvent(QEvent.Type.Leave));self.assertFalse(styler.timer.isActive())
    def test_widget_without_tip_defers_to_its_parent(self):
        from PySide6.QtWidgets import QLabel
        styler=ui_kit.PopupStyler(self.app);self.addCleanup(styler.tip.deleteLater);plain=QLabel(self.window);self.addCleanup(plain.deleteLater)
        self.assertFalse(styler.eventFilter(plain,self.help(plain)))


class ParameterHeaderTests(GuiCase):
    def setUp(self):
        super().setUp();self.panel=self.window.parameter_page;self.window.parameter_enabled.clear()
        self.rows=[{'id':f'A::{i}','name':f'P{i}','effectMatchName':'A' if i<3 else 'B','matchName':str(i)} for i in range(5)];self.panel.loaded({'parameters':self.rows})
    def test_header_box_and_shift_run_cover_visible_parameters(self):
        from PySide6.QtWidgets import QApplication
        p=self.panel;self.assertEqual(p.table.selectionMode(),p.table.SelectionMode.NoSelection)
        p.effects.setCurrentIndex(p.effects.findData('A'));p.toggle_visible()
        self.assertEqual(self.window.parameter_enabled,{'A::0','A::1','A::2'});self.assertEqual(p.check_header.state,Qt.CheckState.Checked)
        p.effects.setCurrentIndex(0);self.assertEqual(p.check_header.state,Qt.CheckState.PartiallyChecked)
        p.table.item(3,0).setCheckState(Qt.CheckState.Checked)
        with patch.object(QApplication,'keyboardModifiers',return_value=Qt.KeyboardModifier.ShiftModifier):p.table.item(0,0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(self.window.parameter_enabled,set())


class DragCheckTests(GuiCase):
    def press(self,table,row,column=0):
        from PySide6.QtTest import QTest
        QTest.mousePress(table.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,table.visualItemRect(table.item(row,column)).center())
    def move(self,table,row,column=0):
        from PySide6.QtTest import QTest
        QTest.mouseMove(table.viewport(),table.visualItemRect(table.item(row,column)).center())
    def release(self,table,row,column=0):
        from PySide6.QtTest import QTest
        QTest.mouseRelease(table.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,table.visualItemRect(table.item(row,column)).center())
    def test_sweeping_down_the_boxes_checks_every_row_passed_and_saves_once(self):
        w=self.window;w.resize(1300,820);w.show();self.addCleanup(w.hide);self.app.processEvents();t=w.table
        with patch.object(w,'save_enabled',wraps=w.save_enabled) as save:
            self.press(t,0);self.move(t,3);self.release(t,3)
        save.assert_called_once();self.assertEqual(w.enabled,{t.item(r,KEY).text() for r in range(4)})
        self.assertTrue(all(t.item(r,0).checkState()==Qt.CheckState.Checked for r in range(4)))
        self.press(t,3);self.move(t,1);self.release(t,1)
        self.assertEqual(w.enabled,{t.item(0,KEY).text()}-{t.item(r,KEY).text() for r in range(1,4)})
    def test_click_without_moving_toggles_one_box_and_skips_hidden_rows_when_sweeping(self):
        w=self.window;w.resize(1300,820);w.show();self.addCleanup(w.hide);self.app.processEvents();t=w.table
        self.press(t,1);self.release(t,1);self.assertEqual(w.enabled,{t.item(1,KEY).text()})
        w.filter.setText('S');visible=w.visible_rows();self.press(t,visible[0]);self.move(t,visible[-1]);self.release(t,visible[-1])
        self.assertNotIn('Atom Key',w.enabled)
    def test_parameter_boxes_sweep_too(self):
        w=self.window;w.resize(1300,820);w.show();self.addCleanup(w.hide);w.go(app_live.PARAMETERS);p=w.parameter_page;w.parameter_enabled.clear()
        p.loaded({'parameters':[{'id':f'A::{i}','name':f'P{i}','effectMatchName':'A','matchName':str(i)} for i in range(4)]});self.app.processEvents()
        self.press(p.table,0);self.move(p.table,2);self.release(p.table,2)
        self.assertEqual(w.parameter_enabled,{'A::0','A::1','A::2'})


class TranslateFirstTests(GuiCase):
    def test_checked_effects_can_be_translated_before_connecting(self):
        w=self.window;self.assertIsNone(w.live)
        for key in ('tc Shine','Atom Key'):self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(w.translate_btn.text(),'翻译勾选项');self.assertTrue(w.translate_btn.isEnabled());self.assertFalse(w.apply_btn.isEnabled())
        with patch.object(w,'translate_rows') as rows:w.translate_btn.click()
        self.assertEqual({self.table.item(r,KEY).text() for r in rows.call_args.args[0]},{'tc Shine','Atom Key'})


class SchemeSystemTests(GuiCase):
    """A scheme is a whole set (effect and parameter names), switched from one control and compared before loading."""
    def check(self,key):self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked)
    def save(self,title):
        with patch.object(app_live,'ask_text',return_value=(title,True)):self.window.save_scheme_as()
    def param(self,name):return identity('CC Drizzle','CC Drizzle-'+name)
    def test_parameters_are_saved_and_loaded_with_the_scheme(self):
        w=self.window;w.parameter_enabled.add(self.param('0001'));w.parameter_pending[self.param('9999')]='新参数';w.save_parameters();self.save('全套')
        saved=app_live.open_scheme(w.scheme_folder/'全套.json')
        self.assertEqual(saved.parameter_enabled,{self.param('0001')});self.assertEqual(saved.parameter_names[self.param('9999')],'新参数')
        w.parameter_page.loaded({'parameters':[{'id':self.param('0001'),'name':'Drip Rate','effectMatchName':'CC Drizzle','matchName':'CC Drizzle-0001'}]})
        w.parameter_pending.clear();w.parameter_enabled.clear();w.save_parameters()
        self.assertTrue(w.scheme_dirty());self.assertEqual(w.scheme_switcher.dirty,True)
        with patch.object(app_live,'confirm',return_value=True):w.discard_changes()
        self.assertEqual(w.parameter_enabled,{self.param('0001')});self.assertEqual(w.parameter_pending[self.param('9999')],'新参数')
        self.assertEqual(w.parameter_page.table.item(0,0).checkState(),Qt.CheckState.Checked);self.assertEqual(w.parameter_page.table.item(0,2).text(),'滴落率')
        self.assertFalse(w.scheme_dirty())
    def test_legacy_scheme_without_parameters_leaves_them_alone(self):
        w=self.window;app_live.write_json(w.scheme_folder/'旧.json',app_live.dump_scheme({'Atom Key':'原子抠像'},{'Atom Key'},'旧'))
        w.refresh_schemes(None);before=dict(w.parameter_pending);w.parameter_enabled.add(self.param('0002'))
        with patch.object(app_live,'confirm',return_value=True):w.choose_scheme(w.schemes[0])
        self.assertEqual((dict(w.parameter_pending),w.parameter_enabled),(before,{self.param('0002')}));self.assertEqual(w.enabled,{'Atom Key'})
        w.parameter_enabled.clear();w.save_parameters();self.assertFalse(w.scheme_dirty())
        w.go(app_live.SCHEMES);w.param_tile.click();self.assertEqual(w.preview_stack.currentIndex(),2);self.assertIn('没有保存参数',w.preview_note.title.text())
        self.assertIn('未保存',w.param_tile.caption);self.assertTrue(w.param_tile.blank)
    def test_changes_and_duplicates_are_counted(self):
        scheme=app_live.SavedScheme(Path('x.json'),'x',{'a':'甲','b':'甲','c':'丙'},{'a'},app_live.datetime.now(),{'p':'参'},set())
        changes=app_live.scheme_changes(({'a':'甲','c':'旧'},{'a','c'}),({},set()),scheme)
        self.assertEqual(changes,{'效果中文':2,'效果勾选':1,'参数中文':1,'参数勾选':0})
        self.assertEqual(app_live.describe_changes(changes),'将替换：效果中文 2 项、效果勾选 1 项、参数中文 1 项')
        self.assertEqual(app_live.duplicate_names(scheme.names),{'甲':['a','b']})
        # Effects AE already lists under one name may share a translation; unknown originals still count.
        self.assertEqual(app_live.duplicate_names(scheme.names,{'a':'Wipe','b':'wipe'}),{})
        self.assertEqual(app_live.duplicate_names(scheme.names,{'a':'Wipe','b':'Other'}),{'甲':['a','b']})
        self.assertEqual(app_live.duplicate_names(scheme.names,{'a':'Wipe'}),{'甲':['a','b']})
        self.assertNotIn('参数中文',app_live.scheme_changes(({},set()),({},set()),scheme._replace(parameter_names=None)))
    def test_preview_compares_with_the_table_and_warns_about_duplicate_names(self):
        w=self.window;w.translated(({'tc Shine':'辉光','Atom Key':'原子抠像'},''));self.check('S_Glow');self.save('甲')
        w.pending['Atom Key']='原子';w.save_translations();w.go(app_live.SCHEMES)
        self.assertEqual(w.dirty_tag.isVisibleTo(w),True);self.assertEqual(w.load_scheme_btn.text(),'保存更改');self.assertTrue(w.discard_btn.isVisibleTo(w))
        table=w.preview_table;rows={table.item(r,1).text():r for r in range(table.rowCount())}
        self.assertEqual(table.item(rows['Atom'],3).text(),'原子');self.assertEqual(table.item(rows['Shine'],3).text(),'相同');self.assertEqual(table.item(rows['S_Glow'],0).checkState(),Qt.CheckState.Checked)
        w.diff_only.setChecked(True);self.assertEqual([r for r in range(table.rowCount()) if not table.isRowHidden(r)],[rows['Atom']])
        w.enabled.add('Gone');w.save_enabled();w.go(app_live.SCHEMES);rows={table.item(r,1).text():r for r in range(table.rowCount())}
        self.assertFalse(table.isRowHidden(rows['Gone（未安装）']));self.assertEqual(table.item(rows['Gone（未安装）'],3).text(),'仅在当前表格')
        w.diff_only.setChecked(False);self.assertTrue(table.isRowHidden(rows['Gone（未安装）']))
        self.assertTrue(w.dup_banner.isVisibleTo(w));self.assertIn('“辉光”（S_Glow、tc Shine）',w.dup_banner.text.text())
    def test_switcher_menu_lists_schemes_and_switches(self):
        w=self.window;self.check('S_Glow');self.save('甲');w.check_visible(False);self.save('乙')
        menu=w.scheme_menu();rows=[a for a in menu.actions() if a.data()]
        self.assertEqual(sorted(a.data() for a in rows),['乙','甲']);self.assertIn(w.no_scheme_action,menu.actions());self.assertIn(w.save_as_action,menu.actions())
        row=next(a for a in rows if a.data()=='甲').defaultWidget();self.assertFalse(row.data['current'])
        row.picked.emit();self.assertEqual(w.active.title,'甲');self.assertEqual(w.enabled,{'S_Glow'});self.assertEqual(w.scheme_switcher.title,'甲')
        w.no_scheme_action.trigger();self.assertIsNone(w.active);self.assertEqual(w.scheme_switcher.title,'未使用方案');self.assertEqual(w.enabled,{'S_Glow'})
    def test_duplicate_default_name_and_page_menu(self):
        w=self.window;self.assertEqual(app_live.next_title([]),'方案 1')
        with patch.object(app_live,'ask_text',side_effect=lambda parent,caption,label_text,text:(text,True)):w.save_scheme_as();w.save_scheme_as()
        self.assertEqual(sorted(s.title for s in w.schemes),['方案 1','方案 2'])
        w.scheme_list.setCurrentRow([s.title for s in w.schemes].index('方案 1'))
        self.assertEqual([a.text() for a in w.list_menu().actions() if a.text()],['载入此方案','用当前表格覆盖…','重命名…','创建副本','导出为文件…','删除…'])
        w.duplicate_scheme();w.duplicate_scheme(next(s for s in w.schemes if s.title=='方案 1'))
        self.assertEqual(sorted(s.title for s in w.schemes),['方案 1','方案 1 副本','方案 1 副本 2','方案 2']);self.assertEqual(w.active.title,'方案 2')
        w.scheme_search.setText('副本');self.assertEqual(sum(not w.scheme_list.item(r).isHidden() for r in range(w.scheme_list.count())),2)
    def test_relative_times(self):
        now=app_live.datetime(2026,10,12,15,0)
        cases=[(now,'刚刚'),(app_live.datetime(2026,10,12,14,55),'5 分钟前'),(app_live.datetime(2026,10,12,9,5),'今天 09:05'),
               (app_live.datetime(2026,10,11,22,0),'昨天 22:00'),(app_live.datetime(2026,3,1,8,0),'03-01 08:00'),(app_live.datetime(2025,3,1),'2025-03-01')]
        for moment,text in cases:self.assertEqual(app_live.ago(moment,now),text)


class SchemeEditTests(GuiCase):
    """Any saved scheme's effects can be checked, renamed, removed and added from the schemes page, and undone."""
    def save(self,title,*keys):
        for key in keys:self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked)
        with patch.object(app_live,'ask_text',return_value=(title,True)):self.window.save_scheme_as()
    def pick(self,title):
        w=self.window;w.go(app_live.SCHEMES);w.scheme_list.setCurrentRow([s.title for s in w.schemes].index(title))
    def row(self,text):
        table=self.window.preview_table;return next(r for r in range(table.rowCount()) if table.item(r,app_live.P_NAME).text()==text)
    def name(self,text):return self.window.preview_table.item(self.row(text),app_live.P_CHINESE)
    def saved(self,title):return app_live.open_scheme(self.window.scheme_folder/f'{title}.json')
    def test_renaming_in_another_scheme_writes_its_file_only(self):
        w=self.window;self.save('甲','S_Glow');self.save('乙','Atom Key');self.pick('甲')
        self.assertTrue(self.name('S_Glow').flags()&Qt.ItemFlag.ItemIsEditable)
        self.assertFalse(w.preview_table.item(self.row('S_Glow'),app_live.P_NAME).flags()&Qt.ItemFlag.ItemIsEditable)
        self.name('S_Glow').setText('  柔光  辉光 ');self.app.processEvents()
        self.assertEqual(self.saved('甲').names['S_Glow'],'柔光 辉光');self.assertEqual(w.pending['S_Glow'],'辉光');self.assertEqual(w.active.title,'乙')
        self.assertEqual(w.preview_title.text(),'甲');self.assertEqual(self.name('S_Glow').text(),'柔光 辉光')
        self.name('S_Glow').setText('');self.app.processEvents();self.assertNotIn('S_Glow',self.saved('甲').names)
    def test_editing_the_active_scheme_keeps_the_table_in_step(self):
        w=self.window;self.save('甲','S_Glow');self.pick('甲')
        self.name('S_Glow').setText('柔光');self.app.processEvents()
        self.assertEqual(w.pending['S_Glow'],'柔光');self.assertFalse(w.scheme_dirty())
        self.assertEqual(self.table.item(self.rows_for('S_Glow')[0],CANDIDATE).text(),'柔光')
        w.toggle_in_scheme(['S_Glow']);self.assertNotIn('S_Glow',self.saved('甲').enabled);self.assertNotIn('S_Glow',w.enabled);self.assertFalse(w.scheme_dirty())
    def test_boxes_header_and_drag_edit_the_scheme(self):
        w=self.window;w.translated(({'tc Shine':'闪耀','Atom Key':'原子抠像'},''));self.save('甲');self.pick('甲');table=w.preview_table
        self.assertEqual(w.preview_header.state,Qt.CheckState.Unchecked);self.assertFalse(w.delete_checked_btn.isEnabled())
        w.paint_preview_checks([0,1],True);w.finish_preview_checks([0,1],True)
        self.assertEqual(self.saved('甲').enabled,{table.item(0,0).data(Qt.ItemDataRole.UserRole+2),table.item(1,0).data(Qt.ItemDataRole.UserRole+2)})
        self.assertEqual(w.preview_header.state,Qt.CheckState.PartiallyChecked);self.assertTrue(w.delete_checked_btn.isEnabled())
        w.toggle_preview_checks();self.assertEqual(self.saved('甲').enabled,{'S_Glow','tc Shine','Atom Key'});self.assertEqual(w.preview_header.state,Qt.CheckState.Checked)
        w.preview_search.setText('Atom');w.toggle_preview_checks();self.assertEqual(self.saved('甲').enabled,{'S_Glow','tc Shine'})
    def test_trash_deletes_one_effect_and_ctrl_z_brings_it_back(self):
        w=self.window;self.save('甲','S_Glow');self.pick('甲');before=self.saved('甲')
        self.assertTrue(w.preview_table.item(self.row('S_Glow'),app_live.P_TRASH).data(Qt.ItemDataRole.UserRole+3))
        with patch.object(app_live,'confirm',side_effect=AssertionError('no confirmation for one row')):w.preview_clicked(self.row('S_Glow'),app_live.P_TRASH)
        self.assertNotIn('S_Glow',self.saved('甲').enabled);self.assertNotIn('S_Glow',self.saved('甲').names);self.assertNotIn('S_Glow',w.enabled)
        w.undo_scheme_edit();after=self.saved('甲')
        self.assertEqual((after.names,after.enabled),(before.names,before.enabled));self.assertIn('S_Glow',w.enabled);self.assertFalse(w.scheme_dirty())
        w.undo_scheme_edit();self.assertIn('没有可以撤销',w.status.text())
    def test_delete_checked_removes_only_listed_checked_effects(self):
        w=self.window;w.translated(({'tc Shine':'闪耀','Atom Key':'原子抠像'},''));self.save('甲','S_Glow','tc Shine');w.check_visible(False);self.save('乙');self.pick('甲')
        w.preview_search.setText('Shine')
        with patch.object(app_live,'confirm',return_value=False):w.delete_checked_btn.click()
        self.assertEqual(self.saved('甲').enabled,{'S_Glow','tc Shine'})
        with patch.object(app_live,'confirm',return_value=True) as asked:w.delete_checked_btn.click()
        self.assertIn('Ctrl+Z',asked.call_args.args[2]);self.assertEqual(self.saved('甲').enabled,{'S_Glow'});self.assertIn('Atom Key',self.saved('甲').names)
        self.assertEqual(w.pending['tc Shine'],'闪耀')
    def test_add_offers_known_chinese_names(self):
        w=self.window;w.translated(({'tc Shine':'闪耀'},''));self.save('甲','tc Shine');w.check_visible(False);w.pending.clear();w.save_translations();self.save('乙');self.pick('乙')
        w.live={'effects':[{'matchName':'Atom Key','displayName':'原子键控','category':'c'}]}
        with patch.object(app_live,'pick_effects',return_value=['Atom Key','tc Shine']) as picked:w.add_effects_btn.click()
        rows={r[0]:r for r in picked.call_args.args[1]}
        self.assertEqual(rows['tc Shine'][2:],('闪耀','方案“甲”'));self.assertEqual(rows['Atom Key'][2:],('原子键控','AE 当前名称'));self.assertEqual(rows['S_Glow'][2:],('辉光','方案“甲”'))
        saved=self.saved('乙');self.assertEqual(saved.names,{'Atom Key':'原子键控','tc Shine':'闪耀'});self.assertEqual(saved.enabled,{'Atom Key','tc Shine'})
        rows['S_Glow']=('S_Glow','S_Glow','','');picker=app_live.EffectPicker(w,list(rows.values()));self.addCleanup(picker.deleteLater)
        visible=lambda:[picker.table.item(r,0).data(Qt.ItemDataRole.UserRole) for r in picker.visible()]
        self.assertTrue(picker.translated_only.isChecked());self.assertEqual(sorted(visible()),['Atom Key','tc Shine'])
        self.assertFalse(picker.ok.isEnabled());picker.toggle_visible();self.assertEqual(picker.ok.text(),'加入 2 项');self.assertEqual(picker.header.state,Qt.CheckState.Checked)
        picker.translated_only.setChecked(False);self.assertEqual(len(visible()),3);self.assertEqual(picker.header.state,Qt.CheckState.PartiallyChecked)
        picker.search.setText('glow');self.assertEqual(visible(),['S_Glow']);picker.toggle_visible();self.assertEqual(len(picker.chosen()),3)
    def test_row_menu(self):
        w=self.window;self.save('甲','S_Glow');w.pending['S_Glow']='柔光';w.enabled.add('Atom Key');w.save_translations();w.save_enabled();self.pick('甲');w.diff_only.setChecked(True)
        texts=[a.text() for a in w.preview_row_menu(self.row('S_Glow')).actions() if a.text()]
        self.assertEqual(texts,['修改中文名称','取消勾选','改用当前表格的中文（1 项）','从方案中删除…'])
        next(a for a in w.preview_row_menu(self.row('S_Glow')).actions() if a.text().startswith('改用')).trigger();self.assertEqual(self.saved('甲').names['S_Glow'],'柔光')
        menu=w.preview_row_menu(self.row('Atom'));self.assertEqual([a.text() for a in menu.actions()],['加入此方案'])
        extra=w.preview_table.item(self.row('Atom'),app_live.P_CHECK)
        self.assertFalse(extra.flags()&Qt.ItemFlag.ItemIsUserCheckable);self.assertFalse(w.preview_table.item(self.row('Atom'),app_live.P_TRASH).data(Qt.ItemDataRole.UserRole+3))
        menu.actions()[0].trigger();self.assertIn('Atom Key',self.saved('甲').enabled)
    def test_parameter_tab_edits_like_the_effects_tab(self):
        w=self.window;self.save('甲','S_Glow');self.pick('甲');w.show();self.addCleanup(w.hide);w.param_tile.click();table=w.param_preview_table
        self.assertTrue(w.add_effects_btn.isVisible());self.assertEqual(w.add_effects_btn.text(),'添加参数…');self.assertIn('垃圾桶删除单个参数',w.edit_hint.text())
        key=identity('CC Drizzle','CC Drizzle-0001');row=next(r for r in range(table.rowCount()) if w.preview_key(r,table)==key)
        self.assertEqual(table.item(row,app_live.Q_NAME).text(),'CC Drizzle-0001');self.assertTrue(table.item(row,app_live.Q_CHINESE).flags()&Qt.ItemFlag.ItemIsEditable)
        table.item(row,app_live.Q_CHINESE).setText('雨滴速度');self.app.processEvents()
        self.assertEqual(self.saved('甲').parameter_names[key],'雨滴速度');self.assertEqual(w.parameter_pending[key],'雨滴速度');self.assertFalse(w.scheme_dirty())
        w.paint_preview_checks([row],True,table);w.finish_preview_checks([row],True,table);self.assertIn(key,self.saved('甲').parameter_enabled);self.assertIn(key,w.parameter_enabled)
        self.assertEqual(table.check_header.state,Qt.CheckState.PartiallyChecked);self.assertTrue(w.delete_checked_btn.isEnabled())
        w.preview_clicked(row,app_live.Q_TRASH,table);self.assertNotIn(key,self.saved('甲').parameter_names);self.assertNotIn(key,w.parameter_pending)
        w.undo_scheme_edit();self.assertEqual(self.saved('甲').parameter_names[key],'雨滴速度');self.assertIn(key,w.parameter_enabled)
        with patch.object(app_live,'confirm',return_value=True):w.delete_checked()
        self.assertNotIn(key,self.saved('甲').parameter_enabled)
        menu=w.preview_row_menu(0,table);self.assertIn('从方案中删除…',[a.text() for a in menu.actions()])
    def test_parameters_can_be_added_from_the_record_and_other_schemes(self):
        w=self.window;w.parameter_pending.clear();w.save_parameters();self.save('空')
        new=identity('S_Glow','S_Glow-Width');w.parameter_pending[new]='宽度';w.parameter_labels[new]='Width';w.save_parameters()
        self.pick('空');w.param_tile.click()
        with patch.object(app_live,'pick_parameters',return_value=[new]) as picked:w.add_effects_btn.click()
        rows={r[0]:r for r in picked.call_args.args[1]};self.assertEqual(rows[new][1:],('S_Glow · Width','宽度','参数记录'))
        saved=self.saved('空');self.assertEqual((saved.parameter_names,saved.parameter_enabled),({new:'宽度'},{new}))

class SchemeTableColumnTests(GuiCase):
    """The 当前表格 column compares Chinese names only, never checks."""
    def test_check_only_difference_reads_as_same(self):
        w=self.window;self.table.item(self.rows_for('S_Glow')[0],CHECK).setCheckState(Qt.CheckState.Checked)
        with patch.object(app_live,'ask_text',return_value=('甲',True)):w.save_scheme_as()
        w.check_keys({'S_Glow'},False);w.go(app_live.SCHEMES);table=w.preview_table
        row=next(r for r in range(table.rowCount()) if table.item(r,app_live.P_NAME).text()=='S_Glow')
        self.assertEqual(table.item(row,app_live.P_TABLE).text(),'相同');w.diff_only.setChecked(True);self.assertTrue(table.isRowHidden(row))
        self.assertIn('效果勾选 1 项',w.preview_meta.text())


class CrossVersionNameTests(GuiCase):
    """Effects shared between AE versions bring the Chinese names saved under the other versions."""
    versions=VersionTests.versions
    installs=VersionTests.installs
    def setUp(self):
        super().setUp();w=self.window;base=Path(self.temp.name)/'AE';self.v26,self.v21=w.spaces
        from test_gui import plugin
        w.show_scan(([plugin('S_Glow','S_Glow','Sapphire',str(base/'Adobe After Effects 2026'/'Support Files'/'Plug-ins'/'S_Glow.aex')),
                      plugin('Old','Old Effect','Legacy',str(base/'Adobe After Effects 2021'/'Support Files'/'Plug-ins'/'Old.aex')),
                      plugin('Shine','tc Shine','RG',str(Path(self.temp.name)/'MediaCore'/'Shine.aex'))],[]))
    def test_other_versions_tables_and_schemes_are_offered(self):
        w=self.window;w.choose_version(1)
        self.table.item(next(r for r in range(self.table.rowCount()) if self.table.item(r,KEY).text()=='tc Shine'),CANDIDATE).setText('旧闪耀')
        app_live.write_json(w.scheme_folder/'旧.json',app_live.dump_scheme({'Old Effect':'旧效果','S_Glow':'旧辉光'},set(),'旧'))
        w.choose_version(0);w.pending.pop('S_Glow',None);w.save_translations()
        with patch.object(app_live,'ask_text',return_value=('新',True)):w.save_scheme_as()
        before=sorted(p.name for p in self.v21.folder.iterdir())
        known=w.known_names(w.active)
        self.assertEqual(known['tc Shine'],('旧闪耀','AE 2021 中文候选'));self.assertEqual(known['S_Glow'],('旧辉光','AE 2021 方案“旧”'))
        self.assertEqual(sorted(p.name for p in self.v21.folder.iterdir()),before)
        w.pending['tc Shine']='闪耀';self.assertEqual(w.known_names(w.active)['tc Shine'],('闪耀','当前表格'))
    def test_picker_explains_when_nothing_is_translated(self):
        picker=app_live.EffectPicker(self.window,[('A','A','',''),('B','B','','')]);self.addCleanup(picker.deleteLater)
        self.assertFalse(picker.translated_only.isChecked());self.assertTrue(picker.none_note.isHidden())
        picker.translated_only.setChecked(True);self.assertFalse(picker.none_note.isHidden());self.assertEqual(picker.visible(),[])

class PutInSchemeTests(GuiCase):
    """Checked effects (or one row) go into any saved scheme, or a new one, with their Chinese candidates."""
    def check(self,key,state=True):self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
    def saved(self,title):return app_live.open_scheme(self.window.scheme_folder/f'{title}.json')
    def test_checked_effects_go_into_another_scheme(self):
        w=self.window;w.translated(({'tc Shine':'闪耀'},''))
        with patch.object(app_live,'ask_text',return_value=('甲',True)):w.save_scheme_as()
        app_live.write_json(w.scheme_folder/'乙.json',app_live.dump_scheme({'tc Shine':'旧名'},set(),'乙'));w.refresh_schemes('甲')
        self.check('tc Shine');self.check('Atom Key');w.filter.setText('Atom')
        menu=w.fill_add_menu(QMenu(),sorted(w.enabled));texts=[a.text() for a in menu.actions() if a.text()]
        self.assertIn('甲（当前方案）',texts);self.assertIn('乙',texts);self.assertEqual(texts[-1],'新方案…')
        next(a for a in menu.actions() if a.text()=='乙').trigger()
        saved=self.saved('乙');self.assertEqual(saved.enabled,{'tc Shine','Atom Key'});self.assertEqual(saved.names,{'tc Shine':'闪耀'})
        self.assertEqual(w.active.title,'甲');self.assertEqual(w.pending['tc Shine'],'闪耀')
        w.undo_scheme_edit();self.assertEqual((self.saved('乙').names,self.saved('乙').enabled),({'tc Shine':'旧名'},set()))
    def test_new_scheme_and_row_menu(self):
        w=self.window;self.check('S_Glow')
        menu=w.fill_add_menu(QMenu(),sorted(w.enabled))
        with patch.object(app_live,'ask_text',return_value=('只有辉光',True)):next(a for a in menu.actions() if a.text()=='新方案…').trigger()
        saved=self.saved('只有辉光');self.assertEqual((saved.names,saved.enabled,saved.parameters),({'S_Glow':'辉光'},{'S_Glow'},None));self.assertIsNone(w.active)
        row=w.row_menu(self.rows_for('Atom Key')[0]);add=next(a.menu() for a in row.actions() if a.text()=='加入方案')
        next(a for a in add.actions() if a.text()=='只有辉光').trigger();self.assertEqual(self.saved('只有辉光').enabled,{'S_Glow','Atom Key'})
        w.check_keys(set(w.enabled),False);empty=w.fill_add_menu(QMenu(),[]);self.assertEqual([a.text() for a in empty.actions()],['先勾选效果'])

    def test_switcher_menu_carries_the_add_submenu(self):
        w=self.window;self.check('S_Glow');self.check('Atom Key')
        with patch.object(app_live,'ask_text',return_value=('甲',True)):w.save_scheme_as()
        menu=w.scheme_menu();sub=next(a.menu() for a in menu.actions() if a.menu() is not None)
        self.assertEqual(sub.title(),'把勾选项加入方案（2 项）');self.assertIn('甲（当前方案）',[a.text() for a in sub.actions()])
        self.assertEqual([a.menu().title() for a in w.scheme_menu().actions() if a.menu() is not None],['把勾选项加入方案（2 项）'])
        self.assertEqual([a.menu() for a in w.check_tool.menu().actions() if a.menu()],[w.folder_checked_menu])   # folders, not schemes

class SchemeScopeTests(GuiCase):
    """With a scheme in use the effects table lists only its effects; 显示全部效果 lists all, greying the rest."""
    def check(self,key,state=True):self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
    def shown(self):return {self.table.item(r,KEY).text() for r in self.window.visible_rows()}
    def grey(self,key):return self.table.item(self.rows_for(key)[0],app_live.ORIGINAL).data(Qt.ItemDataRole.UserRole)
    def test_scheme_limits_rows_until_show_all(self):
        w=self.window;w.pending.clear();w.save_translations();self.assertEqual(self.shown(),{'S_Glow','tc Shine','Atom Key'})
        self.check('Atom Key')
        with patch.object(app_live,'ask_text',return_value=('甲',True)):w.save_scheme_as()
        self.assertEqual(self.shown(),{'Atom Key'});self.assertEqual(w.chips['all'].caption,'方案中');self.assertEqual(w.chips['all'].value,1)
        menu=w.scheme_menu();self.assertIn(w.show_all_action,menu.actions())
        w.show_all_action.trigger();self.assertTrue(w.show_all);self.assertEqual(self.shown(),{'S_Glow','tc Shine','Atom Key'})
        self.assertTrue(self.grey('tc Shine'));self.assertFalse(self.grey('Atom Key'));self.assertEqual(w.chips['all'].caption,'全部')
        self.assertTrue(self.table.item(self.rows_for('tc Shine')[0],CANDIDATE).flags()&Qt.ItemFlag.ItemIsEditable)
        self.check('tc Shine');self.assertFalse(self.grey('tc Shine'))
        w.set_show_all(False);self.assertEqual(self.shown(),{'Atom Key','tc Shine'})
        w.choose_scheme(None);self.assertEqual(self.shown(),{'S_Glow','tc Shine','Atom Key'});self.assertFalse(self.grey('S_Glow'))
    def test_empty_scheme_offers_show_all(self):
        w=self.window;w.pending.clear();w.save_translations()
        with patch.object(app_live,'ask_text',return_value=('空',True)):w.save_scheme_as()
        self.assertEqual(w.table_stack.currentIndex(),0);self.assertIn('还没有效果',w.table_empty.title.text());self.assertFalse(w.empty_all_btn.isHidden())
        w.empty_all_btn.click();self.assertEqual(w.table_stack.currentIndex(),1);self.assertEqual(len(self.shown()),3)

class SchemeToAETests(GuiCase):
    """The schemes page writes a saved scheme's effect names to AE, or restores them, through the existing paths."""
    def test_apply_and_restore_a_saved_scheme(self):
        w=self.window;w.translated(({'tc Shine':'闪耀'},''))
        with patch.object(app_live,'ask_text',return_value=('甲',True)):w.save_scheme_as()
        w.go(app_live.SCHEMES);self.assertFalse(w.scheme_apply_btn.isEnabled());self.assertIn('先连接 AE',w.scheme_apply_btn.toolTip())
        w.show_live(LIVE);w.update_buttons();self.assertTrue(w.scheme_apply_btn.isEnabled());self.assertTrue(w.scheme_restore_btn.isEnabled())
        w.pending['tc Shine']='别的名字';w.save_translations();sent=[]
        with patch.object(w,'work',lambda work,done:sent.append(work)),patch.object(w,'apply_names',lambda names:names),patch.object(w,'restore_effect_names',lambda keys:keys),patch.object(app_live,'read_live',lambda **kw:None):
            with patch.object(app_live,'confirm',return_value=False):w.scheme_apply_btn.click()
            self.assertEqual(sent,[])
            with patch.object(app_live,'confirm',return_value=True):w.scheme_apply_btn.click();w.scheme_restore_btn.click()
            self.assertEqual(sent[0](lambda text:None),{'S_Glow':'辉光','tc Shine':'闪耀'})
            self.assertEqual(sent[1](lambda text:None)[0],['S_Glow','tc Shine'])
        self.assertEqual(w.active.title,'甲');self.assertEqual(w.pending['tc Shine'],'别的名字')


class ParameterRecordTests(GuiCase):
    """The parameters page keeps a record of every translated parameter, usable without AE."""
    def setUp(self):
        super().setUp();w=self.window;self.panel=w.parameter_page
        self.keys=[identity('CC Drizzle','CC Drizzle-000'+str(i)) for i in (1,2,3)]
    def show(self):self.panel.view_buttons[1].setChecked(True);self.panel.show_view()
    def row(self,key):return self.panel.history_keys.index(key)
    def test_record_lists_translations_without_reading_ae(self):
        p=self.panel;self.show();self.assertEqual(p.stack.currentIndex(),2);self.assertTrue(p.history_add.isVisible() or not p.isVisible())
        self.assertEqual(len(p.history_keys),8);self.assertEqual(p.view_buttons[1].text(),'汉化记录 8')
        self.assertEqual(p.history.item(self.row(self.keys[0]),H_EFFECT).text(),'CC Drizzle');self.assertEqual(p.history.item(self.row(self.keys[0]),H_CHINESE).text(),'滴落率')
        p.search.setText('涟漪');self.assertEqual(len(p.history_visible()),1)
    def test_labels_read_from_ae_are_remembered(self):
        p=self.panel;p.loaded({'parameters':[{'id':self.keys[0],'name':'Drip Rate','effectMatchName':'CC Drizzle','matchName':'CC Drizzle-0001'}]})
        self.assertEqual(self.window.parameter_labels[self.keys[0]],'Drip Rate')
        p.loaded({'parameters':[]});self.show();self.assertEqual(p.history.item(self.row(self.keys[0]),H_NAME).text(),'Drip Rate')
    def test_delete_undo_and_checks(self):
        w=self.window;p=self.panel;self.show()
        p.history_clicked(self.row(self.keys[0]),H_TRASH);self.assertNotIn(self.keys[0],w.parameter_pending);self.assertNotIn(self.keys[0],p.history_keys)
        p.undo_record();self.assertEqual(w.parameter_pending[self.keys[0]],'滴落率')
        p.paint_history([self.row(k) for k in self.keys[1:]],True);p.finish_history([self.row(k) for k in self.keys[1:]],True)
        self.assertEqual(set(p.history_checked()),set(self.keys[1:]));self.assertTrue(p.history_delete.isEnabled())
        with patch.object(parameter_dialog,'confirm',return_value=True):p.history_delete.click()
        self.assertFalse(set(self.keys[1:])&set(w.parameter_pending));self.assertEqual(len(p.history_keys),6)
    def test_editing_a_record_and_adding_it_to_a_scheme(self):
        w=self.window;p=self.panel;self.show()
        with patch.object(app_live,'ask_text',return_value=('甲',True)):w.save_scheme_as()
        p.history.item(self.row(self.keys[0]),H_CHINESE).setText('雨滴');self.app.processEvents();self.assertEqual(w.parameter_pending[self.keys[0]],'雨滴')
        p.history.item(self.row(self.keys[1]),H_CHINESE).setText('');self.app.processEvents();self.assertNotIn(self.keys[1],w.parameter_pending)
        app_live.write_json(w.scheme_folder/'乙.json',app_live.dump_scheme({},set(),'乙'));w.refresh_schemes('甲')
        menu=p.history_menu(self.row(self.keys[2]));sub=next(a.menu() for a in menu.actions() if a.text()=='把这条加入方案')
        next(a for a in sub.actions() if a.text()=='乙').trigger()
        saved=app_live.open_scheme(w.scheme_folder/'乙.json');self.assertEqual((saved.parameter_names,saved.parameter_enabled),({self.keys[2]:'涟漪'},{self.keys[2]}))


class NewSchemeFromChecksTests(GuiCase):
    """The switcher's 新建 / 另存为 makes a blank scheme, or one with only the checked effects, without switching."""
    def check(self,key):self.table.item(self.rows_for(key)[0],CHECK).setCheckState(Qt.CheckState.Checked)
    def saved(self,title):return app_live.open_scheme(self.window.scheme_folder/f'{title}.json')
    def test_blank_then_checked(self):
        w=self.window;w.translated(({'tc Shine':'闪耀','Atom Key':'原子抠像'},''));w.update_scheme_bar()
        self.assertEqual(w.save_as_action.text(),'新建方案…');self.assertIn(w.save_as_action,w.scheme_menu().actions())
        with patch.object(app_live,'ask_text',return_value=('空白',True)):w.save_as_action.trigger()
        blank=self.saved('空白');self.assertEqual((blank.names,blank.enabled,blank.parameters),({},set(),({},set())));self.assertIsNone(w.active)
        self.check('tc Shine');self.check('S_Glow');self.assertEqual(w.save_as_action.text(),'另存为新方案（2 个勾选效果）…')
        with patch.object(app_live,'ask_text',return_value=('只有勾选',True)) as asked:w.save_as_action.trigger()
        self.assertIn('只包含勾选的 2 个效果',asked.call_args.args[2])
        saved=self.saved('只有勾选');self.assertEqual((saved.names,saved.enabled,saved.parameters),({'tc Shine':'闪耀','S_Glow':'辉光'},{'tc Shine','S_Glow'},None))
        self.assertIsNone(w.active);self.assertEqual(w.pending['Atom Key'],'原子抠像')


class ModelListTests(GuiCase):
    """The model box can ask the service which models it offers."""
    class Reply:
        def __init__(self,payload):self.payload=payload
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def read(self):return json.dumps(self.payload).encode('utf8')
    def opener(self,payload,seen):
        def open_(request,timeout):seen.append(request);return self.Reply(payload)
        return open_
    def test_openai_style_lists_are_cleaned(self):
        import llm_client;seen=[]
        models=llm_client.list_models('openai','https://api.openai.com/v1','sk-test-only',opener=self.opener({'data':[{'id':'gpt-4.1'},{'id':'text-embedding-3-small'},{'id':'gpt-4.1-mini'},{'id':'gpt-4.1'}]},seen))
        self.assertEqual(models,['gpt-4.1','gpt-4.1-mini']);self.assertEqual(seen[0].full_url,'https://api.openai.com/v1/models');self.assertEqual(seen[0].get_header('Authorization'),'Bearer sk-test-only')
        seen.clear();models=llm_client.list_models('anthropic','https://api.anthropic.com/v1','ant-test-only',opener=self.opener({'data':[{'id':'claude-sonnet-5-5'}],'has_more':False},seen))
        self.assertEqual(models,['claude-sonnet-5-5']);self.assertEqual(seen[0].get_header('X-api-key'),'ant-test-only');self.assertIsNone(seen[0].get_header('Authorization'))
        self.assertEqual(llm_client.list_models('gemini','https://generativelanguage.googleapis.com/v1beta/openai','g-test',opener=self.opener({'data':[{'id':'models/gemini-2.5-flash'}]},[])),['gemini-2.5-flash'])
        self.assertEqual(llm_client.list_models('ollama','http://localhost:11434/v1','',opener=self.opener({'models':[{'name':'qwen2.5:7b'}]},[])),['qwen2.5:7b'])
        with self.assertRaises(ValueError):llm_client.list_models('openai','https://api.openai.com/v1','')
        with self.assertRaises(ValueError):llm_client.list_models('custom','http://example.com/v1','k')
        def refused(request,timeout):
            from urllib.error import HTTPError
            raise HTTPError(request.full_url,401,'no',{},None)
        with self.assertRaisesRegex(RuntimeError,'HTTP 401'):llm_client.list_models('openai','https://api.openai.com/v1','bad',opener=refused)
    def test_dialog_fetches_once_when_the_list_opens_and_keeps_the_typed_model(self):
        dialog=app_live.ApiDialog(self.window.settings,self.window);self.addCleanup(dialog.deleteLater);calls=[]
        def fake(service,base,key):calls.append((service,base,key));return ['deepseek-flash','deepseek-reasoner']
        with patch.object(app_live,'list_models',fake):
            dialog.model.opening.emit();self.assertEqual(calls,[])  # no key yet: nothing is sent
            dialog.key.setText('ds-test-only');dialog.model.setCurrentText('my-model');dialog.model.opening.emit()
            self.assertTrue(dialog.fetch_job.wait(3000));self.app.processEvents()
            dialog.model.opening.emit();self.app.processEvents()
        self.assertEqual(calls,[('deepseek','https://api.deepseek.com','ds-test-only')])
        self.assertEqual([dialog.model.itemText(i) for i in range(dialog.model.count())],['deepseek-flash','deepseek-reasoner'])
        self.assertEqual(dialog.model.currentText(),'my-model');self.assertIn('不在列表中',dialog.error.text())
        dialog.services.setCurrentIndex(dialog.services.findData('openai'));dialog.services.setCurrentIndex(dialog.services.findData('deepseek'))
        self.assertEqual(dialog.model.count(),2)

class SearchClearTests(GuiCase):
    """The search field's own clear button: shown only with text, vertically centred, clears and keeps focus."""
    def test_clear_button(self):
        w=self.window;w.resize(1200,720);w.show();self.addCleanup(w.hide);self.app.processEvents()
        field=w.filter.parentWidget();button=field.clear_btn
        self.assertFalse(w.filter.isClearButtonEnabled());self.assertFalse(button.isVisible())
        w.filter.setText('atom');self.app.processEvents();self.assertTrue(button.isVisible())
        self.assertAlmostEqual(button.geometry().center().y(),field.rect().center().y(),delta=1)
        button.click();self.assertEqual(w.filter.text(),'');self.assertFalse(button.isVisible());self.assertEqual(len(w.visible_rows()),4)

class SettingsCardsTests(GuiCase):
    """Each setting has its own card; the acknowledgement sits beside the log in the bottom row."""
    def test_cards(self):
        w=self.window;titles=lambda frame:frame.findChild(app_live.QLabel).text();card=lambda title:next(f for f in w.settings_frames if titles(f)==title)
        self.assertEqual([titles(f) for f in w.settings_frames],['翻译模型','退出与恢复','界面','适配范围','软件更新','快捷键','致谢'])
        self.assertTrue(card('界面').isAncestorOf(w.motion_box));self.assertTrue(card('界面').isAncestorOf(w.dark_box));self.assertFalse(card('界面').isAncestorOf(w.shortcut_box))
        for widget in (w.shortcut_box,w.shortcut_info,w.shortcut_edit_btn,w.shortcut_reset_btn):self.assertTrue(card('快捷键').isAncestorOf(widget))
        self.assertTrue(card('致谢').isAncestorOf(w.thanks));self.assertEqual(w.thanks.text(),app_live.THANKS);self.assertIn('帧绘剪工作室',w.thanks.text())
        page=card('致谢').parentWidget().layout();grid=next(page.itemAt(i).layout() for i in range(page.count()) if isinstance(page.itemAt(i).layout(),app_live.QGridLayout))
        place=lambda widget:grid.getItemPosition(grid.indexOf(widget))
        self.assertEqual(place(w.log.parentWidget()),(2,0,1,2));self.assertEqual(place(card('致谢')),(2,2,1,1));self.assertEqual(place(card('快捷键')),(1,2,1,1))
