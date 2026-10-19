"""Machine-local, DPAPI-protected validation certificates, never AI configs."""
from dataclasses import asdict,replace
from pathlib import Path
import base64,ctypes,json,os
from datetime import datetime,timezone
from ae_profiles import Profile,LOCAL_PROFILES,PROFILES
from adapter_resolver import Resolver
from runtime import data_root,write_json_atomic
from settings_store import _crypt

AUTOMATED=('catalogMatches','parameterNamesMatch','visibleRenderEffect','valuesPreserved','translatedNames','valueEdit','translatedRender','repeatCycles','projectSwitch','restoredExactly','restoredRender')
NATIVE=('effectChinese','chineseSearchAdd','parameterChinese','nativeValueControl')

def profile_data(profile):
    value=asdict(profile);value['count_bytes']=profile.count_bytes.hex();return value

def validation_complete(checks,native):
    return all(checks.get(key) is True for key in AUTOMATED) and all(native.get(key) is True for key in NATIVE)

def certificate_path(profile,root=None):return Path(root if root is not None else data_root()/'adapters')/(profile.flt_sha+'-'+profile.tdb_sha+'.json')

def save_validated(exe,profile,checks,native,pending,root=None):
    if pending!=0 or not validation_complete(checks,native):raise ValueError('验证或恢复未完成，不能启用适配')
    if not profile.native_dynamic:raise ValueError('本机证书只登记动态解析配置')
    record={'schemaVersion':1,'profile':profile_data(replace(profile,enabled=True)),'checks':checks,'native':native,'pendingRestores':0,'validatedAt':datetime.now(timezone.utc).isoformat(),'codepage':ctypes.windll.kernel32.GetACP()}
    envelope={'schemaVersion':1,'kind':'local-validated-adapter','protected':base64.b64encode(_crypt(json.dumps(record,ensure_ascii=False).encode('utf8'))).decode('ascii')}
    write_json_atomic(certificate_path(profile,root),envelope,ensure_ascii=True,indent=None)
    return load_validated(exe,root=root)

def load_validated(exe,root=None):
    resolver=Resolver(exe)
    try:
        # Static parsing does not execute any host function or accept an address
        # from the certificate. The encrypted record is still treated as data.
        hashes=resolver.modules
        path=Path(root if root is not None else data_root()/'adapters')/(hashes['FLT.dll']['sha256']+'-'+hashes['TDB.dll']['sha256']+'.json')
        if not path.is_file():return None
        if path.stat().st_size>131072:raise ValueError('本机适配证书过大')
        envelope=json.loads(path.read_text(encoding='utf8'))
        if envelope.get('schemaVersion')!=1 or envelope.get('kind')!='local-validated-adapter':raise ValueError('本机证书格式错误')
        record=json.loads(_crypt(base64.b64decode(envelope['protected'],validate=True),True).decode('utf8'))
        if record.get('schemaVersion')!=1 or record.get('pendingRestores')!=0 or not validation_complete(record.get('checks',{}),record.get('native',{})):raise ValueError('本机证书缺少完整验收')
        data=record['profile'];data['count_bytes']=bytes.fromhex(data['count_bytes']);stored=Profile(**data)
        if stored.version!=resolver.version or stored.encoding not in ('utf8','mbcs') or stored.match_encoding not in ('','utf8','mbcs') or type(stored.registry_vector) is not int or stored.registry_vector not in range(0,129,8):raise ValueError('本机证书目标不匹配')
        if 'mbcs' in (stored.encoding,stored.match_encoding) and record.get('codepage')!=ctypes.windll.kernel32.GetACP():raise ValueError('系统编码发生变化，请重新验证')
        fresh=resolver.complete_static(stored.host_version)
        for key,value in asdict(fresh).items():
            if key not in ('registry_vector','encoding','match_encoding','enabled') and getattr(stored,key)!=value:raise ValueError('本机证书与重新解析的结构不符：'+key)
        if not stored.enabled or not stored.native_dynamic:raise ValueError('本机证书未启用')
        # Also for a built-in version whose installed files differ from the built-in build (patched, slimmed or
        # repackaged AE): the running FLT.dll's hash then picks this one. Identical files keep the built-in.
        built=PROFILES.get(stored.version)
        if not built or (built.flt_sha,built.tdb_sha)!=(stored.flt_sha,stored.tdb_sha):LOCAL_PROFILES[stored.version]=stored
        return stored
    finally:resolver.close()

def load_installations(installs,root=None):
    folder=Path(root if root is not None else data_root()/'adapters')
    if not folder.is_dir() or not any(folder.glob('*.json')):return []
    warnings=[]
    for item in installs:
        try:load_validated(Path(item['exe']),folder)
        except (ValueError,OSError,RuntimeError,KeyError,TypeError):warnings.append('一个本机适配证书无法复核；该构建需要重新验证')
    return warnings
