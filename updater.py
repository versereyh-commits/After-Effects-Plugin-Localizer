"""Update checks against a public release repository.

Each release carries two assets: update.json (the manifest) and the installer it names. The app only
reads https://github.com/<repo>/releases/latest/download/update.json, sends nothing about the user or
this computer, and runs an installer only after its size and SHA256 match the manifest and the user
has agreed. Qt-free so it can be tested on its own."""
from __future__ import annotations
import hashlib,json,os,re,time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

# The public GitHub repository ("owner/name") whose Releases carry update.json. Empty until the user creates
# it: the app then never goes online for updates and the settings card says so.
UPDATE_REPO='versereyh-commits/After-Effects-Plugin-Localizer'
MANIFEST='update.json'
MAX_MANIFEST=64*1024
MAX_INSTALLER=512*1024*1024
NAME_RE=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.exe')
VERSION_RE=re.compile(r'\d{1,4}(\.\d{1,6}){1,3}')
REPO_RE=re.compile(r'[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}')
ATTEMPTS=4                  # one download tries this many times, each carrying on from what has arrived
RETRY_WAIT=(2,5,10)         # seconds before the second, third and fourth try
BLOCK=1024*256

class UpdateError(RuntimeError):pass
class _Transient(UpdateError):
    """The connection failed or dropped; worth trying again."""
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
    except (URLError,TimeoutError,ConnectionError,OSError,HTTPException):raise _Transient('无法连接更新服务器，请检查网络') from None
    final=getattr(response,'geturl',lambda:url)()
    if not str(final).startswith('https://'):response.close();raise UpdateError('更新地址不是 HTTPS，已停止')
    return response

def fetch_manifest(repo=None,opener=urlopen)->dict:
    with _open(manifest_url(repo),opener,15) as response:raw=response.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST:raise UpdateError('更新信息过大，已停止')
    try:value=json.loads(raw.decode('utf-8-sig'))
    except (ValueError,UnicodeDecodeError):raise UpdateError('更新信息无法解析') from None
    return validate(value)

def check(current:str,repo=None,opener=urlopen)->dict|None:
    """The manifest when it offers a newer version than `current`, otherwise None."""
    manifest=fetch_manifest(repo,opener)
    return manifest if is_newer(manifest['version'],current) else None

def download(manifest:dict,folder:Path,progress=None,cancelled=None,repo=None,opener=urlopen,wait=time.sleep)->Path:
    """Saves the installer into `folder` and returns its path only if size and SHA256 match the manifest.

    What has arrived is kept in `<name>.part`, with `<name>.part.json` naming the installer it belongs to.
    When the connection drops, the next try asks the server for the rest (HTTP Range) instead of starting
    over; after ATTEMPTS tries the part stays, and a later download carries on from it. Anything that cannot
    be the installer (too long, wrong SHA256) is deleted."""
    manifest=validate(manifest);info=manifest['installer'];folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    target=folder/info['name'];partial=target.with_name(target.name+'.part');note=target.with_name(target.name+'.part.json')
    if target.is_file() and target.stat().st_size==info['bytes'] and _digest(target)==info['sha256']:return target
    url=installer_url(manifest,repo)
    try:
        for attempt in range(ATTEMPTS):
            if attempt:wait(RETRY_WAIT[min(attempt,len(RETRY_WAIT))-1])
            if cancelled and cancelled():raise UpdateError('下载已取消')
            try:_fetch(url,info,partial,note,progress,cancelled,opener);break
            except _Transient as error:failure=error
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
                except (OSError,HTTPException) as error:raise _Transient('连接中断') from error
                if not block:break
                done+=len(block)
                if done>info['bytes']:raise _Bad('下载内容比更新信息里的大，已停止')
                digest.update(block);out.write(block)
                if progress:progress(done,info['bytes'])
    if done<info['bytes']:raise _Transient(f'连接中断（{done}/{info["bytes"]} 字节）')
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
