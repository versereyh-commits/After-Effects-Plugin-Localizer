"""Update checks against a public release repository.

Each release carries two assets: update.json (the manifest) and the installer it names. The app only
reads https://github.com/<repo>/releases/latest/download/update.json, sends nothing about the user or
this computer, and runs an installer only after its size and SHA256 match the manifest and the user
has agreed. Qt-free so it can be tested on its own."""
from __future__ import annotations
import hashlib,json,os,re,socket,ssl,time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler,ProxyHandler,Request,build_opener,getproxies,getproxies_environment,proxy_bypass

# The public GitHub repository ("owner/name") whose Releases carry update.json. Empty until the user creates
# it: the app then never goes online for updates and the settings card says so.
UPDATE_REPO='versereyh-commits/After-Effects-Plugin-Localizer'
MANIFEST='update.json'
MAX_MANIFEST=64*1024
MAX_RELEASE=1024*1024       # the GitHub API's description of the latest release, the fallback for update.json
CHECK_TIMEOUT=10
MAX_INSTALLER=512*1024*1024
NAME_RE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.exe')
VERSION_RE=re.compile(r'\d{1,4}(\.\d{1,6}){1,3}')
REPO_RE=re.compile(r'[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}')
ATTEMPTS=4                  # one download tries this many times, each carrying on from what has arrived
RETRY_WAIT=(2,5,10)         # seconds before the second, third and fourth try
BLOCK=1024*256

class UpdateError(RuntimeError):pass
class _Transient(UpdateError):
    """The connection failed or dropped; worth trying again. `reason` says why in a few words, `refused`
    whether nothing answered at all (through a proxy: the proxy is not running)."""
    def __init__(self,message,reason='',refused=False,reroute=False):super().__init__(message);self.reason=reason or message;self.refused=refused;self.reroute=reroute
class _Bad(UpdateError):
    """What arrived cannot be the installer; what was saved is deleted."""
class _NoRange(Exception):
    """The server refused to continue a saved part (HTTP 416)."""

def parse_version(text:str)->tuple[int,...]:
    if not isinstance(text,str) or not VERSION_RE.fullmatch(text.strip()):raise ValueError('版本号格式不正确')
    parts=[int(x) for x in text.strip().split('.')]
    return tuple(parts+[0]*(4-len(parts)))

def is_newer(candidate:str,current:str)->bool:return parse_version(candidate)>parse_version(current)

def configured()->bool:return bool(REPO_RE.fullmatch(UPDATE_REPO))
def _repo(repo)->str:
    repo=repo or UPDATE_REPO
    if not REPO_RE.fullmatch(repo or ''):raise UpdateError('尚未设置更新发布地址')
    return repo
def manifest_url(repo=None)->str:return f'https://github.com/{_repo(repo)}/releases/latest/download/{MANIFEST}'
def api_url(repo=None)->str:return f'https://api.github.com/repos/{_repo(repo)}/releases/latest'
def installer_url(manifest:dict,repo=None)->str:
    return f"https://github.com/{_repo(repo)}/releases/download/v{manifest['version']}/{manifest['installer']['name']}"
def release_page(version:str,repo=None)->str:
    """The release's web page, for downloading the installer in a browser when the app cannot."""
    parse_version(version);return f'https://github.com/{_repo(repo)}/releases/tag/v{version}'

def validate(value)->dict:
    """The manifest as the app will use it; anything unexpected stops the update."""
    try:
        if not isinstance(value,dict) or value.get('schemaVersion')!=1:raise ValueError('不支持的更新信息版本')
        version=value['version'].strip();parse_version(version)
        notes=value.get('notes','')
        if not isinstance(notes,str) or len(notes)>4000:raise ValueError('更新说明格式不正确')
        installer=value['installer'];name=installer['name'];size=installer['bytes'];digest=installer['sha256']
        if not isinstance(name,str) or not NAME_RE.fullmatch(name):raise ValueError('安装包文件名不正确')
        if not isinstance(size,int) or isinstance(size,bool) or not 0<size<=MAX_INSTALLER:raise ValueError('安装包大小不正确')
        if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):raise ValueError('安装包校验值不正确')
        published=value.get('published','')
        if not isinstance(published,str) or len(published)>40:raise ValueError('发布日期格式不正确')
    except (KeyError,TypeError,AttributeError) as error:raise UpdateError('更新信息缺少必要字段') from error
    except ValueError as error:raise UpdateError(str(error)) from error
    return {'schemaVersion':1,'version':version,'published':published,'notes':notes.strip(),'installer':{'name':name,'bytes':size,'sha256':digest}}

