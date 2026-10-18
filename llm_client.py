"""Name translation through a choice of large-model services.

Most services speak the OpenAI chat-completions protocol; Claude uses Anthropic's
Messages API. Every reply is validated the same way before any name is kept."""
from __future__ import annotations
import errno,json,socket,time
from concurrent.futures import FIRST_COMPLETED,ThreadPoolExecutor,wait as wait_any
from dataclasses import dataclass,field
from http.client import IncompleteRead
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from urllib.parse import urlparse
from live_names import encode_name
from token_usage import reply_tokens

@dataclass(frozen=True)
class Provider:
    key:str
    name:str
    base_url:str
    models:tuple[str,...]
    protocol:str='openai'          # 'openai' (chat/completions) or 'anthropic' (messages)
    json_mode:bool=True            # sends response_format json_object
    needs_key:bool=True
    key_page:str=''
    token_field:str='max_tokens'
    max_tokens:int=4096
    extra:dict=field(default_factory=dict)
    note:str=''

PROVIDERS:dict[str,Provider]={p.key:p for p in (
    Provider('deepseek','DeepSeek','https://api.deepseek.com',('deepseek-flash','deepseek-chat'),key_page='https://platform.deepseek.com/api_keys',extra={'thinking':{'type':'disabled'}}),
    Provider('openai','OpenAI','https://api.openai.com/v1',('gpt-4.1-mini','gpt-4.1','gpt-4o-mini'),key_page='https://platform.openai.com/api-keys',token_field='max_completion_tokens'),
    Provider('anthropic','Anthropic Claude','https://api.anthropic.com/v1',('claude-haiku-5-5','claude-sonnet-5-5','claude-opus-5-5'),protocol='anthropic',json_mode=False,key_page='https://console.anthropic.com/settings/keys'),
    Provider('gemini','Google Gemini','https://generativelanguage.googleapis.com/v1beta/openai',('gemini-2.5-flash','gemini-2.5-pro'),key_page='https://aistudio.google.com/apikey',max_tokens=8192,
             note='使用 Gemini 的 OpenAI 兼容接口'),
    Provider('qwen','通义千问（阿里云百炼）','https://dashscope.aliyuncs.com/compatible-mode/v1',('qwen-plus','qwen-turbo','qwen-max'),key_page='https://bailian.console.aliyun.com/?apiKey=1'),
    Provider('zhipu','智谱 GLM','https://open.bigmodel.cn/api/paas/v4',('glm-4-flash','glm-4-plus','glm-4-air'),key_page='https://open.bigmodel.cn/usercenter/apikeys'),
    Provider('moonshot','月之暗面 Kimi','https://api.moonshot.cn/v1',('moonshot-v1-8k','moonshot-v1-32k','kimi-latest'),key_page='https://platform.moonshot.cn/console/api-keys'),
    Provider('doubao','火山方舟 豆包','https://ark.cn-beijing.volces.com/api/v3',('doubao-1-5-pro-32k-250115','doubao-1-5-lite-32k-250115'),json_mode=False,key_page='https://console.volcengine.com/ark',
             note='模型名也可填写方舟控制台中的推理接入点 ID'),
    Provider('siliconflow','硅基流动 SiliconFlow','https://api.siliconflow.cn/v1',('deepseek-ai/DeepSeek-V3','Qwen/Qwen2.5-72B-Instruct'),json_mode=False,key_page='https://cloud.siliconflow.cn/account/ak'),
    Provider('openrouter','OpenRouter','https://openrouter.ai/api/v1',('openai/gpt-4.1-mini','anthropic/claude-sonnet-5-5','google/gemini-2.5-flash'),json_mode=False,key_page='https://openrouter.ai/keys'),
    Provider('ollama','Ollama（本机）','http://localhost:11434/v1',('qwen2.5:7b','llama3.1:8b'),needs_key=False,note='在本机运行，不需要密钥，名称不离开这台电脑'),
    Provider('custom','自定义（OpenAI 兼容）','',(),json_mode=False,note='填写服务商提供的 OpenAI 兼容接口地址（通常以 /v1 结尾）'),
)}
DEFAULT_PROVIDER='deepseek'

def provider(key:str)->Provider:return PROVIDERS.get(key) or PROVIDERS[DEFAULT_PROVIDER]

