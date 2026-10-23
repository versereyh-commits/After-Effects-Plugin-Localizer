"""Favourite effects, kept in named folders (收藏夹), and the effects quick search added lately. One set for every
AE version, by internal name (matchName, the same in every version). Quick search lists the current folder's
effects first, in the user's order, then the effects it added most recently. Only effects added through quick
search count as used: an effect added in AE itself is not seen by this program.

A file from before folders (schemaVersion 1: one 'favorites' list) becomes the folder DEFAULT. The current
folder's list is also written under 'favorites', so an older version of the program still finds it."""
from __future__ import annotations
from pathlib import Path
import json
from runtime import write_json_atomic
from storage_safety import read_json

FILE='quick-search.json'
RECENT=8        # shown under the favourites
KEEP=40         # remembered, so the eight shown stay full when some of them are favourites too
DEFAULT='默认收藏夹'
NAME_LIMIT=40

def _names(value)->list[str]:
    seen=set();result=[]
    for item in value if isinstance(value,list) else ():
        if isinstance(item,str) and item and item not in seen:seen.add(item);result.append(item)
    return result

def clean_name(name)->str:
    """A folder name as stored: runs of spaces folded; empty or too long is refused."""
    name=' '.join(str(name or '').split())
    if not name:raise ValueError('收藏夹名称不能为空')
    if len(name)>NAME_LIMIT:raise ValueError(f'收藏夹名称最多 {NAME_LIMIT} 个字符')
    return name

class Favorites:
    def __init__(self,path:Path):
        self.path=Path(path);self.folders:dict[str,list[str]]={};self.current=DEFAULT;self.recent:list[str]=[];self.listeners=[]
        self.warning='';self.blocked=False
        try:value,self.warning=read_json(self.path)
        except OSError as error:value={};self.blocked=True;self.warning='收藏记录无法读取，已禁止覆盖：'+str(error)
        if isinstance(value,dict):
            for folder in value.get('folders') if isinstance(value.get('folders'),list) else ():
                if not isinstance(folder,dict):continue
                try:name=clean_name(folder.get('name'))
                except ValueError:continue
                if not any(n.casefold()==name.casefold() for n in self.folders):self.folders[name]=_names(folder.get('favorites'))
            if not self.folders:self.folders[DEFAULT]=_names(value.get('favorites'))
            self.recent=_names(value.get('recent'))[:KEEP]
            if value.get('current') in self.folders:self.current=value['current']
        if not self.folders:self.folders[DEFAULT]=[]
        if self.current not in self.folders:self.current=next(iter(self.folders))
    @property
    def favorites(self)->list[str]:
        """The current folder's effects, in order."""
        return self.folders[self.current]
    @favorites.setter
    def favorites(self,keys):self.folders[self.current]=_names(list(keys))
    def __contains__(self,key)->bool:return key in self.favorites
    def names(self)->list[str]:return list(self.folders)
    def save(self)->str:
        """Writes the file and tells the listeners; returns an error message, or '' when saved."""
        if self.blocked:return self.warning
        value={'schemaVersion':2,'folders':[{'name':name,'favorites':keys} for name,keys in self.folders.items()],'current':self.current,
               'favorites':self.favorites,'recent':self.recent}
        try:write_json_atomic(self.path,value,ensure_ascii=False,indent=1)
        except OSError as error:message='收藏未能保存：'+str(error)
        else:message=''
        for listener in list(self.listeners):listener()
        return message
    # Folders
    def find(self,name)->str|None:
        """The folder's stored name for a name typed in any letter case, or None."""
        name=' '.join(str(name or '').split()).casefold()
        return next((n for n in self.folders if n.casefold()==name),None)
    def _free(self,name,keep=None)->str:
        name=clean_name(name);taken=self.find(name)
        if taken is not None and taken!=keep:raise ValueError(f'已经有名为“{taken}”的收藏夹，请换一个名称')
        return name
    def create(self,name,keys=(),select=False)->str:
        """A new folder at the end, holding keys; returns its name. Raises ValueError for a bad or taken name."""
        name=self._free(name);self.folders[name]=_names(list(keys))
        if select:self.current=name
        self.save();return name
    def rename(self,old,new)->str:
        if old not in self.folders:raise ValueError(f'没有名为“{old}”的收藏夹')
        new=self._free(new,keep=old)
        if new!=old:
            self.folders={new if n==old else n:keys for n,keys in self.folders.items()}
            if self.current==old:self.current=new
            self.save()
        return new
    def delete(self,name):
        """Removes a folder; the last one stays (emptied) so there is always somewhere to collect."""
        if name not in self.folders:return
        if len(self.folders)==1:
            if self.folders[name]:self.folders[name]=[];self.save()
            return
        names=self.names();index=names.index(name);del self.folders[name]
        if self.current==name:self.current=self.names()[min(index,len(self.folders)-1)]
        self.save()
    def select(self,name)->bool:
        if name not in self.folders or name==self.current:return False
        self.current=name;self.save();return True
    def add(self,name,keys)->int:
        """Puts effects at the end of a folder (any folder, not only the current one); returns how many were new."""
        if name not in self.folders:raise ValueError(f'没有名为“{name}”的收藏夹')
        new=[key for key in _names(list(keys)) if key not in self.folders[name]]
        if new:self.folders[name]=self.folders[name]+new;self.save()
        return len(new)
    def holding(self,key)->list[str]:
        """Names of the folders an effect is in."""
        return [name for name,keys in self.folders.items() if key in keys]
    # The current folder
    def set(self,keys,on:bool)->int:
        """Adds (to the end, in the given order) or removes effects in the current folder; returns how many changed."""
        keys=_names(list(keys))
        if on:return self.add(self.current,keys)
        gone=set(keys)&set(self.favorites)
        if gone:self.favorites=[key for key in self.favorites if key not in gone];self.save()
        return len(gone)
    def toggle(self,key)->bool:
        """Returns True when the effect is now in the current folder."""
        on=key not in self.favorites;self.set([key],on);return on
    def replace(self,keys):
        """The current folder's whole list in this order (reordering)."""
        keys=_names(list(keys))
        if keys!=self.favorites:self.favorites=keys;self.save()
    def used(self,key):
        """An effect quick search has just added: first in the recent list."""
        if not key:return
        self.recent=[key]+[k for k in self.recent if k!=key][:KEEP-1];self.save()
    def clear_recent(self):
        if self.recent:self.recent=[];self.save()
    def latest(self,available=None,limit=RECENT,skip_favorites=True)->list[str]:
        """The most recently added effects, without the current folder's (listed above them), and given a set, only those AE has."""
        return [k for k in self.recent if not (skip_favorites and k in self.favorites) and (available is None or k in available)][:limit]
