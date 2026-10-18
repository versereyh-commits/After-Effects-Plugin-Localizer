"""Deterministic completion of the supported x64 AE layout family.

No writes. Export semantics and primary MSVC RTTI identify candidates; the full
running registry must independently match the scripting catalog before use.
"""
from pathlib import Path
import hashlib,struct
import capstone
from adapter_analysis import ModuleTools
from ae_profiles import Profile,nameless

def unique(values,label):
    values=set(values)
    if len(values)!=1:raise ValueError(label+'未找到唯一证据')
    return values.pop()

class Resolver(ModuleTools):
    def symbol(self,module,prefix,suffix=None):
        symbols=getattr(getattr(self.module(module)['pe'],'DIRECTORY_ENTRY_EXPORT',None),'symbols',[])
        return unique((s.address for s in symbols if s.name and not s.forwarder and s.name.decode('ascii','replace').startswith(prefix) and (suffix is None or s.name.decode('ascii','replace').endswith(suffix))),prefix)
    def instructions(self,module,rva,size=256):
        data=self.span(module,rva,size,code=True);result=[]
        for ins in self.dis.disasm(data,rva):
            result.append(ins)
            if ins.mnemonic=='ret':break
        return result
    def writable(self,module,rva):
        section=self.module(module)['pe'].get_section_by_rva(rva)
        return bool(section and section.Characteristics&0x80000000)
    def primary_vtable(self,typename):
        pe=self.module('FLT.dll')['pe'];image=pe.get_memory_mapped_image();result=[];at=0;needle=typename.encode()+b'\0'
        while True:
            at=image.find(needle,at)
            if at<0:break
            descriptor=at-16;at+=1;start=0
            while True:
                found=image.find(struct.pack('<I',descriptor),start)
                if found<0:break
                start=found+1;col=found-12
                if col<0 or col+24>len(image):continue
                signature,offset,_,_,_,self_rva=struct.unpack_from('<IIIIII',image,col)
                if signature!=1 or offset!=0 or self_rva!=col:continue
                ref=struct.pack('<Q',pe.OPTIONAL_HEADER.ImageBase+col);position=0
                while True:
                    position=image.find(ref,position)
                    if position<0:break
                    vt=position+8;position+=1
                    section=pe.get_section_by_rva(vt)
                    if vt%8 or not section or section.Characteristics&0x80000000:continue
                    function=struct.unpack_from('<Q',image,vt)[0]-pe.OPTIONAL_HEADER.ImageBase
                    function_section=pe.get_section_by_rva(function)
                    if function_section and function_section.Characteristics&0x20000000:result.append(vt)
        return unique(result,typename+'主虚表')
    def manager(self):
        # The integer-ID overload obtains the registry manager through a tiny
        # getter. Do not mistake unrelated security-cookie RIP references for it.
        entry=self.symbol('FLT.dll','?FLTp_GetFCAddress@@',suffix='@@F@Z');result=[]
        for ins in self.instructions('FLT.dll',entry):
            if ins.mnemonic!='call' or ins.operands[0].type!=capstone.CS_OP_IMM:continue
            target=ins.operands[0].imm
            try:body=self.instructions('FLT.dll',target,32)
            except ValueError:continue
            if len(body)!=2 or body[0].mnemonic!='mov' or body[1].mnemonic!='ret':continue
            operands=body[0].operands
            if len(operands)!=2 or operands[0].type!=capstone.CS_OP_REG or body[0].reg_name(operands[0].reg)!='rax' or operands[1].type!=capstone.CS_OP_MEM:continue
            mem=operands[1].mem
            if mem.base==capstone.x86_const.X86_REG_RIP:
                rva=body[0].address+body[0].size+mem.disp
                if self.writable('FLT.dll',rva):result.append(rva)
        return unique(result,'效果目录管理器')
    def match_pool(self):
        start=self.symbol('TDB.dll','?GetName@TDB_MatchName@@',suffix='QEBAPEBEXZ');seen=set();result=[]
        def walk(rva,depth):
            if depth>2 or rva in seen:return
            seen.add(rva)
            for ins in self.instructions('TDB.dll',rva,128):
                operands=ins.operands
                if ins.mnemonic=='lea' and len(operands)==2 and operands[0].type==capstone.CS_OP_REG and ins.reg_name(operands[0].reg) in ('rax','rcx') and operands[1].type==capstone.CS_OP_MEM and operands[1].mem.base==capstone.x86_const.X86_REG_RIP:
                    target=ins.address+ins.size+operands[1].mem.disp
                    if self.writable('TDB.dll',target):result.append(target)
                if ins.mnemonic=='call' and operands[0].type==capstone.CS_OP_IMM:
                    walk(operands[0].imm,depth+1)
        walk(start,0);return unique(result,'参数标识池')
    def display_cache(self,entry):
        aliases={'rcx'};result=[]
        for ins in self.instructions('TDB.dll',entry):
            operands=ins.operands
            if ins.mnemonic=='mov' and len(operands)==2 and all(o.type==capstone.CS_OP_REG for o in operands):
                dst,src=(ins.reg_name(o.reg) for o in operands)
                if src in aliases:aliases.add(dst)
                else:aliases.discard(dst)
            if ins.mnemonic=='lea' and len(operands)==2 and operands[0].type==capstone.CS_OP_REG and ins.reg_name(operands[0].reg)=='rax' and operands[1].type==capstone.CS_OP_MEM:
                mem=operands[1].mem
                if ins.reg_name(mem.base) in aliases and not mem.index and 0x80<=mem.disp<=0x800:result.append(mem.disp)
        return unique(result,'参数显示缓存')
    def complete_static(self,host_version):
        fields={e['field']:e['value'] for e in self.getters()}
        missing=[k for k in ('name_get','match_get','category_get','parent_get','stream_match_get','count_get','display_get','group_vector') if k not in fields]
        if missing:raise ValueError('未找到唯一的名称接口：'+'、'.join(missing))
        for field,value in (('name_offset',16),('match_offset',64),('category_offset',96),('parent_offset',32),('guid_offset',8)):
            if fields.get(field)!=value:raise ValueError('当前名称字段族不匹配：'+field)
        count=fields['count_get'];raw=self.span('TDB.dll',count,19,code=True)
        if raw.startswith(bytes.fromhex('488b81')):
            if raw[7:10]!=bytes.fromhex('482b81') or raw[14:19]!=bytes.fromhex('48c1f803c3'):raise ValueError('命名参数向量指令不匹配')
            named=True;count_bytes=raw[:14];named_map=fields['group_vector']+8
        elif raw[:2]==bytes.fromhex('8b81') and raw[6]==0xc3:
            named=False;count_bytes=raw[:7];named_map=struct.unpack_from('<I',raw,2)[0]-8
            # The named group's stream list (head, then its size, which the count getter reads). Up to 26.0 it sat
            # right after the indexed vector's begin (group_vector+8); AE 26.5 moved it further down. Its place is
            # taken from the getter's own instruction; the self-check then walks every real parameter tree with it.
            if named_map%8 or not fields['group_vector']<named_map<=0x800:raise ValueError('命名参数映射位置不合理（%#x）'%named_map)
        else:raise ValueError('尚未识别命名参数容器')
        codec=self.exe.parent/'U.dll'
        if codec.is_symlink() or not codec.is_file():raise ValueError('名称编码模块缺失或是链接')
        return Profile(version=self.version,host_version=host_version,flt_sha=self.modules['FLT.dll']['sha256'],tdb_sha=self.modules['TDB.dll']['sha256'],
            manager=self.manager(),registry_vector=0,fc_vtable=self.primary_vtable('.?AVFLT_FCSpec@@'),root_vtable=self.primary_vtable('.?AVFLT_FCSeqSpec@@'),
            name_get=fields['name_get'],match_get=fields['match_get'],category_get=fields['category_get'],parent_get=fields['parent_get'],stream_match_get=fields['stream_match_get'],
            count_get=count,count_bytes=count_bytes,display_get=fields['display_get'],match_pool=self.match_pool(),display_cache=self.display_cache(fields['display_get']),group_vector=fields['group_vector'],
            named_vector=named,named_map=named_map,codec_sha=hashlib.sha256(codec.read_bytes()).hexdigest(),native_dynamic=True,enabled=False)

