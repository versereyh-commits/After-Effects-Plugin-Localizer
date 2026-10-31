"""Synchronize native Effect-menu captions after explicit palette refresh.

No commands are invoked, no hooks/timers, and no command IDs or item states change.
The tested AE 2026 right-click Effect tree reflects these existing submenu captions too.
"""
from pathlib import Path
import ctypes as C,json,re,time
from ctypes import wintypes as W
from live_names import current_process,Registry,NameSession,_lock,_process_lock,_save
from effect_categories import CategorySession,supported
from runtime import data_root
from ae_ui import main_window

class MenuMatchError(RuntimeError):
    def __init__(self,source,reason):
        self.reason=reason
        super().__init__('菜单文字不能唯一对应效果或分类：'+source)

class Item(C.Structure):
    _fields_=[('cbSize',W.UINT),('fMask',W.UINT),('fType',W.UINT),('fState',W.UINT),('wID',W.UINT),('hSubMenu',W.HMENU),('hbmpChecked',W.HBITMAP),('hbmpUnchecked',W.HBITMAP),('dwItemData',C.c_size_t),('dwTypeData',W.LPWSTR),('cch',W.UINT),('hbmpItem',W.HBITMAP)]

class Menus:
    def __init__(self,hwnd):
        self.hwnd=hwnd;self.u=C.WinDLL('user32',use_last_error=True)
        for name,args,result in [('GetMenu',[W.HWND],W.HMENU),('GetSubMenu',[W.HMENU,C.c_int],W.HMENU),('GetMenuItemCount',[W.HMENU],C.c_int),('GetMenuItemInfoW',[W.HMENU,W.UINT,W.BOOL,C.POINTER(Item)],W.BOOL),('SetMenuItemInfoW',[W.HMENU,W.UINT,W.BOOL,C.POINTER(Item)],W.BOOL)]:
            f=getattr(self.u,name);f.argtypes=args;f.restype=result
        self.root=self.u.GetMenu(hwnd)
        if not self.root:raise RuntimeError('AE 没有可读取的原生菜单')
    def get(self,menu,index,path):
        buf=C.create_unicode_buffer(4096);v=Item();v.cbSize=C.sizeof(v);v.fMask=0x147;v.dwTypeData=C.cast(buf,W.LPWSTR);v.cch=len(buf)
        if not self.u.GetMenuItemInfoW(menu,index,True,C.byref(v)):raise RuntimeError('菜单条目已变化或无法读取')
        if v.cch>=len(buf)-1:raise RuntimeError('菜单文字超出读取上限')
        return dict(menu=menu,index=index,path=path,text=buf.value,id=int(v.wID),submenu=v.hSubMenu or 0,type=int(v.fType))
    def count(self,menu):
        n=self.u.GetMenuItemCount(menu)
        if not 0<=n<=8192:raise RuntimeError('菜单条目数不符')
        return n
    def snapshot(self):
        if self.u.GetMenu(self.hwnd)!=self.root:raise RuntimeError('AE 菜单已重建，请重试')
        roots=[]
        for i in range(self.count(self.root)):
            r=self.get(self.root,i,[i]);title=re.sub(r'\(&.\)|&','',r['text'])
            if title in ('Effect','Effects','效果') and r['submenu']:roots.append(r)
        if len(roots)!=1:raise RuntimeError('未找到唯一的原生效果菜单')
        out=[];seen=set()
        def walk(menu,path):
            if len(path)>6 or menu in seen:raise RuntimeError('菜单结构不唯一或过深')
            seen.add(menu)
            for i in range(self.count(menu)):
                r=self.get(menu,i,path+[i]);out.append(r)
                if len(out)>16384:raise RuntimeError('菜单超过上限')
                if r['submenu']:walk(r['submenu'],r['path'])
        walk(roots[0]['submenu'],roots[0]['path']);return out
    def at(self,path):
        if self.u.GetMenu(self.hwnd)!=self.root:raise RuntimeError('AE 菜单已重建')
        menu=self.root;r=None
        for j,index in enumerate(path):
            if type(index) is not int or not 0<=index<self.count(menu):raise RuntimeError('菜单位置已变化')
            r=self.get(menu,index,path[:j+1])
            if j<len(path)-1:
                if not r['submenu']:raise RuntimeError('菜单层级已变化')
                menu=r['submenu']
        return r
    def put(self,row,value):
        now=self.at(row['path'])
        if any(now[k]!=row[k] for k in ('id','submenu','text','type')) or now['type']&0x900:raise RuntimeError('菜单目标已变化或为自绘菜单')
        if len(value)>1024 or any(c in value for c in '\0\r\n'):raise ValueError('菜单译名格式无效')
        buf=C.create_unicode_buffer(value);v=Item();v.cbSize=C.sizeof(v);v.fMask=0x40;v.dwTypeData=C.cast(buf,W.LPWSTR);v.cch=len(value)
        if getattr(self,'deadline',None) is not None and time.monotonic()>=self.deadline:raise TimeoutError('效果菜单同步超时，未继续修改')
        if not self.u.SetMenuItemInfoW(now['menu'],now['index'],True,C.byref(v)):raise RuntimeError('AE 菜单文字写入失败')
        after=self.at(row['path'])
        if after['text']!=value or any(after[k]!=row[k] for k in ('id','submenu','type')):raise RuntimeError('AE 菜单文字回读不符')

