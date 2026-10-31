"""Non-executing ScriptUI recogniser. Unknown syntax is refused, never evaluated."""
from dataclasses import dataclass
import json,re

@dataclass
class Token:
    value:str
    a:int
    b:int
    string:bool=False
    kind:str='code'

REVISION=2

def syntax_error(source,index,message):
    return ValueError(f'{message}（第 {source.count(chr(10),0,index)+1} 行）')

def slash_kind(ts):
    """Accept unambiguous expression starts/ends; refuse ambiguous brace contexts."""
    if not ts:return 'regex'
    p=ts[-1]
    if p.string or p.kind=='regex':return 'division'
    if p.kind=='control_close':return 'regex'
    if p.value in ('}','break','continue','debugger'):return 'unknown'
    if len(ts)>1 and ts[-2].value in ('break','continue') and not ts[-2].string:return 'unknown'
    if p.value in ('return','throw','case','delete','void','typeof','new','in','instanceof','of','else','do'):return 'regex'
    if p.value in ('=','(','[','{',',',':',';','?','!','~','+','-','*','%','&','|','^','<','>','==','===','!=','!==','&&','||','+=','-=','*=','/=','%=','<=','>=','<<','>>','>>>'):return 'regex'
    if p.value in (')',']','++','--') or re.fullmatch(r'[A-Za-z_$][\w$]*|\d+(?:\.\d+)?',p.value):return 'division'
    return 'unknown'

def regex_end(source,start):
    i=start+1;character_class=False
    while i<len(source):
        c=source[i]
        if c in '\r\n':break
        if c=='\\':
            if i+1>=len(source) or source[i+1] in '\r\n':break
            i+=2;continue
        if c=='[':character_class=True
        elif c==']':character_class=False
        elif c=='/' and not character_class:
            i+=1;begin=i
            while i<len(source) and (source[i].isalnum() or source[i] in '_$'):i+=1
            flags=source[begin:i]
            if any(f not in 'gim' for f in flags) or len(set(flags))!=len(flags):raise syntax_error(source,begin,'JSX 正则标志不支持或重复，未修改')
            return i
        i+=1
    raise syntax_error(source,start,'JSX 正则未闭合，未修改')

def unquote(raw):
    body=raw[1:-1]
    def escape(m):
        s=m.group(1)
        if s.startswith('u'):return chr(int(s[1:],16))
        if s.startswith('x'):return chr(int(s[1:],16))
        return {'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','v':'\v','\n':'','\r\n':''}.get(s,s)
    return re.sub(r'\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])',escape,body)

def quote(text):return json.dumps(text,ensure_ascii=True)

def resource_quote(text,delimiter):
    # Resource specifications have their own string parser. Keep literal Unicode inside the specification;
    # only the outer JavaScript string uses \u escapes. A second escaped \u layer displays incorrectly in AE.
    return delimiter+text.replace('\\','\\\\').replace(delimiter,'\\'+delimiter).replace('\r','\\r').replace('\n','\\n').replace('\t','\\t')+delimiter

