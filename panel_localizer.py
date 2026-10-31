"""CEP / plaintext ScriptUI localisation. AI supplies text, never executable patches."""
from dataclasses import dataclass
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit,unquote
import codecs,json,os,re,tempfile,xml.etree.ElementTree as ET
from panel_files import Files,inside,digest,document
from runtime import data_root,asset_root,write_json_atomic
import jsx_labels
import panel_cache

OVERLAY='.ae-localizer-ui.js'
MARK='<!-- ae-localizer-ui-v1 -->'
ATTRS=('title','placeholder','aria-label','aria-description','alt','data-tooltip','data-tip','data-hint','data-original-title','data-bs-title')
SKIP={'script','style','noscript','code','pre','svg','canvas','iframe'}
MAX_FILE=8*1024*1024
MAX_TOTAL=32*1024*1024
MAX_TERMS=2000

@dataclass(frozen=True)
class Target:
    kind:str
    root:str
    entry:str
    identifier:str
    name:str
    version:str=''
    signed:bool=False
    reason:str=''
    @property
    def key(self):return digest((self.kind+'\0'+os.path.normcase(str(Path(self.root).resolve()))+'\0'+self.identifier+'\0'+self.entry).encode('utf-8'))

def decode(raw):
    for bom,encoding in ((codecs.BOM_UTF8,'utf-8-sig'),(codecs.BOM_UTF16_LE,'utf-16'),(codecs.BOM_UTF16_BE,'utf-16')):
        if raw.startswith(bom):return raw.decode(encoding),encoding,bom
    try:return raw.decode('utf-8'),'utf-8',b''
    except UnicodeDecodeError:
        try:return raw.decode('mbcs'),'mbcs',b''
        except (UnicodeError,LookupError):raise ValueError('文件编码无法严格识别') from None

def encode(text,encoding,bom):
    if encoding=='utf-16':return bom+text.encode('utf-16-le' if bom==codecs.BOM_UTF16_LE else 'utf-16-be')
    return text.encode(encoding)

def inspect(path):
    p=Path(path).absolute()
    if p.is_dir():
        manifest=inside(p,'CSXS/manifest.xml')
        if not manifest.is_file():return Target('unsupported',str(p),'','',p.name,reason='未找到 CEP manifest；UXP 与原生插件不支持')
        raw=manifest.read_bytes()
        if len(raw)>MAX_FILE or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('扩展 manifest 不支持外部实体或超长内容')
        tree=ET.fromstring(raw);identifier=tree.get('ExtensionBundleId')
        if not identifier:raise ValueError('扩展缺少标识')
        if identifier in ('com.verse.aelocalizer','com.verse.aelocalizer.bridge'):raise ValueError('不修改工具自身的后台连接扩展')
        return Target('cep',str(p),'CSXS/manifest.xml',identifier,tree.get('ExtensionBundleName') or p.name,tree.get('ExtensionBundleVersion',''),inside(p,'META-INF/signatures.xml').is_file())
    if p.suffix.lower()=='.jsx':
        inside(p.parent,p.name)
        return Target('jsx',str(p.parent),p.name,p.name,p.stem)
    return Target('unsupported',str(p.parent),p.name,p.name,p.stem,reason='JSXBIN、混淆脚本、UXP 和原生插件界面不支持')

def prepare_plaintext_script(source_path,output_path,*,decoder=None,cancelled=lambda:False):
    """Backend-only extension point; no bundled decoder, automatic registration or execution.

    An explicitly supplied decoder receives bytes and returns plaintext bytes or str.
    The callback is trusted caller code, not a script/plugin discovered from disk.
    Only the independently named output copy can be created by this function.
    Recognition is conservative and does not certify full ExtendScript validity.
    """
    if cancelled():raise RuntimeError('已取消准备明文脚本')
    if decoder is not None and not callable(decoder):raise TypeError('decoder 必须是调用方明确提供的函数')
    source=Path(source_path).absolute();output=Path(output_path).absolute()
    if source.suffix.lower() not in ('.jsx','.jsxbin'):raise ValueError('源文件须为 JSX 或 JSXBIN')
    if output.suffix.lower()!='.jsx':raise ValueError('明文副本须使用 .jsx 扩展名')
    inside(source.parent,source.name);inside(output.parent,output.name)
    if os.path.normcase(str(source.resolve()))==os.path.normcase(str(output.resolve())):raise ValueError('明文副本不能覆盖源脚本')
    if output.exists():raise ValueError('明文副本已存在，不覆盖现有文件')
    with source.open('rb') as f:raw=f.read(MAX_FILE+1)
    if len(raw)>MAX_FILE:raise ValueError('源脚本超过 8 MB 上限')
    if cancelled():raise RuntimeError('已取消准备明文脚本')
    result=decoder(raw) if decoder is not None else raw
    if cancelled():raise RuntimeError('已取消准备明文脚本')
    if not isinstance(result,(bytes,str)):raise ValueError('解码接口须返回明文字节或字符串')
    if len(result)>MAX_FILE:raise ValueError('明文脚本超过 8 MB 上限')
    result=result.encode('utf-8') if isinstance(result,str) else result
    if not result or len(result)>MAX_FILE:raise ValueError('明文脚本为空或超过 8 MB 上限')
    text,_,_=decode(result)
    jsx_labels.analyse(text)  # Preserve all unsupported-syntax and label-use guards.
    if cancelled():raise RuntimeError('已取消准备明文脚本')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent,prefix='.ael-plaintext-',delete=False) as f:
            temporary=f.name;f.write(result);f.flush();os.fsync(f.fileno())
        if cancelled():raise RuntimeError('已取消准备明文脚本')
        # Windows rename never replaces an existing destination. Other hosts use an exclusive hard link.
        if os.name=='nt':os.rename(temporary,output);temporary=None
        else:os.link(temporary,output)
    finally:
        if temporary:
            try:os.unlink(temporary)
            except OSError:pass
    return inspect(output)