def choose(rows,entry,source,current,branch=False):
    """Never associate effects by a guessed command number or a non-unique label."""
    known={source,source.replace('&','&&'),current,current.replace('&','&&')}
    if entry:known.update((entry['originalText'].split('\t')[0],entry['appliedText'].split('\t')[0]))
    # Root commands include "repeat last effect", whose caption can equal an effect's name.
    # Real effect leaves live below a category; category headers are immediate children.
    matches=[r for r in rows if bool(r['submenu'])==branch and (len(r['path'])==2 if branch else len(r['path'])>=3) and r['text'].split('\t')[0] in known and not r['type']&0x900]
    if entry:
        exact=[r for r in matches if r['path']==entry['path'] and r['id']==entry['id']]
        if len(exact)==1:return exact[0]
    if len(matches)!=1:raise MenuMatchError(source,'not_in_menu' if not matches else 'ambiguous_menu')
    return matches[0]

class MenuNameSession:
    def __init__(self,names=None,categories=None):
        self.path=data_root()/'effect-menu-session.json';self.names=names or NameSession();self.categories=categories or CategorySession()
    def journal(self,p):
        try:j=json.loads(self.path.read_text(encoding='utf8'))
        except FileNotFoundError:return {'kind':'effect-menu-captions','entries':{}}
        except ValueError as e:raise RuntimeError('菜单恢复记录损坏，已保留') from e
        try:
            if j['kind']!='effect-menu-captions' or not isinstance(j['entries'],dict):raise ValueError()
            for e in j['entries'].values():
                if e['kind'] not in ('effect','category') or not isinstance(e['key'],str) or not isinstance(e['source'],str) or not isinstance(e['originalText'],str) or not isinstance(e['appliedText'],str) or not isinstance(e['path'],list) or not 1<=len(e['path'])<=6 or any(type(i) is not int or not 0<=i<8192 for i in e['path']) or type(e['id']) is not int:raise ValueError()
                if not isinstance(e.get('members',[]),list) or not all(isinstance(k,str) for k in e.get('members',[])):raise ValueError()
            if j.get('pid')!=p.pid or j.get('creationTime')!=p.creation_time:return {'kind':'effect-menu-captions','entries':{}}
            if Path(j['image'])!=Path(p.image):raise ValueError()
            return j
        except (KeyError,TypeError,ValueError) as e:raise RuntimeError('菜单恢复记录格式不符，已保留') from e
    def sync(self,pid,creation_time,image,*,deadline=None):
        deadline=time.monotonic()+5 if deadline is None else deadline
        def check():
            if time.monotonic()>=deadline:raise TimeoutError('效果菜单同步超时，未继续修改')
        check()
        with _lock,_process_lock(),current_process(expected_image=image) as p:
            if (p.pid,p.creation_time)!=(pid,creation_time):raise RuntimeError('菜单同步宿主已变化')
            registry=Registry(p)
            if not supported(registry.profile):return {'updated':0,'supported':False}
            effects=registry.records();catalog={r['matchName']:r for r in effects}
            if len(catalog)!=len(effects):raise RuntimeError('效果标识不唯一，菜单同步已停止')
            hwnd=main_window(p.pid);menus=Menus(hwnd);menus.deadline=deadline;rows=menus.snapshot();j=self.journal(p);wanted={};check()
            names=self.names.journal();active_names={}
            if (names.get('pid'),names.get('creationTime'))==(p.pid,p.creation_time):
                active_names=names['entries']
                for key,e in active_names.items():wanted['effect:'+key]=dict(kind='effect',key=key,source=e['originalName'],members=[])
            categories=self.categories.journal()
            if (categories.get('pid'),categories.get('creationTime'))==(p.pid,p.creation_time):
                for key,e in categories['entries'].items():
                    token='category:'+e['originalName'];w=wanted.setdefault(token,dict(kind='category',key=e['originalName'],source=e['originalName'],members=[]));w['members'].append(key)
            for token,e in j['entries'].items():wanted.setdefault(token,dict(kind=e['kind'],key=e['key'],source=e['source'],members=e.get('members',[])))
            # Some AE/Premiere aliases share one visible item. Only a consensus display
            # name across all loaded aliases is safe; never guess which internal ID owns it.
            historical={e['key']:e['source'] for e in j['entries'].values() if e['kind']=='effect'}
            aliases={}
            for key,row in catalog.items():
                source=active_names.get(key,{}).get('originalName',historical.get(key,row['displayName']))
                aliases.setdefault(source,set()).add(row['displayName'])
            staged=[];skipped=[];planned={};matched_targets=set()
            def skip(token,w,reason):skipped.append(dict(kind=w['kind'],key=w['key'],source=w['source'],reason=reason))
            for token,w in wanted.items():
                check()
                entry=j['entries'].get(token)
                if w['kind']=='effect':
                    if w['key'] not in catalog:skip(token,w,'effect_not_loaded');continue
                    if len(aliases.get(w['source'],set()))>1:skip(token,w,'alias_translation_conflict');continue
                    desired=catalog[w['key']]['displayName']
                else:
                    values={catalog[k]['category'] for k in w['members'] if k in catalog}
                    if len(values)!=1:skip(token,w,'category_changed');continue
                    desired=values.pop()
                try:row=choose(rows,entry,w['source'],desired,w['kind']=='category')
                except MenuMatchError as error:skip(token,w,error.reason);continue
                matched_targets.add((tuple(row['path']),row['id']))
                suffix=('\t'+row['text'].split('\t',1)[1]) if '\t' in row['text'] else ''
                new=desired.replace('&','&&')+suffix
                if row['text']==new and not entry and desired==w['source']:continue
                m=dict(kind=w['kind'],key=w['key'],source=w['source'],members=w['members'],path=row['path'],id=row['id'],originalText=entry['originalText'] if entry else w['source'].replace('&','&&')+suffix,appliedText=new)
                planned[token]=m;staged.append((token,row,new,desired==w['source']))
            targets={}
            for token,row,new,restored in staged:targets.setdefault((tuple(row['path']),row['id']),set()).add(new)
            valid=[]
            for token,row,new,restored in staged:
                if len(targets[(tuple(row['path']),row['id'])])>1:
                    skip(token,wanted[token],'menu_target_conflict');matched_targets.discard((tuple(row['path']),row['id']))
                else:valid.append((token,row,new,restored));j['entries'][token]=planned[token]
            staged=valid
            result={'updated':0,'supported':True,'matched':len(wanted)-len(skipped),'visibleMatched':len(matched_targets),'skipped':skipped}
            if not staged:return result
            j.update(pid=p.pid,creationTime=p.creation_time,image=p.image);_save(self.path,j);count=0
            written=set()
            for token,row,new,restored in staged:
                check()
                if main_window(p.pid)!=hwnd:raise RuntimeError('AE 主窗口已变化')
                target=(tuple(row['path']),row['id'])
                if target not in written and row['text']!=new:menus.put(row,new);count+=1
                written.add(target)
                if restored:del j['entries'][token]
            _save(self.path,j);result['updated']=count;return result
