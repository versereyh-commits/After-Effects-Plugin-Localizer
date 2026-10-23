import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import json,tempfile,time,unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent,QObject,QPoint,Qt
from PySide6.QtWidgets import QMenu
from PySide6.QtGui import QKeyEvent
import app_live
import favorites as fav
import quick_search as qs
from favorites import Favorites
from quick_search import Entry
from app_live import FAVORITE,KEY
from test_gui import GuiCase

def key_event(key):return QKeyEvent(QEvent.Type.KeyPress,key,Qt.KeyboardModifier.NoModifier)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/fav.FILE
    def test_order_is_kept_and_saved(self):
        store=Favorites(self.path);heard=[];store.listeners.append(lambda:heard.append(1))
        self.assertEqual(store.set(['b','a','b'],True),2);self.assertEqual(store.set(['c','a'],True),1)
        self.assertEqual(store.favorites,['b','a','c']);self.assertTrue(heard)
        store.replace(['c','b','a']);self.assertEqual(Favorites(self.path).favorites,['c','b','a'])
        self.assertTrue(store.toggle('d'));self.assertFalse(store.toggle('b'));self.assertEqual(store.favorites,['c','a','d'])
        self.assertEqual(store.set(['zz'],False),0)
    def test_recent_lists_the_last_added_without_the_favourites(self):
        store=Favorites(self.path);store.set(['x'],True)
        for key in ['a','b','x','c','a']:store.used(key)
        self.assertEqual(store.recent,['a','c','x','b']);self.assertEqual(store.latest(),['a','c','b'])
        self.assertEqual(store.latest(skip_favorites=False),['a','c','x','b']);self.assertEqual(store.latest({'b','c'}),['c','b'])
        for i in range(20):store.used(f'k{i}')
        self.assertEqual(len(store.latest()),fav.RECENT);self.assertLessEqual(len(store.recent),fav.KEEP)
        store.clear_recent();self.assertEqual(Favorites(self.path).recent,[])
    def test_a_damaged_file_is_an_empty_list(self):
        self.path.write_text('{"favorites":[1,"a","a",""],"recent":"x"}',encoding='utf8')
        store=Favorites(self.path);self.assertEqual((store.favorites,store.recent),(['a'],[]))
        self.path.write_text('not json',encoding='utf8');self.assertEqual(Favorites(self.path).favorites,[])
        self.path.write_text('{"folders":[{"name":"  "},{"name":"甲","favorites":["x"]},{"name":"甲","favorites":["y"]},"z"],"current":"乙"}',encoding='utf8')
        store=Favorites(self.path);self.assertEqual(store.folders,{'甲':['x']});self.assertEqual(store.current,'甲')
    def test_a_list_from_before_folders_becomes_the_default_folder(self):
        self.path.write_text('{"schemaVersion":1,"favorites":["b","a"],"recent":["c"]}',encoding='utf8')
        store=Favorites(self.path);self.assertEqual((store.names(),store.current,store.favorites,store.recent),([fav.DEFAULT],fav.DEFAULT,['b','a'],['c']))
        store.save();value=json.loads(self.path.read_text(encoding='utf8'))
        self.assertEqual(value['schemaVersion'],2);self.assertEqual(value['folders'],[{'name':fav.DEFAULT,'favorites':['b','a']}])
        self.assertEqual(value['favorites'],['b','a'])   # an older version of the program still finds the current folder
    def test_folders(self):
        store=Favorites(self.path);store.set(['a'],True);heard=[];store.listeners.append(lambda:heard.append(1))
        self.assertEqual(store.create('  常用   转场 ',['t','u','t']),'常用 转场');self.assertEqual(store.folders['常用 转场'],['t','u'])
        self.assertEqual(store.current,fav.DEFAULT);self.assertTrue(heard)          # making a folder does not switch to it
        for bad in ('','   ','常用 转场','常用 转场'.upper(),'x'*(fav.NAME_LIMIT+1)):
            with self.assertRaises(ValueError,msg=bad):store.create(bad)
        self.assertEqual(store.add('常用 转场',['u','v']),1);self.assertEqual(store.folders['常用 转场'],['t','u','v'])
        self.assertEqual(store.favorites,['a']);self.assertNotIn('t',store)            # the star means the current folder
        self.assertEqual(store.holding('u'),['常用 转场'])
        self.assertTrue(store.select('常用 转场'));self.assertFalse(store.select('常用 转场'));self.assertFalse(store.select('没有'))
        self.assertIn('t',store);self.assertTrue(store.toggle('w'));self.assertEqual(store.favorites,['t','u','v','w'])
        self.assertEqual(store.rename('常用 转场','转场'),'转场');self.assertEqual(store.current,'转场');self.assertEqual(store.names(),[fav.DEFAULT,'转场'])
        with self.assertRaises(ValueError):store.rename('转场',fav.DEFAULT)
        again=Favorites(self.path);self.assertEqual((again.current,again.folders),('转场',{fav.DEFAULT:['a'],'转场':['t','u','v','w']}))
        store.create('乙');store.delete('转场');self.assertEqual(store.current,'乙');self.assertEqual(store.names(),[fav.DEFAULT,'乙'])
        store.delete('乙');store.delete(fav.DEFAULT);self.assertEqual(store.folders,{fav.DEFAULT:[]})   # the last one is only emptied
        with self.assertRaises(ValueError):store.add('没有',['a'])

