"""Guarded PF popup choices. Logical recovery history survives undo and project switches."""
from pathlib import Path
import ctypes as C,json,struct,time
from parameter_names import ParameterCatalog,_host_names,_selected_names,_project_context,run_native,MBI,split_identity,record_read_timing
from live_names import current_process,_lock,_process_lock,_save
from runtime import data_root,file_version
from host_identity import target,same,running_pids,valid,query

BUILD='26.5.0.89'
HASHES=('9a1afe7700229cd0bc39dd37a146b1d3871fc84a8e6f991a7ef56390f21378d3','ef851c3ccbc43da4e0d4899a52baa05a7a71a3cef3a4fdb09abbb0263960ccb5','1a465b53deac7a3d14187ed175ba85498dbf5b5463259dec1f202a3393050fd0')
POPUP_PROMPT=('你是 After Effects 特效术语译者。翻译参数下拉选项为简短简体中文，保留RGB/YCC等缩写、数字、单位、占位符，不能添加竖线或控制字符。'
              '同一参数下不同选项应清楚区分。category含整个选项组按宿主编码计算的原始字节预算，全部译文合计（含竖线分隔符）必须不超过预算，请尽量简短。'
              '不改变id，不生成代码。只输出JSON：{"translations":[{"id":"原id","name":"中文"}]}。')

def option_key(row,index):
    # Bind a saved candidate to its exact source, not just the effect's name.
    return json.dumps([row['id'],row['original'],index],ensure_ascii=False,separators=(',',':'))

REFRESH_NOTE='应用或恢复后，请切换到其他图层再返回，以刷新下拉框显示。新建实例或重新打开工程后需重新应用。'

def host_alive(host):
    if not valid(host):raise RuntimeError('下拉选项恢复记录的宿主身份无效')
    if host['pid'] not in running_pids():return False
    image,created=query(host['pid'])
    from types import SimpleNamespace
    return same(host,target(SimpleNamespace(pid=host['pid'],image=image,creation_time=created)))

def require_support(p,profile):
    # ParameterCatalog has already verified this host's FLT/TDB hashes and getters.
    # The native helper repeats module/layout/codec verification before each write.
    if getattr(profile,'version',file_version(Path(p.image)))!=file_version(Path(p.image)) or profile.encoding not in ('utf8','mbcs'):
        raise RuntimeError('当前 AE 参数适配或编码不匹配，请先完成该版本适配')
    for digest in (profile.flt_sha,profile.tdb_sha):
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest) or set(digest)=={'0'}:
            raise RuntimeError('当前 AE 模块身份无效，未执行下拉写入')

def choices(text):
    if not isinstance(text,str) or len(text.encode('utf8'))>8191 or any(ord(c)<32 or ord(c)==127 for c in text):raise ValueError('下拉选项文本无效')
    items=text.split('|')
    if not 2<=len(items)<=256 or any(not x.strip() for x in items):raise ValueError('下拉选项数量或内容无效')
    return items

def validate_option(source,text):
    if not isinstance(text,str) or not text.strip() or '|' in text or any(ord(c)<32 or ord(c)==127 for c in text):raise ValueError('选项不能为空或包含分隔符、控制字符')
    from panel_localizer import validate_text
    return validate_text(source,text.strip())

def pack_translation(original,values,encoding='utf8'):
    source=choices(original)
    if not isinstance(values,list) or len(values)!=len(source):raise ValueError('不得改变下拉选项数量')
    clean=[]
    for old,new in zip(source,values):
        new=validate_option(old,new)
        if old.strip()=='(-' and new!='(-':raise ValueError('不能翻译菜单分隔项')
        clean.append(new)
    joined='|'.join(clean);extra=len(original.encode(encoding))-len(joined.encode(encoding))
    if extra<0:raise ValueError(f'译文超出原始容量 {-extra} 个 {encoding} 字节，请缩短选项译文')
    # Keep strlen, not only allocation size: copy-on-write and undo use strlen.
    return joined+' '*extra

