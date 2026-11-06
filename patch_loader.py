"""Lets the assistant fix this program on a user's computer. A corrected module placed in the data folder's
patches/<version> is imported instead of the one built into the EXE, for that version only. Nothing here can lock the
user out or leave the assistant dead:
  a new patch is on trial until the user keeps it, and goes back at the next start if they do not, or if a start fails;
  a patched start only counts once the assistant has passed a check (patch_check), not merely when a window appears;
  when the program crashed and restarting failed too, the user is offered a start without any patches (safe mode),
  from here, where no patch can touch it. It is never offered otherwise.
runtime, patch_check and this file cannot be patched. Native DLLs cannot be changed this way."""
from __future__ import annotations
import atexit,ctypes,importlib.abc,importlib.util,os,runpy,shutil,sys,time,json,hashlib,uuid
from pathlib import Path
from runtime import asset_root,data_root,write_json_atomic

NOT_PATCHABLE={'runtime','patch_loader','patch_check'}
SELFTEST='--selftest-patch';SAFE_TITLE='AE汉化工具（安全模式）'
notice='';safe_mode=False;_active=None;_override_ran=False;_owner=False;_mutex=None
chat_source=lambda:None    # the conversation a patch came from, so the assistant can pick it up again after the restart
def folder(version:str)->Path:return data_root()/'patches'/version
def source_folder()->Path:
    """The program's own source: bundled beside the EXE, or the project folder when run from source."""
    bundled=asset_root()/'source'
    return bundled if bundled.is_dir() else Path(__file__).resolve().parent

class PatchFinder(importlib.abc.MetaPathFinder):
    def __init__(self,root:Path):self.root=root
    def find_spec(self,name,path=None,target=None):
        file=self.root/f'{name}.py'
        return importlib.util.spec_from_file_location(name,file) if '.' not in name and name not in NOT_PATCHABLE and file.is_file() else None

def instance_tag()->str:
    """Tells copies of the program with different data folders apart. One data folder, one running copy."""
    return hashlib.sha256(str(data_root()).casefold().encode('utf8')).hexdigest()[:12]
def _another_running()->bool:
    # Only a copy that holds this may judge the last start: a second start must not take the first one's patches away.
    global _mutex
    if _mutex is not None:return False
    try:
        kernel=ctypes.windll.kernel32;kernel.CreateMutexW.restype=ctypes.c_void_p;kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        ctypes.set_last_error(0);handle=kernel.CreateMutexW(None,False,'Local\\AE-Localizer-run-'+instance_tag());other=kernel.GetLastError()==183
        _mutex=handle or 1;return other
    except (AttributeError,OSError):return False
def _read(file:Path,default):
    try:value=json.loads(file.read_text(encoding='utf8'))
    except (OSError,ValueError):return default
    return value if isinstance(value,type(default)) else default
def _drop(*files):
    for file in files:
        try:file.unlink(missing_ok=True)
        except OSError:pass
def _ask_safe_mode(modules)->str:
    """The program crashed and could not start again. Asked natively, before any module that a patch could have changed."""
    text=('AE汉化工具崩溃了，重新启动也没有成功，可能是 AI 改过的这些程序模块造成的：\n'+'、'.join(modules)+'\n\n'
          '是：进入安全模式。这次不加载这些修改，它们还留着，可以让 AI 查原因。\n否：停用这些修改后正常启动。\n取消：退出。')
    try:answer=ctypes.windll.user32.MessageBoxW(None,text,SAFE_TITLE,0x3|0x30|0x10000|0x40000)
    except (AttributeError,OSError):answer=6
    return {6:'safe',7:'disable'}.get(answer,'quit')
def _clear_running(root):_drop(root/'.running')
def _trial_crashed(result:Path):
    # A windowed EXE would otherwise sit on its error box until the trial times out, and the reason would be lost with it.
    def hook(kind,value,trace):
        import traceback
        try:result.write_text(json.dumps({'ok':False,'stage':'启动','error':''.join(traceback.format_exception(kind,value,trace))[-1500:]},ensure_ascii=False),encoding='utf8')
        finally:os._exit(1)
    return hook