def discover(executables=(),extra=(),cancelled=lambda:False):
    folders=[];app=Path(os.environ.get('APPDATA',Path.home()/'AppData/Roaming'))
    folders.append(app/'Adobe/CEP/extensions')
    for env in ('ProgramFiles','ProgramFiles(x86)'):
        if os.environ.get(env):folders.append(Path(os.environ[env])/'Common Files/Adobe/CEP/extensions')
    candidates=list(extra)
    for root in folders:
        if root.is_dir():candidates.extend(p for p in root.iterdir() if p.is_dir())
    for exe in executables:
        scripts=Path(exe).parent/'Scripts'
        for root in (scripts,scripts/'ScriptUI Panels'):
            if root.is_dir():candidates.extend(p for p in root.iterdir() if p.suffix.lower() in ('.jsx','.jsxbin'))
    result={};warnings=[]
    for p in candidates:
        if cancelled():raise RuntimeError('已取消发现')
        try:t=inspect(p);result[t.key]=t
        except (ValueError,OSError,ET.ParseError) as error:warnings.append(Path(p).name+'：'+str(error))
    return sorted(result.values(),key=lambda t:(t.kind,t.name.casefold(),t.root)),warnings

class Page(HTMLParser):
    def __init__(self,text):
        super().__init__(convert_charrefs=True);self.terms=[];self.links=[];self.stack=[];self.csp=False;self.feed(text)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs);blocked=tag in SKIP or attrs.get('contenteditable','').lower() not in ('','false') or 'contenteditable' in attrs and attrs['contenteditable']!='false' or any(x in (attrs.get('class') or '') for x in ('monaco','ace_editor','CodeMirror'))
        skip=blocked or bool(self.stack and self.stack[-1][1])
        if tag not in ('input','meta','link','img','br','hr','source','area','base','embed','param','wbr'):self.stack.append((tag,skip or tag=='textarea'))
        if tag=='meta' and (attrs.get('http-equiv') or '').lower()=='content-security-policy':self.csp=True
        if tag in ('iframe','frame','a','script'):
            link=attrs.get('src') or attrs.get('href')
            if link:self.links.append((tag,link))
        if not skip:
            for attr in ATTRS:
                if attrs.get(attr):self.terms.append((attrs[attr],'tip' if attr in ('title','data-tooltip','data-tip','data-hint','data-original-title','data-bs-title') else 'text'))
    def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs);self.handle_endtag(tag)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:del self.stack[i:];break
    def handle_data(self,text):
        if self.stack and self.stack[-1][1]:return
        if text.strip():self.terms.append((text.strip(),'text'))

def local_link(root,parent,link):
    url=urlsplit(link)
    if url.scheme or url.netloc or url.path.startswith(('/','\\')):raise ValueError('远程或绝对页面不注入')
    p=(Path(parent).parent/unquote(url.path)).resolve()
    try:relative=p.relative_to(Path(root).resolve()).as_posix()
    except ValueError:raise ValueError('页面引用越出目标目录') from None
    inside(root,relative);return relative

def placeholders(text):
    return sorted(re.findall(r'\$\{[^{}\r\n]+\}|\{[A-Za-z_][\w.:-]*\}|%(?:\d+\$)?[sdif]|%\d+|\\[nrt]',text))