def tokens(source):
    result=[];i=0;parens=[]
    while i<len(source):
        if source[i].isspace():i+=1;continue
        if source.startswith('//',i):
            n=source.find('\n',i);i=len(source) if n<0 else n;continue
        if source.startswith('/*',i):
            n=source.find('*/',i+2)
            if n<0:raise syntax_error(source,i,'JSX 注释未闭合')
            i=n+2;continue
        if source[i]=='#' and not source[source.rfind('\n',0,i)+1:i].strip():
            if re.match(r'#\s*include\b',source[i:]):raise syntax_error(source,i,'JSX 引用了外部代码，无法确认界面文字用途，未修改')
            n=source.find('\n',i);i=len(source) if n<0 else n;continue
        if source[i] in '\"\'':
            a=i;q=source[i];i+=1
            while i<len(source) and source[i]!=q:
                if source[i]=='\\':i+=3 if source[i+1:i+3]=='\r\n' else 2
                elif source[i] in '\r\n':raise syntax_error(source,a,'JSX 字符串格式不支持')
                else:i+=1
            if i>=len(source):raise syntax_error(source,a,'JSX 字符串未闭合')
            i+=1;result.append(Token(unquote(source[a:i]),a,i,True));continue
        if source[i]=='/':
            mode=slash_kind(result)
            if mode=='unknown':raise syntax_error(source,i,'JSX 斜杠上下文不明确，未修改')
            if mode=='regex':
                end=regex_end(source,i);result.append(Token('\0regex',i,end,kind='regex'));i=end;continue
        # Templates and E4X still require a different parser; never scan their contents as code.
        if source[i]=='`' or (source[i]=='<' and slash_kind(result)!='division' and re.match(r'<\s*(?:/?[A-Za-z!]|>)',source[i:])):raise syntax_error(source,i,'JSX 含模板或 E4X 等未支持语法，未修改')
        m=re.match(r'[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|===|!==|>>>|==|!=|=>|\+\+|--|&&|\|\||[+*/%<>=!&|^-]=|<<|>>|[\s\S]',source[i:]);s=m.group();kind='code'
        if s=='(':
            control=bool(result and result[-1].value in ('if','while','for','with','switch','catch') and not result[-1].string and (len(result)<2 or result[-2].value!='.'))
            each=bool(len(result)>=2 and [t.value for t in result[-2:]]==['for','each'] and not any(t.string for t in result[-2:]))
            parens.append(control or each)
        elif s==')' and parens:
            if parens.pop():kind='control_close'
        result.append(Token(s,i,i+len(s),kind=kind));i+=len(s)
    return result

UI={'button','statictext','panel','group','checkbox','radiobutton','dropdownlist','listbox','tabbedpanel','tab','item'}