def popup_record(p,row,encoding='utf8'):
    raw=p.read(row['address']+0x3c,0x3c)
    if struct.unpack_from('<I',raw)[0]!=7:return None
    value,count,default,pointer=struct.unpack_from('<IHHQ',raw,0x2c)
    if not 2<=count<=256 or not 1<=value<=count or not 1<=default<=count:return None
    m=MBI();p.k.VirtualQueryEx.argtypes=[C.c_void_p,C.c_void_p,C.POINTER(MBI),C.c_size_t];p.k.VirtualQueryEx.restype=C.c_size_t
    if not p.k.VirtualQueryEx(p.handle,C.c_void_p(pointer),C.byref(m),C.sizeof(m)) or m.State!=0x1000 or m.Type!=0x20000 or m.Protect&0x100 or m.Protect&255 not in (4,8):return None
    limit=min(8192,(m.BaseAddress or 0)+m.RegionSize-pointer);data=bytearray()
    while len(data)<limit:
        part=p.read(pointer+len(data),min(32,limit-len(data)));nul=part.find(b'\0')
        if nul>=0:data.extend(part[:nul]);break
        data.extend(part)
    else:return None
    try:text=data.decode(encoding);parts=choices(text)
    except (UnicodeError,ValueError):return None
    if len(parts)!=count:return None
    return {**row,'text':text,'pointer':pointer,'flags':struct.unpack_from('<I',raw,0x24)[0],'default':default,'count':count}