def _open(url:str,opener,timeout,headers=None):
    request=Request(url,headers={'User-Agent':'ae-localizer-update-check','Accept':'application/octet-stream',**(headers or {})})
    try:response=opener(request,timeout=timeout)
    except HTTPError as error:
        code=error.code;error.close()
        if code==416:raise _NoRange from None
        if code==404:raise UpdateError('还没有发布任何版本') from None
        raise (_Transient if code>=500 or code==429 else UpdateError)(f'更新服务器返回错误（HTTP {code}）') from None
    except (URLError,TimeoutError,ConnectionError,OSError,HTTPException) as error:
        if isinstance(_cause(error),ssl.SSLError):raise UpdateError('无法连接更新服务器（'+_reason(error)+'）') from None
        reason=_reason(error);raise _Transient(f'无法连接更新服务器（{reason}），请检查网络',reason,_refused(error),True) from None
    final=getattr(response,'geturl',lambda:url)()
    if not str(final).startswith('https://'):response.close();raise UpdateError('更新地址不是 HTTPS，已停止')
    return response

def _cause(error):
    return error.reason if isinstance(error,URLError) and isinstance(error.reason,BaseException) else error
def _refused(error)->bool:return isinstance(_cause(error),ConnectionRefusedError)
def _reason(error)->str:
    """What went wrong with the connection, in a few words a user can act on."""
    cause=_cause(error);text=str(getattr(error,'reason',error))
    if isinstance(cause,ssl.SSLCertVerificationError):
        # OpenSSL verify codes: 9/10 not yet valid / expired, 18/19 self-signed, 20/21 unknown issuer, 62 wrong name.
        code=getattr(cause,'verify_code',None)
        if code in (9,10):return '证书校验失败：证书不在有效期内，请检查电脑的日期和时间'
        if code==62:return '证书校验失败：证书与网址不符，可能被网络劫持'
        if code in (18,19):return '证书校验失败：证书链中有未知的自签名证书，可能被安全软件或代理拦截'
        if code in (20,21):return '证书校验失败：找不到签发证书的根证书，可能被安全软件或代理拦截'
        return '证书校验失败，可能被安全软件或代理拦截'
    if isinstance(cause,ssl.SSLError):return '加密连接失败'
    if isinstance(cause,socket.gaierror):return '域名解析失败'
    if isinstance(cause,TimeoutError) or 'timed out' in text:return '连接超时'
    if isinstance(cause,ConnectionRefusedError):return '连接被拒绝'
    if isinstance(cause,(ConnectionResetError,ConnectionAbortedError)):return '连接被重置'
    if text.startswith('Tunnel connection failed'):return '代理无法连到 GitHub'
    if isinstance(cause,HTTPException):return '连接中断'
    return '网络错误'

def routes(url:str)->list[tuple[str,str|None]]:
    """Ways to reach `url`, tried in turn: (label, proxy URL, or None for a direct connection).

    First the way any program would go (proxy environment variables or the Windows proxy setting); then the
    proxy a proxy auto-config (PAC) script names, which urllib cannot read by itself; and when either of those
    goes through a proxy, a direct connection as well, in case the proxy program is not running."""
    found=[];host=urlsplit(url).hostname or ''
    proxy=getproxies().get('https')
    if proxy and not proxy_bypass(host):found.append(('代理（环境变量）' if getproxies_environment().get('https') else '系统代理',proxy))
    pac=_pac_proxy(url)
    if pac and pac not in [p for _,p in found]:found.append(('自动代理脚本',pac))
    return found+[('直连',None)]

SYSTEM='system'   # _connect: whatever proxy the system names, as any program would
def _connect(proxy:str|None):
    handlers=[HTTPSHandler(context=tls_context())]
    if proxy!=SYSTEM:handlers.append(ProxyHandler({'https':proxy,'http':proxy} if proxy else {}))
    return build_opener(*handlers).open

