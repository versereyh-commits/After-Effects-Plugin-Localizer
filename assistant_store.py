"""Saved assistant conversations: one JSON file each in the data folder. Local only. They hold what the model saw,
including file contents read at the highest permission level, and never the API key."""
from __future__ import annotations
import json,time,uuid,math
from pathlib import Path
from runtime import write_json_atomic

KEEP=200;FOLDER='assistant-chats'
def new_id()->str:return time.strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:6]
def title_of(text:str)->str:
    line=' '.join(text.split());return line[:28]+('…' if len(line)>28 else '')

class ChatStore:
    def __init__(self,root:Path):
        self.folder=Path(root)/FOLDER;self.index=self.folder/'.index';self.cache={}
        try:
            if self.index.stat().st_size>256000:raise ValueError('index too large')
            value=json.loads(self.index.read_text(encoding='utf8'))
            if isinstance(value,dict):self.cache=value
        except (OSError,ValueError):pass
    def path(self,chat_id:str)->Path:
        # Ids come from new_id(); a saved file's name is never trusted to climb out of the folder.
        if not chat_id or any(c in chat_id for c in '\\/:.'):raise ValueError('对话记录名称无效')
        return self.folder/f'{chat_id}.json'
    def save(self,chat:dict):
        self.folder.mkdir(parents=True,exist_ok=True);chat['updated']=time.time();file=self.path(chat['id']);write_json_atomic(file,chat)
        self.cache[file.stem]={'signature':self.signature(file),'row':self.row(file,chat)}
        for stale in self.list()[KEEP:]:self.delete(stale['id'])
        self.save_index()
    def load(self,chat_id:str)->dict:
        value=json.loads(self.path(chat_id).read_text(encoding='utf8'))
        if not isinstance(value,dict) or not isinstance(value.get('messages'),list) or not isinstance(value.get('items'),list):raise ValueError('对话记录已损坏')
        for m in value['messages']:
            if not isinstance(m,dict) or m.get('role') not in ('user','assistant') or not isinstance(m.get('content'),str):raise ValueError('对话记录已损坏')
        return value
    def list(self)->list[dict]:
        """Newest first; a damaged file is skipped and left in place."""
        rows=[]
        if not self.folder.is_dir():return rows
        found={};changed=False
        for file in self.folder.glob('*.json'):
            try:
                self.path(file.stem)
                signature=self.signature(file);cached=self.cache.get(file.stem)
                if not self.valid_cached(cached,file.stem,signature):
                    value=json.loads(file.read_text(encoding='utf8'));cached={'signature':signature,'row':self.row(file,value)};changed=True
                rows.append(dict(cached['row']));found[file.stem]=cached
            except (OSError,ValueError,AttributeError,TypeError):pass
        if changed or set(found)!=set(self.cache):self.cache=found;self.save_index()
        rows.sort(key=lambda r:-r['updated']);return rows
    @staticmethod
    def signature(file):
        s=file.stat();return [s.st_mtime_ns,s.st_size,s.st_ino]
    @staticmethod
    def row(file,value):
        updated=float(value.get('updated') or 0)
        if not math.isfinite(updated):raise ValueError('时间无效')
        return {'id':file.stem,'title':str(value.get('title') or '未命名对话'),'updated':updated,'count':sum(1 for i in value.get('items',[]) if i.get('kind')=='user')}
    @staticmethod
    def valid_cached(cached,chat_id,signature):
        if not isinstance(cached,dict) or cached.get('signature')!=signature:return False
        row=cached.get('row')
        return isinstance(row,dict) and row.get('id')==chat_id and isinstance(row.get('title'),str) and isinstance(row.get('count'),int) and isinstance(row.get('updated'),(int,float)) and math.isfinite(row['updated'])
    def save_index(self):
        # This is an expendable acceleration index; the original conversation stays authoritative.
        try:write_json_atomic(self.index,self.cache)
        except OSError:pass
    def delete(self,chat_id:str):
        self.path(chat_id).unlink(missing_ok=True);self.cache.pop(chat_id,None);self.save_index()
def when(stamp:float)->str:
    now=time.localtime();then=time.localtime(stamp)
    if then.tm_year==now.tm_year and then.tm_yday==now.tm_yday:return time.strftime('今天 %H:%M',then)
    if then.tm_year==now.tm_year and now.tm_yday-then.tm_yday==1:return time.strftime('昨天 %H:%M',then)
    return time.strftime('%m-%d %H:%M' if then.tm_year==now.tm_year else '%Y-%m-%d',then)