def _readings(raw:bytes,encoding:str)->set[str]:
    """How AE's scripting may report a name field: in the build's encoding, or — only for bytes that encoding
    cannot read (a plugin shipping GBK names, a Chinese internal name) — one character per byte, as AE shows
    them. Either way the bytes are matched exactly, never loosely."""
    raw=raw.split(b'\0',1)[0]
    try:return {raw.decode(encoding)}
    except UnicodeDecodeError:pass
    found={raw.decode('latin-1')}
    try:found.add(raw.decode('cp1252'))
    except UnicodeDecodeError:pass
    return found

class CatalogMismatch(ValueError):
    """No unique registry vector. `details` says how the closest candidates differed from AE's own catalog:
    effect names and field bytes only, never addresses."""
    def __init__(self,message,details):super().__init__(message);self.details=details

FIELD_LABELS={'displayName':'显示名称','matchName':'内部标识','category':'分类'}
EXAMPLES=20

def _differences(rows,expected,encoding,key_encoding=None):
    """How the memory rows differ from the host catalog when names are read in `encoding` and internal names in
    `key_encoding` (default: the same); every difference counted, the first EXAMPLES kept."""
    count=0;examples=[];seen=set();key_encoding=key_encoding or encoding
    def note(item):
        nonlocal count
        count+=1
        if len(examples)<EXAMPLES:examples.append(item)
    for name,key,category in rows:
        keys=[k for k in _readings(key,key_encoding) if k in expected]
        if len(keys)!=1 or keys[0] in seen:
            note({'field':'matchName','memoryHex':key.split(b'\0',1)[0].hex(),'memoryText':sorted(_readings(key,key_encoding))});continue
        seen.add(keys[0]);shown,group=expected[keys[0]]
        for field,raw,want in (('displayName',name,shown),('category',category,group)):
            if want not in _readings(raw,encoding):
                note({'matchName':keys[0],'field':field,'memoryHex':raw.split(b'\0',1)[0].hex(),'memoryText':sorted(_readings(raw,encoding)),'host':want})
    for key in sorted(set(expected)-seen)[:max(0,EXAMPLES-len(examples))]:examples.append({'matchName':key,'field':'missing'})
    return count+len(set(expected)-seen),examples