class PopupSession:
    def __init__(self,path=None):self.path=Path(path) if path is not None else data_root()/'popup-session.json';self.expected_image=None
    def process(self):return current_process(expected_image=self.expected_image)
    def journal(self):
        try:d=json.loads(self.path.read_text(encoding='utf8'))
        except FileNotFoundError:return {'schemaVersion':1,'entries':{}}
        except (ValueError,OSError) as e:raise RuntimeError('下拉选项恢复记录不可读，已保留原文件') from e
        try:
            if type(d['schemaVersion']) is not int or d['schemaVersion']!=1 or not isinstance(d['entries'],dict):raise ValueError()
            if d['entries'] and not valid(d['host']):raise ValueError()
            for key,e in d['entries'].items():
                split_identity(key);choices(e['original'])
                encoding=e.get('encoding','utf8')
                if encoding not in ('utf8','mbcs'):raise ValueError()
                if not isinstance(e['variants'],list) or len(e['variants'])>64 or e['state'] not in ('applied','restored'):raise ValueError()
                for v in e['variants']:
                    if len(choices(v))!=len(choices(e['original'])) or len(v.encode(encoding))!=len(e['original'].encode(encoding)):raise ValueError()
        except (KeyError,TypeError,ValueError):raise RuntimeError('下拉选项恢复记录损坏，已保留原文件') from None
        return d
    def _journal_for(self,p):
        d=self.journal()
        if d['entries'] and not same(d.get('host'),target(p)):
            if host_alive(d['host']):raise RuntimeError('另一 AE 会话仍有下拉选项恢复记录，请先恢复')
            d={'schemaVersion':1,'entries':{}}
        return d
    def _records(self,p,catalog,actual,progress=None,full=False,fresh=False):
        """Writes use whatever structure is cached, a default-heap scan if none, as parameter labels do. full asks for
        all of AE's memory (3.5-5 s): a write that found too few copies. fresh (restores) scans all of it again so no
        copy keeps a translation. Every field and choice is read from AE now in any case."""
        rows=[]
        for row in catalog.rows(rescan=fresh,progress=progress,full=full or fresh):
            if row['id'] in actual:
                item=popup_record(p,row,catalog.profile.encoding)
                if item:rows.append(item)
        return rows
    def read(self,scope='selected',progress=None):
        if scope not in ('selected','project'):raise ValueError('未知读取范围')
        with _lock,_process_lock(),self.process() as p:
            catalog=ParameterCatalog(p);require_support(p,catalog.profile)
            if scope=='selected':actual,info=_selected_names(p)
            else:actual=_host_names(p);info={'projectToken':_project_context[(p.pid,p.creation_time)]}
            public=self._public(p,catalog,actual,progress)
            if scope=='selected':
                _,latest=_selected_names(p,action='selection')
                if (latest['projectToken'],latest['selectionSnapshot'])!=(info['projectToken'],info['selectionSnapshot']):raise RuntimeError('读取时选择发生变化，请重新读取')
            p.ensure_alive()
            return {'host':target(p),'projectToken':info['projectToken'],'scope':scope,'parameters':public}
    def reader(self,scope):
        """For read_parameters(extra=): the choices come with the labels. A failure here leaves the labels usable."""
        def read(p,catalog,actual,info):
            try:
                require_support(p,catalog.profile)
                token=info['projectToken'] if scope=='selected' else _project_context[(p.pid,p.creation_time)]
                return {'popups':{'host':target(p),'projectToken':token,'scope':scope,'parameters':self._public(p,catalog,actual)}}
            except Exception as error:return {'popupError':str(error)}
        return read
    def _public(self,p,catalog,actual,progress=None):
        d=self._journal_for(p);items={};public=[]
        if actual:
            for row in self._records(p,catalog,actual,progress):items.setdefault(row['id'],[]).append(row)
        for key,rows in items.items():
            contents={r['text'] for r in rows}
            if len(contents)!=1:continue # Mixed/undo copies need a clean, explicit restore first.
            text=next(iter(contents));entry=d['entries'].get(key)
            if entry and text not in [entry['original'],*entry['variants']]:continue
            original=entry['original'] if entry else text
            public.append({'id':key,'effectMatchName':rows[0]['effectMatchName'],'name':rows[0]['name'],'original':original,'current':text,'options':[s.rstrip() for s in choices(original)],'currentOptions':[s.rstrip() for s in choices(text)],'capacity':len(original.encode(catalog.profile.encoding)),'encoding':catalog.profile.encoding,'instances':len(actual.get(key,[]))})
        return sorted(public,key=lambda r:r['id'])
    def _change(self,values,binding,restore=False,progress=None,cancelled=None):
        if cancelled and cancelled():raise RuntimeError('操作已取消')
        with _lock,_process_lock(),self.process() as p:
            if binding and not same(binding.get('host'),target(p)):raise RuntimeError('AE 会话发生变化，请重新读取下拉选项')
            catalog=ParameterCatalog(p);require_support(p,catalog.profile);actual=_host_names(p)
            token=_project_context[(p.pid,p.creation_time)]
            if binding and binding.get('projectToken')!=token:raise RuntimeError('工程已变化，请重新读取下拉选项')
            d=self._journal_for(p);wanted=set(d['entries']) if restore and values is None else set(values or {})
            if not wanted:return {'changed':0,'parameters':0,'pending':0}
            started=time.perf_counter()
            def grouped(records):
                found={}
                for row in records:
                    if row['id'] in wanted:found.setdefault(row['id'],[]).append(row)
                return found
            groups=grouped(self._records(p,catalog,actual,progress,full=restore,fresh=restore))
            # Fewer copies in memory than AE shows, so the cached or heap-only structure cannot hold them all.
            if not restore and any(len(groups.get(key,()))<len(actual.get(key,())) for key in wanted):
                groups=grouped(self._records(p,catalog,actual,progress,full=True))
            scanned=time.perf_counter()
            if not restore and wanted-set(groups):raise RuntimeError('部分下拉选项已失效或不受支持，请重新读取')
            operations=[];changes={}
            for key,rows in groups.items():
                e=d['entries'].get(key);contents={r['text'] for r in rows}
                if e is None:
                    if restore:continue
                    if len(contents)!=1:raise RuntimeError('同一参数选项不一致，不能安全统一汉化')
                    e={'original':next(iter(contents)),'variants':[],'state':'restored','encoding':catalog.profile.encoding}
                if contents-set([e['original'],*e['variants']]):raise RuntimeError('下拉选项被外部修改，未覆盖')
                original=e['original']
                if e.get('encoding','utf8')!=catalog.profile.encoding:raise RuntimeError('下拉恢复记录编码与当前 AE 不同，未覆盖')
                if not restore:
                    # The preview is not write authority: compare its exact baseline again.
                    shown=next((x for x in binding['parameters'] if x['id']==key),None) if binding else None
                    if not shown or shown['original']!=original:raise RuntimeError('下拉选项原文已变化，请重新读取')
                after=original if restore else pack_translation(original,values[key],catalog.profile.encoding)
                if after!=original and after not in e['variants']:
                    if len(e['variants'])>=64:raise RuntimeError('该会话已达64次译文修订上限；请重启AE后重新应用')
                    e['variants'].append(after)
                e['state']='restored' if restore else 'applied';changes[key]=e
                for row in rows:
                    if row['text']!=after:
                        operations.append({'address':hex(row['address']),'guid':row['guid'],'parent':hex(row['parent']),'pointer':hex(row['pointer']),'before':row['text'],'after':after,'pfName':row['pfName'],'flags':hex(row['flags']),'default':hex(row['default'])})
            # Keep originals and every prior translation for undo/redo; no native addresses persisted.
            d.update(host=target(p));d['entries'].update(changes)
            for e in changes.values():e['state']='applied' # uncertain until native completion and re-read
            _save(self.path,d)
            if cancelled and cancelled():raise RuntimeError('操作已取消；恢复记录已保留')
            if operations:
                result=run_native(p,operations,action='popups',project_token=token)
                if result.get('error'):raise RuntimeError('下拉选项操作未完成，恢复记录已保留：'+result['error'])
            written=time.perf_counter()
            # Read back live from the structures just scanned: addresses and text come from AE now, and none are kept.
            latest=ParameterCatalog(p);require_support(p,latest.profile);after_actual=_host_names(p)
            if _project_context[(p.pid,p.creation_time)]!=token:raise RuntimeError('写入后工程发生变化，恢复记录已保留')
            verified=self._records(p,latest,after_actual,progress)
            for key,e in changes.items():
                expected=e['original'] if restore else pack_translation(e['original'],values[key],latest.profile.encoding)
                found=[r for r in verified if r['id']==key]
                if not found or any(r['text']!=expected for r in found):raise RuntimeError('下拉选项回读不一致，恢复记录已保留')
                e['state']='restored' if restore else 'applied'
            _save(self.path,d)
            done=time.perf_counter();timings=getattr(catalog,'timings',{})
            record_read_timing({'event':'popupWrite','popups':len(changes),'operations':len(operations),'structureMs':(scanned-started)*1000,'scanMs':timings.get('scanMs',0),'scanTarget':timings.get('scanTarget','cache'),'nativeMs':(written-scanned)*1000,'readbackMs':(done-written)*1000})
            pending=sum(1 for k in wanted-set(changes) if d['entries'].get(k,{}).get('state')!='restored')
            return {'changed':len(operations),'parameters':len(changes),'pending':pending,'requiresControlRebuild':True}
    def apply(self,values,binding,progress=None,cancelled=None):return self._change(values,binding,progress=progress,cancelled=cancelled)
    def restore(self,keys=None,progress=None,cancelled=None):
        d=self.journal()
        if not d['entries']:return {'changed':0,'parameters':0,'pending':0}
        # Enumeration failure is not proof the old AE exited.
        if not host_alive(d['host']):
            _save(self.path,{'schemaVersion':1,'entries':{}});return {'changed':0,'parameters':0,'pending':0,'oldProcessEnded':True}
        return self._change(None if keys is None else dict.fromkeys(keys),None,restore=True,progress=progress,cancelled=cancelled)
