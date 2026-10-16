from __future__ import annotations
import base64,ctypes as C,json,os
from ctypes import wintypes as W
from runtime import data_root,write_json_atomic
from llm_client import PROVIDERS,DEFAULT_PROVIDER,provider

class Blob(C.Structure):_fields_=[('size',W.DWORD),('data',C.POINTER(C.c_ubyte))]

def _crypt(data:bytes,decrypt=False)->bytes:
    crypt=C.WinDLL('crypt32',use_last_error=True);kernel=C.WinDLL('kernel32',use_last_error=True)
    raw=C.create_string_buffer(data,len(data));source=Blob(len(data),C.cast(raw,C.POINTER(C.c_ubyte)));target=Blob()
    if decrypt:
        function=crypt.CryptUnprotectData
        function.argtypes=[C.POINTER(Blob),C.c_void_p,C.c_void_p,C.c_void_p,C.c_void_p,W.DWORD,C.POINTER(Blob)]
        okay=function(C.byref(source),None,None,None,None,1,C.byref(target))
    else:
        function=crypt.CryptProtectData
        function.argtypes=[C.POINTER(Blob),W.LPCWSTR,C.c_void_p,C.c_void_p,C.c_void_p,W.DWORD,C.POINTER(Blob)]
        okay=function(C.byref(source),'AE 名称汉化 翻译服务',None,None,None,1,C.byref(target))
    if not okay:raise C.WinError(C.get_last_error())
    kernel.LocalFree.argtypes=[C.c_void_p]
    try:return C.string_at(target.data,target.size)
    finally:kernel.LocalFree(target.data)

class Settings:
    """Translation-service choice plus each service's address, model and key.
    Switching services keeps what was typed for the others; keys are only written DPAPI-encrypted."""
    def __init__(self):
        self.path=data_root()/'api-settings.json';self.service=DEFAULT_PROVIDER;self.remember=False
        self.profiles={key:{'base_url':p.base_url,'model':p.models[0] if p.models else '','key':''} for key,p in PROVIDERS.items()}
        try:
            value=json.loads(self.path.read_text(encoding='utf8'))
            if not isinstance(value,dict):return
            if isinstance(value.get('providers'),dict):
                for key,entry in value['providers'].items():
                    if key in self.profiles and isinstance(entry,dict):self.load_profile(key,entry)
                if value.get('provider') in self.profiles:self.service=value['provider']
            else:
                # Settings saved before services were selectable belong to DeepSeek, or to a custom endpoint.
                base=value.get('baseUrl')
                self.service=DEFAULT_PROVIDER if not isinstance(base,str) or 'deepseek.com' in base else 'custom'
                self.load_profile(self.service,value)
        except (FileNotFoundError,ValueError,OSError):pass
    def load_profile(self,key,entry):
        profile=self.profiles[key]
        if isinstance(entry.get('baseUrl'),str):profile['base_url']=entry['baseUrl']
        if isinstance(entry.get('model'),str):profile['model']=entry['model']
        if isinstance(entry.get('protectedKey'),str) and entry['protectedKey']:
            profile['key']=_crypt(base64.b64decode(entry['protectedKey']),True).decode('utf8');self.remember=True
    def current(self)->dict:return self.profiles[self.service]
    @property
    def provider(self):return provider(self.service)
    @property
    def base_url(self):return self.current()['base_url']
    @base_url.setter
    def base_url(self,value):self.current()['base_url']=value
    @property
    def model(self):return self.current()['model']
    @model.setter
    def model(self,value):self.current()['model']=value
    @property
    def key(self):return self.current()['key']
    @key.setter
    def key(self,value):self.current()['key']=value
    def ready(self)->bool:
        return bool(self.base_url.strip() and self.model.strip() and (self.key.strip() or not self.provider.needs_key))
    def save(self):
        providers={}
        for key,profile in self.profiles.items():
            entry={'baseUrl':profile['base_url'].strip().rstrip('/'),'model':profile['model'].strip()}
            if self.remember and profile['key']:entry['protectedKey']=base64.b64encode(_crypt(profile['key'].encode('utf8'))).decode('ascii')
            providers[key]=entry
        value={'provider':self.service,'providers':providers}
        write_json_atomic(self.path,value,ensure_ascii=True)
