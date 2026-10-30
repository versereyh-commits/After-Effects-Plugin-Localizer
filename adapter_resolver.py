"""Deterministic completion of the supported x64 AE layout family.

No writes. Export semantics and primary MSVC RTTI identify candidates; the full
running registry must independently match the scripting catalog before use.
"""
from pathlib import Path
import hashlib,struct
import capstone
from adapter_analysis import ModuleTools,unique
from ae_profiles import Profile,nameless,fc_fields,fc_size,fc_object,catalog_addresses

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
    def primary_vtable(self,typename):return unique(self.vtables('FLT.dll',typename),typename+'主虚表')
    def registry_list(self)->bool:
        """Whether the catalog is AE 2018's LIST.dll list of inline objects: its by-ID lookup then returns the object
        itself (FLT_FCSpec*, through LIST_GetAddress); later builds return a shared_ptr held in a vector."""
        names={'?FLTp_GetFCAddress@@YAPEAVFLT_FCSpec@@F@Z':True,'?FLTp_GetFCAddress@@YA?AV?$shared_ptr@VFLT_FCSpec@@@boost@@F@Z':False}
        symbols=getattr(getattr(self.module('FLT.dll')['pe'],'DIRECTORY_ENTRY_EXPORT',None),'symbols',[])
        found={names[n] for n in (s.name.decode('ascii','replace') for s in symbols if s.name and not s.forwarder) if n in names}
        if len(found)!=1:raise ValueError('效果目录容器未找到唯一证据')
        return found.pop()
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
        # const unsigned char* since AE 2019; const char* in AE 2018.
        start=self.symbol('TDB.dll','?GetName@TDB_MatchName@@',suffix=('QEBAPEBEXZ','QEBAPEBDXZ'));seen=set();result=[]
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
    def named_container(self)->bool:
        """Whether the named group keeps its streams in a std::map (AE 26.5) rather than an unordered_map's list:
        every exported LookupStream returns that container's iterator, so its type is spelled in their names."""
        symbols=getattr(getattr(self.module('TDB.dll')['pe'],'DIRECTORY_ENTRY_EXPORT',None),'symbols',[])
        kinds={'tree' if '_Tree_' in n else 'list' if '_List_' in n else None for n in (s.name.decode('ascii','replace') for s in symbols if s.name and not s.forwarder) if n.startswith('?LookupStream@TDB_NamedStreamGroup@@')}
        if kinds not in ({'tree'},{'list'}):raise ValueError('尚未识别命名参数容器')
        return kinds=={'tree'}
    def tree_evidence(self,named_map):
        """The const LookupStream must itself read the map's head at `named_map` and use MSVC's tree node: _Isnil
        at +0x19, the key (the parameter's match name) at +0x20 and the stream pointer at +0x30."""
        entry=self.symbol('TDB.dll','?LookupStream@TDB_NamedStreamGroup@@AEBA?AV?$_Tree_const_iterator@')
        seen=set()
        for ins in self.instructions('TDB.dll',entry,512):
            operands=ins.operands
            for index,o in enumerate(operands):
                if o.type!=capstone.CS_OP_MEM or o.mem.base in (0,capstone.x86_const.X86_REG_RIP) or o.mem.index:continue
                disp=o.mem.disp
                if disp==named_map and ins.mnemonic in ('mov','lea','cmp'):seen.add('head')
                if disp==0x19 and ins.mnemonic=='cmp' and index==0 and o.size==1 and len(operands)==2 and operands[1].type==capstone.CS_OP_IMM and operands[1].imm==0:seen.add('nil')
                if disp==0x20 and ins.mnemonic=='lea' and index==1:seen.add('key')
                if disp==0x30 and ins.mnemonic=='mov' and index==1 and o.size==8:seen.add('value')
        if seen!={'head','nil','key','value'}:raise ValueError('命名参数树结构不匹配')
    def complete_static(self,host_version):
        fields={e['field']:e['value'] for e in self.getters()}
        missing=[k for k in ('name_get','match_get','category_get','parent_get','stream_match_get','count_get','display_get','group_vector') if k not in fields]
        if missing:raise ValueError('未找到唯一的名称接口：'+'、'.join(missing))
        # The three name fields keep their sizes (48, 32, 64 bytes); only where they start may differ (AE 2018: +4).
        name=fields.get('name_offset')
        for field,value in (('name_offset',name if type(name) is int and 8<=name<=64 else None),('match_offset',(name or 0)+48),('category_offset',(name or 0)+80),('parent_offset',32),('guid_offset',8)):
            if value is None or fields.get(field)!=value:raise ValueError('当前名称字段族不匹配：'+field)
        fc_vtable,fc_tag,fc_tail=self.fc_layout(name)
        count=fields['count_get'];raw=self.span('TDB.dll',count,19,code=True)
        if raw.startswith(bytes.fromhex('488b81')):
            if raw[7:10]!=bytes.fromhex('482b81') or raw[14:19]!=bytes.fromhex('48c1f803c3'):raise ValueError('命名参数向量指令不匹配')
            # The same vector as the indexed group's (what the native helper compares the getter with).
            if struct.unpack_from('<I',raw,3)[0]!=fields['group_vector']+8 or struct.unpack_from('<I',raw,10)[0]!=fields['group_vector']:raise ValueError('命名参数向量位置不匹配')
            named=True;tree=False;count_bytes=raw[:14];named_map=fields['group_vector']+8
        elif raw[:2]==bytes.fromhex('8b81') and raw[6]==0xc3:
            named=False;count_bytes=raw[:7];named_map=struct.unpack_from('<I',raw,2)[0]-8
            # The named group's container (its head, then its size, which the count getter reads). Up to 26.0 it is
            # an unordered_map whose list sits right after the indexed vector's begin (group_vector+8); AE 26.5 made
            # it a std::map at the start of the class's own members — the same offset as the indexed group's vector,
            # which is a different class. Which one it is comes from LookupStream's exported type; the self-check
            # then walks every real parameter tree with it.
            tree=self.named_container()
            if tree:
                if named_map%8 or not 0x80<=named_map<=0x800:raise ValueError('命名参数映射位置不合理（%#x）'%named_map)
                self.tree_evidence(named_map)
            elif named_map%8 or not fields['group_vector']<named_map<=0x800:raise ValueError('命名参数映射位置不合理（%#x）'%named_map)
        else:raise ValueError('尚未识别命名参数容器')
        codec=self.exe.parent/'U.dll'
        if codec.is_symlink() or not codec.is_file():raise ValueError('名称编码模块缺失或是链接')
        return Profile(version=self.version,host_version=host_version,flt_sha=self.modules['FLT.dll']['sha256'],tdb_sha=self.modules['TDB.dll']['sha256'],
            manager=self.manager(),registry_vector=0,fc_vtable=fc_vtable,root_vtable=self.primary_vtable('.?AVFLT_FCSeqSpec@@'),
            name_get=fields['name_get'],match_get=fields['match_get'],category_get=fields['category_get'],parent_get=fields['parent_get'],stream_match_get=fields['stream_match_get'],
            count_get=count,count_bytes=count_bytes,display_get=fields['display_get'],match_pool=self.match_pool(),display_cache=self.display_cache(fields['display_get']),group_vector=fields['group_vector'],
            named_vector=named,named_map=named_map,codec_sha=hashlib.sha256(codec.read_bytes()).hexdigest(),native_dynamic=True,enabled=False,
            named_tree=tree,name_base=name,fc_tag=fc_tag,fc_tail=fc_tail,registry_list=self.registry_list())
    def fc_layout(self,name):
        """How a catalog object is recognised as an FLT_FCSpec: (vtable, tag, tail). AE 2019 and later give the class
        a vtable (+0, display name at +16). AE 2018's has none -- no RTTI, a non-virtual destructor -- and its
        default constructor writes one tag word at +0 and again after the names, and zeroes each name field."""
        if self.vtables('FLT.dll','.?AVFLT_FCSpec@@'):
            if name!=16:raise ValueError('当前名称字段族不匹配：name_offset')
            return self.primary_vtable('.?AVFLT_FCSpec@@'),0,0
        self.symbol('FLT.dll','??1FLT_FCSpec@@QEAA@XZ')   # non-virtual destructor, or this is no known family
        regs={};words={};zeroed=set()
        for ins in self.instructions('FLT.dll',self.symbol('FLT.dll','??0FLT_FCSpec@@QEAA@XZ'),1024):
            ops=ins.operands
            if len(ops)!=2:continue
            if ops[0].type==capstone.CS_OP_REG:
                reg=_family(ins.reg_name(ops[0].reg))
                if ins.mnemonic=='mov' and ops[1].type==capstone.CS_OP_IMM:regs[reg]=ops[1].imm
                elif ins.mnemonic=='xor' and ops[1].type==capstone.CS_OP_REG and ops[0].reg==ops[1].reg:regs[reg]=0
                else:regs.pop(reg,None)
            elif ins.mnemonic=='mov' and ops[0].type==capstone.CS_OP_MEM and ins.reg_name(ops[0].mem.base)=='rcx' and not ops[0].mem.index and ops[1].type==capstone.CS_OP_REG:
                value=regs.get(_family(ins.reg_name(ops[1].reg)))
                if value is None:continue
                if ops[0].size==2 and value:words.setdefault(value&0xffff,set()).add(ops[0].mem.disp)
                if ops[0].size==1 and value==0:zeroed.add(ops[0].mem.disp)
        tags=[(tag,places) for tag,places in words.items() if 0 in places and len(places)==2]
        if len(tags)!=1 or not {name,name+48,name+80}<=zeroed:raise ValueError('效果对象类型标记未找到唯一证据')
        tag,places=tags[0];tail=max(places)
        if tail<name+144 or tail>0x400:raise ValueError('效果对象类型标记位置不合理（%#x）'%tail)
        return 0,tag,tail