def analyse(source):
    if source.lstrip('\ufeff \t\r\n').startswith('@JSXBIN@'):raise ValueError('JSXBIN 编码脚本不支持自动修改')
    ts=tokens(source);stack=[];pairs={}
    for i,t in enumerate(ts):
        if t.string:continue
        if t.value in ('eval','Function','evalFile'):raise syntax_error(source,t.a,'JSX 使用动态执行，无法确认界面文字用途，未修改')
        if t.value in ('(','[','{'):stack.append(i)
        elif t.value in (')',']','}'):
            if not stack or ts[stack[-1]].value!={')':'(',']':'[','}':'{'}[t.value]:raise syntax_error(source,t.a,'JSX 括号不匹配')
            start=stack.pop();pairs[start]=i
    if stack:raise syntax_error(source,ts[stack[-1]].a,'JSX 括号未闭合')
    values=[t.value for t in ts];sites=[];known=set();bindings={};uses={};warnings=[];writes={}
    for i,t in enumerate(ts):
        if not t.string:uses.setdefault(t.value,[]).append(i)
        if not t.string and values[i+1:i+2]==['='] and (i==0 or values[i-1]!='.'):
            writes[t.value]=writes.get(t.value,0)+1
        if i>=2 and values[i-1]=='=' and re.fullmatch(r'[A-Za-z_$][\w$]*',values[i-2]) and t.string and (i+1==len(ts) or values[i+1] in (';',',')):
            bindings.setdefault(values[i-2],[]).append(i)
    def lhs(i):
        name=values[i-2] if i>=2 and values[i-1]=='=' and (i<3 or values[i-3]!='.') and re.fullmatch(r'[A-Za-z_$][\w$]*',values[i-2]) else None
        return name if name and writes.get(name)==1 else None
    def args(i):
        end=pairs[i];out=[];start=i+1;j=start
        while j<end:
            if j in pairs:j=pairs[j]+1;continue
            if values[j]==',':out.append((start,j));start=j+1
            j+=1
        out.append((start,end));return out
    def label(a,b,kind='text',control=None):
        if b-a==1:
            t=ts[a]
            if t.string:sites.append(dict(a=t.a,b=t.b,text=t.value,kind=kind,control=control));return
            name=t.value
            if name in bindings and len(bindings[name])==1 and len(uses[name])==2:
                t=ts[bindings[name][0]];sites.append(dict(a=t.a,b=t.b,text=t.value,kind=kind,control=control));return
        warnings.append('动态或复用的界面文案已跳过')
    for i,t in enumerate(ts):
        if not t.string and values[i:i+3]==['new','Window','(']:
            aa=args(i+2);name=lhs(i)
            if name:known.add(name)
            if len(aa)>1:label(*aa[1],control=name)
        if i>=2 and t.value=='add' and not t.string and values[i-1]=='.' and values[i-2] in known and i+1 in pairs:
            aa=args(i+1)
            if not aa:continue
            a,b=aa[0];name=lhs(i-2)
            if b-a!=1 or not ts[a].string: warnings.append('动态 ScriptUI 资源已跳过');continue
            if ts[a].value.lower() in UI:
                if name:known.add(name)
                if len(aa)>2:
                    a,b=aa[2]
                    if a in pairs and values[a]=='[' and pairs[a]==b-1:
                        for j in range(a+1,b-1):
                            if ts[j].string:label(j,j+1,control=name)
                            elif values[j]!=',':warnings.append('动态列表项已跳过')
                    else:label(a,b,control=name)
            elif re.match(r'\s*(?:group|panel|dialog|palette)\s*\{',ts[a].value,re.I):
                if name:known.add(name)
                resource=ts[a];rt=tokens(resource.value);inner=[]
                # Static resource declarations only. Interpolated resources never arrive as one argument token.
                for j,x in enumerate(rt):
                    if j>=2 and rt[j-1].value==':' and rt[j-2].value in ('text','helpTip') and x.string:
                        inner.append(dict(a=x.a,b=x.b,text=x.value,kind='tip' if rt[j-2].value=='helpTip' else 'text'))
                for x in inner:sites.append({**x,'outer':(resource.a,resource.b,resource.value),'control':name})
        if i>=2 and t.value in ('text','helpTip') and values[i-1]=='.' and values[i-2] in known and values[i+1:i+2]==['=']:
            j=i+2
            while j<len(ts) and values[j]!=';':j+=1
            label(i+2,j,'tip' if t.value=='helpTip' else 'text',values[i-2])
    unsafe=set();unknown_read=False
    for i,t in enumerate(ts):
        if t.value=='text' and i>=1 and values[i-1]=='.' and values[i+1:i+2]!=['=']:unknown_read=True
        if t.string and t.value=='text' and i>=1 and values[i-1]=='[':unknown_read=True
        if t.value=='text' and i>=2 and values[i-1]=='.' and values[i-2] in known and values[i+1:i+2]!=['=']:unsafe.add(values[i-2])
        if t.value in known and not t.string:
            if values[i+1:i+2] not in (['.'],['=']):unknown_read=True
            j=i+1
            while j+1<len(ts) and values[j]=='.':
                prop=values[j+1]
                if prop=='text' and values[j+2:j+3]!=['=']:unsafe.add(t.value)
                if prop=='add' and values[j+2:j+3]==['=']:unsafe.add(t.value)
                j+=2
    if unknown_read:return [],sorted(set(warnings+['脚本读取界面文字或将控件传入未知逻辑，首版不修改标签']))
    out=[];seen=set()
    for s in sites:
        if s['control'] in unsafe:warnings.append('界面文字被代码读取，已跳过该控件');continue
        if not s['text'].strip() or not re.search('[A-Za-z]',s['text']):continue
        key=(s.get('outer',(0,))[0],s['a'],s['b'])
        if key not in seen:out.append(s);seen.add(key)
    return out,sorted(set(warnings))

def apply(source,sites,translations):
    patches={};resources={}
    for s in sites:
        if s['text'] not in translations:continue
        if 'outer' in s:
            a,b,text=s['outer'];resources.setdefault((a,b,text),{})[(s['a'],s['b'])]=resource_quote(translations[s['text']],text[s['a']])
        else:patches[s['a'],s['b']]=quote(translations[s['text']])
    for (a,b,text),inner in resources.items():
        for (start,end),value in sorted(inner.items(),reverse=True):text=text[:start]+value+text[end:]
        patches[a,b]=quote(text)
    for (a,b),value in sorted(patches.items(),reverse=True):source=source[:a]+value+source[b:]
    return source
