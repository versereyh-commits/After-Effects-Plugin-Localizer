import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
import agent_tools,app_live,ui_kit
from test_gui import GuiCase
def J(**v):
    # A reply that acts says first what it understood, or the loop sends it back.
    if 'calls' in v:v.setdefault('understood','测试')
    return json.dumps(v,ensure_ascii=False)

class AssistantGui(GuiCase):
    def setUp(self):
        super().setUp();self.panel=self.window.assistant;self.host=self.panel.host
    def pump(self,until,seconds=5):
        end=time.time()+seconds
        while time.time()<end:
            QApplication.processEvents()
            if until():return
            time.sleep(.005)
        self.fail('超时')
    def chat(self,*replies,text='帮我'):
        queue=list(replies);seen=[]
        def turn(system,messages,config,**options):
            seen.append(messages[-1]['content']);return queue.pop(0),False
        with patch('agent_core.chat_turn',turn),patch.object(self.window.settings,'ready',lambda:True):
            self.panel.send(text);self.pump(lambda:not self.panel.running and not (self.panel.thread and self.panel.thread.is_alive()))
            self.pump(lambda:not self.panel.in_turn)
        return seen
    def tool(self,name):return next(t for t in self.host.all_tools() if t.name==name)
    def test_default_level_is_data_and_full_is_never_remembered(self):
        self.assertEqual(self.panel.level,'data')
        with patch('assistant_panel.confirm',lambda *a,**k:True):self.panel.choose_level(3)
        self.assertEqual(self.panel.level,'full');self.assertNotEqual(self.window.ui_preferences().get('assistantLevel'),'full')
        self.assertEqual(self.panel.saved_level(),'data')
    def test_refusing_the_full_warning_keeps_the_level(self):
        with patch('assistant_panel.confirm',lambda *a,**k:False):self.panel.choose_level(3)
        self.assertEqual(self.panel.level,'data');self.assertIn('改数据',self.panel.level_chip.text())
    def test_lists_effects_from_the_table(self):
        out=self.tool('list_effects').run({'query':'S_Glow'});self.assertEqual(out['total'],1);self.assertEqual(out['items'][0]['matchName'],'S_Glow')
        self.assertEqual(self.tool('list_effects').run({'limit':1})['total'],3)
    def test_status_never_carries_the_key(self):
        self.window.settings.key='sk-secret-value';out=json.dumps(self.tool('app_status').run({}),ensure_ascii=False);self.assertNotIn('sk-secret-value',out)
    def test_saves_names_and_checks_and_undo_puts_them_back(self):
        before=dict(self.window.pending)
        self.chat(J(calls=[{'tool':'set_effect_names','args':{'names':{'Atom Key':'原子键控'}}},{'tool':'check_effects','args':{'keys':['Atom Key'],'checked':True}}]),J(say='好了'))
        self.assertEqual(self.window.pending['Atom Key'],'原子键控');self.assertIn('Atom Key',self.window.enabled);self.assertTrue(self.panel.undo_btn.isEnabled())
        row=next(r for r in self.window.effect_rows() if self.window.table.item(r,app_live.KEY).text()=='Atom Key');self.assertEqual(self.window.table.item(row,app_live.CANDIDATE).text(),'原子键控')
        self.panel.undo();self.assertEqual(dict(self.window.pending),before);self.assertNotIn('Atom Key',self.window.enabled);self.assertEqual(self.window.table.item(row,app_live.CANDIDATE).text(),before.get('Atom Key',''))
    def test_overlong_name_is_reported_not_saved(self):
        out=self.tool('set_effect_names').run({'names':{'Atom Key':'超'*40,'Nope':'x'}});self.assertEqual(out['saved'],0);self.assertIn('Atom Key',out['problems']);self.assertIn('Nope',out['problems'])
    def test_translate_passes_the_users_wishes_and_only_saves_candidates(self):
        calls=[]
        def fake(items,*a,**k):
            calls.append((items,k.get('system_prompt')));return {i['id']:'译'+i['id'] for i in items}
        apply=patch.object(self.window,'apply_names',side_effect=AssertionError('不该写入'))
        with patch('agent_tools.translate_names',fake),apply:
            self.chat(J(calls=[{'tool':'translate_effects','args':{'query':'Atom','instructions':'不要音译'}}]),J(say='译好了'))
        self.pump(lambda:not self.window.busy());self.assertEqual(self.window.pending['Atom Key'],'译Atom Key');self.assertIn('不要音译',calls[0][1]);self.assertEqual(len(calls[0][0]),1)
    def test_writing_to_ae_shows_a_card_and_cancel_runs_nothing(self):
        self.panel.level='ae';self.window.space.live=True;self.window.live={'effects':[]};self.window.pending['Atom Key']='原子'
        with patch.object(self.window,'apply_names',side_effect=AssertionError('不该写入')),patch.object(self.window.settings,'ready',lambda:True):
            queue=[J(calls=[{'tool':'apply_effects','args':{'keys':['Atom Key']}}]),J(say='已取消')];seen=[]
            def turn(system,messages,config,**options):
                seen.append(messages[-1]['content']);return queue.pop(0),False
            with patch('agent_core.chat_turn',turn):
                self.panel.send('写入');self.pump(lambda:self.panel.card is not None);self.assertEqual(self.panel.ball.emotion,'waiting')
                self.panel.pending_answer(False,False);self.panel.pending_answer=None;self.pump(lambda:not self.panel.in_turn)
        self.assertIn('没有确认',seen[-1])
    def test_a_refused_level_changes_nothing(self):
        self.panel.level='read';seen=self.chat(J(calls=[{'tool':'set_effect_names','args':{'names':{'Atom Key':'原子'}}}]),J(say='权限不够'));self.assertNotEqual(self.window.pending.get('Atom Key'),'原子');self.assertIn('权限不够',seen[-1])
    def test_face_follows_what_the_program_does(self):
        self.panel.job_started();self.assertEqual(self.panel.ball.emotion,'busy');self.panel.job_finished();self.assertIn(self.panel.ball.emotion,('idle','dim'))
        self.panel.job_failed();self.assertEqual(self.panel.ball.emotion,'error');self.panel.rest();self.window.assistant_note('done');self.assertEqual(self.panel.ball.emotion,'done')
    def test_face_shows_tool_work(self):
        self.panel.agent_state('tool','net|翻译效果');self.assertEqual(self.panel.ball.emotion,'translating');self.panel.agent_state('tool','read');self.assertEqual(self.panel.ball.emotion,'search')
        self.panel.agent_state('failed','limit');self.assertEqual(self.panel.ball.emotion,'tired');self.panel.agent_state('refused','x');self.assertEqual(self.panel.ball.emotion,'refuse')
    def test_finishing_a_change_celebrates_and_a_failure_does_not(self):
        self.chat(J(calls=[{'tool':'check_effects','args':{'keys':['Atom Key'],'checked':True}}]),J(say='好了'));self.assertEqual(self.panel.ball.emotion,'done')
        self.chat(J(calls=[{'tool':'set_effect_names'}]),J(say='失败'));self.assertEqual(self.panel.ball.emotion,'helpless')
    def test_a_turn_stays_put_on_failure_of_the_service(self):
        from llm_client import ChatError
        def turn(*a,**k):raise ChatError('network','连不上')
        with patch('agent_core.chat_turn',turn),patch.object(self.window.settings,'ready',lambda:True):
            self.panel.send('hi');self.pump(lambda:not self.panel.in_turn)
        self.assertEqual(self.panel.ball.emotion,'error')
    def test_ball_opens_the_drawer_and_fits_the_smallest_window(self):
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents();self.panel.toggle();QApplication.processEvents()
        area=self.window.centralWidget().rect();self.assertTrue(self.panel.drawer.isVisible());self.assertTrue(area.contains(self.panel.drawer.geometry()));self.assertTrue(self.panel.ball.timer.isActive())
        self.panel.toggle();self.assertFalse(self.panel.drawer.isVisible())
    def test_ball_stays_put_and_sits_on_the_send_spot(self):
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents();closed=self.panel.ball.geometry()
        self.panel.ball_clicked();QApplication.processEvents();self.assertTrue(self.panel.drawer.isVisible());self.assertEqual(self.panel.ball.geometry(),closed)
        card=self.panel.composer;corner=card.mapTo(self.window.centralWidget(),card.rect().bottomRight());self.assertLessEqual(abs(corner.x()-closed.right()),14);self.assertLessEqual(abs(corner.y()-closed.bottom()),14);self.assertTrue(self.panel.drawer.geometry().contains(closed))
        spot=self.panel.input.mapTo(self.window.centralWidget(),self.panel.input.rect().topRight());self.assertLessEqual(spot.x(),closed.left()+14)
    def test_no_title_and_no_send_button(self):
        from PySide6.QtWidgets import QLabel,QPushButton
        self.assertNotIn('AI 助手',[x.text() for x in self.panel.drawer.findChildren(QLabel)]);self.assertNotIn('发送',[x.text() for x in self.panel.drawer.findChildren(QPushButton)])
    def test_clicking_the_ball_sends_and_stops(self):
        self.panel.ball_clicked();self.panel.ball_clicked();self.assertEqual(self.panel.agent.messages,[]);self.assertIn('先在输入框',self.panel.hint.text());self.assertTrue(self.panel.open)
        queue=[J(say='好')]
        with patch('agent_core.chat_turn',lambda *a,**k:(queue.pop(0),False)),patch.object(self.window.settings,'ready',lambda:True):
            self.panel.input.setPlainText('你好');self.assertIn('点小球发送',self.panel.hint.text());self.panel.ball_clicked();self.assertEqual(self.panel.hint.text(),'点小球停止');self.pump(lambda:not self.panel.in_turn)
        self.assertEqual(self.panel.input.toPlainText(),'');self.assertEqual(self.panel.agent.messages[0]['content'],'你好');self.assertIn('写好后',self.panel.hint.text())
    def test_clicking_while_working_stops(self):
        self.panel.open=True;self.panel.in_turn=True;self.panel.ball_clicked();self.assertTrue(self.panel.agent.stop);self.panel.in_turn=False;self.panel.agent.stop=False
    def test_first_word_makes_the_ball_hop_once(self):
        self.panel.ball.engine.reduced=False;self.panel.input.setPlainText('a');self.assertEqual(len(self.panel.ball.engine.jumps),1);self.panel.input.setPlainText('ab');self.assertEqual(len(self.panel.ball.engine.jumps),1)
    def test_new_chat_clears(self):
        self.chat(J(say='你好'));self.assertTrue(self.panel.agent.messages);self.panel.new_chat();self.assertEqual(self.panel.agent.messages,[]);self.assertTrue(self.panel.greeting.isVisibleTo(self.panel.drawer));self.assertEqual(self.panel.items,[])
    def test_dark_theme_inverts_the_ball(self):
        ui_kit.set_theme(True);self.addCleanup(ui_kit.set_theme,False);image=self.panel.ball.grab().toImage();from PySide6.QtGui import QColor
        self.assertEqual(QColor(image.pixel(image.width()//2,image.height()//4)).name(),'#ffffff')

    def wait_turn(self):self.pump(lambda:not self.panel.in_turn)
    def test_history_saves_lists_restores_and_deletes(self):
        self.chat(J(say='第一段回复'),text='第一段对话');first=self.panel.chat_id;self.assertTrue(first);self.assertEqual(self.panel.store.list()[0]['title'],'第一段对话')
        self.panel.new_chat();self.assertEqual(self.panel.agent.messages,[]);self.chat(J(say='第二段回复'),text='第二段');self.panel.new_chat();self.assertEqual(len(self.panel.store.list()),2)
        self.panel.toggle_history();self.assertEqual(self.panel.pages.currentIndex(),1);self.panel.history_search.setText('第一');self.assertEqual(sum(1 for i in range(self.panel.history_box.count()) if self.panel.history_box.itemAt(i).widget()),1)
        self.panel.open_chat(first);self.assertEqual(self.panel.pages.currentIndex(),0);self.assertEqual(self.panel.agent.messages[0]['content'],'第一段对话');self.assertEqual([i['kind'] for i in self.panel.items],['user','reply']);self.assertEqual(self.panel.ball.emotion,'recall')
        self.chat(J(say='接着说'),text='继续');self.assertEqual(self.panel.store.load(first)['messages'][-1]['content'],J(say='接着说'))
        self.panel.store.delete(first);self.assertEqual(len(self.panel.store.list()),1)
    def test_saved_history_never_holds_the_key(self):
        self.window.settings.key='sk-secret-value';self.chat(J(say='好'));path=self.panel.store.path(self.panel.chat_id);self.assertNotIn('sk-secret-value',path.read_text(encoding='utf8'))
    def test_damaged_history_does_not_break_the_list(self):
        self.panel.store.folder.mkdir(parents=True);(self.panel.store.folder/'bad.json').write_text('{',encoding='utf8');self.assertEqual(self.panel.store.list(),[]);self.panel.open_chat('bad');self.assertTrue(any(i.get('kind')=='note' for i in self.panel.items))
    def test_commands_compact_and_the_button(self):
        self.chat(J(say='一'),text='说一');self.chat(J(say='二'),text='说二');count=len(self.panel.agent.messages);self.assertEqual(count,4)
        with patch('agent_core.chat_turn',lambda *a,**k:('摘要内容',False)),patch.object(self.window.settings,'ready',lambda:True):
            for command in ('/compact','/压缩'):
                self.panel.input.setPlainText(command);self.panel.send();self.assertEqual(self.panel.ball.emotion,'compressing');self.wait_turn();self.assertEqual(self.panel.input.toPlainText(),'');self.assertEqual(len(self.panel.agent.messages),2);self.assertIn('摘要内容',self.panel.agent.messages[0]['content']);self.assertEqual(self.panel.agent.messages[0]['role'],'user')
        self.assertEqual(self.panel.items[-1]['kind'],'divider')
        self.panel.open_gauge();self.assertTrue(self.panel.pop.is_gauge);self.panel.close_pop()
    def test_compact_failure_keeps_the_conversation(self):
        from llm_client import ChatError
        self.chat(J(say='一'),text='说一');kept=list(self.panel.agent.messages)
        def boom(*a,**k):raise ChatError('network','连不上')
        with patch('agent_core.chat_turn',boom):self.panel.compact();self.wait_turn()
        self.assertEqual(self.panel.agent.messages,kept);self.assertIn('压缩没有完成',self.panel.items[-1]['text'])
    def test_empty_conversation_cannot_compact(self):
        self.panel.compact();self.assertFalse(self.panel.in_turn);self.assertIn('没有可压缩',self.panel.items[-1]['text'])
    def test_unknown_slash_is_sent_as_words(self):
        seen=self.chat(J(say='好'),text='/etc 是什么');self.assertEqual(seen[0],'/etc 是什么')
    def test_slash_shows_commands(self):
        self.panel.input.setPlainText('/');self.assertTrue(self.panel.pop.is_slash);self.panel.input.setPlainText('hello');self.assertIsNone(self.panel.pop)
    def test_gauge_counts_each_kind_and_the_window(self):
        c=self.panel.agent.context();self.assertGreater(c['system'],0);self.assertGreater(c['tools'],0);self.assertEqual(c['messages'],0);self.assertEqual(c['used'],c['system']+c['tools']+c['messages'])
    def test_full_level_offers_more_tools_in_the_gauge(self):
        low=self.panel.agent.context()['tools']
        with patch('assistant_panel.confirm',lambda *a,**k:True):self.panel.choose_level(3)
        self.assertGreater(self.panel.agent.context()['tools'],low);self.assertIn('完全访问',self.panel.level_chip.text());self.assertEqual(self.panel.ball.emotion,'alert')
    def test_model_choice_is_for_the_assistant_only(self):
        translate=self.window.settings.model;self.panel.choose_model('other-model');self.assertEqual(self.panel.chat_model(),'other-model');self.assertEqual(self.window.settings.model,translate);self.assertEqual(self.host.config()['model'],'other-model');self.assertIn('other-model',self.panel.model_chip.text())
        self.panel.choose_model('');self.assertEqual(self.panel.chat_model(),translate)
    def test_model_menu_lists_what_the_service_offers(self):
        with patch('assistant_panel.list_models',lambda *a,**k:['upstream-a','upstream-b']):
            self.panel.open_models();self.pump(lambda:self.window.settings.service in self.panel.models)
        from assistant_panel import PopRow
        names=[b.text() for b in self.panel.pop.findChildren(PopRow)];self.assertIn('upstream-a',names);self.assertIn('跟随翻译设置',names)
    def test_model_list_failure_still_lets_you_choose(self):
        def boom(*a,**k):raise RuntimeError('HTTP 404')
        with patch('assistant_panel.list_models',boom):
            self.panel.open_models();pop=self.panel.pop;self.pump(lambda:any('取不到' in x.text() for x in pop.findChildren(app_live.QLabel)))
    def test_effort_is_saved_and_sent(self):
        self.assertEqual(self.panel.effort,'default');self.assertEqual(self.host.config()['effort'],'default');self.panel.choose_effort('max');self.assertEqual(self.host.config()['effort'],'max');self.assertEqual(self.panel.saved_effort(),'max');self.assertIn('Max',self.panel.effort_chip.text())
    def test_new_states_show_up(self):
        for name,detail,face in (('tool','file|读文件','reading'),('tool','danger|删除','panic'),('tool','patch|修补','patching'),('tool','write|写入','writing'),('compressing','','compressing')):
            self.panel.agent_state(name,detail);self.assertEqual(self.panel.ball.emotion,face)
        self.panel.running=True;self.panel.ball.set_emotion('thinking');self.panel.pondering();self.assertEqual(self.panel.ball.emotion,'pondering');self.panel.running=False
        self.panel.input.setPlainText('hi');self.assertEqual(self.panel.ball.emotion,'listening');self.panel.input.clear();self.assertNotEqual(self.panel.ball.emotion,'listening')
    def test_thanks_makes_it_shy(self):
        self.chat(J(say='不客气'),text='谢谢你');self.assertEqual(self.panel.ball.emotion,'shy')
    def test_repeated_failure_turns_sad(self):
        self.panel.agent_state('failed','network');self.assertEqual(self.panel.ball.emotion,'error');self.panel.agent_state('failed','network');self.assertEqual(self.panel.ball.emotion,'sad')
    def test_steps_group_tools_and_report_failures(self):
        self.panel.agent_state('tool','read|查询效果。');self.assertIn('查询效果',self.panel.steps.head.text());self.panel.agent_state('tool_done','failed');self.assertIn('1 步失败',self.panel.steps.head.text())
    def test_motion_on_runs_the_panel_animations(self):
        handle=patch.object(ui_kit,'motion',lambda:True);handle.start();self.addCleanup(handle.stop);self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents()
        self.panel.toggle();self.assertIsNotNone(self.panel.reveal);self.assertFalse(self.panel.drawer.isVisible());self.pump(lambda:self.panel.reveal is None);self.assertTrue(self.panel.drawer.isVisible())
        self.panel.open_efforts();pop=self.panel.pop;self.pump(lambda:pop.graphicsEffect() is None);self.panel.close_pop()
        self.panel.toggle();self.assertFalse(self.panel.drawer.isVisible());self.pump(lambda:self.panel.reveal is None)
    def test_toggling_during_the_animation_is_safe(self):
        handle=patch.object(ui_kit,'motion',lambda:True);handle.start();self.addCleanup(handle.stop);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents()
        self.panel.toggle();self.panel.toggle();self.panel.toggle();self.pump(lambda:self.panel.reveal is None);self.assertTrue(self.panel.drawer.isVisible())

    def test_old_saved_levels_become_default(self):
        for old in ('off','medium'):
            self.window.save_ui_preference('assistantEffort',old);self.assertEqual(self.panel.saved_effort(),'default')
    def test_menu_shows_the_four_levels_in_order(self):
        from assistant_panel import PopRow
        self.panel.open_efforts();self.assertEqual([r.text() for r in self.panel.pop.findChildren(PopRow)],['默认','低','高','Max'])
    def test_reveal_is_slow_enough_to_see_and_settles_in_place(self):
        # The panel used to be nearly all out after a few frames, which looked like no animation at all.
        handle=patch.object(ui_kit,'motion',lambda:True);handle.start();self.addCleanup(handle.stop);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents();self.panel.toggle();reveal=self.panel.reveal;self.assertGreaterEqual(reveal.anim.duration(),400);self.assertGreater(reveal.shape_at(.1),.08,'answers the click at once');self.assertLess(reveal.shape_at(.25),.6)
        self.assertGreater(max(reveal.shape_at(i/50) for i in range(51)),1.005);self.assertAlmostEqual(reveal.shape_at(1.0),1.0,places=3);self.assertAlmostEqual(reveal.ball_squash(1.0),0.0);self.pump(lambda:self.panel.reveal is None);self.assertEqual(self.panel.ball.squash,0.0)
    def test_panel_is_compact_and_stands_on_the_bottom_edge(self):
        self.window.resize(1500,1000);QApplication.processEvents();area=self.window.centralWidget().rect();box=self.panel.drawer.geometry();self.assertLessEqual(box.width(),380);self.assertLessEqual(box.height(),600);self.assertEqual(area.bottom()-box.bottom(),8);self.assertEqual(area.right()-box.right(),8)
    def test_a_long_model_name_is_shortened_for_the_narrower_panel(self):
        with patch.object(self.panel,'chat_model',lambda:'a-very-long-model-name-indeed'):self.panel.update_chips()
        self.assertEqual(self.panel.model_chip.text(),'a-very-long… ▾')
    def test_glass_shows_the_page_and_thins_upwards(self):
        from PySide6.QtGui import QColor
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);self.panel.toggle();QApplication.processEvents();drawer=self.panel.drawer;self.assertNotIn(self.panel.ball,[w for w in self.window.centralWidget().children() if w is drawer])
        from assistant_panel import GLASS_WASH
        self.assertLess(GLASS_WASH[0][1],GLASS_WASH[-1][1]);self.assertEqual([a for a,_ in GLASS_WASH],sorted(a for a,_ in GLASS_WASH));self.assertFalse(drawer.backdrop.isNull());self.assertEqual(drawer.glass().deviceIndependentSize().toSize(),drawer.size())
        # Dark paper with a bright page behind: thinner at the top means lighter there.
        ui_kit.set_theme(True);self.addCleanup(ui_kit.set_theme,False);drawer.pane_key=None;image=drawer.glass().toImage();self.assertGreater(QColor(image.pixel(40,4)).lightness(),QColor(image.pixel(40,image.height()-4)).lightness())
    def test_glass_follows_the_page_live(self):
        from PySide6.QtWidgets import QFrame
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);self.panel.toggle();QApplication.processEvents();drawer=self.panel.drawer;self.assertTrue(drawer.timer.isActive());before=drawer.glass().toImage();drawer.refresh();self.assertFalse(drawer.refresh(),'nothing moved')
        block=QFrame(self.window.centralWidget());block.setStyleSheet('background:#000000;');block.setGeometry(drawer.geometry());block.show();drawer.raise_();self.panel.ball.raise_();self.addCleanup(block.deleteLater)
        self.assertTrue(drawer.refresh());self.assertNotEqual(drawer.glass().toImage(),before)
        self.panel.toggle();self.assertFalse(drawer.timer.isActive())
    def test_animation_and_panel_sit_on_whole_screen_pixels(self):
        # At 125% a corner between two screen pixels put the page's picture half a pixel off: the page behind jumped as the animation began and ended.
        from types import SimpleNamespace
        from PySide6.QtCore import QPoint
        from assistant_panel import pixel_aligned
        widget=SimpleNamespace(devicePixelRatioF=lambda:1.25,mapTo=lambda window,point:QPoint(3,2),window=lambda:None)
        for x,y in ((1017,397),(1000,400),(5,1)):
            at=pixel_aligned(QPoint(x,y),widget);self.assertTrue(abs(x-at.x())<4 and abs(y-at.y())<4)
            for v,o in ((at.x(),3),(at.y(),2)):self.assertAlmostEqual((v+o)*1.25,round((v+o)*1.25))
        self.assertEqual(pixel_aligned(QPoint(1017,397),SimpleNamespace(devicePixelRatioF=lambda:1.0,mapTo=lambda w,p:QPoint(3,2),window=lambda:None)),QPoint(1017,397))
    def test_glass_can_be_turned_off_for_a_plain_panel(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QFrame
        w=self.window;self.assertTrue(w.glass_box.isChecked(),'on by default');self.assertTrue(self.panel.drawer.frosted)
        w.resize(1200,720);w.show();self.addCleanup(w.hide);self.panel.toggle();QApplication.processEvents();drawer=self.panel.drawer;self.assertTrue(drawer.timer.isActive())
        w.glass_box.setChecked(False);self.assertFalse(drawer.frosted);self.assertFalse(drawer.timer.isActive());self.assertIsNone(drawer.watched);self.assertFalse(drawer.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent))
        self.assertIs(w.ui_preferences().get('frostedGlass'),False)
        block=QFrame(w.centralWidget());block.setStyleSheet('background:#000000;');block.setGeometry(drawer.geometry());block.show();drawer.raise_();self.panel.ball.raise_();self.addCleanup(block.deleteLater)
        self.assertFalse(drawer.refresh(),'never samples the page');image=drawer.glass().toImage();paper=QColor(ui_kit.PAPER)
        for x,y in ((40,4),(40,image.height()-4),(image.width()//2,image.height()//2)):self.assertEqual(image.pixelColor(x,y).rgb(),paper.rgb())
        self.panel.toggle();self.panel.toggle();QApplication.processEvents();self.assertFalse(drawer.timer.isActive(),'stays off when opened again')
        w.glass_box.setChecked(True);self.assertTrue(drawer.frosted);self.assertTrue(drawer.timer.isActive());self.assertNotEqual(drawer.glass().toImage().pixelColor(40,image.height()//2).rgb(),paper.rgb())
    def test_glass_takes_in_what_lies_beside_the_panel(self):
        from PySide6.QtWidgets import QFrame
        from assistant_panel import GLASS_BLEED
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);self.panel.toggle();QApplication.processEvents();drawer=self.panel.drawer;drawer.refresh();box=drawer.geometry();before=drawer.glass().toImage().copy()
        block=QFrame(self.window.centralWidget());block.setStyleSheet('background:#000000;');block.setGeometry(box.left()-GLASS_BLEED,box.top()+100,GLASS_BLEED,200);block.show();self.addCleanup(block.deleteLater);drawer.raise_();self.panel.ball.raise_()
        self.assertTrue(drawer.refresh());after=drawer.glass().toImage();ratio=after.devicePixelRatio();x,y=int(3*ratio),int(200*ratio);self.assertLess(after.pixelColor(x,y).lightness(),before.pixelColor(x,y).lightness())
    def test_the_frost_stays_put_while_the_shape_moves(self):
        # It used to be stretched into the shape, which is what made it read as a picture.
        handle=patch.object(ui_kit,'motion',lambda:True);handle.start();self.addCleanup(handle.stop);self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents();self.panel.toggle();reveal=self.panel.reveal;drawer=self.panel.drawer;self.assertEqual(reveal.frost_at+reveal.pos(),drawer.reach().topLeft());self.assertTrue(reveal.frost.deviceIndependentSize().toSize()==drawer.reach().size());self.pump(lambda:self.panel.reveal is None)
    def test_glass_never_samples_the_animation(self):
        # It is still up, showing a picture of the panel, when the panel appears: the blur used to catch it and flash for a frame.
        from assistant_panel import Reveal
        handle=patch.object(ui_kit,'motion',lambda:True);handle.start();self.addCleanup(handle.stop);self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents();self.panel.toggle();reveal=self.panel.reveal;self.assertIsInstance(reveal,Reveal);self.panel.reveal=None;drawer=self.panel.drawer;self.assertNotIn(reveal,[child for child,_,_ in drawer.under()]);self.panel.reveal=reveal;self.pump(lambda:self.panel.reveal is None)
    def test_closing_answers_the_click_at_once(self):
        handle=patch.object(ui_kit,'motion',lambda:True);handle.start();self.addCleanup(handle.stop);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents();self.panel.toggle();self.pump(lambda:self.panel.reveal is None);self.panel.toggle();reveal=self.panel.reveal;self.assertLess(reveal.anim.duration(),350);self.assertLess(reveal.shape_at(.1),.97);self.assertLessEqual(max(reveal.shape_at(i/20) for i in range(21)),1.0);self.pump(lambda:self.panel.reveal is None)
    def choosing(self):
        reply=json.dumps({'understood':'想翻译效果','question':'你想翻译哪些？','options':[{'title':'翻译勾选的效果','detail':'只改候选'},{'title':'翻译全部效果'}]},ensure_ascii=False);return self.chat(reply)
    def test_unclear_request_shows_cards_and_a_click_sends_the_words_on_it(self):
        from assistant_panel import Choice
        self.choosing();self.assertIn('理解：想翻译效果',[w.text() for w in self.panel.feed.findChildren(app_live.QLabel)]);cards=self.panel.choices.findChildren(Choice);self.assertEqual(len(cards),2);self.assertFalse(self.panel.in_turn)
        with patch('agent_core.chat_turn',lambda system,messages,config,**o:(seen_box.append(messages[-1]['content']) or J(say='好的'),False)) as _t,patch.object(self.window.settings,'ready',lambda:True):
            seen_box=[];cards[0].clicked.emit();self.pump(lambda:not self.panel.in_turn)
        self.assertEqual(seen_box,['翻译勾选的效果：只改候选']);self.assertIsNone(self.panel.choices);self.assertEqual(self.panel.items[-1]['kind'],'reply')
    def test_typing_something_else_clears_the_cards(self):
        self.choosing();self.assertIsNotNone(self.panel.choices);self.chat(J(say='好的'),text='我要翻译分类名');self.assertIsNone(self.panel.choices)
    def test_the_way_out_of_the_cards_just_focuses_the_input(self):
        self.choosing();calls=[]
        with patch('agent_core.chat_turn',lambda *a,**k:calls.append(a) or (J(say=''),False)):next(b for b in self.panel.choices.findChildren(app_live.QPushButton) if b.text()=='都不是，我自己说').click()
        self.assertEqual(calls,[]);self.assertIsNotNone(self.panel.choices)
    def test_only_the_last_turns_cards_work_in_an_old_conversation(self):
        from assistant_panel import Choice
        self.choosing();chat=self.panel.chat_id;self.panel.save_chat();self.panel.new_chat();self.panel.open_chat(chat);self.assertEqual(len(self.panel.choices.findChildren(Choice)),2);self.assertEqual(self.panel.items[-1]['kind'],'options')
        self.chat(J(say='好的'),text='翻译全部');self.panel.save_chat();self.panel.new_chat();self.panel.open_chat(chat);self.assertIsNone(self.panel.choices);self.assertIn('当时的选项：翻译勾选的效果 / 翻译全部效果',[w.text() for w in self.panel.feed.findChildren(app_live.QLabel)])
    def test_glass_paces_itself(self):
        from assistant_panel import GLASS_FAST,GLASS_IDLE,GLASS_SHARE
        self.assertEqual(GLASS_IDLE,1000,'once a second at rest, as the user asked')
        drawer=self.panel.drawer;self.assertEqual(drawer.next_wait(True,False,.1,.001),GLASS_FAST);self.assertEqual(drawer.next_wait(True,False,5,.001),GLASS_IDLE);self.assertEqual(drawer.next_wait(False,False,.1,.001),GLASS_IDLE);self.assertEqual(drawer.next_wait(True,True,.1,.001),GLASS_IDLE);self.assertEqual(drawer.next_wait(True,False,.1,.1),int(.1*1000*GLASS_SHARE))
    def test_glass_rests_at_one_hertz_and_wakes_on_what_changes_the_page(self):
        from PySide6.QtCore import QEvent,QPointF,Qt
        from PySide6.QtGui import QMouseEvent
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);self.panel.toggle();QApplication.processEvents();drawer=self.panel.drawer;active=patch.object(self.window,'isActiveWindow',lambda:True);active.start();self.addCleanup(active.stop)
        drawer.changed_at=0.0;drawer.tick();self.assertEqual(drawer.timer.interval(),1000)
        hover=QMouseEvent(QEvent.Type.MouseMove,QPointF(5,5),QPointF(5,5),Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier);drawer.eventFilter(None,hover);self.assertGreater(drawer.timer.remainingTime(),16,'hovering alone does not')
        for event in (QEvent(QEvent.Type.Move),QEvent(QEvent.Type.Resize),QEvent(QEvent.Type.KeyPress),QEvent(QEvent.Type.Wheel)):
            drawer.changed_at=0.0;drawer.timer.start(1000);self.assertFalse(drawer.eventFilter(None,event),'never eats the event');self.assertEqual(drawer.timer.remainingTime(),0);drawer.tick();self.assertLess(drawer.timer.interval(),1000)
        drag=QMouseEvent(QEvent.Type.MouseMove,QPointF(5,5),QPointF(5,5),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier);drawer.changed_at=0.0;drawer.timer.start(1000);drawer.eventFilter(None,drag);self.assertEqual(drawer.timer.remainingTime(),0)
    def test_programs_own_work_wakes_the_glass(self):
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);self.panel.toggle();QApplication.processEvents();drawer=self.panel.drawer
        for action in (self.panel.job_started,self.panel.job_finished,self.panel.job_failed):
            drawer.changed_at=0.0;action();self.assertGreater(drawer.changed_at,0.0)
    def test_glass_stops_watching_the_window_when_closed(self):
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);self.panel.toggle();QApplication.processEvents();drawer=self.panel.drawer;self.assertIs(drawer.watched,self.window.windowHandle());self.panel.toggle();self.assertIsNone(drawer.watched)
    def test_glass_contents_leave_out_the_glass(self):
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);self.panel.toggle();QApplication.processEvents();image=self.panel.drawer.contents().toImage();self.assertEqual(image.pixelColor(2,image.height()//2).alpha(),0);self.assertFalse(self.panel.drawer.bare)
    def test_level_notes_show_in_full(self):
        from assistant_panel import PopRow
        self.window.resize(1200,720);self.window.show();self.addCleanup(self.window.hide);self.panel.toggle();QApplication.processEvents();self.panel.open_levels();QApplication.processEvents();rows=self.panel.pop.findChildren(PopRow);self.assertEqual([r.text() for r in rows][-1],'完全访问权限')
        for row in rows:self.assertGreaterEqual(row.note.height(),row.note.heightForWidth(row.note.width()))
        self.assertTrue(self.panel.drawer.rect().contains(self.panel.pop.geometry()))
    def test_gauge_stays_while_hovered_and_goes_after_leaving(self):
        self.panel.open=True;self.panel.open_gauge();pop=self.panel.pop;self.panel.gauge_hover(False);self.assertTrue(self.panel.gauge_leave.isActive());self.assertEqual(self.panel.gauge_leave.interval(),1250)
        self.panel.gauge_hover(True);self.assertFalse(self.panel.gauge_leave.isActive());self.panel.gauge_hover(False);self.panel.gauge_left();self.assertIsNone(self.panel.pop)
    def test_reveal_does_not_paint_over_a_live_page(self):
        from assistant_panel import Reveal
        handle=patch.object(ui_kit,'motion',lambda:True);handle.start();self.addCleanup(handle.stop);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents();self.panel.toggle();reveal=self.panel.reveal;self.assertIsInstance(reveal,Reveal);self.assertFalse(reveal.background.isNull());self.assertTrue(reveal.testAttribute(app_live.Qt.WidgetAttribute.WA_OpaquePaintEvent));self.pump(lambda:self.panel.reveal is None)
    def test_closing_hides_the_panel_before_the_cover_is_gone(self):
        handle=patch.object(ui_kit,'motion',lambda:True);handle.start();self.addCleanup(handle.stop);self.window.show();self.addCleanup(self.window.hide);QApplication.processEvents();self.panel.toggle();self.pump(lambda:self.panel.reveal is None);self.panel.toggle();self.assertFalse(self.panel.drawer.isVisible());self.assertTrue(self.panel.reveal.isVisible());self.assertIs(self.panel.ball.blank,False);self.pump(lambda:self.panel.reveal is None);self.assertFalse(self.panel.drawer.isVisible())

    def test_translating_the_checked_effects_only(self):
        calls=[]
        def fake(items,*a,**k):
            calls.append([i['id'] for i in items]);return {i['id']:'译' for i in items}
        self.window.enabled.add('Atom Key')
        with patch('agent_tools.translate_names',fake):
            self.chat(J(calls=[{'tool':'translate_effects','args':{'checkedOnly':True,'instructions':'按影视后期习惯'}}]),J(say='好'))
        self.pump(lambda:not self.window.busy());self.assertEqual(calls,[['Atom Key']])
    def test_translating_needs_a_range(self):
        fake=lambda *a,**k:self.fail('不该翻译')
        with patch('agent_tools.translate_names',fake),patch.object(self.window.settings,'ready',lambda:True):
            for args in ({},{'keys':[]},{'instructions':'x'}):
                with self.assertRaises(ValueError):self.tool('translate_effects').run(args)

    def picture(self,w=2000,h=1000):
        from PySide6.QtGui import QImage
        image=QImage(w,h,QImage.Format.Format_ARGB32);image.fill(0xffff0000);return image
    def test_attached_picture_is_sent_shrunk_and_cleared(self):
        import base64
        from PySide6.QtGui import QImage
        self.panel.attach_image(self.picture());self.assertEqual(len(self.panel.attachments),1);self.assertTrue(self.panel.strip.isVisibleTo(self.panel.composer));seen=[]
        def turn(system,messages,config,**o):
            seen.append(messages[-1]);return J(say='看到了'),False
        with patch('agent_core.chat_turn',turn),patch.object(self.window.settings,'ready',lambda:True):
            self.panel.send();self.pump(lambda:not self.panel.in_turn)
        self.assertEqual(seen[0]['content'],'看看这张图');image=QImage.fromData(base64.b64decode(seen[0]['images'][0]['data']));self.assertEqual(max(image.width(),image.height()),1568);self.assertEqual(self.panel.attachments,[]);self.assertFalse(self.panel.strip.isVisibleTo(self.panel.composer));self.assertIn('thumbs',self.panel.items[0])
    def test_ball_sends_with_only_a_picture(self):
        self.panel.open=True;self.panel.attach_image(self.picture(10,10));self.assertIn('点小球发送',self.panel.hint.text())
        with patch('agent_core.chat_turn',lambda *a,**k:(J(say='好'),False)),patch.object(self.window.settings,'ready',lambda:True):
            self.panel.ball_clicked();self.pump(lambda:not self.panel.in_turn)
        self.assertEqual(len(self.panel.agent.messages[0]['images']),1)
    def test_no_more_than_four_and_removable(self):
        for _ in range(6):self.panel.attach_image(self.picture(10,10))
        self.assertEqual(len(self.panel.attachments),4);self.assertIn('最多 4',self.panel.hint.text());self.panel.remove_attachment(self.panel.attachments[0]);self.assertEqual(len(self.panel.attachments),3)
    def test_paste_and_files(self):
        from PySide6.QtCore import QMimeData,QUrl
        data=QMimeData();data.setImageData(self.picture(10,10));self.assertTrue(self.panel.input.canInsertFromMimeData(data));self.panel.input.insertFromMimeData(data);self.assertEqual(len(self.panel.attachments),1)
        path=self.temp.name+'/a.png';self.picture(10,10).save(path);files=QMimeData();files.setUrls([QUrl.fromLocalFile(path)]);self.panel.input.insertFromMimeData(files);self.assertEqual(len(self.panel.attachments),2)
        text=QMimeData();text.setText('普通文字');self.panel.input.insertFromMimeData(text);self.assertEqual(self.panel.input.toPlainText(),'普通文字');self.assertEqual(len(self.panel.attachments),2)
        bad=self.temp.name+'/b.png';open(bad,'wb').write(b'x');self.panel.attach_files([bad,self.temp.name+'/none.png']);self.assertEqual(len(self.panel.attachments),2)
    def test_refused_picture_keeps_the_words_and_later_turns_work(self):
        from llm_client import ChatError
        self.panel.attach_image(self.picture(10,10));calls=[]
        def turn(system,messages,config,**o):
            calls.append(messages)
            if any(m.get('images') for m in messages):raise ChatError('vision','不支持图片')
            return J(say='好'),False
        with patch('agent_core.chat_turn',turn),patch.object(self.window.settings,'ready',lambda:True):
            self.panel.send('这是什么');self.pump(lambda:not self.panel.in_turn);self.assertEqual(self.panel.ball.emotion,'refuse');self.assertIn('不支持图片',self.panel.items[-1]['text'])
            self.panel.send('那说说别的');self.pump(lambda:not self.panel.in_turn)
        self.assertEqual(self.panel.items[-1]['text'],'好');self.assertIn('没有看到',self.panel.agent.messages[0]['content'])
    def test_saved_chat_brings_pictures_back(self):
        self.panel.attach_image(self.picture(10,10))
        with patch('agent_core.chat_turn',lambda *a,**k:(J(say='好'),False)),patch.object(self.window.settings,'ready',lambda:True):
            self.panel.send('看');self.pump(lambda:not self.panel.in_turn)
        chat=self.panel.chat_id;self.panel.new_chat();self.panel.open_chat(chat);self.assertEqual(len(self.panel.agent.messages[0]['images']),1);self.assertIn('thumbs',self.panel.items[0])
    def test_pictures_count_in_the_gauge(self):
        self.panel.agent.messages=[{'role':'user','content':'x','images':[{'media':'image/jpeg','data':'a'}]}];self.assertGreaterEqual(self.panel.agent.context()['messages'],900)

if __name__=='__main__':unittest.main()