def _family(reg:str)->str:
    """The 64-bit register a capstone register name is part of (eax, ax, al -> rax; r8d, r8w -> r8)."""
    if reg.startswith('r') and reg[1:2].isdigit():return reg.rstrip('dwb')
    return {'al':'rax','ax':'rax','eax':'rax','bl':'rbx','bx':'rbx','ebx':'rbx','cl':'rcx','cx':'rcx','ecx':'rcx','dl':'rdx','dx':'rdx','edx':'rdx',
            'sil':'rsi','si':'rsi','esi':'rsi','dil':'rdi','di':'rdi','edi':'rdi','bpl':'rbp','bp':'rbp','ebp':'rbp'}.get(reg,reg)

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

# AE 2020 (17.5) hands its byte names to scripting without decoding them: each byte becomes one UTF-16 unit as a
# signed char, so a byte b >= 0x80 arrives as U+FF00+b (GBK 声道 C9 F9 B5 C0 -> U+FFC9 U+FFF9 U+FFB5 U+FFC0).
# Lossless, and only U+FF80..U+FFFF can be such a byte.
SIGNED=0xff00
def _signed(raw:bytes)->str:return ''.join(chr(b) if b<0x80 else chr(SIGNED+b) for b in raw)
def _signed_bytes(text:str)->bytes|None:
    """The bytes behind a name in that form, or None when it is not one (any other character, or no high byte)."""
    if not any(ord(c)>=0xff80 for c in text) or any(0x80<=ord(c)<0xff80 for c in text):return None
    return bytes(ord(c)-SIGNED if ord(c)>=0xff80 else ord(c) for c in text)