def _host_entries(host_catalog):
    """AE's catalog as matchName -> (displayName, category), and the matchNames it lists more than once.

    AE lists a plugin twice when two copies are installed (its own Plug-ins folder and Common\\Plug-ins\\7.0\\
    MediaCore, or an old and a new version). Copies that agree are one effect and are compared once; the memory
    vector must still hold each matchName exactly once. Copies whose names differ leave no single truth to compare
    against, so the check stops and names them."""
    expected={};variants={};times={};unnamed=[]
    for row in host_catalog:
        key=row['matchName'];value=(row['displayName'],row['category'])
        # An effect a plugin registered without an internal name (AE reports ''): nothing to compare it by.
        # It is counted, not compared; the memory vector must hold exactly as many unnamed entries.
        if not key:unnamed.append(row['displayName']);continue
        times[key]=times.get(key,0)+1;variants.setdefault(key,[])
        if value not in variants[key]:variants[key].append(value)
        expected.setdefault(key,value)
    duplicates=[{'matchName':k,'times':n,'names':[{'displayName':d,'category':c} for d,c in variants[k]]} for k,n in times.items() if n>1]
    conflicts=[d for d in duplicates if len(d['names'])>1]
    if conflicts:
        first=conflicts[0];shown=' / '.join(v['displayName'] for v in first['names'][:3])
        raise CatalogMismatch(f'AE 效果列表里有 {len(conflicts)} 个内部标识对应不同的名称（例如 {first["matchName"]}：{shown}），'
                              '可能同一插件的不同版本装了两份；请删掉多余的一份并重启 AE 后重试',
                              {'hostCount':len(expected),'hostRows':len(host_catalog),'duplicates':duplicates[:EXAMPLES],'conflicts':len(conflicts),'unnamed':unnamed[:EXAMPLES]})
    return expected,duplicates[:EXAMPLES],unnamed

