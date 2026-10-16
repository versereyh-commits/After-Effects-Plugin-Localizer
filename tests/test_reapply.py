"""After AE restarts its palette shows the original names; the names last applied here are written back once per AE process."""
import json
from pathlib import Path
from unittest.mock import patch
from test_gui import GuiCase
import app_live

def live(pid,names):
    return {'pid':pid,'creationTime':pid*10,'effects':[{'matchName':k,'displayName':v,'category':'c'} for k,v in names.items()]}

class KeptNamesTests(GuiCase):
    def kept(self):return json.loads(self.window.kept_path().read_text(encoding='utf8'))
    def run_apply(self,values,applied=None,instances=True):
        w=self.window
        with patch.object(w.session,'apply',return_value={'applied':applied if applied is not None else dict(values)}),\
             patch.object(w.instance_session,'apply',return_value={'changed':0}) as sync,patch.object(app_live,'with_live',lambda op,exe:(op,None,'')):
            w.apply_names(values,instances=instances)
        return sync
    def test_applied_names_are_kept_in_chinese_and_restores_forget_them(self):
        w=self.window;w.bilingual_box.setChecked(True)
        self.run_apply({'tc Shine':'闪耀','S_Glow':'辉光','Atom Key':'原子'},applied={'tc Shine':'Shine (闪耀)','S_Glow':'S_Glow (辉光)'})
        self.assertEqual(self.kept(),{'tc Shine':'闪耀','S_Glow':'辉光'})
        self.assertEqual(w.kept_path().parent,w.space.folder)
        with patch.object(w.session,'restore',return_value={'restored':{}}),patch.object(w.instance_session,'restore',return_value={}):
            w.restore_effect_names(['S_Glow']);self.assertEqual(self.kept(),{'tc Shine':'闪耀'})
            w.restore_effect_names();self.assertEqual(self.kept(),{})
    def test_a_failed_restore_still_forgets(self):
        w=self.window;self.run_apply({'S_Glow':'辉光'})
        with patch.object(w.session,'restore',side_effect=RuntimeError('guard')),patch.object(w.instance_session,'restore',return_value={}):
            result=w.restore_effect_names(['S_Glow'])
        self.assertIn('restoreErrors',result);self.assertEqual(self.kept(),{})
    def test_catalog_only_write_skips_layer_titles(self):
        self.assertFalse(self.run_apply({'S_Glow':'辉光'},instances=False).called)
        self.assertTrue(self.run_apply({'S_Glow':'辉光'}).called)

class AutoReapplyTests(GuiCase):
    def setUp(self):
        super().setUp();w=self.window
        app_live.write_json(w.kept_path(),{'S_Glow':'辉光','tc Shine':'闪耀','Gone FX':'消失'})
        self.calls=[]
        def apply(values,instances=True):self.calls.append((dict(values),instances));return ({'applied':dict(values)},None,'')
        for name,value in (('apply_names',apply),('work',lambda work,done,failure=None:done(work(lambda text:None)))):
            handle=patch.object(w,name,value);handle.start();self.addCleanup(handle.stop)
    def connect(self,pid,names):self.window.show_live(live(pid,names));self.window.auto_reapply()
    def test_names_that_went_back_are_written_once_per_process(self):
        self.connect(1,{'S_Glow':'S_Glow','tc Shine':'闪耀'})
        self.assertEqual(self.calls,[({'S_Glow':'辉光'},False)])
        self.connect(1,{'S_Glow':'S_Glow','tc Shine':'闪耀'});self.assertEqual(len(self.calls),1)
        self.connect(2,{'S_Glow':'S_Glow','tc Shine':'Shine'});self.assertEqual(self.calls[-1],({'S_Glow':'辉光','tc Shine':'闪耀'},False))
        self.assertIn('自动重新应用',self.window.log.toPlainText())
    def test_nothing_due_means_no_write(self):
        self.connect(1,{'S_Glow':'辉光','tc Shine':'闪耀'});self.assertEqual(self.calls,[])
    def test_bilingual_form_counts_as_applied(self):
        w=self.window;w.bilingual_box.setChecked(True)
        self.connect(1,{'S_Glow':'S_Glow (辉光)','tc Shine':'闪耀'});self.assertEqual(self.calls,[({'tc Shine':'闪耀'},False)])
    def test_switch_off(self):
        w=self.window;self.assertTrue(w.reapply_box.isChecked());w.reapply_box.setChecked(False)
        self.assertIs(json.loads((Path(self.temp.name)/'ui.json').read_text(encoding='utf8'))['reapplyNames'],False)
        self.connect(1,{'S_Glow':'S_Glow'});self.assertEqual(self.calls,[])
    def test_failure_is_reported_and_not_retried_for_the_same_process(self):
        w=self.window;errors=[]
        with patch.object(w,'work',lambda work,done,failure=None:failure('AE 有对话框打开')),patch.object(w,'error',errors.append):
            self.connect(1,{'S_Glow':'S_Glow'});self.connect(1,{'S_Glow':'S_Glow'})
        self.assertEqual(errors,['自动重新应用效果名称未完成：AE 有对话框打开'])

class SettingsTextTests(GuiCase):
    def test_limits_and_wording(self):
        w=self.window;self.assertEqual(w.limits.text(),app_live.LIMITS);self.assertIn('AE 重启后恢复原名',w.limits.text())
        thanks=next(f for f in w.settings_frames if f.findChild(app_live.QLabel).text()=='致谢')
        self.assertTrue(thanks.isAncestorOf(w.limits) and thanks.isAncestorOf(w.thanks) and thanks.isAncestorOf(w.limits_title))
        self.assertEqual((w.limits_title.text(),w.limits_title.objectName()),('局限','cardTitle'))
