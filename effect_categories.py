"""Verified category labels. Uses only the first 48 bytes of a verified 64-byte field.

Independent journal, exact original bytes, no value/identifier/plugin-file changes.
Writes require an enabled exact-build adapter and matching module hash.
"""
from pathlib import Path
import json
from ae_profiles import fc_fields,fc_size,PROFILES,get_profile,is_supported
from live_names import Registry,current_process,_lock,_process_lock,_save,encode_name
from runtime import data_root
from ae_ui import main_window
from host_identity import running_pids

def supported_build(version,flt_sha)->bool:
    if not flt_sha or not is_supported(version,flt_sha):return False
    verified=get_profile(version,flt_sha)
    start,end=fc_fields(verified)[2]
    return verified.flt_sha==flt_sha and end-start==64

def supported(profile):return supported_build(profile.version,profile.flt_sha)

class CategorySession:
    def __init__(self):self.expected_image=None;self.path=data_root()/'effect-category-session.json'
    def process(self,write=False):return current_process(write=write,expected_image=self.expected_image)
    def journal(self):
        try:j=json.loads(self.path.read_text(encoding='utf8'))
        except FileNotFoundError:return {'schemaVersion':1,'kind':'effect-categories','entries':{}}
        except (ValueError,UnicodeError) as e:raise RuntimeError('分类恢复记录损坏，未覆盖') from e
        try:
            if j['schemaVersion']!=1 or j['kind']!='effect-categories' or not isinstance(j['entries'],dict):raise ValueError()
            if len(j['entries'])>32768:raise ValueError()
            if j['entries'] and (type(j['pid']) is not int or type(j['creationTime']) is not int or not isinstance(j['image'],str) or not isinstance(j['profileSha256'],str)):raise ValueError()
            for key,e in j['entries'].items():
                if not isinstance(key,str) or not isinstance(e['originalName'],str) or not isinstance(e['newName'],str):raise ValueError()
                for field in ('originalHex','appliedHex'):
                    raw=bytes.fromhex(e[field])
                    if len(raw)!=64 or b'\0' not in raw[:48]:raise ValueError()
                if bytes.fromhex(e['originalHex'])[48:]!=bytes.fromhex(e['appliedHex'])[48:]:raise ValueError()
            return j
        except (ValueError,KeyError,TypeError,AttributeError) as e:raise RuntimeError('分类恢复记录损坏，未覆盖') from e
    def _active(self,p,j):
        if j['entries'] and (j['pid']!=p.pid or j['creationTime']!=p.creation_time):return {'schemaVersion':1,'kind':'effect-categories','entries':{}}
        if j['entries'] and Path(j['image'])!=Path(p.image):raise RuntimeError('分类恢复记录宿主路径不符')
        return j
    @staticmethod
    def _rows(registry):
        rows=registry.records();by={r['matchName']:r for r in rows}
        if len(by)!=len(rows):raise RuntimeError('效果标识不唯一，分类操作已停止')
        return by
    @staticmethod
    def _span(registry):
        start,end=fc_fields(registry.profile)[2]
        if end-start!=64:raise RuntimeError('分类字段长度未验证')
        return start
    def group(self,key):
        with _lock,_process_lock(),self.process() as p:
            r=Registry(p);rows=self._rows(r)
            if key not in rows:raise ValueError('当前 AE 未加载此效果')
            category=rows[key]['category']
            return {'category':category,'keys':[k for k,row in rows.items() if row['category']==category],
                    'supported':supported(r.profile),'version':r.profile.version}
    def apply_group(self,key,value,expected_category,expected_members=None):
        with _lock,_process_lock(),self.process(write=True) as p:
            main_window(p.pid);r=Registry(p)
            if not supported(r.profile):raise RuntimeError('此构建或效果模块尚未验证分类写入：'+r.profile.version)
            rows=self._rows(r)
            if key not in rows or rows[key]['category']!=expected_category:raise RuntimeError('效果分类已变化，请重新操作')
            if not expected_category:raise ValueError('空分类暂不支持修改')
            encoded=encode_name(value,r.profile.encoding);value=encoded.split(b'\0',1)[0].decode(r.profile.encoding)
            if value!=expected_category and any(row['category']==value for row in rows.values()):raise ValueError('此分类名称已存在，请使用不同名称')
            wanted={k:row for k,row in rows.items() if row['category']==expected_category}
            if expected_members is not None and set(wanted)!=set(expected_members):raise RuntimeError('分类包含的效果已变化，请重新确认')
            j=self._active(p,self.journal())
            if j['entries'] and j['profileSha256']!=r.profile.flt_sha:raise RuntimeError('分类恢复记录模块不符')
            previous=json.loads(json.dumps(j));j.update(pid=p.pid,creationTime=p.creation_time,image=p.image,profileSha256=r.profile.flt_sha)
            offset=self._span(r);staged=[]
            for k,row in wanted.items():
                before=p.read(row['address']+offset,64)
                if b'\0' not in before[:48]:raise ValueError('分类超出已验证的48字节写入范围，未修改：'+k)
                entry=j['entries'].get(k)
                if entry and before.hex()!=entry['appliedHex']:raise RuntimeError('分类已被外部修改，未覆盖：'+k)
                after=encoded+before[48:]
                if before==after:continue
                j['entries'][k]={'originalHex':entry['originalHex'] if entry else before.hex(),'appliedHex':after.hex(),
                                  'originalName':entry['originalName'] if entry else row['category'],'newName':value}
                full=p.read(row['address'],fc_size(r.profile));staged.append((k,row,before,after,full))
            if not staged:return {'applied':{},'unchanged':len(wanted)}
            _save(self.path,j);attempted=[]
            try:
                for k,row,before,after,full in staged:
                    current=r.target(k)
                    if current['address']!=row['address'] or p.read(row['address'],len(full))!=full:raise RuntimeError('分类目录在写入前变化，已停止')
                    attempted.append((k,row,before,after));p.write_buffer(row['address']+offset,after[:48])
                    expected=full[:offset]+after+full[offset+64:]
                    if p.read(row['address'],len(full))!=expected or r.target(k)['category']!=value:raise RuntimeError('分类写入后核验失败')
            except Exception:
                failed=False
                for k,row,before,after in reversed(attempted):
                    try:
                        current=r.target(k);actual=p.read(current['address']+offset,64)
                        if current['address']!=row['address'] or actual not in (before,after):raise RuntimeError('回滚冲突')
                        if actual==after:p.write_buffer(current['address']+offset,before[:48])
                    except Exception:failed=True
                if not failed:_save(self.path,previous)
                raise
            return {'applied':{k:value for k,row,*rest in staged}}
    def restore(self,keys=None):
        """keys: only these effects' categories (a whole AE group is passed, so no group is split); None for all."""
        with _lock,_process_lock():
            j=self.journal()
            if not j['entries']:return {'restored':{}}
            if j['pid'] not in running_pids():
                _save(self.path,{'schemaVersion':1,'kind':'effect-categories','entries':{}});return {'restored':{},'oldProcessEnded':True}
            with self.process(write=True) as p:
                active=self._active(p,j)
                if not active['entries']:_save(self.path,active);return {'restored':{},'oldProcessEnded':True}
                main_window(p.pid);r=Registry(p)
                if not supported(r.profile) or r.profile.flt_sha!=j['profileSha256']:raise RuntimeError('分类恢复构建不符')
                offset=self._span(r);rows=self._rows(r);staged=[]
                for k,e in j['entries'].items():
                    if keys is not None and k not in keys:continue
                    if k not in rows:raise RuntimeError('分类恢复效果未加载：'+k)
                    row=rows[k];before=p.read(row['address']+offset,64)
                    if before.hex() not in (e['originalHex'],e['appliedHex']):raise RuntimeError('分类已被外部修改，未强制恢复：'+k)
                    staged.append((k,row,before,bytes.fromhex(e['originalHex'])))
                restored={}
                try:
                    for k,row,before,after in staged:
                        current=r.target(k)
                        if current['address']!=row['address'] or p.read(current['address']+offset,64)!=before:raise RuntimeError('分类恢复目标已变化')
                        if before!=after:p.write_buffer(current['address']+offset,after[:48])
                        if p.read(current['address']+offset,64)!=after:raise RuntimeError('分类恢复回读失败')
                        restored[k]=j['entries'][k]['originalName'];del j['entries'][k]
                finally:
                    if restored:_save(self.path,j)
                return {'restored':restored}