# (names, internal names): the same encoding for both, or -- as AE 2021 (18.0) does -- names in UTF-8 and internal
# names in the system code page (its scripting showed UTF-8 Chinese internal names decoded as GBK, and ''
# where GBK could not decode them).
READINGS=(('utf8','utf8'),('mbcs','mbcs'),('utf8','mbcs'))

def registry_offset(process,profile,host_catalog):return registry_layout(process,profile,host_catalog)['offset']

def registry_layout(process,profile,host_catalog):
    """Read-only exhaustive cross-check of a small bounded manager header: the one vector holding AE's catalog,
    and how AE reads internal names ({'offset', 'matchEncoding'}). When no vector matches, raises
    CatalogMismatch telling how the closest ones differed, so a failure on someone else's AE can be read."""
    base=next(m['base'] for m in process.modules() if m['name'].casefold()=='flt.dll');manager=process.u64(base+profile.manager)
    expected,duplicates,unnamed=_host_entries(host_catalog)
    unnamed_rows=[(row['displayName'],row['category']) for row in host_catalog if not row['matchName']]
    if not 1<len(expected)<32768:raise CatalogMismatch(f'AE 报告的效果数量异常（{len(expected)} 个），不能作为对照',{'hostCount':len(expected),'hostRows':len(host_catalog),'unnamed':unnamed[:EXAMPLES]})
    total=len(expected)+len(unnamed)
    matches=[];vectors=[];candidates=[]
    for offset in range(0,129,8):
        try:begin,end=struct.unpack('<QQ',process.read(manager+offset,16))
        except (RuntimeError,OSError,struct.error):continue
        if begin<end and (end-begin)%16==0 and (end-begin)//16<32768:vectors.append({'offset':offset,'count':(end-begin)//16})
        if end-begin!=total*16:continue
        candidate={'offset':offset};candidates.append(candidate)
        try:
            values=struct.unpack('<'+'Q'*(total*2),process.read(begin,end-begin));rows=[];keys=set()
            for index,address in enumerate(values[::2]):
                raw=process.read(address,160)
                if struct.unpack_from('<Q',raw)[0]!=base+profile.fc_vtable:raise ValueError(f'第 {index+1} 项对象类型不符')
                fields=(raw[16:64],raw[64:96],raw[96:160])
                for label,field in zip(('displayName','matchName','category'),fields):
                    if b'\0' not in field:raise ValueError(f'第 {index+1} 项{FIELD_LABELS[label]}没有结尾符（{field.hex()}）')
                if fields[1].split(b'\0',1)[0] and fields[1] in keys:raise ValueError(f'第 {index+1} 项内部标识重复（{fields[1].split(bytes(1),1)[0].hex()}）')
                keys.add(fields[1]);rows.append(fields)
        except (ValueError,RuntimeError,OSError,struct.error) as error:
            candidate['problem']=str(error) or type(error).__name__;continue
        for encoding,key_encoding in READINGS:
            # Entries AE reports without an internal name (empty, or bytes AE cannot read: it returns '' for those)
            # pair one to one with AE's nameless rows by exact name and category; every other object must be one
            # host row, each field the host's exact reading of its bytes. One reading holds for the whole catalog.
            named=[r for r in rows if not nameless(r[1],key_encoding)];bare=[r for r in rows if nameless(r[1],key_encoding)]
            count,examples=_differences(named,expected,encoding,key_encoding)
            left=list(unnamed_rows)
            for name,key,category in bare:
                pair=next((u for u in left if u[0] in _readings(name,encoding) and u[1] in _readings(category,encoding)),None)
                if pair:left.remove(pair);continue
                count+=1
                if len(examples)<EXAMPLES:examples.append({'field':'unnamed','matchNameHex':key.split(b'\0',1)[0].hex(),'memoryText':sorted(_readings(name,encoding))})
            count+=len(left);examples+=[{'field':'unnamedMissing','host':u[0]} for u in left][:max(0,EXAMPLES-len(examples))]
            label=encoding if encoding==key_encoding else f'{encoding}+{key_encoding}'
            candidate[label]={'differences':count,'examples':examples,'unnamedInMemory':len(bare)}
            # '' when internal names read like the names: the profile then follows the encoding the helper detects.
            if not count:matches.append((offset,'' if key_encoding==encoding else key_encoding));break
    if len(matches)==1:return {'offset':matches[0][0],'matchEncoding':matches[0][1]}
    details={'hostCount':len(expected),'hostRows':len(host_catalog),'duplicates':duplicates,'unnamedCount':len(unnamed),'unnamed':unnamed[:EXAMPLES],'vectors':vectors,'candidates':candidates}
    twice=f'；AE 列表里有 {len(duplicates)} 个效果重复出现（例如 {duplicates[0]["matchName"]}），可能同一插件装了两份，已按一份对比' if duplicates else ''
    if matches:raise CatalogMismatch(f'完整效果目录向量未找到唯一证据（{len(matches)} 处都与 AE 目录一致{twice}）',details)
    raise CatalogMismatch('完整效果目录向量未找到唯一证据（'+_summary(details)+twice+'）',details)