class EffectsPageTests(GuiCase):
    def star(self,key):return self.table.item(self.rows_for(key)[0],FAVORITE).text()
    def test_star_column_menu_detail_and_tile(self):
        w=self.window;header=self.table.horizontalHeader()
        self.assertEqual(header.visualIndex(app_live.STATUS),1);self.assertEqual(header.visualIndex(FAVORITE),2)
        w.table_clicked(self.rows_for('Atom Key')[0],FAVORITE);self.assertEqual(self.star('Atom Key'),'★');self.assertEqual(w.favorites.favorites,['Atom Key'])
        self.assertEqual(w.chips['favorite'].value,1)
        # Every row of the same effect (two AE folders) shows one state.
        w.toggle_favorite('S_Glow');self.assertEqual({self.table.item(r,FAVORITE).text() for r in self.rows_for('S_Glow')},{'★'})
        self.select(self.rows_for('tc Shine')[0]);self.assertTrue(w.detail_star.isVisibleTo(w));w.detail_star.click();self.assertIn('tc Shine',w.favorites)
        menu=w.row_menu(self.rows_for('tc Shine')[0]);self.assertIn('移出收藏夹“默认收藏夹”',[a.text() for a in menu.actions()])
        self.assertEqual(w.favorites.favorites,['Atom Key','S_Glow','tc Shine']);self.assertEqual(w.nav_buttons[app_live.QUICK].count.text(),'3')
    def test_every_row_shows_a_star_outline_until_favourited(self):
        # Not favourited: an outline at rest (faint) and under the pointer (darker); favourited: the solid star.
        from PySide6.QtGui import QImage,QPainter
        from PySide6.QtWidgets import QStyle,QStyleOptionViewItem
        import ui_kit
        w=self.window;w.toggle_favorite('S_Glow');delegate=self.table.itemDelegateForColumn(FAVORITE);drawn=[]
        def paint(key,hover=False):
            index=self.table.model().index(self.rows_for(key)[0],FAVORITE);option=QStyleOptionViewItem();option.rect=self.table.visualRect(index)
            if hover:option.state|=QStyle.StateFlag.State_MouseOver
            image=QImage(200,200,QImage.Format.Format_ARGB32);painter=QPainter(image)
            with patch.object(ui_kit,'pixmap',side_effect=lambda name,color,size:drawn.append((name,color)) or ui_kit.QPixmap(size,size)):delegate.paint(painter,option,index)
            painter.end();return drawn.pop()
        self.assertEqual(paint('Atom Key'),('star',ui_kit.FAINT));self.assertEqual(paint('Atom Key',True),('star',ui_kit.MUTED))
        self.assertEqual(paint('S_Glow'),('star_fill',ui_kit.INK));self.assertEqual(paint('S_Glow',True),('star_fill',ui_kit.INK))
    def test_check_menu_favourites_the_checked_effects(self):
        w=self.window;w.check_keys({'S_Glow','Atom Key'},True)
        w.favorite_checked_action.trigger();self.assertEqual(w.favorites.favorites,['Atom Key','S_Glow'])
        w.unfavorite_checked_action.trigger();self.assertEqual(w.favorites.favorites,[])