def install(version:str):
    """Called first thing at start: judges how the last start went, then loads the patches that survive that."""
    global notice,safe_mode,_active,_owner
    if _active is not None:return
    if SELFTEST in sys.argv:
        # A trial copy of the program (patch_check): only the candidate patches, and nothing here is judged or changed.
        at=sys.argv.index(SELFTEST);_active=Path(sys.argv[at+1]);sys.meta_path.insert(0,PatchFinder(_active));sys.excepthook=_trial_crashed(Path(sys.argv[at+2]));return
    root=folder(version);marker=root/'.starting';booted=root/'.boot.json';running=root/'.running';trial=root/'.trial.json'
    if not root.is_dir():return
    if not any(root.glob('*.py')):
        _drop(marker,booted,running,trial);shutil.rmtree(root/'.confirmed',ignore_errors=True);return
    if _another_running():
        _active=root;sys.meta_path.insert(0,PatchFinder(root));return
    failed=marker.exists();boot=_read(booted,{});state=_read(trial,{})
    crashed=bool(boot.get('crashed')) or running.exists();fails=int(boot.get('fails',0))+(1 if failed else 0)
    if state and (failed or int(state.get('starts',0))>=1):
        why='上次启动没有成功' if failed else '上次的修改没有确认保留'
        try:notice=f'{why}，已撤销 AI 这次对程序的修改（留存在 {rollback_trial(version)}）'
        except OSError as error:notice=f'{why}，但修改未能撤销：{error}'
        failed=False;fails=0;crashed=False;state={}
        if not any(root.glob('*.py')):return
    elif failed and (fails>=2 or crashed):
        modules=sorted(p.stem for p in root.glob('*.py'));choice=_ask_safe_mode(modules);_drop(marker,running)
        write_json_atomic(booted,{'fails':fails,'crashed':True})
        if choice=='quit':raise SystemExit(0)
        if choice=='safe':
            safe_mode=True;notice='安全模式：这次没有加载 AI 对程序的修改（'+'、'.join(modules)+'）。'
            return
        try:notice=f'已停用 AI 对程序的修改（留存在 {disable_all(version)}）'
        except OSError as error:notice=f'修改未能停用：{error}'
        return
    if state:
        state['starts']=int(state.get('starts',0))+1;write_json_atomic(trial,state)
    try:
        marker.write_text('',encoding='utf8');_drop(running);write_json_atomic(booted,{'fails':fails,'crashed':crashed})
    except OSError:return
    _active=root;_owner=True;sys.meta_path.insert(0,PatchFinder(root));atexit.register(_clear_running,root)
def run_main_override():
    """The window's own file is the entry script, which no finder sees: run the patched copy in its place."""
    global _override_ran
    if _active is None or _override_ran or not (_active/'app_live.py').is_file():return
    _override_ran=True;runpy.run_path(str(_active/'app_live.py'),run_name='__main__');raise SystemExit(0)
def started():
    """The assistant has passed its check: this start counts as a good one. .running stays until a normal exit, so a later crash can be told."""
    if _active is not None and _owner:
        _drop(_active/'.starting',_active/'.boot.json')
        try:(_active/'.running').write_text('',encoding='utf8')
        except OSError:pass
def health_failed()->str:
    """The window came up but the assistant cannot work: undo at once, while something still can. Takes effect at the next start."""
    if _active is None or not _owner:return ''
    version=_active.name
    try:
        place=rollback_trial(version) if _read(_active/'.trial.json',{}) else disable_all(version)
        return f'AI 助手自检没有通过，已撤销 AI 对程序的修改（留存在 {place}），重启程序后恢复正常'
    except OSError as error:return f'AI 助手自检没有通过，但修改未能撤销：{error}。可以手动删除 {_active}'
def _aside(version:str)->Path:return folder(version).with_name(f'{version}.disabled-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:6]}')
def disable_all(version:str)->Path:
    root=folder(version);aside=_aside(version);shutil.move(str(root),str(aside))
    for name in ('.starting','.boot.json','.running','.trial.json'):_drop(aside/name)
    return aside
# Trial: a change counts only once the user keeps it. What was confirmed before it waits in .confirmed.
def trial_state(version:str)->dict:return _read(folder(version)/'.trial.json',{})
def begin_trial(version:str,module:str):
    root=folder(version);root.mkdir(parents=True,exist_ok=True);file=root/'.trial.json';state=_read(file,{})
    if not state:
        saved=root/'.confirmed';shutil.rmtree(saved,ignore_errors=True);saved.mkdir()
        for path in root.glob('*.py'):shutil.copy2(path,saved/path.name)
        state={'starts':0,'modules':[]}
    # Changed again before being kept: the next start is this version's first.
    state['starts']=0;state['modules']=sorted({*state.get('modules',[]),module});state['chat']=chat_source() or state.get('chat');write_json_atomic(file,state)
def confirm_trial(version:str):
    root=folder(version);_drop(root/'.trial.json');shutil.rmtree(root/'.confirmed',ignore_errors=True)