def host_text(text:str,encoding:str)->str:
    """A name AE's scripting reported, as text: AE 2020's byte form decoded in the build's encoding (strictly; bytes
    it cannot read leave the text as reported). Only builds keeping names in the system code page do this; UTF-8
    builds' names (which may hold real halfwidth forms, U+FF61..) come back unchanged."""
    raw=_signed_bytes(text) if isinstance(text,str) and encoding=='mbcs' else None
    if raw is None:return text
    try:return raw.decode(encoding)
    except (UnicodeDecodeError,LookupError):return text
def _host_forms(raw:bytes,encoding:str)->set[str]:
    """The strings AE's scripting may report for these bytes: _readings, or AE 2020's form of the same bytes."""
    raw=raw.split(b'\0',1)[0];return _readings(raw,encoding)|{_signed(raw)}

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
        keys=[k for k in _host_forms(key,key_encoding) if k in expected]
        if len(keys)!=1 or keys[0] in seen:
            note({'field':'matchName','memoryHex':key.split(b'\0',1)[0].hex(),'memoryText':sorted(_readings(key,key_encoding))});continue
        seen.add(keys[0]);shown,group=expected[keys[0]]
        for field,raw,want in (('displayName',name,shown),('category',category,group)):
            if want not in _host_forms(raw,encoding):
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
        try:addresses=catalog_addresses(process,profile,manager+offset)
        except (ValueError,RuntimeError,OSError,struct.error):continue
        vectors.append({'offset':offset,'count':len(addresses)})
        if len(addresses)!=total:continue
        candidate={'offset':offset};candidates.append(candidate)
        try:
            rows=[];keys=set()
            for index,address in enumerate(addresses):
                raw=process.read(address,fc_size(profile))
                if not fc_object(profile,base,raw):raise ValueError(f'第 {index+1} 项对象类型不符')
                fields=tuple(raw[a:b] for a,b in fc_fields(profile))
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
                pair=next((u for u in left if u[0] in _host_forms(name,encoding) and u[1] in _host_forms(category,encoding)),None)
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