def validate_text(source,text):
    if not isinstance(text,str) or not text.strip() or len(text)>12000 or '\0' in text or any(0xd800<=ord(c)<=0xdfff for c in text):raise ValueError('界面译文为空、过长或含非法字符')
    if placeholders(source)!=placeholders(text):raise ValueError('译文占位符或转义不一致')
    if re.findall(r'\r\n|\r|\n|\t',source)!=re.findall(r'\r\n|\r|\n|\t',text):raise ValueError('译文换行或制表符不一致')
    return text

class Localizer:
    def __init__(self,target,folder=None):
        self.target=target;self.folder=Path(folder) if folder else data_root()/'panels'
        self.home=self.folder/target.key
        binding={'kind':target.kind,'root':os.path.normcase(str(Path(target.root).resolve())),'id':target.identifier,'entry':target.entry}
        if target.kind=='cep':binding['manifest']=digest(inside(target.root,target.entry).read_bytes())
        self.files=Files(target.root,self.home/'recovery',binding,label=target.name)
    def dictionary(self):
        d=document(self.home/'dictionary.json',{})
        if not isinstance(d,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in d.items()):raise ValueError('词典格式无效')
        return d
    def save(self,values):
        for k,v in values.items():validate_text(k,v)
        self.dictionary();self.home.mkdir(parents=True,exist_ok=True);write_json_atomic(self.home/'dictionary.json',values)
    def _read(self,rel,baseline=True):
        p=inside(self.target.root,rel)
        if p.stat().st_size>MAX_FILE:raise ValueError('文件超过 8 MB 扫描上限：'+rel)
        raw=self.files.baseline(rel) if baseline else p.read_bytes()
        if raw is None:raise ValueError('目标文件不存在：'+rel)
        return raw
    def cached_report(self,cancelled=lambda:False):
        """Return only a validated existing report; never start a scan."""
        if cancelled():raise RuntimeError('已取消读取')
        return panel_cache.get(self.target.key,self.target.root,self.files,(jsx_labels.REVISION,MAX_FILE,MAX_TOTAL,MAX_TERMS),cancelled)
    def extract(self,cancelled=lambda:False,*,force=False):
        if cancelled():raise RuntimeError('已取消读取')
        rules=(jsx_labels.REVISION,MAX_FILE,MAX_TOTAL,MAX_TERMS)
        if not force:
            cached=self.cached_report(cancelled)
            if cached is not None:return cached
        report=self._extract(cancelled)
        stamps=report.pop('_stamps');report['readMode']='scan'
        if cancelled():raise RuntimeError('已取消读取')
        panel_cache.put(self.target.key,self.target.root,self.files,rules,report,stamps)
        return report
    def _extract(self,cancelled):
        t=self.target
        if t.kind=='unsupported':raise ValueError(t.reason)
        warnings=[];terms={};hashes={};pages=[];sites=[];total=0;limit=False;stamps={};skipped=[]
        def read(rel,optional=False):
            nonlocal total
            if cancelled():raise RuntimeError('已取消读取')
            stamps[rel]=panel_cache.signature(inside(t.root,rel))
            # Scripts are only searched for text and never changed, so an oversized bundle is skipped; pages and the JSX entry are required.
            if optional:
                size=inside(t.root,rel).stat().st_size
                if size>MAX_FILE or total+size>MAX_TOTAL:
                    reason='单文件大小超限' if size>MAX_FILE else '扫描总量超限'
                    skipped.append(dict(file=rel,reason=reason))
                    warnings.append(f'未扫描超过 {MAX_FILE//1048576} MB 的脚本：{rel}' if size>MAX_FILE else '已达 32 MB 扫描总量，未扫描：'+rel);return None
            raw=self._read(rel);total+=len(raw)
            if total>MAX_TOTAL:raise ValueError('扫描超过 32 MB 上限，请缩小目标')
            actual=inside(t.root,rel).read_bytes();hashes[rel]=digest(actual)
            return decode(raw)[0]
        def add(text,kind,rel,line=None):
            nonlocal limit
            if len(text)>12000 or not re.search('[A-Za-z]',text) or re.match(r'^(?:https?://|[\w.-]+\.(?:js|png|json|css)$)',text):return
            if text not in terms and len(terms)>=MAX_TERMS:limit=True;return
            row=terms.setdefault(text,dict(text=text,kind=kind,sources=[]))
            source=rel+((':'+str(line)) if line else '')
            if source not in row['sources']:row['sources'].append(source)
        if t.kind=='jsx':
            source=read(t.entry)
            try:sites,warnings=jsx_labels.analyse(source)
            except ValueError as error:raise ValueError(f'{t.entry}: {error}') from error
            for s in sites:add(s['text'],s['kind'],t.entry,source.count('\n',0,s.get('outer',(s['a'],))[0])+1)
        else:
            manifest=read(t.entry);tree=ET.fromstring(manifest)
            if re.search(r'content-security-policy',manifest,re.I):raise ValueError('扩展声明了 CSP，首版不自动修改')
            queue=[]
            for el in tree.iter():
                if el.tag.split('}')[-1]=='MainPath' and el.text:queue.append(local_link(t.root,Path(t.root)/'root.html',el.text.strip()))
            if not queue:raise ValueError('manifest 未找到本地 HTML 入口')
            visited=set()
            while queue:
                rel=queue.pop(0)
                if rel in visited:continue
                visited.add(rel)
                if len(visited)>200:raise ValueError('页面引用过多，已停止')
                if Path(rel).suffix.lower() not in ('.html','.htm'):warnings.append('未覆盖页面：'+rel);continue
                text=read(rel);parser=Page(text)
                if parser.csp:raise ValueError('页面含 CSP，首版不自动修改：'+rel)
                pages.append(rel)
                for label,kind in parser.terms:add(label,kind,rel)
                for tag,link in parser.links:
                    try:child=local_link(t.root,Path(t.root)/rel,link)
                    except ValueError as error:warnings.append(str(error));continue
                    if Path(child).suffix.lower() in ('.html','.htm'):queue.append(child)
                    elif tag=='script' and Path(child).suffix.lower()=='.js' and not any(p in ('node_modules','vendor','vendors') for p in Path(child).parts):
                        js=read(child,optional=True)
                        if js is None:continue
                        # Only explicitly named UI properties; never arbitrary code string constants.
                        for m in re.finditer(r'\b(?:label|caption|tooltip|helpTip|placeholder|title|textContent|innerText)\s*[:=]\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')',js):
                            add(jsx_labels.unquote(m.group(1)),'tip' if m.group().startswith(('tooltip','helpTip','title')) else 'text',child,js.count('\n',0,m.start())+1)
            warnings.append('Canvas、自绘、跨域页面及无法静态确认的动态文案不保证覆盖')
        if limit:warnings.append('达到 2000 词条上限；本次只返回已列出的词条')
        return dict(target=t.key,terms=list(terms.values()),hashes=hashes,pages=pages,sites=sites,warnings=sorted(set(warnings)),
                    diagnostics=dict(scannedFiles=len(hashes),scannedBytes=total,skippedFiles=skipped,truncated=limit,coverage='只统计静态可确认词条，不能据此判断整个面板已全部汉化'),_stamps=stamps)
    def preview(self,report,translations,allow_signed=False):
        t=self.target
        if report.get('target')!=t.key:raise ValueError('读取结果属于其他安装副本')
        if t.signed and not allow_signed:raise ValueError('带签名扩展默认不写入，需先确认签名影响')
        for rel,sha in report['hashes'].items():
            if digest(inside(t.root,rel).read_bytes())!=sha:raise ValueError('读取后文件已变化，请重新读取界面文字：'+rel)
        allowed={r['text'] for r in report['terms']};translations={k:validate_text(k,v) for k,v in translations.items() if k in allowed and v}
        if not translations:raise ValueError('没有可应用的译文')
        if t.kind=='jsx':
            raw=self._read(t.entry);source,enc,bom=decode(raw);sites,_=jsx_labels.analyse(source)
            changed=jsx_labels.apply(source,sites,translations)
            jsx_labels.analyse(changed) # syntax/escape recogniser must accept generated source too
            return {t.entry:encode(changed,enc,bom)}
        changes={}
        for rel in report['pages']:
            raw=self._read(rel);text,enc,bom=decode(raw)
            if MARK in text:raise ValueError('基准页面已有汉化标记，未重复注入')
            ref=os.path.relpath(OVERLAY,Path(rel).parent).replace('\\','/')
            addition=MARK+'<script src="'+ref+'"></script>'
            m=re.search(r'</body\s*>',text,re.I);at=m.start() if m else len(text)
            changes[rel]=encode(text[:at]+addition+text[at:],enc,bom)
        overlay=inside(t.root,OVERLAY)
        state=self.files.state()
        if overlay.exists() and not (state and state['status']=='applied' and any(x['path']==OVERLAY for x in state['files'])):raise ValueError('覆盖层文件名已被占用，未覆盖')
        script=(asset_root()/'scripts'/'panel_overlay.js').read_text(encoding='utf-8')
        changes[OVERLAY]=script.replace('/*DICTIONARY*/{}',json.dumps(translations,ensure_ascii=True)).encode('utf-8')
        return changes
    def apply(self,report,translations,allow_signed=False,cancelled=lambda:False):
        changes=self.preview(report,translations,allow_signed)
        try:return self.files.apply(changes,cancelled)
        finally:panel_cache.clear(self.target.key)
    def restore(self):
        try:return self.files.restore()
        finally:panel_cache.clear(self.target.key)