class QuickPageTests(GuiCase):
    def test_page_lists_orders_and_removes(self):
        w=self.window;w.go(app_live.QUICK);self.assertEqual(w.pages.currentIndex(),app_live.QUICK);self.assertTrue(w.nav_buttons[app_live.QUICK].isChecked())
        self.assertEqual(w.fav_stack.currentIndex(),0)
        w.favorites.set(['S_Glow','tc Shine','Atom Key'],True)
        self.assertEqual(w.fav_list.keys(),['S_Glow','tc Shine','Atom Key'])
        w.move_favorites(['Atom Key'],'top');self.assertEqual(w.favorites.favorites,['Atom Key','S_Glow','tc Shine']);self.assertEqual(w.fav_list.keys(),w.favorites.favorites)
        w.move_favorites(['Atom Key'],'down');self.assertEqual(w.favorites.favorites,['S_Glow','Atom Key','tc Shine'])
        w.move_favorites(['S_Glow'],'bottom');self.assertEqual(w.favorites.favorites,['Atom Key','tc Shine','S_Glow'])
        # A drag reports the new order.
        w.fav_list.reordered.emit(['S_Glow','Atom Key','tc Shine']);self.assertEqual(w.favorites.favorites,['S_Glow','Atom Key','tc Shine'])
        # The boxes pick several: the buttons, the select-all box and the menu act on them.
        self.assertFalse(w.fav_remove_btn.isEnabled())
        w.fav_list.set_checked(['tc Shine','Atom Key']);self.assertEqual(w.fav_remove_btn.text(),'移除 2 项');self.assertEqual(w.fav_all.checkState(),Qt.CheckState.PartiallyChecked)
        w.fav_top_btn.click();self.assertEqual(w.favorites.favorites,['Atom Key','tc Shine','S_Glow'])
        w.fav_list.set_checked(['tc Shine'],False);w.remove_favorites(w.selected_favorites());self.assertEqual(w.favorites.favorites,['tc Shine','S_Glow'])
        self.assertEqual(w.fav_list.keys(),['tc Shine','S_Glow'])
        with patch.object(app_live,'pick_effects',return_value=['Atom Key']) as pick:w.add_favorites()
        rows=pick.call_args.args[1];self.assertEqual([r[0] for r in rows],['Atom Key'])   # only effects not yet favourites
        self.assertEqual(w.favorites.favorites,['tc Shine','S_Glow','Atom Key'])
    def test_recent_list_and_switches(self):
        w=self.window;w.favorites.set(['S_Glow'],True)
        for key in ('S_Glow','Atom Key'):w.favorites.used(key)
        self.assertEqual([w.recent_list.item(r).data(256) for r in range(w.recent_list.count())],['Atom Key'])
        w.quick_recent_box.setChecked(False);self.assertEqual(w.recent_list.count(),0);self.assertIs(app_live.ui_prefs()['quickRecent'],False)
        w.quick_fav_box.setChecked(False);self.assertIs(app_live.ui_prefs()['quickFavorites'],False)
        # The parameter switches moved here with their behaviour.
        w.quick_params_box.setChecked(False);self.assertFalse(w.quick_translate_box.isEnabled())
    def test_the_box_gets_favourites_and_recent_only_when_switched_on(self):
        w=self.window;w.favorites.set(['tc Shine'],True);w.favorites.used('Atom Key');w.favorites.used('tc Shine')
        with patch.object(w.quick,'open') as opened:w.open_quick_search(0)
        kwargs=opened.call_args.kwargs;self.assertEqual([e.match_name for e in kwargs['pinned']],['tc Shine']);self.assertEqual([e.match_name for e in kwargs['recent']],['Atom Key'])
        w.quick_fav_box.setChecked(False)
        with patch.object(w.quick,'open') as opened:w.open_quick_search(0)
        kwargs=opened.call_args.kwargs;self.assertEqual(kwargs['pinned'],[]);self.assertEqual([e.match_name for e in kwargs['recent']],['tc Shine','Atom Key'])
        w.quick_recent_box.setChecked(False)
        with patch.object(w.quick,'open') as opened:w.open_quick_search(0)
        self.assertEqual(opened.call_args.kwargs['recent'],[])
    def test_only_quick_search_adds_count_as_used(self):
        w=self.window;entry=w.quick_entries()[0]
        w.open_quick_search(0);self.assertEqual(w.quick.list.count(),0)
        with patch.object(w,'quick_parameters'):w.quick_added(entry,{'changed':1,'skipped':0,'route':'relay'},0.1)
        self.assertEqual(w.favorites.recent,[entry.match_name])
        # The closed box already shows what its next opening lists: the effect just added.
        self.assertFalse(w.quick.isVisible());self.assertEqual([w.quick.list.item(r).text().strip() for r in range(w.quick.list.count())],['最近使用',entry.title])
        with patch.object(w,'quick_parameters'):w.quick_added(entry,'请先选中图层',0.1)   # a failed add is not "used"
        self.assertEqual(w.favorites.recent,[entry.match_name])
        w.favorites.clear_recent();w.quick_recent_box.setChecked(False)
        with patch.object(w,'quick_parameters'):w.quick_added(entry,{'changed':1,'skipped':0,'route':'relay'},0.1)
        self.assertEqual(w.favorites.recent,[])