SYSTEM_PROMPT=('你是 After Effects 视觉特效术语译者。将效果名称翻译为简洁自然的简体中文，用分类作为上下文。保留专有品牌含义，避免泛化成同一个名字。'
               '不要改 id，不要生成代码。每个中文名称最多47个UTF-8字节。只输出 JSON：{"translations":[{"id":"原id","name":"中文名称"}]}。')

# Parameter labels sit in the narrow Effect Controls panel and must fit 31 bytes (validate_label checks it again).
PARAMETER_PROMPT=('你是 After Effects 视觉特效术语译者。将“效果控件”面板中的参数标签翻译为简洁自然的简体中文；category 是参数所属的效果，用作上下文。'
                  '优先采用 Adobe 中文版 After Effects 的通用译法（如 Opacity→不透明度、Blend Mode→混合模式、Angle→角度）。'
                  '同一效果的不同参数不要译成同一个名字。保留常见缩写和专有名词（如 RGB、Alpha、HDR、CC），数字和单位原样保留。'
                  '参数标签显示空间很小：每个中文名称最多31个UTF-8字节（约10个汉字），宁可简短也不要超长。'
                  '不要改 id，不要生成代码。只输出 JSON：{"translations":[{"id":"原id","name":"中文名称"}]}。')

class TranslationError(RuntimeError):
    def __init__(self,message,partial,total):
        self.partial=dict(partial)
        super().__init__(f'{message}；已完成 {len(self.partial)}/{total} 项')

def check_endpoint(base_url:str):
    parsed=urlparse(base_url)
    if not parsed.hostname:raise ValueError('请填写接口地址')
    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('接口地址需要 HTTPS；本机接口（localhost）可使用 HTTP')

def build_request(service:Provider,base_url:str,key:str,model:str,batch:list[dict],*,system_prompt=None,messages=None)->Request:
    content=json.dumps({'items':batch},ensure_ascii=False);headers={'Content-Type':'application/json'}
    system_prompt=SYSTEM_PROMPT if system_prompt is None else system_prompt
    messages=[{'role':'user','content':content}] if messages is None else messages
    if service.protocol=='anthropic':
        body={'model':model,'max_tokens':service.max_tokens,'system':system_prompt,'messages':messages}
        headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'});url=base_url.rstrip('/')+'/messages'
    else:
        body={'model':model,'messages':[{'role':'system','content':system_prompt},*messages],service.token_field:service.max_tokens,'stream':False,**service.extra}
        if service.json_mode:body['response_format']={'type':'json_object'}
        if key:headers['Authorization']='Bearer '+key
        url=base_url.rstrip('/')+'/chat/completions'
    return Request(url,data=json.dumps(body,ensure_ascii=False).encode('utf8'),headers=headers,method='POST')

def analysis_turn(system_prompt,messages,config,*,wait=time.sleep,cancelled=None):
    """One bounded JSON turn for the adapter agent; never executes model output."""
    chosen=provider(config['service']);key=config['key'].strip();model=config['model'].strip()
    if chosen.needs_key and not key:raise ValueError('请先配置翻译模型的 API Key')
    if not model:raise ValueError('请填写模型名称')
    check_endpoint(config['base_url'])
    request=build_request(chosen,config['base_url'],key,model,[],system_prompt=system_prompt,messages=messages)
    for attempt in range(3):
        if cancelled and cancelled():raise RuntimeError('AI 识别已取消')
        try:
            with urlopen(request,timeout=40) as response:
                raw=response.read(262145)
            if cancelled and cancelled():raise RuntimeError('AI 识别已取消')
            if len(raw)>262144:raise RuntimeError('AI 分析响应过大，已停止')
            content,cut=reply_text(chosen,json.loads(raw.decode('utf8')))
            if cut:raise RuntimeError('AI 分析响应被截断，已停止')
            value=parse_json(content)
            if not isinstance(value,dict):raise ValueError()
            return value
        except HTTPError as error:
            code=error.code;error.close()
            if attempt==2 or not (code==429 or 500<=code<600):
                raise RuntimeError(f'{chosen.name} 分析请求失败（HTTP {code}），请检查接口、模型和余额') from None
        except (URLError,TimeoutError,ConnectionError):
            if attempt==2:raise RuntimeError(f'{chosen.name} 分析连接失败或超时') from None
        except (ValueError,KeyError,IndexError,TypeError,AttributeError,UnicodeError,IncompleteRead,OSError):
            raise RuntimeError(f'{chosen.name} 分析响应格式无效或读取失败') from None
        if cancelled and cancelled():raise RuntimeError('AI 识别已取消')
        wait(2**attempt)