def tls_context()->ssl.SSLContext:
    """Certificates are checked as strictly as ever, against Windows' trusted roots plus Mozilla's (certifi).
    Windows often installs a root only when a browser first needs it, and Python reads just the installed ones:
    GitHub chains to USERTrust ECC, which a fresh or trimmed Windows may not have yet."""
    context=ssl.create_default_context()
    try:
        import certifi;context.load_verify_locations(certifi.where())
    except (ImportError,OSError,ssl.SSLError):pass
    return context

def _pac_proxy(url:str)->str|None:
    """The proxy the Windows proxy auto-config script picks for `url`, or None (no script, DIRECT, or any failure)."""
    if os.name!='nt':return None
    try:
        import ctypes;from ctypes import wintypes
        class IEConfig(ctypes.Structure):
            _fields_=[('fAutoDetect',wintypes.BOOL),('lpszAutoConfigUrl',ctypes.c_void_p),('lpszProxy',ctypes.c_void_p),('lpszProxyBypass',ctypes.c_void_p)]
        class Options(ctypes.Structure):
            _fields_=[('dwFlags',wintypes.DWORD),('dwAutoDetectFlags',wintypes.DWORD),('lpszAutoConfigUrl',ctypes.c_void_p),
                      ('lpvReserved',ctypes.c_void_p),('dwReserved',wintypes.DWORD),('fAutoLogonIfChallenged',wintypes.BOOL)]
        class Info(ctypes.Structure):
            _fields_=[('dwAccessType',wintypes.DWORD),('lpszProxy',ctypes.c_void_p),('lpszProxyBypass',ctypes.c_void_p)]
        winhttp=ctypes.WinDLL('winhttp');free=ctypes.WinDLL('kernel32').GlobalFree;free.argtypes=[ctypes.c_void_p]
        winhttp.WinHttpOpen.restype=ctypes.c_void_p;winhttp.WinHttpOpen.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD]
        winhttp.WinHttpSetTimeouts.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int]
        winhttp.WinHttpGetProxyForUrl.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR,ctypes.POINTER(Options),ctypes.POINTER(Info)]
        winhttp.WinHttpCloseHandle.argtypes=[ctypes.c_void_p]
        config=IEConfig()
        if not winhttp.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(config)):return None
        try:
            if not config.lpszAutoConfigUrl:return None
            session=winhttp.WinHttpOpen('ae-localizer-update-check',1,None,None,0)   # 1: no proxy for the session itself
            if not session:return None
            try:
                winhttp.WinHttpSetTimeouts(session,3000,3000,3000,3000)
                options=Options(dwFlags=2,lpszAutoConfigUrl=config.lpszAutoConfigUrl,fAutoLogonIfChallenged=True)   # 2: the script's URL
                info=Info()
                if not winhttp.WinHttpGetProxyForUrl(session,url,ctypes.byref(options),ctypes.byref(info)):return None
                try:listed=ctypes.wstring_at(info.lpszProxy) if info.dwAccessType==3 and info.lpszProxy else ''   # 3: a named proxy
                finally:
                    for pointer in (info.lpszProxy,info.lpszProxyBypass):
                        if pointer:free(pointer)
            finally:winhttp.WinHttpCloseHandle(session)
        finally:
            for pointer in (config.lpszAutoConfigUrl,config.lpszProxy,config.lpszProxyBypass):
                if pointer:free(pointer)
    except (OSError,AttributeError,ValueError):return None
    return _proxy_entry(listed)

def _proxy_entry(listed:str)->str|None:
    """The first plain host:port in a WinHTTP proxy list ("a:1;b:2", or per scheme "https=a:1"), as an http:// proxy URL."""
    for entry in re.split(r'[;\s]+',listed or ''):
        if '=' in entry:
            scheme,_,entry=entry.partition('=')
            if scheme.lower()!='https':continue
        entry=entry.removeprefix('http://')
        if re.fullmatch(r'[A-Za-z0-9.\-]{1,253}:\d{1,5}|\[[0-9A-Fa-f:.]+\]:\d{1,5}',entry):return 'http://'+entry
    return None

