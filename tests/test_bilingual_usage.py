"""Bilingual effect-name writing and the translation token tile."""
import datetime,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_gui import GuiCase
import app_live,llm_client,token_usage
from app_live import CANDIDATE,STATUS,bilingual_name

DAY=datetime.date(2026,10,16)

class TokenUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_reply_tokens_reads_both_protocols(self):
        self.assertEqual(token_usage.reply_tokens({'usage':{'prompt_tokens':120,'completion_tokens':30,'total_tokens':150}}),150)
        self.assertEqual(token_usage.reply_tokens({'usage':{'prompt_tokens':120,'completion_tokens':30}}),150)
        self.assertEqual(token_usage.reply_tokens({'usage':{'input_tokens':80,'output_tokens':20}}),100)
        for payload in ({},{'usage':None},{'usage':{'total_tokens':'9'}},{'usage':{'total_tokens':True}},[]):self.assertEqual(token_usage.reply_tokens(payload),0)
    def test_periods_count_back_from_today(self):
        for days_ago,tokens in ((0,100),(6,20),(7,3),(29,4000),(30,50000),(400,600000)):token_usage.record(tokens,self.root,DAY-datetime.timedelta(days=days_ago))
        token_usage.record(5,self.root,DAY);token_usage.record(0,self.root,DAY);token_usage.record(-3,self.root,DAY)
        self.assertEqual(token_usage.summary(self.root,DAY),{'all':654128,'30d':4128,'7d':125})
        stored=json.loads((self.root/token_usage.FILE).read_text(encoding='utf8'))
        self.assertEqual(stored['days'][DAY.isoformat()],105);self.assertEqual(set(stored),{'days'})
    def test_damaged_file_counts_as_empty(self):
        (self.root/token_usage.FILE).write_text('{broken',encoding='utf8')
        self.assertEqual(token_usage.summary(self.root,DAY),{'all':0,'30d':0,'7d':0})
        token_usage.record(7,self.root,DAY);self.assertEqual(token_usage.summary(self.root,DAY)['all'],7)
    def test_compact_figures(self):
        self.assertEqual([token_usage.compact(v) for v in (0,49,1500,9999,14400,123456,144000,12345678)],
                         ['0k','0.05k','1.5k','10k','14.4k','123k','144k','12,346k'])

class FakeReply:
    def __init__(self,payload):self.body=json.dumps(payload).encode('utf8')
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,*args):return self.body

class TranslationUsageTests(unittest.TestCase):
    item=[{'id':'S_Glow','sourceName':'S_Glow','category':'Sapphire Lighting'}]
    def reply(self,content,usage):return FakeReply({'choices':[{'message':{'content':content},'finish_reason':'stop'}],'usage':usage})
    def test_each_reply_reports_its_tokens(self):
        seen=[];good=self.reply(json.dumps({'translations':[{'id':'S_Glow','name':'辉光'}]}),{'total_tokens':321})
        with patch.object(llm_client,'urlopen',return_value=good):
            self.assertEqual(llm_client.translate_names(self.item,'test-only','https://example.invalid',usage=seen.append),{'S_Glow':'辉光'})
        self.assertEqual(seen,[321])
    def test_a_rejected_reply_still_counts_and_a_failing_counter_is_ignored(self):
        seen=[]
        with patch.object(llm_client,'urlopen',return_value=self.reply('not json',{'prompt_tokens':40,'completion_tokens':2})):
            with self.assertRaises(llm_client.TranslationError):llm_client.translate_names(self.item,'test-only','https://example.invalid',usage=seen.append)
        self.assertEqual(seen,[42])
        def broken(tokens):raise OSError('disk full')
        good=self.reply(json.dumps({'translations':[{'id':'S_Glow','name':'辉光'}]}),{'total_tokens':5})
        with patch.object(llm_client,'urlopen',return_value=good):
            self.assertEqual(llm_client.translate_names(self.item,'test-only','https://example.invalid',usage=broken),{'S_Glow':'辉光'})

class BilingualNameTests(unittest.TestCase):
    def test_pairs_english_with_chinese_when_it_fits(self):
        self.assertEqual(bilingual_name('Gaussian Blur','高斯模糊'),'Gaussian Blur (高斯模糊)')
        self.assertEqual(bilingual_name(' CC Bender ',' CC 弯曲 '),'CC Bender (CC 弯曲)')
    def test_falls_back_to_chinese(self):
        long_english='Very Long Third Party Effect Name'
        self.assertEqual(bilingual_name(long_english,'很长的第三方效果名称'),'很长的第三方效果名称')
        self.assertEqual(bilingual_name('Glow','glow 辉光'),'glow 辉光')
        self.assertEqual(bilingual_name('','辉光'),'辉光');self.assertEqual(bilingual_name('Glow',''),'')
        self.assertLessEqual(len(bilingual_name('Lumetri Color','Lumetri 颜色').encode('utf8')),47)