def _summary(details)->str:
    """One line on the closest candidate, for the error shown to the user."""
    total=details['hostCount']+details.get('unnamedCount',0);candidates=details['candidates']
    if not candidates:
        counts='、'.join(str(v['count']) for v in details['vectors'][:6]) or '无'
        return f'AE 报告 {total} 个效果，内存里没有数量相同的目录；找到的目录数量：{counts}'
    readable=[c for c in candidates if 'problem' not in c]
    if not readable:return f'AE 报告 {total} 个效果；数量相同的目录读取时{candidates[0]["problem"]}'
    labels=[a if a==b else f'{a}+{b}' for a,b in READINGS]
    best=min(((c[e]['differences'],e,c) for c in readable for e in labels if e in c),key=lambda x:x[0])
    count,encoding,candidate=best;first=candidate[encoding]['examples'][0] if candidate[encoding]['examples'] else {}
    where=f"，例如 {first.get('matchName') or first.get('memoryHex','?')} 的{FIELD_LABELS.get(first.get('field'),'条目')}" if first else ''
    return f'AE 报告 {total} 个效果，最接近的目录有 {count} 项不一致{where}'


def run_static_analysis(exe,progress=None,cancel=None,*,resolver_factory=Resolver):
    """Offline evidence only: never enables or persists a runtime profile."""
    from adapter_analysis import AnalysisCancelled
    resolver=None;stage='读取模块';report={'schemaVersion':1,'kind':'ae-adapter-analysis','analysisSource':'code','version':'','modules':{},'state':'blocked','runtimeEnabled':False,'hostVerified':False,'candidateReadConfig':{},'fieldEvidence':{},'trace':[],'evidence':[],'missing':['完整宿主目录与参数树一致性校验','原生中文显示、搜索与添加验证','受控中文写入、调节、渲染与恢复验证']}
    def check():
        if cancel and cancel.is_set():raise AnalysisCancelled('自动识别已取消；未启用任何配置')
    def step(text):
        nonlocal stage
        check();stage=text
        if progress:progress('自动识别：'+text)
    try:
        step(stage);resolver=resolver_factory(exe);report['version']=resolver.version
        report['modules']={n:m['sha256'] for n,m in resolver.modules.items()}
        step('识别名称接口与字段');observations=resolver.getters();fields={};proofs={}
        for e in observations:
            key=e['field']
            if key in fields and fields[key]!=e['value']:raise ValueError('字段证据冲突：'+key)
            fields[key]=e['value'];proofs[key]=e['evidenceId']
        if not fields:raise ValueError('没有可复现的名称字段')
        step('核查静态结构与容器');resolver.complete_static('')
        step('复核模块未变化');resolver.verify_files();check()
        report.update(state='candidate',candidateReadConfig=fields,fieldEvidence=proofs,summary='离线静态识别通过；仍需用户启动自检及原生界面核对。')
    except AnalysisCancelled:raise
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as error:
        # Only a module that changed mid-run (an AE update in progress) may pass
        # on retry; unreadable modules need another install path, and a
        # structure mismatch needs new rules — AI evidence cannot replace them.
        kind='transient' if stage=='复核模块未变化' else 'module' if stage=='读取模块' else 'structure'
        check();report.update(failureStage=stage,failureKind=kind,summary=stage+'失败：'+str(error))
    finally:
        if resolver:
            report['evidence']=list(resolver.evidence.values());resolver.close()
    return report