def _read_json(url:str,opener,limit:int,headers=None):
    with _open(url,opener,CHECK_TIMEOUT,headers) as response:raw=response.read(limit+1)
    if len(raw)>limit:raise UpdateError('更新信息过大，已停止')
    try:return json.loads(raw.decode('utf-8-sig'))
    except (ValueError,UnicodeDecodeError):raise UpdateError('更新信息无法解析') from None

def from_release(value)->dict:
    """The manifest made from the GitHub API's description of a release: the version from its tag, the installer
    from its only .exe asset with the SHA256 GitHub lists for it. Checked exactly like update.json."""
    try:
        version=value['tag_name'];assets=value['assets']
        if not isinstance(version,str) or not isinstance(assets,list):raise TypeError
        exes=[a for a in assets if isinstance(a,dict) and isinstance(a.get('name'),str) and a['name'].lower().endswith('.exe')]
        if len(exes)!=1:raise UpdateError('发布页里的安装包不唯一')
        digest=exes[0].get('digest')
        if not isinstance(digest,str) or not digest.startswith('sha256:'):raise UpdateError('发布页缺少安装包校验值')
        notes=value.get('body') or '';published=value.get('published_at') or ''
        if not isinstance(notes,str) or not isinstance(published,str):raise TypeError
    except (KeyError,TypeError,AttributeError):raise UpdateError('发布信息缺少必要字段') from None
    return validate({'schemaVersion':1,'version':version.removeprefix('v'),'published':published[:10],'notes':notes.replace('\r\n','\n')[:4000],
                     'installer':{'name':exes[0]['name'],'bytes':exes[0].get('size'),'sha256':digest.removeprefix('sha256:')}})

def fetch_manifest(repo=None,opener=None)->dict:
    """The latest release's manifest: update.json, or when that cannot be reached by any route, the same facts
    from the GitHub API (api.github.com is often reachable when the release download host is not). Only
    connection failures move on to the next route or source; an answer that is wrong (404, not HTTPS, not
    a valid manifest) stops the check."""
    sources=((manifest_url(repo),lambda url,open_:validate(_read_json(url,open_,MAX_MANIFEST)),''),
             (api_url(repo),lambda url,open_:from_release(_read_json(url,open_,MAX_RELEASE,{'Accept':'application/vnd.github+json'})),'备用地址'))
    failures=[];dead=set()
    for url,read,source in sources:
        ways=[('',opener,None)] if opener else [(label,_connect(proxy),proxy) for label,proxy in routes(url)]
        for label,open_,proxy in ways:
            if proxy in dead:continue
            try:return read(url,open_)
            except _Transient as error:
                reason='代理连不上（代理软件可能没开）' if proxy and error.refused else error.reason
                if proxy and error.refused:dead.add(proxy)
                failures.append(('·'.join(x for x in (label,source) if x),reason))
    seen=[];[seen.append(f) for f in failures if f not in seen]
    raise UpdateError('无法连接更新服务器，请检查网络（'+'；'.join(f'{where}：{why}' if where else why for where,why in seen)+'）')

def check(current:str,repo=None,opener=None)->dict|None:
    """The manifest when it offers a newer version than `current`, otherwise None."""
    manifest=fetch_manifest(repo,opener)
    return manifest if is_newer(manifest['version'],current) else None

