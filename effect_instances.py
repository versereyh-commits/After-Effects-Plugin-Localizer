"""Synchronize default effect instance titles through AE's scripting API.

Catalog localization remains separate. Custom titles are never overwritten;
restore records contain logical locations, not pointers, and are project scoped.
"""
import json
from runtime import data_root,native_probe
from live_names import current_process,_lock,_process_lock,_save
from parameter_names import run_native

LOCATION=('compId','layerIndex','layerId','layerName','effectIndex','matchName')

def identity(token,row):
    layer=['id',row['layerId']] if row['layerId'] is not None else ['index',row['layerIndex'],row['layerName']]
    return json.dumps([token,row['compId'],layer,row['effectIndex'],row['matchName']],ensure_ascii=False,separators=(',',':'))

def validate_row(row):
    if not isinstance(row,dict):raise ValueError('invalid instance')
    for name in ('compId','layerIndex','effectIndex'):
        if type(row.get(name)) is not int or row[name]<1:raise ValueError('invalid instance location')
    if row.get('layerId') is not None and (type(row['layerId']) is not int or row['layerId']<1):raise ValueError('invalid layer identity')
    for name in ('layerName','matchName','name'):
        if not isinstance(row.get(name),str):raise ValueError('invalid instance name')

class EffectInstanceSession:
    def __init__(self):self.path=data_root()/'effect-instance-session.json';self.expected_image=None
    def process(self):return current_process(expected_image=self.expected_image)
    def journal(self):
        try:
            value=json.loads(self.path.read_text(encoding='utf8'))
            if not isinstance(value,dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion']!=1 or not isinstance(value.get('entries'),dict):raise ValueError('invalid journal')
            if value['entries'] and any(type(value.get(k)) is not int for k in ('pid','creationTime')):raise ValueError('invalid host')
            for key,entry in value['entries'].items():
                validate_row(dict(entry,name=entry.get('original')))
                if not isinstance(entry.get('applied'),str) or not isinstance(entry.get('projectToken'),str) or key!=identity(entry['projectToken'],entry):raise ValueError('invalid entry')
            return value
        except FileNotFoundError:return {'schemaVersion':1,'entries':{}}
        except (ValueError,TypeError,KeyError) as error:raise RuntimeError('效果实例恢复记录损坏，已停止并保留原文件') from error
    def _catalog(self,p):
        result=run_native(p,[],action='instanceCatalog')
        self._recheck(p)
        if result.get('error'):raise RuntimeError(result['error'])
        token=result.get('projectToken');rows=result.get('instances')
        if not isinstance(token,str) or not token or not isinstance(rows,list):raise RuntimeError('效果实例读取不完整')
        for row in rows:validate_row(row)
        keys=[identity(token,row) for row in rows]
        if len(keys)!=len(set(keys)):raise RuntimeError('效果实例身份不唯一')
        return token,rows
    def _write(self,p,token,ops):
        if not ops:return
        result=run_native(p,ops,action='instanceNames',project_token=token)
        self._recheck(p)
        if result.get('error'):raise RuntimeError('图层效果标题同步失败；恢复记录已保留：'+result['error'])
        if result.get('changed')!=len(ops):raise RuntimeError('图层效果标题写入数量不符；恢复记录已保留')
    @staticmethod
    def _same_host(p,journal):return journal.get('pid')==p.pid and journal.get('creationTime')==p.creation_time
    def _recheck(self,p):
        with self.process() as actual:
            if (actual.pid,actual.creation_time,actual.image)!=(p.pid,p.creation_time,p.image):raise RuntimeError('AE 宿主已变化，效果实例记录已保留')
    def apply(self,names,originals):
        if not names:return {'changed':0,'customSkipped':0}
        with _lock,_process_lock(),self.process() as p:
            journal=self.journal()
            if not self._same_host(p,journal):journal={'schemaVersion':1,'entries':{}}
            token,rows=self._catalog(p);ops=[];custom=0
            for row in rows:
                match=row['matchName']
                if match not in names:continue
                key=identity(token,row);entry=journal['entries'].get(key);before=row['name'];after=names[match]
                if before==after:continue
                if entry:
                    if before not in (entry['applied'],entry['original']):custom+=1;continue
                elif before not in (originals.get(match),match):custom+=1;continue
                op={k:row[k] for k in LOCATION};op.update(before=before,after=after);ops.append(op)
                journal['entries'][key]=dict(op,projectToken=token,original=entry['original'] if entry else before,applied=after)
            if ops:
                journal.update(pid=p.pid,creationTime=p.creation_time);_save(self.path,journal)
                self._write(p,token,ops)
            return {'changed':len(ops),'customSkipped':custom}
    def restore(self,keys=None):
        with _lock,_process_lock():
            journal=self.journal()
            if not journal['entries']:return {'restored':0,'pending':0}
            probe=native_probe()
            if probe.get('error'):raise RuntimeError('无法核对 AE 宿主；效果实例恢复记录已保留')
            if not any(row.get('pid')==journal.get('pid') for row in probe.get('processes',[])):
                _save(self.path,{'schemaVersion':1,'entries':{}});return {'restored':0,'pending':0,'oldProcessEnded':True}
            with self.process() as p:
                if not self._same_host(p,journal):
                    _save(self.path,{'schemaVersion':1,'entries':{}});return {'restored':0,'pending':0,'oldProcessEnded':True}
                token,rows=self._catalog(p);actual={identity(token,row):row for row in rows};ops=[];finished=[]
                for key,entry in journal['entries'].items():
                    if keys is not None and entry['matchName'] not in keys:continue
                    if entry['projectToken']!=token or key not in actual:continue
                    row=actual[key]
                    if row['name'] not in (entry['original'],entry['applied']):raise RuntimeError('效果实例标题已被手动修改，未强制恢复：'+entry['matchName'])
                    if row['name']!=entry['original']:
                        ops.append(dict({k:row[k] for k in LOCATION},before=row['name'],after=entry['original']))
                    finished.append(key)
                self._write(p,token,ops)
                for key in finished:del journal['entries'][key]
                if finished:_save(self.path,journal)
                return {'restored':len(ops),'pending':len(journal['entries'])}