LIVE_PLAIN={'pid':1,'creationTime':5,'effects':[{'matchName':'S_Glow','displayName':'辉光','category':'c'},{'matchName':'tc Shine','displayName':'Shine (闪耀)','category':'c'}]}

class BilingualWindowTests(GuiCase):
    def status(self,key):return self.table.item(self.rows_for(key)[0],STATUS).text()
    def prefs(self):return json.loads((Path(self.temp.name)/'ui.json').read_text(encoding='utf8'))
    def test_off_by_default_and_remembered(self):
        w=self.window;self.assertFalse(w.bilingual_box.isChecked());self.assertFalse(w.bilingual);self.assertEqual(w.bilingual_box.text(),'双语写入')
        w.bilingual_box.setChecked(True);self.assertTrue(w.bilingual);self.assertIs(self.prefs()['bilingualNames'],True)
        w2=app_live.Window();self.addCleanup(w2.deleteLater);self.assertTrue(w2.bilingual_box.isChecked())
        w2.bilingual_box.setChecked(False);self.assertIs(self.prefs()['bilingualNames'],False)
    def test_status_follows_the_written_form(self):
        w=self.window;w.translated(({'tc Shine':'闪耀'},''));w.show_live(LIVE_PLAIN)
        self.assertEqual((self.status('S_Glow'),self.status('tc Shine')),('已应用','待应用'))
        self.assertEqual(w.chips['applied'].value,2)
        w.bilingual_box.setChecked(True)
        self.assertEqual((self.status('S_Glow'),self.status('tc Shine')),('待应用','已应用'))
        self.assertEqual(w.chips['applied'].value,1)
        self.assertEqual(self.table.item(self.rows_for('tc Shine')[0],CANDIDATE).text(),'闪耀')
    def test_writes_send_the_bilingual_form_and_keep_candidates_chinese(self):
        w=self.window;sent={};w.translated(({'tc Shine':'闪耀'},''))
        def apply(values,skip_missing):sent.update(values);return {'applied':dict(values)}
        with patch.object(w.session,'apply',side_effect=apply),patch.object(w.instance_session,'apply',return_value={'changed':0}) as instances,patch.object(app_live,'with_live',lambda op,exe:(op,None,'')):
            w.apply_names({'tc Shine':'闪耀'});self.assertEqual(sent,{'tc Shine':'闪耀'})
            w.bilingual_box.setChecked(True);w.apply_names({'tc Shine':'闪耀','S_Glow':'辉光'})
        self.assertEqual(sent,{'tc Shine':'Shine (闪耀)','S_Glow':'S_Glow (辉光)'})
        self.assertEqual(instances.call_args.args[0],{'tc Shine':'Shine (闪耀)','S_Glow':'S_Glow (辉光)'})
        self.assertEqual(w.pending['tc Shine'],'闪耀')

class DetailFitTests(GuiCase):
    def test_detail_panel_fits_the_smallest_window(self):
        w=self.window;w.translated(({'tc Shine':'闪耀'},''));self.select(self.rows_for('tc Shine')[0])
        for size,roomy in (((1200,720),False),((1600,1000),True)):
            w.resize(*size);w.show();self.addCleanup(w.hide);self.app.processEvents();frame=w.detail_frame
            self.assertLessEqual(frame.layout().totalHeightForWidth(frame.width()),frame.height(),size)
            self.assertTrue(w.bilingual_box.isVisible());self.assertTrue(w.reveal_btn.isVisible())
            if roomy:self.assertTrue(w.detail_hint.isVisible() and w.detail_legend.isVisible() and w.path_label.isVisible())

class UsageTileTests(GuiCase):
    def test_tile_shows_totals_after_work(self):
        w=self.window;self.assertEqual((w.usage_tile.figure,w.usage_tile.lines),('0k',[('近 30 天','0k'),('近 7 天','0k')]))
        app_live.record_usage(1500);w.record_usage(123456)
        token_usage.record(900,Path(self.temp.name),datetime.date.today()-datetime.timedelta(days=10))
        w.finished();self.assertEqual(w.usage_tile.figure,'126k')
        self.assertEqual(w.usage_tile.lines,[('近 30 天','126k'),('近 7 天','125k')])
        self.assertIn('125,856',w.usage_tile.toolTip());self.assertIn('不含 AI 诊断',w.usage_tile.toolTip())
        w.usage_tile.resize(170,78);w.usage_tile.grab()  # paints at the narrowest width without error

if __name__=='__main__':unittest.main()