def download(manifest:dict,folder:Path,progress=None,cancelled=None,repo=None,opener=None,wait=time.sleep)->Path:
    """Saves the installer into `folder` and returns its path only if size and SHA256 match the manifest.

    What has arrived is kept in `<name>.part`, with `<name>.part.json` naming the installer it belongs to.
    When the connection drops, the next try asks the server for the rest (HTTP Range) instead of starting
    over; after ATTEMPTS tries the part stays, and a later download carries on from it. Anything that cannot
    be the installer (too long, wrong SHA256) is deleted."""
    manifest=validate(manifest);info=manifest['installer'];folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    target=folder/info['name'];partial=target.with_name(target.name+'.part');note=target.with_name(target.name+'.part.json')
    if target.is_file() and target.stat().st_size==info['bytes'] and _digest(target)==info['sha256']:return target
    url=installer_url(manifest,repo)
    ways=[None] if opener else [proxy for _,proxy in routes(url)]
    route=0;open_route=opener or _connect(ways[route])
    try:
        for attempt in range(ATTEMPTS):
            if attempt:wait(RETRY_WAIT[min(attempt,len(RETRY_WAIT))-1])
            if cancelled and cancelled():raise UpdateError('下载已取消')
            try:_fetch(url,info,partial,note,progress,cancelled,open_route);break
            except _Transient as error:
                failure=error
                if not opener and error.reroute and route+1<len(ways):
                    route+=1;open_route=_connect(ways[route])
        else:
            kept=partial.stat().st_size if partial.is_file() else 0
            raise UpdateError(f'下载未完成：{failure}'+(f'。已保存 {kept/1048576:.1f} MB，再次下载会接着下载' if kept else ''))
        os.replace(partial,target);note.unlink(missing_ok=True)
    except _Bad:
        _discard(partial,note);raise
    except OSError as error:
        # Saving failed (disk full, file locked): what is on disk cannot be trusted to continue from.
        _discard(partial,note);raise UpdateError(f'无法保存安装包：{error.strerror or error}') from None
    finally:
        if partial.is_file() and partial.stat().st_size==0:_discard(partial,note)
    return target

def _fetch(url,info,partial,note,progress,cancelled,opener):
    """One try: continues the saved part when the server agrees, otherwise starts over. Raises _Transient
    when the connection fails or ends early, _Bad when the content cannot be the installer."""
    start=_resumable(partial,note,info);digest=hashlib.sha256()
    if start:
        with open(partial,'rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    try:response=_open(url,opener,60,{'Range':f'bytes={start}-'} if start else None)
    except _NoRange:
        _discard(partial,note);raise _Transient('服务器无法接着下载，将从头下载') from None
    with response:
        if start and not _continues(response,start,info['bytes']):start=0;digest=hashlib.sha256()
        if not start:note.write_text(json.dumps(info),encoding='utf-8')
        done=start
        if progress and done:progress(done,info['bytes'])
        with open(partial,'ab' if start else 'wb') as out:
            while True:
                if cancelled and cancelled():raise UpdateError('下载已取消')
                try:block=response.read(BLOCK)
                except (OSError,HTTPException) as error:
                    if isinstance(error,ssl.SSLError):raise UpdateError(_reason(error)) from error
                    raise _Transient('连接中断',reroute=True) from error
                if not block:break
                done+=len(block)
                if done>info['bytes']:raise _Bad('下载内容比更新信息里的大，已停止')
                digest.update(block);out.write(block)
                if progress:progress(done,info['bytes'])
    if done<info['bytes']:raise _Transient(f'连接中断（{done}/{info["bytes"]} 字节）',reroute=True)
    if digest.hexdigest()!=info['sha256']:raise _Bad('安装包校验值不一致，已删除下载内容')

def _resumable(partial:Path,note:Path,info:dict)->int:
    """Bytes saved by an earlier try at this same installer; a part of anything else is deleted."""
    try:same=json.loads(note.read_text(encoding='utf-8'))==info
    except (OSError,ValueError):same=False
    size=partial.stat().st_size if partial.is_file() else 0
    if same and 0<size<info['bytes']:return size
    _discard(partial,note);return 0

def _continues(response,start:int,total:int)->bool:
    """Whether the server sent the rest from `start` (206 with a matching Content-Range) rather than the whole file."""
    if getattr(response,'status',200)!=206:return False
    headers=getattr(response,'headers',None);match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)',(headers.get('Content-Range','') if headers else '').strip())
    return bool(match) and int(match[1])==start and match[3] in (str(total),'*')

def _discard(partial:Path,note:Path):
    partial.unlink(missing_ok=True);note.unlink(missing_ok=True)

def _digest(path:Path)->str:
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def make_manifest(installer:Path,version:str,notes:str,published:str)->dict:
    """For the publisher: the manifest for an installer already renamed to its ASCII asset name."""
    installer=Path(installer)
    return validate({'schemaVersion':1,'version':version,'published':published,'notes':notes,
                     'installer':{'name':installer.name,'bytes':installer.stat().st_size,'sha256':_digest(installer)}})
