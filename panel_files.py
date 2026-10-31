"""Bounded, recoverable file transactions for panel localisation; never touches an AE project."""
from pathlib import Path
import hashlib,json,os,tempfile,uuid
from datetime import datetime,timezone
from runtime import write_json_atomic

def digest(raw):return hashlib.sha256(raw).hexdigest()
def utc_stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')

def inside(root,relative):
    root=Path(root).absolute();relative=Path(relative)
    if relative.is_absolute() or '..' in relative.parts:raise ValueError('文件路径越出目标目录')
    path=root/relative
    if not path.resolve().is_relative_to(root.resolve()):raise ValueError('文件路径越出目标目录')
    for p in (root,*[root.joinpath(*relative.parts[:i]) for i in range(1,len(relative.parts)+1)]):
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ValueError('不处理链接或目录联接')
    return path

def atomic_bytes(path,raw):
    path=Path(path);name=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.ael-',delete=False) as f:
            name=f.name;f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(name,path);name=None
    finally:
        if name:
            try:os.unlink(name)
            except OSError:pass

def document(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:return default
    except (ValueError,UnicodeError):raise ValueError('记录损坏，已保留原文件，未覆盖：'+Path(path).name) from None

class Files:
    def __init__(self,root,folder,binding,label=''):
        self.root=Path(root);self.folder=Path(folder);self.binding=binding;self.record=self.folder/'transaction.json';self.label=label
    def state(self):
        r=document(self.record)
        if r is None:return None
        if not isinstance(r,dict) or r.get('status') not in ('prepared','applied','restored') or not isinstance(r.get('files'),list):raise ValueError('恢复记录身份或格式不符')
        if r.get('binding')!=self.binding:
            if r['status']=='restored':return None
            raise ValueError('恢复记录身份或格式不符')
        for x in r['files']:
            if not isinstance(x,dict) or not all(k in x for k in ('path','before','after','backup')):raise ValueError('恢复文件记录格式不符')
            inside(self.root,x['path'])
            if x['backup'] and (Path(x['backup']).name!=x['backup'] or not x['backup'].endswith('.bin')):raise ValueError('备份路径无效')
        return r
    def _current(self,x):
        p=inside(self.root,x['path']);return digest(p.read_bytes()) if p.exists() else None
    def baseline(self,relative):
        r=self.state()
        if r and r['status']=='prepared':raise ValueError('有未完成文件事务，请先恢复原文')
        if r and r['status']=='applied':
            x=next((x for x in r['files'] if x['path']==relative),None)
            if x:
                if self._current(x)!=x['after']:raise ValueError('文件已被外部修改，请先处理冲突：'+relative)
                return self._original(x)
        p=inside(self.root,relative);return p.read_bytes() if p.exists() else None
    def _original(self,x):
        if x['before'] is None:return None
        raw=(self.folder/x['backup']).read_bytes()
        if digest(raw)!=x['before']:raise ValueError('备份哈希不符，未执行恢复')
        return raw
    def apply(self,changes,cancelled=lambda:False):
        if cancelled():raise RuntimeError('已取消，未开始文件写入')
        old=self.state()
        if old and old['status']=='prepared':raise ValueError('有未完成文件事务，请先恢复原文')
        # Updating a dictionary cannot drop an earlier patched page from its recovery set.
        if old and old['status']=='applied' and not {x['path'] for x in old['files']}<=set(changes):raise ValueError('目标页面集合变化，请先恢复原文')
        entries=[];current={}
        self.folder.mkdir(parents=True,exist_ok=True)
        for rel,raw in changes.items():
            p=inside(self.root,rel);before=self.baseline(rel);now=p.read_bytes() if p.exists() else None
            current[rel]=now;backup=uuid.uuid4().hex+'.bin' if before is not None else None
            if backup:atomic_bytes(self.folder/backup,before)
            entries.append(dict(path=rel,before=digest(before) if before is not None else None,after=digest(raw),previous=digest(now) if now is not None else None,backup=backup))
        if cancelled():raise RuntimeError('已取消，未开始文件写入')
        record=dict(binding=self.binding,status='prepared',files=entries,targetName=self.label,createdAt=utc_stamp(),updatedAt=utc_stamp())
        previous=document(self.record)
        if previous:write_json_atomic(self.folder/('history-'+uuid.uuid4().hex+'.json'),previous)
        write_json_atomic(self.record,record) # recovery is durable before the first target write
        attempted=[]
        try:
            for x in entries:
                p=inside(self.root,x['path']);now=p.read_bytes() if p.exists() else None
                if now!=current[x['path']]:raise ValueError('写入前文件发生变化：'+x['path'])
                attempted.append(x);atomic_bytes(p,changes[x['path']])
                if self._current(x)!=x['after']:raise RuntimeError('写入回读不符')
            record['status']='applied';record['updatedAt']=utc_stamp();write_json_atomic(self.record,record)
        except BaseException:
            failed=False
            for x in reversed(attempted):
                try:
                    # Only undo our own bytes. Never overwrite a concurrent external edit.
                    now=self._current(x)
                    if now==x['after']:
                        p=inside(self.root,x['path']);raw=current[x['path']]
                        if raw is None:p.unlink()
                        else:atomic_bytes(p,raw)
                    elif now!=(digest(current[x['path']]) if current[x['path']] is not None else None):failed=True
                except OSError:failed=True
            if not failed:
                if old:write_json_atomic(self.record,old)
                else:record['status']='restored';write_json_atomic(self.record,record)
            raise
        return len(entries)
    def restore(self):
        r=self.state()
        if not r or r['status']=='restored':return 0
        # Preflight every file and backup before restoring even one file.
        originals={x['path']:self._original(x) for x in r['files']}
        for x in r['files']:
            if self._current(x) not in (x['before'],x['after'],x.get('previous',x['before'])):raise ValueError('文件已更新或被外部修改，未覆盖：'+x['path'])
        for x in r['files']:
            if self._current(x)==x['before']:continue
            if self._current(x) not in (x['after'],x.get('previous',x['before'])):raise ValueError('恢复前文件发生变化')
            p=inside(self.root,x['path']);raw=originals[x['path']]
            if raw is None:p.unlink()
            else:atomic_bytes(p,raw)
            if self._current(x)!=x['before']:raise RuntimeError('恢复回读不符，记录已保留')
        write_json_atomic(self.folder/('history-'+uuid.uuid4().hex+'.json'),r)
        r['status']='restored';r['restoredAt']=utc_stamp();r['updatedAt']=utc_stamp();write_json_atomic(self.record,r);return len(r['files'])
