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
    # How AE reads the internal-name field, when that differs from the names: AE 2021 (18.0) reads names as UTF-8
    # but internal names in the system code page. '' means the same as `encoding`.
    match_encoding:str=''
    # The named group keeps its streams in a std::map (AE 26.5) instead of an unordered_map's list; named_map is
    # then the tree's head, and its size is the count getter's field.
    named_tree:bool=False

def match_encoding(profile)->str:return getattr(profile,'match_encoding','') or profile.encoding

PROFILES={
    # Built in from this build's own automatic adaptation (static resolution + full owned-QA self-check with all
    # restores, 2026-10-19). Its named group is a std::map, which only the dynamic helper (v9) walks, so it keeps
    # the dynamic path: v9 re-checks the FLT/TDB/U hashes, getters, tree export and encoding before any write.
    '26.5.0.89':Profile('26.5.0.89','26.5x89',
        '9a1afe7700229cd0bc39dd37a146b1d3871fc84a8e6f991a7ef56390f21378d3',
        'ef851c3ccbc43da4e0d4899a52baa05a7a71a3cef3a4fdb09abbb0263960ccb5',
        0x12d998,0x20,0xbf0b0,0xbf110,0x66750,0x67790,0x65ab0,
        0x38d30,0x389f0,0x2ab70,bytes.fromhex('8b8128020000c3'),0x36fb0,0x8dcd0,0xf0,0x220,enabled=True,named_map=0x220,
        codec_sha='1a465b53deac7a3d14187ed175ba85498dbf5b5463259dec1f202a3393050fd0',native_dynamic=True,named_tree=True),
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

def _candidates(version):
    """The built-in profile of this version first, then this machine's validated one. Both can exist when the
    installed files differ from the built-in build's (patched, slimmed or repackaged AE with the same version)."""
    return [p for p in (PROFILES.get(version),LOCAL_PROFILES.get(version)) if p]

def ambiguous(version)->bool:
    """Whether choosing needs the running FLT.dll's hash (otherwise the only candidate is checked as before)."""
    trial=_trial.get()
    return not (trial and trial[0].version==version) and len(_candidates(version))>1

def get_profile(version,flt_sha=None):
    """The profile for `version`; with `flt_sha`, the one validated for that FLT.dll (else the first, whose own
    hash check then stops the operation)."""
    trial=_trial.get()
    if trial and trial[0].version==version:return trial[0]
    found=_candidates(version)
    if not found:raise RuntimeError('此 AE 构建尚未适配实时写入：'+str(version))
    return next((p for p in found if flt_sha and p.flt_sha==flt_sha),found[0])

def is_supported(version,flt_sha=None):
    """Enabled for this version; with `flt_sha`, only if one enabled profile was validated for that FLT.dll."""
    return any(p.enabled and (flt_sha is None or p.flt_sha==flt_sha) for p in _candidates(version))

def runtime_allowed(version):
    trial=_trial.get()
    return bool(trial and trial[0].version==version) or is_supported(version)

def nameless(raw:bytes,encoding:str)->bool:
    """Whether AE reports this internal-name field to scripts as '': empty, or bytes `encoding` cannot read.
    (On AE 2021 seven effects came back from app.effects with an empty matchName while memory held non-empty
    bytes for each.) Such an effect cannot be identified, so it is never renamed or restored."""
    raw=raw.split(b'\0',1)[0]
    if not raw:return True
    try:raw.decode(encoding);return False
    except UnicodeDecodeError:return True

def supported_text():return '、'.join([*(p.version for p in PROFILES.values() if p.enabled),*(p.version+'（本机验证）' for key,p in LOCAL_PROFILES.items() if p.enabled and (key not in PROFILES or p.flt_sha!=PROFILES[key].flt_sha))])

def mismatch_text(version)->str:
    """Why nothing is written when the running FLT.dll is not the one any profile of this version was validated
    for: the same version number with different files (patched, slimmed or repackaged AE, or a broken install)."""
    return (f'当前 AE 的效果模块（FLT.dll）与已验证的 {version} 不同，未执行写入。常见原因是 AE 文件被补丁、精简版或重新打包修改过'
            '（可右键 Support Files\\FLT.dll → 属性 → 数字签名，查看是否为有效的 Adobe Inc. 签名）。'
            '请在“AE 版本”页点“自动识别与适配”，自检通过后即可在这台电脑上使用')
