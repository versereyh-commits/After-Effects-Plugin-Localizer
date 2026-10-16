"""Audited private layouts, keyed by exact build and both module hashes.

Discovery is read-only. A newly found build is never assigned another build's
addresses; it needs its own evidence and an adapter entry.
"""
from dataclasses import dataclass
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path

@dataclass(frozen=True)
class Profile:
    version:str
    host_version:str
    flt_sha:str
    tdb_sha:str
    manager:int
    registry_vector:int
    fc_vtable:int
    root_vtable:int
    name_get:int
    match_get:int
    category_get:int
    parent_get:int
    stream_match_get:int
    count_get:int
    count_bytes:bytes
    display_get:int
    match_pool:int
    display_cache:int
    group_vector:int
    named_vector:bool=False
    encoding:str='utf8'
    enabled:bool=False
    named_map:int=0x228
    codec_sha:str=''
    native_dynamic:bool=False

PROFILES={
    '26.0.0.67':Profile('26.0.0.67','26.0x67',
        '68b229950e95dedf630f5d09653715268e8beba4830590433abe7cb58e78a357',
        '8e1ea3d5e08d91dd25e899d43e0ad69a525b161716bb4c29250e3711b2591c80',
        0x14dee0,0x20,0xc8e20,0xc8e80,0x68850,0x69920,0x67bb0,
        0x3ab80,0x3a840,0x2c390,bytes.fromhex('8b8130020000c3'),0x38d30,0xa8b60,0xf0,0x220,enabled=True),
    '25.3.0.71':Profile('25.3.0.71','25.3x71',
        'a4896d34e20f1fb08ba9afd0fc6e428081bd51dba6abb39e87aa6342c973a183',
        '6d8f80729ef02538415bb6578399cee667101b6c218ea73d85d74357558b660e',
        0x153938,0x20,0xce290,0xce2f0,0x6b090,0x6c160,0x6a3f0,
        0x39cf0,0x399b0,0x2b3b0,bytes.fromhex('8b8130020000c3'),0x37e60,0xa6a00,0xf0,0x220,encoding='mbcs',enabled=True),
    '18.2.1.8':Profile('18.2.1.8','18.2.1x8',
        'abf6673b380450cc98549876c3738135bc025345b619cc4cba9d17bf98d05bc9',
        '2214da04c37c197f8cefb3cc825eaab6f45efa3c1bdd300e2157842e4b38113d',
        0xddcc8,0x10,0x8e9e8,0x8f0f0,0x34e90,0x522e0,0x51d00,
        0x30da0,0x275c0,0x253a0,bytes.fromhex('488b8198010000482b8190010000'),0x2f590,0x718a0,0xe8,0x190,True,'mbcs',enabled=True),
}

_trial=ContextVar('ae_trial_adapter',default=None)
LOCAL_PROFILES={}

@contextmanager
def trial_adapter(profile,projects):
    # Only the validation worker receives this capability; the GUI and other
    # threads retain their usual write gates. JSX independently checks files.
    paths=tuple(Path(p) for p in projects)
    if not profile.native_dynamic or len(paths)!=2 or any(not p.is_absolute() for p in paths) or paths[0].parent!=paths[1].parent or {p.name for p in paths}!={'QA.aep','QA_second.aep'} or paths[0].parent.parent.name!='adapter-qa' or paths[0].parent.parent.parent.name!='ae-bridge':
        raise ValueError('试验适配只能绑定软件自有 QA 工程')
    token=_trial.set((profile,tuple(str(p) for p in paths)))
    try:yield
    finally:_trial.reset(token)

def trial_projects():return _trial.get()[1] if _trial.get() else ()

def get_profile(version):
    trial=_trial.get()
    if trial and trial[0].version==version:return trial[0]
    try:return PROFILES.get(version) or LOCAL_PROFILES[version]
    except KeyError:raise RuntimeError('此 AE 构建尚未适配实时写入：'+str(version)) from None

def is_supported(version):
    profile=PROFILES.get(version) or LOCAL_PROFILES.get(version)
    return bool(profile and profile.enabled)

def runtime_allowed(version):
    trial=_trial.get()
    return bool(trial and trial[0].version==version) or is_supported(version)

def supported_text():return '、'.join([*(p.version for p in PROFILES.values() if p.enabled),*(p.version+'（本机验证）' for key,p in LOCAL_PROFILES.items() if p.enabled and key not in PROFILES)])