def reply_text(service:Provider,payload:dict)->tuple[str,bool]:
    """The reply's text and whether the service cut it off at the token limit."""
    if service.protocol=='anthropic':
        text=''.join(part.get('text','') for part in payload['content'] if part.get('type')=='text')
        return text,payload.get('stop_reason')=='max_tokens'
    choice=payload['choices'][0]
    return choice['message']['content'],choice.get('finish_reason')=='length'

def parse_json(text:str):
    """Services without a JSON mode may wrap the object in a code fence or a sentence."""
    text=text.strip()
    try:return json.loads(text)
    except ValueError:pass
    start,end=text.find('{'),text.rfind('}')
    if start<0 or end<=start:raise ValueError('返回内容不是 JSON')
    return json.loads(text[start:end+1])

class _BatchFailed(Exception):
    """One batch could not be translated; `throttled` tells whether the service asked us to slow down (HTTP 429)."""
    def __init__(self,message,throttled=False):super().__init__(message);self.throttled=throttled

BATCH_SIZE=10
PARALLEL_BATCHES=3

def translate_names(items:list[dict],key:str,base_url='https://api.deepseek.com',model='deepseek-flash',progress=None,cancelled=None,*,service:str=DEFAULT_PROVIDER,wait=time.sleep,usage=None,workers=PARALLEL_BATCHES,system_prompt=None)->dict[str,str]:
    """Translates in batches of 10, up to `workers` batches at a time; once the service answers 429 the rest go one at a time.

    Progress is reported after each validated batch; cancellation is checked before each batch is sent. On a failure no
    new batch starts, batches already in flight finish and keep their results, and TranslationError carries everything
    finished so far. wait injects the retry delay; usage receives each reply's token count (rejected replies included).
    system_prompt replaces the effect-name prompt (parameter labels use PARAMETER_PROMPT)."""
    chosen=provider(service);name=chosen.name;key=key.strip()
    if chosen.needs_key and not key:raise ValueError(f'请先在翻译模型设置中填写 {name} 的 API Key')
    if not model.strip():raise ValueError('请先在翻译模型设置中填写模型名称')
    check_endpoint(base_url)
    result={};total=len(items);model=model.strip()
    if len({item['id'] for item in items})!=total:raise TranslationError('待翻译的效果标识重复',result,total)

    def run(batch):
        # The model sees short numbers, not the plugins' internal identifiers: it tends to "correct" odd ones (CC Composite's
        # is "CS Composite"), and one changed id used to throw away the whole batch.
        numbers={str(n):item['id'] for n,item in enumerate(batch,1)};expected=set(numbers.values())
        sent=[{**item,'id':n} for n,item in zip(numbers,batch)]
        request=build_request(chosen,base_url,key,model,sent,system_prompt=system_prompt);throttled=False
        for attempt in range(3):
            try:
                response=urlopen(request,timeout=60)
                break
            except HTTPError as error:
                code=error.code;error.close();throttled=throttled or code==429
                if attempt==2 or not (code==429 or 500<=code<600):
                    raise _BatchFailed(f'{name} 请求失败（HTTP {code}），请检查密钥、余额和模型配置',throttled) from None
            except (URLError,TimeoutError,ConnectionError) as error:
                reason=error.reason if isinstance(error,URLError) else error
                retryable=isinstance(reason,(TimeoutError,ConnectionError,socket.gaierror)) or (isinstance(reason,OSError) and reason.errno in (errno.ETIMEDOUT,errno.ENETUNREACH,errno.EHOSTUNREACH))
                if attempt==2 or not retryable:
                    raise _BatchFailed(f'{name} 连接失败或超时，请检查网络与接口地址',throttled) from None
            except IncompleteRead:raise _BatchFailed(f'{name} 响应被截断',throttled) from None
            except ValueError:raise _BatchFailed(f'{name} 接口地址或请求格式错误',throttled) from None
            wait(2**attempt)
        try:
            with response:payload=json.loads(response.read().decode('utf8'))
        except (IncompleteRead,OSError,URLError):raise _BatchFailed(f'{name} 响应读取失败或被截断',throttled) from None
        except (ValueError,UnicodeDecodeError):raise _BatchFailed(f'{name} 返回了无法解析的响应',throttled) from None
        if usage is not None:
            # Counting must never cost the user a finished batch.
            try:usage(reply_tokens(payload))
            except Exception:pass
        try:
            content,cut=reply_text(chosen,payload)
            if cut:raise _BatchFailed(f'{name} 译文被截断',throttled)
            if not content:raise ValueError('返回空译文')
            rows=parse_json(content)['translations'];translated={}
            if not isinstance(rows,list):raise ValueError('译文格式错误')
            for row in rows:
                raw,text=row['id'],row['name']
                # 1 as well as "1"; the real identifier is taken too when a model echoes it back.
                label=str(raw).strip() if isinstance(raw,(str,int)) and not isinstance(raw,bool) else None
                identity=numbers.get(label) or (label if label in expected else None)
                if identity is None or identity in translated:raise ValueError(f'返回了重复或未知标识（{str(raw)[:40]}）')
                encoded=encode_name(text);translated[identity]=encoded.split(b'\0',1)[0].decode('utf8')
            if set(translated)!=expected:raise ValueError('返回的译文不完整')
        except (KeyError,IndexError,TypeError,ValueError,AttributeError) as error:
            # Our own validation messages carry no secrets and tell the user what to fix.
            detail=str(error) if isinstance(error,ValueError) and not isinstance(error,json.JSONDecodeError) else '译文格式错误'
            raise _BatchFailed(f'{name} 译文校验失败：'+detail,throttled) from None
        return translated,throttled

    pending=[items[i:i+BATCH_SIZE] for i in range(0,total,BATCH_SIZE)];running=set();failure=None;throttled=False;limit=max(1,int(workers))
    with ThreadPoolExecutor(max_workers=limit) as pool:
        while pending or running:
            while pending and failure is None and len(running)<(1 if throttled else limit):
                if cancelled is not None and cancelled():failure=f'{name} 翻译已取消';break
                running.add(pool.submit(run,pending.pop(0)))
            if failure is not None:pending.clear()
            if not running:break
            done,running=wait_any(running,return_when=FIRST_COMPLETED)
            for future in done:
                try:translated,hit=future.result()
                except _BatchFailed as error:
                    throttled=throttled or error.throttled;failure=failure or str(error);continue
                throttled=throttled or hit;result.update(translated)
                if progress is not None:progress(len(result),total)
    if failure is not None:raise TranslationError(failure,result,total)
    return result

