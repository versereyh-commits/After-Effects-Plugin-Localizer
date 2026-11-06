"""Checks that a corrected module leaves the assistant able to work, because a broken assistant cannot repair itself.
A proposed patch is tried in a separate copy of the program before it is saved; after a restart, the same kind of check
decides whether the patches are kept. Both run one whole conversation turn, request building and reply parsing included,
against a scripted reply: nothing goes over the network and the real API key is never involved. Cannot be patched."""
from __future__ import annotations
import importlib,json,os,shutil,subprocess,sys,tempfile,threading,time,traceback
from pathlib import Path
import patch_loader

REPLY_OK='自检通过'
CONFIG={'service':'deepseek','key':'selftest','base_url':'https://selftest.invalid/v1','model':'selftest','effort':'off'}
# What the assistant cannot live without, tried even when the patch is elsewhere: a module may break another on import.
CHAIN=('llm_client','agent_core','agent_files','agent_tools','assistant_store','mochi_face','ui_kit','assistant_panel')
CHILD_TIMEOUT=120;TURN_TIMEOUT=40;HEALTH_TIMEOUT=30000

class Response:
    def __init__(self,body):self.body=body
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def read(self,_size=-1):return self.body
class ScriptedService:
    """Stands where urlopen stands: first asks for a harmless tool, then says the agreed words."""
    def __init__(self):self.count=0
    def __call__(self,request,timeout=None):
        self.count+=1
        reply={'understood':'自检','calls':[{'tool':'app_status','args':{}}]} if self.count==1 else {'say':REPLY_OK}
        text=json.dumps(reply,ensure_ascii=False)
        return Response(json.dumps({'choices':[{'message':{'content':text},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1}},ensure_ascii=False).encode('utf8'))

def tool_worked(messages)->bool:
    return any(m.get('role')=='user' and str(m.get('content','')).startswith('[工具结果') and '"ok": true' in m['content'] for m in messages)

# Trying a patch before it is saved
def child_command(candidate:Path,result:Path)->list[str]:
    base=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(patch_loader.source_folder()/'app_live.py')]
    return base+[patch_loader.SELFTEST,str(candidate),str(result)]
def preflight(name:str,version:str,text:str)->dict:
    """Runs the program in a separate process with this patch added to the current ones. The running program, its data and its settings stay out of it."""
    work=Path(tempfile.mkdtemp(prefix='ae-patch-check-'));candidate=work/'candidate';candidate.mkdir();result=work/'result.json';home=work/'home';home.mkdir()
    try:
        for file in (patch_loader.folder(version).glob('*.py') if patch_loader.folder(version).is_dir() else ()):shutil.copy2(file,candidate/file.name)
        (candidate/f'{name}.py').write_text(text,encoding='utf8')
        env={**os.environ,'LOCALAPPDATA':str(home),'APPDATA':str(home),'QT_QPA_PLATFORM':'offscreen','AE_LOCALIZER_NO_MOTION':'1'}
        try:subprocess.run(child_command(candidate,result),env=env,cwd=str(work),timeout=CHILD_TIMEOUT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired:return {'ok':False,'stage':'超时','error':f'{CHILD_TIMEOUT} 秒内没有完成，程序可能卡住了'}
        try:value=json.loads(result.read_text(encoding='utf8'))
        except (OSError,ValueError):return {'ok':False,'stage':'启动','error':'程序还没跑到自检就退出了，可能在导入或启动时崩溃'}
        return value if isinstance(value,dict) and 'ok' in value else {'ok':False,'stage':'启动','error':'自检结果无效'}
    finally:shutil.rmtree(work,ignore_errors=True)

def run_child(window_class,candidate:Path,result:Path):
    """The other side of preflight, in the copy of the program being tried. Never returns."""
    stage='导入模块';code=1
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        # Startup's scheduled work (scanning, connecting to AE, checking for updates) has no place in a trial.
        QTimer.singleShot=staticmethod(lambda *a,**k:None)
        for name in (*(p.stem for p in candidate.glob('*.py') if p.stem!='app_live'),*CHAIN):importlib.import_module(name)
        stage='打开窗口';window=window_class();window.show();app=QApplication.instance();app.processEvents()
        stage='打开助手面板';panel=window.assistant;panel.toggle();app.processEvents()
        stage='助手对话';service=ScriptedService();panel.agent.opener=service;panel.host.config=lambda:dict(CONFIG);window.settings.ready=lambda:True;panel.send('自检')
        deadline=time.monotonic()+TURN_TIMEOUT
        while panel.in_turn and time.monotonic()<deadline:
            app.processEvents();time.sleep(.005)
        if panel.in_turn:raise RuntimeError('助手对话没有结束')
        # What the assistant itself said is usually the real error, which whoever made the patch needs in order to fix it.
        said=' '.join(str(i.get('text','')) for i in panel.items[-2:])[:600]
        if service.count!=2:raise RuntimeError(f'助手没有按预期请求模型（{service.count} 次）。它说：{said}')
        if not tool_worked(panel.agent.messages):raise RuntimeError('助手调用工具失败：'+str(panel.agent.messages[-2:])[:600])
        if not panel.items or panel.items[-1].get('text')!=REPLY_OK:raise RuntimeError('助手的回复没有显示出来')
        value={'ok':True};code=0
    except BaseException:
        value={'ok':False,'stage':stage,'error':traceback.format_exc()[-1500:]}
    try:result.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')
    except OSError:code=1
    os._exit(code)

# Deciding after a restart
class HealthEvents:
    def __init__(self):self.said=[];self.states=[]
    def state(self,name,detail):self.states.append((name,detail))
    def say(self,text):self.said.append(text)
class HealthHost:
    """The real prompt and tools, read-only, with the scripted service's settings: the turn leaves no trace in the conversation, the usage figures or the key."""
    def __init__(self,host):self.host=host
    def level(self):return 'read'
    def all_tools(self):return [t for t in self.host.all_tools() if t.level=='read']
    def tools(self):return self.all_tools()
    def prompt(self):return self.host.prompt()
    def config(self):return dict(CONFIG)
    def usage(self,*_):pass
    def confirm(self,*_):return False
def health(window,done):
    """One conversation turn beside the user's own, in this running program. done(ok, detail) comes on the main thread, once."""
    from PySide6.QtCore import QTimer
    from agent_core import Agent
    panel=window.assistant;told=threading.Event()
    def finish(ok,detail):
        if not told.is_set():told.set();done(ok,detail)
    def work():
        try:
            service=ScriptedService();events=HealthEvents();agent=Agent(HealthHost(panel.host),events);agent.opener=service;agent.run('自检')
            if service.count!=2:raise RuntimeError(f'助手没有按预期请求模型（{service.count} 次）：{str(events.said)[:300]}')
            if not tool_worked(agent.messages):raise RuntimeError('助手调用工具失败')
            if events.said!=[REPLY_OK]:raise RuntimeError('助手没有给出回复：'+str(events.said)[:200])
            ok,detail=True,''
        except Exception:ok,detail=False,traceback.format_exc()[-800:]
        panel.bridge.call.emit(lambda:finish(ok,detail))
    QTimer.singleShot(HEALTH_TIMEOUT,lambda:finish(False,'助手自检超时'))
    threading.Thread(target=work,daemon=True,name='assistant-health').start()
