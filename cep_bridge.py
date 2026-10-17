"""AE's background bridge: the signed CEP extension (cep/) that runs parameter requests inside AE.

Handing a script over with AfterFX.exe -r makes AE restore its maximized window; no switch avoids that. Once the
user installs this extension and restarts AE, requests go through a folder the extension watches instead: no
second process, no window or focus change. The extension runs only its own signed copy of
scripts/parameter_bridge.jsx filled with the request data (request.json). Whenever it is not installed, not
loaded, of another build, or does not take a request within CLAIM_WAIT, callers fall back to the -r relay."""
from __future__ import annotations
import hashlib,json,os,shutil,time,uuid
from pathlib import Path
from runtime import asset_root

EXTENSION_ID='com.verse.aelocalizer.bridge'
CLAIM_WAIT=3.0      # seconds the extension has to take a request before the relay is used
FRESH=3.0           # seconds a heartbeat counts as alive (the extension writes one every second)
OWNED=('request.json','claimed.json','withdrawn.json','bridge-error.json')

class Rejected(RuntimeError):
    """The extension took the request but AE did not run it."""

def _appdata()->Path:return Path(os.environ.get('APPDATA') or Path.home()/'AppData'/'Roaming')
def root()->Path:
    """The folder the extension watches (it cannot be told another one: it reads AE's own user-data path)."""
    return _appdata()/'AE-Localizer'/'bridge'
def installed()->Path:return _appdata()/'Adobe'/'CEP'/'extensions'/EXTENSION_ID

def bundled()->Path|None:
    """This build's signed extension and its bridge id (written by build.py), or None for a build without one."""
    for base in (asset_root()/'cep_signed',Path(__file__).resolve().parent/'build'/'cep'/'signed'):
        if (base/EXTENSION_ID/'META-INF'/'signatures.xml').is_file() and (base/'bridge.id').is_file():return base
    return None
def bridge_id()->str|None:
    base=bundled()
    return (base/'bridge.id').read_text(encoding='ascii').strip() if base else None

def _tree(folder:Path)->dict[str,str]:
    return {p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob('*')) if p.is_file()}

def heartbeat()->dict|None:
    try:value=json.loads((root()/'heartbeat.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return None
    return value if isinstance(value,dict) else None

def connected()->bool:
    """AE has this build's extension loaded and running right now."""
    expected=bridge_id();beat=heartbeat()
    if not expected or not beat or beat.get('bridge')!=expected or not isinstance(beat.get('time'),(int,float)):return False
    return abs(time.time()*1000-beat['time'])<FRESH*1000

def status()->str:
    """'unavailable' (this build carries no signed extension), 'missing', 'outdated' (the installed files are not
    this build's), 'installed' (current files; AE has not loaded them yet: restart AE) or 'connected'."""
    base=bundled()
    if not base:return 'unavailable'
    if not installed().is_dir():return 'missing'
    try:same=_tree(installed())==_tree(base/EXTENSION_ID)
    except OSError:same=False
    if not same:return 'outdated'
    return 'connected' if connected() else 'installed'

# Adobe's extension debug mode (HKCU\Software\Adobe\CSXS.<n>\PlayerDebugMode = "1") makes AE load an extension
# whatever its signature. The user chose to switch it on when installing, so the extension loads even where the
# self-signed signature would not be accepted; removing the extension puts back only what this program changed.
CSXS_VERSIONS=(10,11,12,13)    # CEP 10: AE 2021; 11: AE 2022-2024; 12: AE 2025-2026; 13: the next one
def _debug_record()->Path:return _appdata()/'AE-Localizer'/'debug-mode.json'
def _registry_get(version:int)->str|None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,rf'Software\Adobe\CSXS.{version}') as key:return str(winreg.QueryValueEx(key,'PlayerDebugMode')[0])
    except OSError:return None
def _registry_set(version:int,value:str|None):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,rf'Software\Adobe\CSXS.{version}') as key:
        if value is None:
            try:winreg.DeleteValue(key,'PlayerDebugMode')
            except FileNotFoundError:pass
        else:winreg.SetValueEx(key,'PlayerDebugMode',0,winreg.REG_SZ,value)
def _read_record()->dict:
    try:value=json.loads(_debug_record().read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}
    return value if isinstance(value,dict) else {}

def enable_debug_mode()->list[int]:
    """Switches the debug mode on for every CEP version AE uses; returns the versions changed. The value each had
    before (or that it had none) is kept, once, so remove() can put it back; ones already on are not recorded."""
    record=_read_record();changed=[]
    for version in CSXS_VERSIONS:
        before=_registry_get(version)
        if before=='1':continue
        record.setdefault(str(version),{'value':before})   # the first original wins over later reinstalls
        _registry_set(version,'1');changed.append(version)
    if record:
        _debug_record().parent.mkdir(parents=True,exist_ok=True);_debug_record().write_text(json.dumps(record),encoding='utf-8')
    return changed

def restore_debug_mode()->list[int]:
    """Puts back the values enable_debug_mode() changed, where they are still on; the rest is left as it is."""
    restored=[]
    for version,entry in _read_record().items():
        if not str(version).isdigit() or not isinstance(entry,dict):continue
        if _registry_get(int(version))=='1':_registry_set(int(version),entry.get('value'));restored.append(int(version))
    _debug_record().unlink(missing_ok=True)
    return restored

def install(debug_mode=False):
    """Copies this build's extension into the user's CEP extensions folder (no administrator rights needed);
    with debug_mode, also switches Adobe's extension debug mode on (see enable_debug_mode)."""
    base=bundled()
    if not base:raise RuntimeError('这个版本没有附带后台连接扩展')
    target=installed();staging=target.with_name(target.name+'.new-'+uuid.uuid4().hex[:8])
    shutil.copytree(base/EXTENSION_ID,staging)
    try:
        if target.exists():_delete()
        os.replace(staging,target)
    except BaseException:
        shutil.rmtree(staging,ignore_errors=True);raise
    if debug_mode:enable_debug_mode()

def _delete():
    target=installed()
    if target.name!=EXTENSION_ID:raise RuntimeError('扩展路径异常，未删除')
    if target.exists():shutil.rmtree(target)

def remove():
    """Deletes only this extension's own folder and puts back the debug mode values this program changed."""
    _delete();restore_debug_mode()

def submit(folder:Path,request:dict):
    """Hands a request to the extension; written under a temporary name so it is never read half-written."""
    temporary=folder/'request.json.tmp'
    temporary.write_text(json.dumps(request,ensure_ascii=True),encoding='ascii')
    os.replace(temporary,folder/'request.json')

def withdraw(folder:Path)->bool:
    """Takes back a request the extension has not claimed; False when it already has (then it runs there)."""
    try:os.replace(folder/'request.json',folder/'withdrawn.json');return True
    except FileNotFoundError:return False

def problem(folder:Path)->str|None:
    """Why the extension could not run a claimed request, as a message, or None."""
    try:reason=json.loads((folder/'bridge-error.json').read_text(encoding='utf-8')).get('error')
    except (OSError,ValueError,AttributeError):return None
    if reason=='evalScript':return 'AE 没有执行请求：AE 可能正显示对话框或正忙。请关闭 AE 中的对话框后重试'
    return '后台连接拒绝了这个请求'