def rollback_trial(version:str)->Path:
    """Back to what was last kept. The undone version is left aside, so it can still be looked at."""
    root=folder(version);aside=_aside(version);aside.mkdir(parents=True)
    for path in root.glob('*.py'):shutil.move(str(path),str(aside/path.name))
    if (root/'.history').is_dir():shutil.move(str(root/'.history'),str(aside/'.history'))
    for path in (root/'.confirmed').glob('*.py') if (root/'.confirmed').is_dir() else ():shutil.copy2(path,root/path.name)
    shutil.rmtree(root/'.confirmed',ignore_errors=True);_drop(root/'.trial.json',root/'.starting',root/'.boot.json',root/'.running')
    return aside
def active()->list[str]:return sorted(p.stem for p in _active.glob('*.py')) if _active else []

def read_source(name:str,version:str)->str:
    if not name.isidentifier():raise ValueError('模块名无效')
    patched=folder(version)/f'{name}.py';original=source_folder()/f'{name}.py'
    file=patched if patched.is_file() else original
    if not file.is_file():raise FileNotFoundError(f'没有模块 {name}')
    return file.read_text(encoding='utf8')
def list_source()->list[str]:return sorted(p.stem for p in source_folder().glob('*.py'))

def patch_file(name,version):
    if not name.isidentifier():raise ValueError('模块名无效')
    if name in NOT_PATCHABLE:raise PermissionError(f'{name} 不能打补丁')
    return folder(version)/f'{name}.py'

def _hash(raw):return hashlib.sha256(raw).hexdigest() if raw is not None else None

def _history(file):
    journal=file.parent/'.history'/f'{file.stem}.json'
    try:records=json.loads(journal.read_text(encoding='utf8'))
    except FileNotFoundError:records=[]
    if not isinstance(records,list):raise ValueError('补丁恢复记录损坏，未修改')
    return journal,records

def _replace(file,raw):
    if raw is None:file.unlink(missing_ok=True);return
    temp=file.with_name(file.name+f'.tmp{uuid.uuid4().hex}')
    try:
        with temp.open('wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,file)
    finally:temp.unlink(missing_ok=True)

def _change(file,raw):
    """Persist a recoverable before-image before touching a patch, including removal."""
    file.parent.mkdir(parents=True,exist_ok=True);begin_trial(file.parent.name,file.stem);journal,records=_history(file)
    before=file.read_bytes() if file.exists() else None
    history=journal.parent;history.mkdir(exist_ok=True);saved=uuid.uuid4().hex+'.bak' if before is not None else None
    if saved:_replace(history/saved,before)
    records.append({'backup':saved,'before':_hash(before),'after':_hash(raw)})
    write_json_atomic(journal,records)
    _replace(file,raw)
    return str(history/saved) if saved else None

def restore_patch(name,version):
    file=patch_file(name,version);journal,records=_history(file)
    if not records:return {'restored':False,'note':'没有补丁恢复记录'}
    record=records[-1];saved=record.get('backup')
    if saved and (Path(saved).name!=saved or not saved.endswith('.bak')):raise ValueError('补丁备份路径无效')
    raw=(journal.parent/saved).read_bytes() if saved else None
    if _hash(raw)!=record['before']:raise ValueError('补丁备份哈希不符')
    current=file.read_bytes() if file.exists() else None
    if _hash(current) not in (record['after'],record['before']):raise ValueError('补丁被外部修改，未覆盖')
    begin_trial(version,name);_replace(file,raw);write_json_atomic(journal,records[:-1])
    return {'restored':True,'note':'已恢复上一份补丁，重启程序后生效'}
def apply_patch(name:str,version:str,old:str,new:str,check=None)->dict:
    """One exact text replacement in the module's current source. Refused unless the result compiles and check(name, version, text) passes."""
    file=patch_file(name,version)
    text=read_source(name,version);count=text.count(old)
    if count!=1:raise ValueError(f'要替换的文字必须在模块里恰好出现一次，现在是 {count} 次')
    changed=text.replace(old,new)
    try:compile(changed,f'{name}.py','exec')
    except SyntaxError as error:raise ValueError(f'改完有语法错误（第 {error.lineno} 行）：{error.msg}，未保存') from None
    if check:
        outcome=check(name,version,changed)
        if not outcome.get('ok'):raise ValueError(f"试运行没有通过，未保存。卡在“{outcome.get('stage','')}”：{outcome.get('error','')}")
    saved=_change(file,changed.encode('utf8'))
    return {'module':name,'file':str(file),'backup':saved,'note':'已通过试运行，还没生效。请用户重启程序，重启后用户确认保留才算数；不确认，下次启动会自动撤销。'}
def remove_patch(name:str,version:str)->bool:
    file=patch_file(name,version);found=file.is_file()
    if found:_change(file,None)
    return found
def list_patches(version:str)->list[str]:
    root=folder(version);return sorted(p.stem for p in root.glob('*.py')) if root.is_dir() else []
