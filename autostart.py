"""Start with Windows (the user's choice in settings, off by default): one value of this program under the current
user's Run key, starting it minimized to the tray. Switching it off deletes only that value, so nothing is left."""
from __future__ import annotations
import sys

RUN=r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE='AE汉化工具'
MINIMIZED='--minimized'

def command()->str|None:
    """What Windows should run: the installed EXE, minimized. None from source (there is no EXE to start)."""
    if not getattr(sys,'frozen',False):return None
    return f'"{sys.executable}" {MINIMIZED}'

def _registry_get()->str|None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN) as key:return str(winreg.QueryValueEx(key,VALUE)[0])
    except OSError:return None
def _registry_set(value:str|None):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN) as key:
        if value is None:
            try:winreg.DeleteValue(key,VALUE)
            except FileNotFoundError:pass
        else:winreg.SetValueEx(key,VALUE,0,winreg.REG_SZ,value)

def available()->bool:return command() is not None
def enabled()->bool:
    """On only when the value starts this very EXE (an older copy elsewhere does not count)."""
    wanted=command();return wanted is not None and _registry_get()==wanted
def set_enabled(on:bool):
    if on:
        wanted=command()
        if wanted is None:raise RuntimeError('只有安装版（EXE）可以开机启动')
        _registry_set(wanted)
    else:_registry_set(None)