class ReorderListTests(GuiCase):
    def make(self,keys='abcd'):
        from ui_kit import ReorderList
        view=ReorderList();view.resize(300,400);view.show();self.addCleanup(view.deleteLater)
        view.set_items([(k,k.upper(),'',False,'') for k in keys]);self.app.processEvents();heard=[];view.reordered.connect(heard.append);return view,heard
    def drag(self,view,key,dy):
        from PySide6.QtTest import QTest
        # The row moves under the pointer: each event is placed relative to where the row is now.
        row=view.rows[key];start=QPoint(row.width()-20,view.ROW//2);origin=row.pos()+start;QTest.mousePress(row,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        for step in range(1,6):QTest.mouseMove(row,origin+QPoint(0,dy*step//5)-row.pos())
        QTest.mouseRelease(row,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,origin+QPoint(0,dy)-row.pos())
    def positions(self,view):return [view.rows[k].y() for k in view.keys()]
    def test_dragging_one_row_and_a_checked_block(self):
        view,heard=self.make();R=view.ROW
        self.assertEqual(self.positions(view),[0,R,2*R,3*R])
        self.drag(view,'d',-R);self.assertEqual(heard[-1],['a','b','d','c'])                # one place, not to the top
        self.drag(view,'c',-3*R);self.assertEqual(heard[-1],['c','a','b','d']);self.assertEqual(self.positions(view),[0,R,2*R,3*R])
        self.assertFalse(view.rows['c'].lifted or view.rows['c'].box.isChecked())    # landed; a drag is no click
        view.set_checked(['a','b']);self.drag(view,'a',2*R)                          # the checked rows move as one block
        self.assertEqual(heard[-1],['c','d','a','b'])
    def test_with_motion_rows_slide_and_drag_again_after_settling(self):
        # Offscreen tests run without motion; here it is on, as on a real screen (a finished slide deletes itself).
        import ui_kit
        with patch.object(ui_kit,'motion',lambda:True):
            view,heard=self.make();R=view.ROW
            def settle():
                end=time.time()+.4
                while time.time()<end:self.app.processEvents()
            view.set_items([(k,k,'',False,'') for k in 'dabc']);self.assertNotEqual(view.rows['d'].y(),0)   # still sliding
            settle();self.assertEqual(self.positions(view),[0,R,2*R,3*R])
            self.drag(view,'c',-int(1.4*R));settle();self.assertEqual(heard[-1],list('dacb'));self.assertEqual(self.positions(view),[0,R,2*R,3*R])
            self.drag(view,'d',3*R);settle();self.assertEqual(heard[-1],list('acbd'));self.assertFalse(any(r.lifted for r in view.rows.values()))
    def test_no_row_stays_lifted_when_the_list_is_reloaded_at_once(self):
        # The window saves a dropped order and fills the list again straight away; that used to stop the landing
        # slide midway and leave the dropped rows lifted for good (white card, shadow, dark grip).
        import ui_kit
        with patch.object(ui_kit,'motion',lambda:True):
            view,heard=self.make('abcdef');R=view.ROW
            view.reordered.connect(lambda order:view.set_items([(k,k,'',False,'') for k in order]))
            def settle(seconds=.4):
                end=time.time()+seconds
                while time.time()<end:self.app.processEvents()
            def lifted():return [k for k,r in view.rows.items() if r.lifted]
            self.drag(view,'e',-int(2.4*R));settle();self.assertEqual(lifted(),[]);self.assertEqual(self.positions(view),[i*R for i in range(6)])
            view.set_checked(['a','c','f']);self.drag(view,'c',int(1.6*R));settle();self.assertEqual(lifted(),[])          # a block
            view.set_items([(k,k,'',False,'') for k in reversed(view.keys())]);settle(.05)                                   # a menu move, cut short
            self.drag(view,view.keys()[0],int(2.3*R));settle();self.assertEqual(lifted(),[]);self.assertEqual(self.positions(view),[i*R for i in range(6)])
    def test_sweeping_the_box_column_checks_a_run(self):
        from PySide6.QtTest import QTest
        view,heard=self.make('abcde');R=view.ROW
        def sweep(key,rows):
            row=view.rows[key];start=QPoint(20,R//2);origin=row.pos()+start;QTest.mousePress(row,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
            for step in range(1,5):QTest.mouseMove(row,origin+QPoint(0,rows*R*step//4)-row.pos())
            QTest.mouseRelease(row,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,origin+QPoint(0,rows*R)-row.pos())
        sweep('a',2);self.assertEqual(view.checked_keys(),['a','b','c']);self.assertEqual(heard,[])     # a sweep, not a drag
        sweep('b',3);self.assertEqual(view.checked_keys(),['a'])                                         # starting on a checked box unchecks
        sweep('e',-1);self.assertEqual(view.checked_keys(),['a','d','e'])                                # upwards too
        QTest.mouseClick(view.rows['d'],Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(20,R//2));self.assertEqual(view.checked_keys(),['a','e'])
        self.assertFalse(any(r.lifted for r in view.rows.values()))
        # Pressing on the name and sliding selects too; only the grip at the right drags.
        row=view.rows['b'];start=QPoint(150,R//2);origin=row.pos()+start;QTest.mousePress(row,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        for step in range(1,5):QTest.mouseMove(row,origin+QPoint(0,2*R*step//4)-row.pos())
        QTest.mouseRelease(row,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,origin+QPoint(0,2*R)-row.pos())
        self.assertEqual(view.checked_keys(),['a','b','c','d','e']);self.assertEqual(heard,[]);self.assertEqual(view.keys(),list('abcde'))
        self.assertTrue(view.rows['b'].on_grip(view.rows['b'].width()-10));self.assertFalse(view.rows['b'].on_grip(150))
    def test_a_click_checks_and_a_new_order_slides_in(self):
        view,heard=self.make();from PySide6.QtTest import QTest
        QTest.mouseClick(view.rows['b'],Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(80,10));self.assertEqual(view.checked_keys(),['b']);self.assertEqual(heard,[])
        view.set_items([(k,k,'',False,'') for k in 'dcba']);self.assertEqual(view.keys(),list('dcba'));self.assertEqual(view.rows['d'].y(),0)
        self.assertEqual(view.checked_keys(),['b'])     # boxes stay with their effects
        view.set_hidden({'c'});self.assertFalse(view.draggable);self.assertEqual(view.rows['b'].y(),view.ROW);self.assertEqual(view.visible_keys(),['d','b','a'])
        self.drag(view,'a',-view.ROW*2);self.assertEqual(heard,[])                    # no dragging while rows are hidden
    def test_move_keys(self):
        self.assertEqual(app_live.move_keys(list('abcde'),['b','d'],'up'),list('badce'));self.assertEqual(app_live.move_keys(list('abcde'),['a','b'],'up'),list('abcde'))
        self.assertEqual(app_live.move_keys(list('abcde'),['b','d'],'bottom'),list('acebd'));self.assertEqual(app_live.move_keys(list('abcde'),['d'],'down'),list('abced'))

class BoxTests(GuiCase):
    def settle(self):
        end=time.time()+.3
        while time.time()<end:self.app.processEvents()
    entries=[Entry(f'm{i}',f'Blur {i}',f'模糊{i}','') for i in range(12)]
    def test_favourites_then_recent_before_typing(self):
        box=qs.QuickSearch();e=self.entries
        box.open(e,0,QPoint(50,50),pinned=[e[3],e[1]],recent=[e[5]]);self.settle()
        texts=[box.list.item(r).text().strip() for r in range(box.list.count())]
        self.assertEqual(texts,['收藏','模糊3','模糊1','最近使用','模糊5']);self.assertEqual(box.list.currentRow(),1)
        self.assertEqual(box.list.height(),2*qs.HEAD+3*qs.ROW+4)
        # Captions really are shorter (uniform row sizes would make them full rows and cut off the last effect).
        self.assertEqual([box.list.visualItemRect(box.list.item(r)).height() for r in (0,1)],[qs.HEAD,qs.ROW]);self.assertFalse(box.list.verticalScrollBar().maximum())
        for expected in (2,4,1,2):box.eventFilter(box.edit,key_event(Qt.Key.Key_Down));self.assertEqual(box.list.currentRow(),expected)   # captions are skipped
        box.eventFilter(box.edit,key_event(Qt.Key.Key_Up));self.assertEqual(box.list.currentRow(),1)
        with patch.object(box,'chosen') as chosen:box.choose(0);chosen.emit.assert_not_called()      # a caption adds nothing
        box.edit.setText('模糊1');self.assertNotIn('收藏',[box.list.item(r).text().strip() for r in range(box.list.count())])
        box.edit.clear();self.assertEqual(box.list.item(0).text().strip(),'收藏')
        box.close()
    def test_closed_box_rests_as_the_next_opening_starts(self):
        # Windows may show a closed see-through box's last picture for the first frame of the next opening; measured on
        # a dark box, resting compact before an opening that lists favourites put up one frame of the compact box.
        box=qs.QuickSearch();e=self.entries;upcoming=[(e[:3],[])];box.upcoming=lambda:upcoming[0]
        box.open(e,0,QPoint(50,50));self.settle();compact=box.height()
        box.open(e,0,QPoint(50,50),pinned=e[:3]);self.settle();tall=box.height();self.assertGreater(tall,compact)
        box.edit.setText('模糊');self.settle();box.hide();self.settle()
        self.assertEqual(box.edit.text(),'');self.assertEqual(box.list.count(),4);self.assertEqual(box.height(),tall)   # nothing typed, the favourites again
        upcoming[0]=([],[]);box.open(e,0,QPoint(50,50));box.dismiss();self.settle();self.assertEqual(box.height(),compact)
        seen=[]
        class Spy(QObject):
            def eventFilter(self,obj,event):
                if obj is box and event.type() in (QEvent.Type.Show,QEvent.Type.Resize):seen.append((event.type(),box.height()))
                return False
        spy=Spy();box.installEventFilter(spy);box.open(e,0,QPoint(50,50),pinned=e[:3]);self.settle()
        shown=[i for i,(kind,_) in enumerate(seen) if kind==QEvent.Type.Show]
        self.assertEqual(seen[shown[0]][1],tall);self.assertEqual([h for kind,h in seen[shown[0]:] if kind==QEvent.Type.Resize],[])
        box.close()

class SchemeTests(GuiCase):
    def save(self,title):
        with patch.object(app_live,'ask_text',return_value=(title,True)):self.window.save_scheme_as()
        return next(s for s in self.window.schemes if s.title==title)
    def test_format_round_trip_and_bad_part(self):
        path=Path(self.temp.name)/'s.json';app_live.write_json(path,app_live.dump_scheme({},set(),'甲',favorites=['b','a','b']))
        self.assertEqual(app_live.open_scheme(path).favorites,['b','a'])
        app_live.write_json(path,app_live.dump_scheme({},set(),'乙'));self.assertIsNone(app_live.open_scheme(path).favorites)
        value=json.loads(path.read_text(encoding='utf8'));value['favorites']='a';path.write_text(json.dumps(value),encoding='utf8')
        with self.assertRaises(ValueError):app_live.open_scheme(path)
    def test_schemes_leave_the_favourites_alone(self):
        # The user's choice: favourites live in folders of their own; saving, loading and switching schemes do not touch them.
        w=self.window;w.favorites.set(['Atom Key','S_Glow'],True);first=self.save('甲')
        self.assertIsNone(first.favorites);self.assertNotIn('收藏',app_live.scheme_counts(first))
        w.favorites.replace(['tc Shine']);self.assertFalse(w.scheme_dirty())
        with patch.object(app_live,'confirm',return_value=True):w.choose_scheme(None);w.switch_to(first)
        self.assertEqual(w.favorites.favorites,['tc Shine'])
        self.assertNotIn('收藏',app_live.describe_changes(w.changes_for(first)))
        self.assertEqual(len(w.preview_group.buttons()),2)   # the scheme page shows effects and parameters only
    def test_favourites_an_older_scheme_saved_are_kept_and_become_a_folder(self):
        w=self.window;path=w.scheme_folder/'旧.json'
        app_live.write_json(path,app_live.dump_scheme({'S_Glow':'辉光'},{'S_Glow'},'旧',favorites=['Atom Key','S_Glow']));w.refresh_schemes(None)
        old=next(s for s in w.schemes if s.title=='旧');w.favorites.set(['tc Shine'],True)
        with patch.object(app_live,'confirm',return_value=True):w.switch_to(old)
        self.assertEqual(w.favorites.favorites,['tc Shine'])                      # loading does not replace them
        # Editing, overwriting, renaming and copying keep them in the file.
        w.preview_path=old.path;w.edit_scheme(lambda names,enabled:enabled.add('Atom Key'),{'Atom Key'},'勾选')
        w.write_scheme(old.path,'旧','已保存');self.assertEqual(app_live.open_scheme(path).favorites,['Atom Key','S_Glow'])
        w.duplicate_scheme(next(s for s in w.schemes if s.title=='旧'));self.assertEqual(next(s for s in w.schemes if s.title=='旧 副本').favorites,['Atom Key','S_Glow'])
        w.preview_path=path;menu=w.list_menu();texts=[a.text() for a in menu.actions()]
        self.assertIn('把此方案里的收藏存为收藏夹（2 个）…',texts)
        with patch.object(app_live,'ask_text',side_effect=lambda *a:(a[3],True)) as asked:
            next(a for a in menu.actions() if a.text().startswith('把此方案里的收藏')).trigger()
        self.assertEqual(asked.call_args.args[3],'旧');self.assertEqual(w.favorites.folders['旧'],['Atom Key','S_Glow']);self.assertEqual(w.favorites.current,fav.DEFAULT)
        w.preview_path=path;menu=w.list_menu()
        with patch.object(app_live,'ask_text',side_effect=lambda *a:(a[3],True)) as asked:
            next(a for a in menu.actions() if a.text().startswith('把此方案里的收藏')).trigger()
        self.assertEqual(asked.call_args.args[3],'旧 2')                         # the name is taken: a free one is proposed
        self.save('新');w.preview_path=next(s for s in w.schemes if s.title=='新').path
        self.assertFalse([a for a in w.list_menu().actions() if '收藏' in a.text()])   # a scheme without them offers nothing

class FolderTests(GuiCase):
    def test_check_menu_puts_the_checked_effects_into_a_folder(self):
        w=self.window;w.check_keys({'S_Glow','tc Shine'},True);w.favorites.create('转场',['tc Shine'])
        menu=w.fill_folder_menu(w.folder_checked_menu,w.checked_keys());texts=[a.text() for a in menu.actions()]
        self.assertEqual(texts[0],f'{fav.DEFAULT}（当前）');self.assertTrue(texts[1].startswith('转场'));self.assertEqual(texts[-1],'新建收藏夹…')
        self.assertIn(w.folder_checked_menu,[a.menu() for a in w.check_tool.menu().actions()])
        menu.actions()[1].trigger();self.assertEqual(w.favorites.folders['转场'],['tc Shine','S_Glow']);self.assertEqual(w.favorites.favorites,[])
        self.assertFalse(w.fill_folder_menu(w.folder_checked_menu,w.checked_keys()).actions()[1].isEnabled())   # both already in it
        with patch.object(app_live,'ask_text',return_value=('  我的 常用 ',True)) as asked:w.put_in_folder(w.checked_keys(),None)
        self.assertEqual(asked.call_args.args[3],'收藏夹 1');self.assertEqual(w.favorites.folders['我的 常用'],['S_Glow','tc Shine'])
        with patch.object(app_live,'ask_text',return_value=('转场',True)),patch.object(w,'error') as error:w.put_in_folder(['Atom Key'],None)
        self.assertIn('已经有名为“转场”',error.call_args.args[0])
        empty=QMenu();self.assertEqual(w.fill_folder_menu(empty,[]).actions()[0].text(),'先勾选效果')
        # The row menu offers the same.
        row=w.row_menu(self.rows_for('Atom Key')[0]);sub=next(a.menu() for a in row.actions() if a.text()=='加入收藏夹')
        self.assertEqual(sub.actions()[-1].text(),'新建收藏夹…')
    def test_the_quick_page_switches_renames_and_deletes_folders(self):
        w=self.window;w.go(app_live.QUICK);w.favorites.set(['S_Glow'],True);w.favorites.create('转场',['tc Shine','Atom Key'])
        self.assertEqual([w.folder_box.itemText(i) for i in range(w.folder_box.count())],[fav.DEFAULT,'转场']);self.assertEqual(w.fav_list.keys(),['S_Glow'])
        w.choose_folder(1);self.assertEqual(w.favorites.current,'转场');self.assertEqual(w.fav_list.keys(),['tc Shine','Atom Key'])
        self.assertEqual(w.folder_box.currentText(),'转场');self.assertEqual(self.table.item(self.rows_for('S_Glow')[0],FAVORITE).text(),'')
        self.assertEqual(self.table.item(self.rows_for('tc Shine')[0],FAVORITE).text(),'★');self.assertEqual(w.chips['favorite'].value,2)
        self.assertEqual(w.favorite_checked_action.text(),'收藏勾选项到“转场”')
        # Quick search lists the current folder, under its name.
        with patch.object(w.quick,'open') as opened:w.open_quick_search(0)
        self.assertEqual([e.match_name for e in opened.call_args.kwargs['pinned']],['tc Shine','Atom Key']);self.assertEqual(w.quick.pinned_caption,'转场')
        w.folder_menu();texts=[a.text() for a in w.folder_more.menu().actions()];self.assertEqual(texts,['新建收藏夹…','重命名“转场”…','','删除“转场”…'])
        with patch.object(app_live,'ask_text',return_value=('常用转场',True)):w.rename_folder()
        self.assertEqual(w.favorites.current,'常用转场');self.assertEqual(w.folder_box.currentText(),'常用转场')
        with patch.object(app_live,'ask_text',return_value=('空的',True)):w.new_folder()
        self.assertEqual(w.favorites.current,'空的');self.assertEqual(w.fav_stack.currentIndex(),0)
        with patch.object(app_live,'confirm',return_value=True):w.delete_folder()
        self.assertEqual(w.favorites.names(),[fav.DEFAULT,'常用转场']);self.assertEqual(w.favorites.current,'常用转场')
        with patch.object(app_live,'confirm',return_value=False):w.delete_folder()
        self.assertIn('常用转场',w.favorites.names())
        w.favorites.delete(fav.DEFAULT);w.folder_menu();self.assertEqual(w.folder_more.menu().actions()[-1].text(),'清空“常用转场”…')
        with patch.object(app_live,'confirm',return_value=True):w.delete_folder()
        self.assertEqual(w.favorites.folders,{'常用转场':[]})
        # A favourite's row menu can put it into another folder too.
        w.favorites.create('乙');w.favorites.set(['S_Glow'],True);w.fill_quick_lists()
        menu=w.favorite_row_menu('S_Glow');sub=next(a.menu() for a in menu.actions() if a.text()=='也加入收藏夹')
        next(a for a in sub.actions() if a.text().startswith('乙')).trigger();self.assertEqual(w.favorites.folders['乙'],['S_Glow'])
        self.assertEqual(w.favorites.current,'常用转场')

if __name__=='__main__':unittest.main()
