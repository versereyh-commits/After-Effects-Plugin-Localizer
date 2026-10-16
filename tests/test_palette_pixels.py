import unittest
from ae_palette_pixels import menu_glyphs

class MenuGlyphTests(unittest.TestCase):
    def image(self,lines=3,gap=2,length=8,stem=False,color=160):
        width,height=90,90;data=bytearray(bytes((40,40,40,255))*width*height)
        def pixel(x,y):data[(y*width+x)*4:(y*width+x)*4+3]=bytes((color,color,color))
        for row in range(lines):
            for x in range(20,20+length):pixel(x,40+row*gap)
        if stem:
            for y in range(40,40+(lines-1)*gap+1):pixel(20,y)
        return data,width,height
    def test_three_separate_strokes_locate_menu_center(self):self.assertEqual(menu_glyphs(*self.image()),[(24,42)])
    def test_scaled_menu_is_detected(self):self.assertEqual(menu_glyphs(*self.image(gap=3,length=12)),[(26,43)])
    def test_letter_with_joining_stem_is_rejected(self):self.assertEqual(menu_glyphs(*self.image(stem=True)),[])
    def test_two_strokes_are_not_a_menu(self):self.assertEqual(menu_glyphs(*self.image(lines=2)),[])
    def test_low_contrast_mark_is_rejected(self):self.assertEqual(menu_glyphs(*self.image(color=50)),[])
