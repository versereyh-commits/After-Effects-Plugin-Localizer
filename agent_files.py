"""File access for the assistant's highest permission level. Every overwrite or removal first copies the old content to
assistant-backups, and every change is logged, so what it did can be seen and put back. Deleting moves to the backup
folder instead of erasing. The saved API key file stays closed even here: nothing needs it and it must not reach a model."""
from __future__ import annotations
import json,os,shutil,sys,time,uuid
from pathlib import Path
from runtime import data_root

MAX_READ=200000;MAX_WRITE=2000000
MAX_FILE_BYTES=4*1024*1024
MAX_BACKUP_BYTES=256*1024*1024;MAX_BACKUP_FILES=10000
def backups()->Path:return data_root()/'assistant-backups'
def closed_paths()->list[Path]:return [data_root()/'api-settings.json']
def program_paths()->list[Path]:
    """Where the program itself lives. Changing it goes through patches only, which are tried, undoable and guarded at start; a file written here directly would be none of those."""
    places=[data_root()/'patches']
    if getattr(sys,'frozen',False):places.append(Path(sys.executable).parent)
    return places
def checked(path:str,*,mutation=False)->Path:
    if not isinstance(path,str) or not path.strip():raise ValueError('请给出文件路径')
    target=Path(os.path.expandvars(os.path.expanduser(path.strip())))
    if not target.is_absolute():raise ValueError('请使用完整路径')
    resolved=target.resolve()
    for p in closed_paths():
        protected=p.resolve()
        same=resolved==protected
        if resolved.is_file() and protected.is_file():same=same or resolved.samefile(protected)
        if same or (mutation and protected.is_relative_to(resolved)):
            raise PermissionError('保存 API 密钥的文件及其上级目录不开放修改')
    if mutation and resolved.is_relative_to(backups().resolve()):raise PermissionError('不能修改助手恢复备份')
    if mutation:
        for place in program_paths():
            place=place.resolve()
            if resolved.is_relative_to(place) or place.is_relative_to(resolved):raise PermissionError('程序文件和补丁目录不能直接修改；要改程序请用 patch_module，这样改错了能自动撤销')
    return resolved

def bounded_bytes(path:Path)->bytes:
    with path.open('rb') as stream:raw=stream.read(MAX_FILE_BYTES+1)
    if len(raw)>MAX_FILE_BYTES:raise ValueError('文件超过 4 MB，请先选择较小的文字文件')
    return raw

def check_tree(path:Path):
    """Refuse recursive copies of links, protected files, or unbounded directory trees."""
    count=total=0
    for folder,dirs,files in os.walk(path,followlinks=False):
        for name in dirs+files:
            count+=1
            if count>MAX_BACKUP_FILES:raise ValueError('目录超过备份上限，未操作')
            p=Path(folder)/name
            if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise PermissionError('目录包含链接或联接，未操作')
            checked(str(p),mutation=True)
            if name in files:
                total+=p.stat().st_size
                if total>MAX_BACKUP_BYTES:raise ValueError('目录超过备份上限，未操作')
def decode(raw:bytes)->tuple[str,str]:
    if b'\0' in raw[:4096]:raise ValueError('这是二进制文件，不能按文字读取')
    for encoding in (('utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf8'),'gb18030'):
        try:return raw.decode(encoding),encoding
        except UnicodeError:pass
    raise ValueError('无法识别文件编码')
def log_change(action:str,path:Path,backup:Path|None=None):
    backups().mkdir(parents=True,exist_ok=True)
    with (backups()/'changes.jsonl').open('a',encoding='utf8') as stream:
        stream.write(json.dumps({'time':time.strftime('%Y-%m-%d %H:%M:%S'),'action':action,'path':str(path),'backup':str(backup) if backup else None},ensure_ascii=False)+'\n')
def backup(path:Path)->Path|None:
    """A copy of what is about to change, keeping the original path readable under a time-stamped folder."""
    if not path.exists():return None
    path=checked(str(path),mutation=True)
    if backups().resolve().is_relative_to(path):raise PermissionError('不能备份包含备份目录的上级目录')
    if path.is_dir():check_tree(path)
    elif path.stat().st_size>MAX_BACKUP_BYTES:raise ValueError('文件超过备份上限，未操作')
    folder=backups()/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]);target=folder/str(path).replace(':','').lstrip('\\/')
    target.parent.mkdir(parents=True,exist_ok=True)
    if path.is_dir():shutil.copytree(path,target)
    else:shutil.copy2(path,target)
    return target

def list_dir(path:str,limit:int=200)->dict:
    folder=checked(path);items=[]
    if not folder.is_dir():raise ValueError('不是文件夹')
    entries=sorted(folder.iterdir(),key=lambda p:(p.is_file(),p.name.casefold()))
    for entry in entries[:max(1,min(limit,200))]:
        try:items.append({'name':entry.name,'dir':entry.is_dir(),'bytes':entry.stat().st_size if entry.is_file() else None})
        except OSError:items.append({'name':entry.name,'error':'无法访问'})
    return {'path':str(folder),'total':len(entries),'items':items}
def read_file(path:str,offset:int=0,limit:int=MAX_READ)->dict:
    target=checked(path);text,encoding=decode(bounded_bytes(target));limit=max(1,min(limit,MAX_READ));offset=max(0,offset)
    return {'path':str(target),'encoding':encoding,'length':len(text),'offset':offset,'text':text[offset:offset+limit]}
def write_file(path:str,content:str)->dict:
    target=checked(path,mutation=True)
    if target.is_dir():raise ValueError('这是文件夹')
    data=content.encode('utf8')
    if len(data)>MAX_WRITE:raise ValueError('内容过大')
    saved=backup(target);target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_name(target.name+f'.tmp{os.getpid()}')
    try:temp.write_bytes(data);os.replace(temp,target)
    finally:temp.unlink(missing_ok=True)
    log_change('write',target,saved);return {'path':str(target),'bytes':len(data),'backup':str(saved) if saved else None}
def replace_in_file(path:str,old:str,new:str)->dict:
    target=checked(path,mutation=True);raw=bounded_bytes(target);text,encoding=decode(raw);count=text.count(old)
    if count!=1:raise ValueError(f'要替换的文字必须在文件里恰好出现一次，现在是 {count} 次')
    data=text.replace(old,new).encode(encoding)
    if len(data)>MAX_FILE_BYTES:raise ValueError('替换后的文件过大，未修改')
    saved=backup(target);temp=target.with_name(target.name+f'.tmp{os.getpid()}')
    try:temp.write_bytes(data);os.replace(temp,target)
    finally:temp.unlink(missing_ok=True)
    log_change('replace',target,saved);return {'path':str(target),'backup':str(saved)}
def move_path(source:str,target:str)->dict:
    a=checked(source,mutation=True);b=checked(target,mutation=True)
    if b.exists():raise FileExistsError('目标已存在，未覆盖')
    if b.is_relative_to(a):raise ValueError('不能把目录移动到自身内部')
    if a.is_dir():check_tree(a)
    b.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(a),str(b));log_change('move',a,b);return {'from':str(a),'to':str(b)}
def delete_path(path:str)->dict:
    """Moved to the backup folder, so a wrong delete can be put back."""
    target=checked(path,mutation=True)
    if not target.exists():raise FileNotFoundError('路径不存在')
    if target==Path(target.anchor):raise PermissionError('不能删除磁盘根目录')
    saved=backup(target)
    if target.is_dir():shutil.rmtree(target)
    else:target.unlink()
    log_change('delete',target,saved);return {'path':str(target),'backup':str(saved)}
