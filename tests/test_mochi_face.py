import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import random
import unittest
from pathlib import Path
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication,QWidget
import mochi_face,ui_kit
from mochi_emotions import EMOTIONS
from mochi_face import FaceEngine,MochiFace

GOLDEN=json.loads((Path(__file__).parent/'mochi_golden.json').read_text(encoding='utf8'))

class PortMatchesTheOriginal(unittest.TestCase):
    """tests/mochi_golden.json is exported from the JavaScript package."""
    def close(self,mine,theirs,places=4):
        self.assertEqual(len(mine),len(theirs))
        for a,b in zip(mine,theirs):self.assertAlmostEqual(a,b,places)
    def test_every_pose_contour(self):
        self.assertEqual(sum(1 for d in EMOTIONS.values() if d['id']<'50'),32)
        checked=0
        for d in EMOTIONS.values():
            if d['id']>='50':continue
            for i,pose in enumerate(d['poses']):
                left=mochi_face.EYE_RIGHT if pose['l'].get('_m') else mochi_face.EYE_LEFT
                self.close(mochi_face.build_ring(pose['l'],48,left),GOLDEN['rings'][f"{d['key']}/{i}/l"])
                self.close(mochi_face.build_ring(pose['r'],48,mochi_face.EYE_RIGHT),GOLDEN['rings'][f"{d['key']}/{i}/r"])
                checked+=2
                if pose.get('m'):
                    self.close(mochi_face.build_ring(pose['m'],32,mochi_face.MOUTH),GOLDEN['rings'][f"{d['key']}/{i}/m"]);checked+=1
        self.assertEqual(checked,len(GOLDEN['rings']))
    def test_alignment_springs_and_keyframes(self):
        a=mochi_face.build_ring({'s':'oval','w':.2,'h':.27},48,mochi_face.EYE_RIGHT);b=mochi_face.build_ring({'s':'star','size':.34},48,mochi_face.EYE_RIGHT)
        self.close(mochi_face.align_ring(a,b,48),GOLDEN['aligned'])
        for (w,z,dt),coef in zip(((10,.68,.033),(34,.6,.016),(17,.56,.05)),GOLDEN['spring']):self.close(mochi_face.spring_coef(w,z,dt),coef,8)
        for t,(y,q) in zip((0,90,300,480,700,1000),GOLDEN['bounce']):
            self.assertAlmostEqual(mochi_face.keyframe_at(mochi_face.BOUNCE['y'],t),y,9);self.assertAlmostEqual(mochi_face.keyframe_at(mochi_face.BOUNCE['q'],t),q,9)
        for t,(y,q) in zip((0,70,200,400,520,700),GOLDEN['hop']):
            self.assertAlmostEqual(mochi_face.keyframe_at(mochi_face.HOP['y'],t),y,9);self.assertAlmostEqual(mochi_face.keyframe_at(mochi_face.HOP['q'],t),q,9)

class EngineTests(unittest.TestCase):
    def engine(self,**options):return FaceEngine(rng=random.Random(7),**options)
    def test_every_emotion_runs(self):
        e=self.engine()
        for key in EMOTIONS:
            self.assertTrue(e.set_emotion(key))
            for _ in range(90):e.frame(.033)
    def test_unknown_emotion_is_refused(self):
        e=self.engine();self.assertFalse(e.set_emotion('nope'));self.assertEqual(e.key,'idle')
    def test_next_moves_on_by_itself(self):
        e=self.engine();e.set_emotion('wake')
        for _ in range(60):e.frame(.033)
        self.assertEqual(e.key,'idle')
    def test_entrance_effects_fire(self):
        e=self.engine();e.set_emotion('done')
        for _ in range(5):e.frame(.033)
        self.assertTrue(e.confetti);self.assertTrue(e.ribbon_lines)
    def test_reduced_motion_has_no_effects(self):
        e=self.engine(reduced=True);e.set_emotion('done')
        for _ in range(30):e.frame(.033)
        self.assertFalse(e.confetti);self.assertFalse(e.jumps);self.assertFalse(e.floaters)
    def test_bounce_does_not_stack(self):
        e=self.engine();e.bounce();e.bounce();self.assertEqual(len(e.jumps),1)
    def test_quiet_time_leads_to_sleep_and_a_poke_wakes(self):
        e=self.engine(idle=(100,300))
        for _ in range(20):e.frame(.033)
        self.assertEqual(e.key,'sleep')
        e.poke();self.assertEqual(e.key,'wake')
    def test_working_states_never_doze(self):
        e=self.engine(idle=(100,300));e.set_emotion('busy')
        for _ in range(30):e.frame(.033)
        self.assertEqual(e.key,'busy')

class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def tearDown(self):ui_kit.set_theme(False)
    def centre_and_page(self,dark):
        ui_kit.set_theme(dark);host=QWidget();host.resize(200,200);host.setStyleSheet(f'background:{ui_kit.CANVAS}')
        face=MochiFace(host,follow=False);face.resize(200,200);image=host.grab().toImage()
        return QColor(image.pixel(100,60)),QColor(image.pixel(100,100)),QColor(image.pixel(100,175))
    def test_light_is_a_black_ball(self):
        top,_,_=self.centre_and_page(False);self.assertEqual(top.name(),'#000000')
    def test_dark_inverts_to_a_white_ball(self):
        top,_,_=self.centre_and_page(True);self.assertEqual(top.name(),'#ffffff')
    def test_face_is_the_opposite_colour(self):
        for dark,want in ((False,'#ffffff'),(True,'#000000')):
            ui_kit.set_theme(dark);face=MochiFace(follow=False);face.resize(200,200);image=face.grab().toImage()
            colours={QColor(image.pixel(x,y)).name() for x in range(40,160,2) for y in range(50,130,2)}
            self.assertIn(want,colours)
    def test_timer_only_runs_while_shown(self):
        face=MochiFace(follow=False);face.resize(80,80);self.assertFalse(face.timer.isActive())
        face.show();self.assertTrue(face.timer.isActive());face.hide();self.assertFalse(face.timer.isActive())
    def test_click_only_counts_on_the_body(self):
        face=MochiFace(follow=False);face.resize(200,200)
        from PySide6.QtCore import QPointF
        self.assertTrue(face.on_body(QPointF(100,100)));self.assertFalse(face.on_body(QPointF(5,5)))

if __name__=='__main__':unittest.main()