# Words in model ids that mark services other than chat (embeddings, speech, images, moderation).
NOT_CHAT=('embed','tts','whisper','dall-e','moderation','transcribe','audio','realtime','image','davinci','babbage','rerank','search')

def list_models(service:str,base_url:str,key:str,*,opener=urlopen)->list[str]:
    """Model ids the service offers right now (GET .../models). Only the key is sent; obvious non-chat
    models are left out unless that would leave nothing."""
    chosen=provider(service);name=chosen.name;key=key.strip()
    if chosen.needs_key and not key:raise ValueError(f'请先填写 {name} 的 API Key')
    check_endpoint(base_url)
    headers={'Accept':'application/json'};url=base_url.rstrip('/')+'/models'
    if chosen.protocol=='anthropic':headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'});url+='?limit=1000'
    elif key:headers['Authorization']='Bearer '+key
    try:
        with opener(Request(url,headers=headers,method='GET'),timeout=20) as response:payload=json.loads(response.read().decode('utf8'))
    except HTTPError as error:
        code=error.code;error.close()
        raise RuntimeError(f'{name} 获取模型列表失败（HTTP {code}）'+('，请检查 API Key' if code in (401,403) else '，此服务可能不提供模型列表，可直接输入模型名称' if code in (404,405) else '')) from None
    except (URLError,TimeoutError,ConnectionError,IncompleteRead,OSError):raise RuntimeError(f'{name} 连接失败或超时，请检查网络与接口地址') from None
    except (ValueError,UnicodeDecodeError):raise RuntimeError(f'{name} 返回了无法解析的模型列表') from None
    rows=payload.get('data',payload.get('models')) if isinstance(payload,dict) else payload
    if not isinstance(rows,list):raise RuntimeError(f'{name} 返回的模型列表格式无法识别')
    ids=[]
    for row in rows:
        value=row.get('id') or row.get('name') if isinstance(row,dict) else row
        if isinstance(value,str) and value.strip():
            value=value.strip()
            if value.startswith('models/'):value=value[len('models/'):]  # Gemini lists "models/<id>"; chat takes the bare id
            if value not in ids:ids.append(value)
    chat=[m for m in ids if not any(word in m.casefold() for word in NOT_CHAT)]
    return sorted(chat or ids,key=str.casefold)
