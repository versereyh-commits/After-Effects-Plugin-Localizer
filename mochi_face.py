"""The assistant's character: a flat ball with a live face, drawn with QPainter.

A port of the mono, round-body parts of the mochi-face package. FaceEngine holds the springs and timing without
Qt, so it can be tested and timed alone; MochiFace paints it. The body is black on the light theme and white on
the dark theme, with the face the other way round. Coordinates follow the original: a 200-unit box centred on 0."""
from __future__ import annotations
import math,random,time
from PySide6.QtCore import QPointF,QRectF,Qt,QTimer,Signal
from PySide6.QtGui import QColor,QCursor,QImage,QPainter,QPainterPath,QPen
from PySide6.QtWidgets import QWidget
import ui_kit
from mochi_emotions import EMOTIONS

# States beyond the original 32, in the same data form (the original's numbering leaves 50 and up for this).
def _both(eye):return {'l':{**eye,'_m':1},'r':eye}
def _oval(w,h,**o):return {'s':'oval','w':w,'h':h,**o}
def _moon(w,h,cut,**o):return {'s':'moon','w':w,'h':h,'cut':cut,**o}
def _arc(w,bend,**o):return {'s':'arc','w':w,'bend':bend,'t':.055,**o}
_SMILE={'s':'arc','w':.2,'bend':-.05,'t':.036};_SOFT={'s':'arc','w':.13,'bend':-.03,'t':.034};_TINY={'s':'pill','w':.07,'h':.03};_FLAT={'s':'pill','w':.16,'h':.032};_O={'s':'oval','w':.085,'h':.1}
_SCAN={'r':1.22,'inc':.16,'tilt':1.34,'y':0,'speed':3.4,'len':2.2,'w':3.2};_HALO={'r':.92,'inc':.3,'tilt':-.16,'y':-.68,'speed':2.8,'len':3.6,'w':3.6};_BREATHE=['q','sin',.012,3400]
EXTRA=(
    dict(id='50',key='greeting',group='custom',name='打招呼',desc='面板刚打开',blink=None,poses=[{**_both(_arc(.22,.07)),'m':_SMILE}],enter=[[0,{'hop':.6}]],motion=[['roll','sin',.05,900,0,1400]],next='idle',nextAfter=1700),
    dict(id='51',key='goodbye',group='custom',name='再见',desc='面板收起',blink=None,poses=[{**_both(_arc(.2,-.05)),'m':_SOFT}],base={'pitch':.1},enter=[[0,{'q':.05,'y':2}],[320,{'q':0,'y':0}]],next='idle',nextAfter=900),
    dict(id='52',key='listening',group='custom',name='聆听',desc='你正在输入',blink=[2400,4200],gaze=False,poses=[{**_both(_oval(.22,.29)),'m':_TINY,'look':[-.42,.3]}],base={'roll':-.06},motion=[['pitch','beat',.09,1500],_BREATHE]),
    dict(id='53',key='reading',group='custom',name='阅读',desc='读取资料或文件',blink=[3000,5200],gaze=False,micro=False,poses=[{**_both(_moon(.21,.2,.34)),'m':_TINY,'look':[0,.34]}],motion=[['lookX','tri',.3,900],['lookY','sin',.1,2700],_BREATHE]),
    dict(id='54',key='translating',group='custom',name='翻译中',desc='正在把名称翻成中文',blink=[2600,4200],micro=False,hold=[520,900],poses=[{**_both(_oval(.2,.26)),'m':_O,'look':[.3,-.06]},{**_both(_oval(.2,.26)),'m':_SOFT,'look':[-.3,-.06]}],motion=[['y','beat',-2,1040],['q','sin',.008,1040]],ribbons=[_SCAN],emit=[['dots',2600]]),
    dict(id='55',key='writing',group='custom',name='写入中',desc='正在写入',micro=False,blink=[2800,4400],poses=[{**_both(_moon(.22,.2,.36,rot=-6)),'m':_FLAT,'look':[0,.16]}],base={'pitch':.08,'q':-.015},motion=[['q','beat',.03,640],['y','beat',1.6,640]],emit=[['check',2400]]),
    dict(id='56',key='compressing',group='custom',name='压缩中',desc='正在压缩上下文',blink=None,micro=False,gaze=False,poses=[{**_both(_arc(.2,-.05)),'m':{'s':'wave','w':.16,'amp':.014,'cycles':1.5,'t':.03}}],base={'q':.1,'y':4},enter=[[0,{'q':.16,'y':6}],[420,{'q':.08,'y':3}]],motion=[['q','sin',.045,900],['y','sin',1.8,900]],emit=[['steam',1700]]),
    dict(id='57',key='pondering',group='custom',name='想了很久',desc='模型回复得比较慢',blink=[2200,3800],gaze=False,hold=[1800,3000],poses=[{**_both(_oval(.18,.24)),'m':{**_SOFT,'rot':-14,'x':.03},'look':[.36,-.46]},{**_both(_moon(.2,.22,.28)),'m':_TINY,'look':[-.2,-.5]}],base={'roll':-.1},motion=[['roll','sin',.035,3800],_BREATHE],ribbons=[_HALO],emit=[['question',3400]]),
    dict(id='58',key='alert',group='custom',name='警觉',desc='已开启完全控制',blink=[1800,3400],poses=[{**_both(_oval(.21,.3)),'m':_FLAT}],base={'q':-.02},motion=[['lookX','hold',.12,3600],_BREATHE],enter=[[0,{'hop':.35,'emit':'bang'}]]),
    dict(id='59',key='patching',group='custom',name='修补中',desc='正在修补程序',micro=False,blink=[2600,4200],hold=[900,1500],poses=[{'l':{**_oval(.25,.3,y=-.12),'_m':1},'r':_moon(.2,.18,.5),'m':{**_FLAT,'rot':8},'look':[.14,.2]},{'l':{**_moon(.2,.18,.5),'_m':1},'r':_oval(.25,.3,y=-.12),'m':{**_FLAT,'rot':-8},'look':[-.14,.2]}],motion=[['y','beat',-2.4,700],['roll','noise',.02,500]],emit=[['sweat',3200],['steam',4200]]),
    dict(id='60',key='hover',group='custom',name='注意到你',desc='鼠标移到球上',blink=[2200,3600],poses=[{**_both(_oval(.23,.3)),'m':_SMILE}],base={'q':-.012},motion=[_BREATHE]),
    dict(id='61',key='choosing',group='custom',name='换挡',desc='换了模型或思考强度',blink=None,poses=[{**_both(_oval(.24,.3)),'m':_O,'look':[.2,-.2]}],enter=[[0,{'spin':1}]]),
)
EMOTIONS.update({d['key']:d for d in EXTRA})

TAU=math.pi*2;EYE_N=48;MOUTH_N=32;R=72;FACE_SCALE=1.14;SEGMENTS=26;SLOTS=3
def clamp(v,a,b):return a if v<a else b if v>b else v
def lerp(a,b,t):return a+(b-a)*t
def smooth01(a,b,v):
    t=clamp((v-a)/(b-a),0,1);return t*t*(3-2*t)

# Contours: each returns a closed polyline in face space (1 unit = body radius, y down).
def ellipse_pts(w,h,n=40):return [(math.cos(i/n*TAU)*w*.5,math.sin(i/n*TAU)*h*.5) for i in range(n)]
def stadium_pts(w,h):
    horizontal=w>=h;r=(h if horizontal else w)/2;reach=(w if horizontal else h)/2-r;pts=[];steps=14
    for s in range(2):
        sign=1 if s==0 else -1
        for i in range(steps+1):
            a=i/steps*math.pi+(-math.pi/2 if horizontal else 0)+(math.pi if s else 0)
            pts.append(((sign*reach if horizontal else 0)+math.cos(a)*r,(0 if horizontal else sign*reach)+math.sin(a)*r))
    return pts
def moon_pts(w,h,cut):
    yc=-h/2+cut*h;n=44;limit=math.asin(clamp(yc/(h*.5),-1,1));out=[]
    for i in range(n+1):
        a=lerp(limit,math.pi-limit,i/n);out.append((math.cos(a)*w*.5,math.sin(a)*h*.5))
    return out
def round_cap(p,q,r):
    base=math.atan2(p[1]-q[1],p[0]-q[0])
    return [(p[0]+math.cos(base-math.pi/2+i/6*math.pi)*r,p[1]+math.sin(base-math.pi/2+i/6*math.pi)*r) for i in range(1,6)]
def band_pts(line,t):
    r=t/2;n=len(line);left=[];right=[]
    for i in range(n):
        a=line[max(0,i-1)];b=line[min(n-1,i+1)];dx=b[0]-a[0];dy=b[1]-a[1];d=math.hypot(dx,dy) or 1;dx/=d;dy/=d
        left.append((line[i][0]+dy*r,line[i][1]-dx*r));right.append((line[i][0]-dy*r,line[i][1]+dx*r))
    return left+round_cap(line[n-1],line[n-2],r)+right[::-1]+round_cap(line[0],line[1],r)
def arc_line(w,bend):return [((i/18*2-1)*w*.5,-bend*(1-(i/18*2-1)**2)) for i in range(19)]
def wave_line(w,amp,cycles):return [((i/26-.5)*w,math.sin(i/26*TAU*cycles)*amp) for i in range(27)]
def spiral_line(s,turns):return [(math.cos(i/34*turns*TAU)*lerp(.02,s*.5,i/34),math.sin(i/34*turns*TAU)*lerp(.02,s*.5,i/34)) for i in range(35)]
def star_pts(s,inner,points):
    return [(math.cos(-math.pi/2+i/(points*2)*TAU)*(inner if i%2 else 1)*s*.5,math.sin(-math.pi/2+i/(points*2)*TAU)*(inner if i%2 else 1)*s*.5) for i in range(points*2)]
def cross_pts(s,t):
    a=s/2;b=t/2;c=math.sqrt(.5)
    return [((x-y)*c,(x+y)*c) for x,y in ((-b,-a),(b,-a),(b,-b),(a,-b),(a,b),(b,b),(b,a),(-b,a),(-b,b),(-a,b),(-a,-b),(-b,-b))]
def chevron_pts(s):
    d=s.get('dir',1);w=s['w'];h=s['h']
    return band_pts([(-d*w*.5,-h*.5),(-d*w*.1,-h*.2),(d*w*.5,0),(-d*w*.1,h*.2),(-d*w*.5,h*.5)],s.get('t',.05))
GENERATORS={'oval':lambda s:ellipse_pts(s['w'],s['h']),'pill':lambda s:stadium_pts(s['w'],s['h']),'moon':lambda s:moon_pts(s['w'],s['h'],s.get('cut',.42)),
            'arc':lambda s:band_pts(arc_line(s['w'],s['bend']),s.get('t',.05)),'wave':lambda s:band_pts(wave_line(s['w'],s.get('amp',.02),s.get('cycles',1.5)),s.get('t',.035)),
            'spiral':lambda s:band_pts(spiral_line(s['size'],s.get('turns',1.7)),s.get('t',.042)),'chevron':chevron_pts,
            'star':lambda s:star_pts(s['size'],s.get('inner',.5),s.get('points',5)),'cross':lambda s:cross_pts(s['size'],s.get('t',.075))}

def resample(poly,N):
    n=len(poly);seg=[math.hypot(poly[(i+1)%n][0]-poly[i][0],poly[(i+1)%n][1]-poly[i][1]) for i in range(n)]
    out=[0.0]*(N*2);step=sum(seg)/N;idx=0;walked=0.0
    for k in range(N):
        want=k*step
        while idx<n-1 and walked+seg[idx]<want:walked+=seg[idx];idx+=1
        a=poly[idx];b=poly[(idx+1)%n];t=(want-walked)/seg[idx] if seg[idx]>1e-9 else 0
        out[k*2]=lerp(a[0],b[0],t);out[k*2+1]=lerp(a[1],b[1],t)
    return out
def signed_area(pts,N):
    return sum(pts[i*2]*pts[(i+1)%N*2+1]-pts[(i+1)%N*2]*pts[i*2+1] for i in range(N))/2
def build_ring(spec,N,defaults=None):
    """A spec turned into a clockwise ring of N points, so springs never have to turn a contour inside out."""
    defaults=defaults or {};poly=GENERATORS[spec['s']](spec);rot=spec.get('rot',0)*math.pi/180;c=math.cos(rot);s=math.sin(rot)
    ox=spec.get('x',defaults.get('x',0));oy=spec.get('y',defaults.get('y',0));flip=-1 if spec.get('_m') else 1
    ring=resample([(flip*(x*c-y*s+ox),x*s+y*c+oy) for x,y in poly],N)
    if signed_area(ring,N)<0:
        for i in range(N//2):
            j=N-1-i;ring[i*2],ring[j*2]=ring[j*2],ring[i*2];ring[i*2+1],ring[j*2+1]=ring[j*2+1],ring[i*2+1]
    return ring
def centroid(ring,N):return sum(ring[0::2][:N])/N,sum(ring[1::2][:N])/N
def align_ring(start,target,N):
    """target rotated so each point travels the shortest way from start."""
    ax,ay=centroid(start,N);bx,by=centroid(target,N);fx=[v-ax for v in start[0::2]];fy=[v-ay for v in start[1::2]]
    tx=[v-bx for v in target[0::2]];ty=[v-by for v in target[1::2]];best=0;best_cost=math.inf
    for k in range(N):
        cost=0.0
        for i in range(N):
            j=i+k
            if j>=N:j-=N
            dx=fx[i]-tx[j];dy=fy[i]-ty[j];cost+=dx*dx+dy*dy
        if cost<best_cost:best_cost=cost;best=k
    out=[0.0]*(N*2)
    for i in range(N):
        j=(i+best)%N;out[i*2]=target[j*2];out[i*2+1]=target[j*2+1]
    return out

BOUNCE=dict(ms=1100,y=((0,0),(180,0),(480,-7),(780,0),(1100,0)),q=((0,0),(180,.022),(480,-.008),(780,.014),(1100,0)))
HOP=dict(ms=720,y=((0,0),(70,0),(270,-11),(470,0),(580,-2),(720,0)),q=((0,0),(70,.032),(260,-.02),(470,.034),(580,-.006),(720,0)))
def keyframe_at(track,t):
    if t<=track[0][0]:return track[0][1]
    for i in range(1,len(track)):
        at,value=track[i]
        if t<=at:return lerp(track[i-1][1],value,smooth01(track[i-1][0],at,t))
    return track[-1][1]
def spring_coef(w,z,dt):
    a=w*z;b=w*math.sqrt(max(1e-4,1-z*z));e=math.exp(-a*dt);c=math.cos(b*dt);s=math.sin(b*dt)
    return e*(c+a/b*s),e*s/b,-e*s*w*w/b,e*(c-a/b*s)
def wave_at(kind,t,period,phase):
    u=t/period+(phase or 0)
    if kind=='hold':return clamp(math.sin(TAU*u)*3,-1,1)
    if kind=='tri':return 1-4*abs(u%1-.5)
    if kind=='beat':return max(0,math.sin(TAU*u))**6
    if kind=='noise':return (math.sin(TAU*u)+math.sin(TAU*u*2.3+1.3)+math.sin(TAU*u*3.7+4))/2.2
    return math.sin(TAU*u)

# name: stiffness, damping ratio, rest value
CHANNELS={'yaw':(10,.68,0),'pitch':(10,.7,0),'roll':(8,.6,0),'spin':(4.2,.82,0),'x':(16,.5,0),'y':(20,.42,0),'q':(24,.36,0),'lookX':(8,.78,0),'lookY':(8,.78,0),
          'mx':(26,.8,0),'my':(26,.8,0),'open':(34,.6,1),'openL':(24,.6,1),'openR':(24,.6,1),'mo':(22,.6,1),'mouthA':(18,.95,1),'gazeAmt':(7,.98,1)}
EYE_LEFT={'x':-.32,'y':-.08};EYE_RIGHT={'x':.32,'y':-.08};MOUTH={'x':0,'y':.24}
HIDDEN_MOUTH={'s':'pill','w':.03,'h':.02}

class Ring:
    def __init__(self,N,rng):
        self.N=N;self.cur=[0.0]*(N*2);self.vel=[0.0]*(N*2);self.tgt=[0.0]*(N*2);self.flat=[0.0]*(N*2);self.centre=(0.0,0.0)
        phase=rng.random()*TAU;self.bucket=[int((math.cos(TAU*i/N+phase)+1)/2*5.99) for i in range(N)]
    def set(self,spec,defaults,instant):
        self.tgt=align_ring(self.cur,build_ring(spec,self.N,defaults),self.N)
        if instant:self.cur=self.tgt[:];self.vel=[0.0]*(self.N*2)
    def step(self,dt,w,z):
        coefs=[spring_coef(w*(.8+.4*b/5),z,dt) for b in range(6)];cur=self.cur;vel=self.vel;tgt=self.tgt;bucket=self.bucket
        for i in range(self.N*2):
            c0,c1,c2,c3=coefs[bucket[i>>1]];t=tgt[i];off=cur[i]-t;v=vel[i]
            cur[i]=t+c0*off+c1*v;vel[i]=c2*off+c3*v
        self.centre=centroid(cur,self.N)
    def settle(self):
        self.cur=self.tgt[:];self.vel=[0.0]*(self.N*2);self.centre=centroid(self.cur,self.N)

class Slot:
    """One orbit ribbon around the body."""
    def __init__(self,rng):
        self.a=0.0;self.aT=0;self.angle=rng.random()*TAU;self.ph=rng.random()*TAU;self.r=1.2;self.inc=1;self.tilt=0;self.y=0;self.speed=3;self.len=2.5;self.w=4;self.cfg=None;self.ttl=0.0
    def assign(self,cfg):
        for k in ('r','inc','tilt','y','speed','len','w'):setattr(self,k,cfg[k])

# Floating marks: where they start, how they drift, their life in seconds and size range; drawn by mark_path.
MARKS={'zzz':(True,(.5,-.82),-15,7,2.9,(.55,1.05),False),'question':(True,(.72,-.76),-9,3,1.9,(.75,1),False),'bang':(True,(.76,-.74),-11,5,1,(.8,1.25),False),
       'dots':(True,(.72,-.84),-3,0,1.9,(.85,1),False),'check':(True,(.74,-.74),-10,3,1.2,(.8,1.15),False),'sweat':(False,(.68,-.58),24,7,1.15,(.5,.72),False),
       'tear':(False,(.36,.08),34,0,1.1,(.42,.6),False),'sparkle':(False,(.82,-.62),-8,4,1.35,(.5,.95),True),'heart':(False,(.68,-.74),-14,5,1.8,(.6,.92),False),
       'steam':(False,(.74,-.68),-24,16,.95,(.45,.95),True)}

class FaceEngine:
    """Springs, schedules and effects, without Qt. frame(dt) advances; the widget reads the results."""
    def __init__(self,emotion='idle',rng=None,reduced=False,idle=None):
        self.rng=rng or random.Random();self.reduced=reduced;self.clock=0.0;self.queue=[];self.emitters=[];self.jumps=[];self.handlers=[]
        self.ch={name:{'p':rest,'v':0.0,'t':rest,'w':w,'z':z} for name,(w,z,rest) in CHANNELS.items()}
        self.motion={name:0.0 for name in CHANNELS};self.gaze=[0.0,0.0,0.0,0.0];self.eye_l=Ring(EYE_N,self.rng);self.eye_r=Ring(EYE_N,self.rng);self.mouth=Ring(MOUTH_N,self.rng)
        self.slots=[];self.confetti=[];self.floaters=[];self.last_touch=0.0;self.idle=idle;self.def_=None;self.emotion_start=0.0
        self.set_emotion(emotion,instant=True);self.settle()
    @property
    def key(self)->str:return self.def_['key']
    def rand(self,span):return span[0]+self.rng.random()*(span[1]-span[0])
    def set_emotion(self,key,instant=False,auto=False)->bool:
        d=EMOTIONS.get(key)
        if d is None:return False
        if not auto:self.last_touch=self.clock
        if self.def_ is d:return True
        self.def_=d;self.emotion_start=now=self.clock;self.queue=[];base=d.get('base') or {}
        for name in CHANNELS:
            if name not in ('spin','gazeAmt'):self.ch[name]['t']=base.get(name,CHANNELS[name][2])
        self.ch['gazeAmt']['t']=0 if d.get('gaze') is False else 1
        self.pose_index=0;self.set_pose(0,instant)
        if instant:self.settle_channels()
        self.next_blink=now+self.rand(d['blink']) if d.get('blink') else math.inf
        self.next_pose=now+self.rand(d['hold']) if d.get('hold') and len(d['poses'])>1 else math.inf
        self.next_antic=now+9000+self.rng.random()*9000 if d.get('antics') else math.inf
        self.next_micro=now+600
        use_fx=not self.reduced
        for f in self.floaters:f['age']=max(f['age'],MARKS[f['name']][4]*.72)
        self.set_ribbons(d.get('ribbons') if use_fx else None)
        if use_fx and d.get('ribbons') and d.get('ribbonsUntil'):self.queue.append((now+d['ribbonsUntil'],lambda:self.set_ribbons(None)))
        self.emitters=[{'name':name,'every':every,'at':now+500+self.rng.random()*400} for name,every in d.get('emit') or ()] if use_fx else []
        if d.get('enter') and not instant:
            last=0
            for at,keys in d['enter']:
                last=max(last,at);self.queue.append((now+at,lambda keys=keys:self.apply_key(keys)))
            if not d.get('keep'):
                def rest():
                    for name,value in base.items():
                        if name in self.ch:self.ch[name]['t']=value
                self.queue.append((now+last+320,rest))
        if d.get('next'):self.queue.append((now+d.get('nextAfter',1500),lambda:self.set_emotion(d['next'],auto=True)))
        return True
    def set_pose(self,index,instant=False):
        d=self.def_;pose=d['poses'][index] if index<len(d['poses']) else d['poses'][0];self.pose_index=index
        self.eye_l.set(pose['l'],EYE_RIGHT if pose['l'].get('_m') else EYE_LEFT,instant);self.eye_r.set(pose['r'],EYE_RIGHT,instant)
        if pose.get('m'):self.mouth.set(pose['m'],MOUTH,instant);self.ch['mouthA']['t']=1
        else:self.mouth.set(HIDDEN_MOUTH,MOUTH,instant);self.ch['mouthA']['t']=0
        base=d.get('base') or {}
        if pose.get('look'):self.ch['lookX']['t'],self.ch['lookY']['t']=pose['look']
        elif index==0 or any(p.get('look') for p in d['poses']):self.ch['lookX']['t']=base.get('lookX',0);self.ch['lookY']['t']=base.get('lookY',0)
    def apply_key(self,keys):
        for k,v in keys.items():
            if k in self.ch and k!='spin':self.ch[k]['t']=v
            elif k=='pose':self.set_pose(v)
            elif k=='hop':self.hop(v)
            elif k=='spin':self.spin(v)
            elif k=='burst':self.burst(v)
            elif k=='emit':self.mark(v)
    def settle_channels(self):
        for c in self.ch.values():c['p']=c['t'];c['v']=0.0
    def settle(self):
        self.settle_channels();self.eye_l.settle();self.eye_r.settle();self.mouth.settle();self.project_face()
    def hop(self,size=1):
        if not self.reduced:self.jumps.append((HOP,self.clock,size))
    def bounce(self):
        if not self.reduced and not self.jumps:self.jumps.append((BOUNCE,self.clock,1))
    def spin(self,turns=1):
        if self.reduced:return
        self.ch['spin']['t']+=turns*TAU;self.fling()
    def poke(self):
        """A nudge from the user: wakes a sleeping face, hops an awake one."""
        self.last_touch=self.clock;k=self.key
        if k in ('sleep','dim'):self.set_emotion('wake')
        elif k=='daze':self.set_emotion('idle')
        else:self.bounce()
    def blink(self,twice=False):
        opened=(self.def_.get('base') or {}).get('open',1)
        def close(at):
            self.queue.append((self.clock+at,lambda:self.ch['open'].__setitem__('t',.04)));self.queue.append((self.clock+at+80,lambda:self.ch['open'].__setitem__('t',opened)))
        close(0)
        if twice or self.rng.random()<.16:close(210)
    # Effects
    def ensure_slots(self):
        if not self.slots:self.slots=[Slot(self.rng) for _ in range(SLOTS)]
    def set_ribbons(self,items):
        if items:self.ensure_slots()
        for i,slot in enumerate(self.slots):
            cfg=items[i] if items and i<len(items) else None
            if cfg:
                if slot.a<.02:slot.assign(cfg);slot.angle=self.rng.random()*TAU
                slot.cfg=cfg;slot.aT=1;slot.ttl=0.0
            else:slot.aT=0
    def fling(self,duration=1.5):
        self.ensure_slots();slot=next((s for s in self.slots if s.aT==0),self.slots[-1])
        cfg={'r':1.34,'inc':.22+self.rng.random()*.4,'tilt':(self.rng.random()-.5)*1.5,'y':0,'speed':8.4,'len':3.4,'w':4.6}
        if slot.a<.02:slot.assign(cfg);slot.r=.98
        slot.cfg=cfg;slot.aT=1;slot.ttl=duration
    def burst(self,count=26):
        if self.reduced:return
        cx=self.ch['x']['p'];cy=self.ch['y']['p']
        for i in range(count):
            if len(self.confetti)>=90:break
            a=-math.pi/2+(self.rng.random()-.5)*2.5;v=150+self.rng.random()*210;w=3+self.rng.random()*4;rnd=self.rng.random()<.25
            self.confetti.append({'w':w,'h':w if rnd else 2+self.rng.random()*3,'round':rnd,'tone':i%3,'x':cx+math.cos(a)*R*.8,'y':cy+math.sin(a)*R*.95,'vx':math.cos(a)*v,'vy':math.sin(a)*v,
                                  'rot':self.rng.random()*360,'vr':(self.rng.random()-.5)*760,'flip':self.rng.random()*TAU,'vf':5+self.rng.random()*10,'life':0.0,'max':1.5+self.rng.random()*.8})
    def mark(self,name):
        if self.reduced or name not in MARKS or len(self.floaters)>=12:return
        stroke,start,_vy,_vx,_life,_size,scatter=MARKS[name];side=-1 if scatter and self.rng.random()<.5 else 1;jitter=(self.rng.random()-.5)*.3 if scatter else 0
        self.floaters.append({'name':name,'side':side,'x':self.ch['x']['p']+side*start[0]*R+jitter*R,'y':self.ch['y']['p']+start[1]*R+jitter*R,'age':0.0,'sway':self.rng.random()*TAU})
    # One frame
    def frame(self,dt):
        self.clock+=dt*1000;now=self.clock
        if self.queue:
            due=[item for item in self.queue if item[0]<=now];self.queue=[item for item in self.queue if item[0]>now]
            for _at,fn in due:fn()
        self.run_schedules(now);d=self.def_;mo=self.motion
        for name in mo:mo[name]=0.0
        t=now-self.emotion_start
        for m in d.get('motion') or ():
            c,kind,amp,period=m[:4];phase=m[4] if len(m)>4 else 0;decay=m[5] if len(m)>5 else 0
            a=amp*(math.exp(-t/decay) if decay else 1)*(.25 if self.reduced else 1);mo[c]+=a*wave_at(kind,t,period,phase)
        for jump in self.jumps[:]:
            track,start,size=jump;t2=now-start
            if t2>=track['ms']:self.jumps.remove(jump);continue
            mo['y']+=keyframe_at(track['y'],t2)*size;mo['q']+=keyframe_at(track['q'],t2)*size
        for c in self.ch.values():
            c0,c1,c2,c3=spring_coef(c['w'],c['z'],dt);off=c['p']-c['t'];v=c['v'];c['p']=c['t']+c0*off+c1*v;c['v']=c2*off+c3*v
        fast=d.get('morph',17);self.eye_l.step(dt,fast,.56);self.eye_r.step(dt,fast,.56);self.mouth.step(dt,fast*.92,.6)
        follow=1-math.exp(-dt*9);self.gaze[0]+=(self.gaze[2]-self.gaze[0])*follow;self.gaze[1]+=(self.gaze[3]-self.gaze[1])*follow
        self.project_face();self.step_ribbons(dt);self.step_confetti(dt);self.step_floaters(dt);self.check_idle(now)
    def run_schedules(self,now):
        d=self.def_
        if now>=self.next_blink:self.blink();self.next_blink=now+self.rand(d['blink'])
        if now>=self.next_pose:
            n=self.pose_index
            while n==self.pose_index:n=self.rng.randrange(len(d['poses']))
            self.set_pose(n);self.next_pose=now+self.rand(d['hold'])
        if now>=self.next_micro:
            on=d.get('micro') is not False;self.ch['mx']['t']=(self.rng.random()-.5)*.09 if on else 0;self.ch['my']['t']=(self.rng.random()-.5)*.06 if on else 0
            self.next_micro=now+1100+self.rng.random()*2400
        if now>=self.next_antic:
            roll=self.rng.random()
            if roll<.34:self.spin(1)
            elif roll<.7:self.bounce()
            else:self.blink(True)
            self.next_antic=now+9000+self.rng.random()*11000
        for e in self.emitters:
            if now>=e['at']:self.mark(e['name']);e['at']=now+e['every']*(.8+self.rng.random()*.4)
    def check_idle(self,now):
        """Long quiet moves an idle face to spacing out, then to sleep; the seconds come from the widget's owner."""
        if not self.idle:return
        quiet=now-self.last_touch;k=self.key
        if quiet>self.idle[1] and k in ('idle','daze'):self.set_emotion('sleep',auto=True)
        elif quiet>self.idle[0] and k=='idle':self.set_emotion('daze',auto=True)
    def val(self,k):return self.ch[k]['p']+self.motion[k]
    def project_face(self):
        """The body's transform, and every contour mapped onto the rotated sphere."""
        val=self.val;ch=self.ch;stretch=clamp(ch['y']['v']*.00042,-.11,.11);q=val('q')-abs(stretch)
        self.sx=1+q;self.sy=1-q;self.bx=val('x');self.by=val('y');self.ground=R*(1-self.sy)*.92;roll=val('roll')
        gx=(val('lookX')+self.gaze[0]*ch['gazeAmt']['p']*.72+ch['mx']['p'])*.64;gy=(val('lookY')+self.gaze[1]*ch['gazeAmt']['p']*.72+ch['my']['p'])*.52
        yaw=val('yaw')+ch['spin']['p']+gx;pitch=val('pitch')+gy
        self._rot=(math.cos(yaw),math.sin(yaw),math.cos(pitch),math.sin(pitch),math.cos(roll),math.sin(roll));self.folded=False
        opened=clamp(val('open'),.02,1.25);es=FACE_SCALE*EYE_BOOST
        self.show_l=self.project_ring(self.eye_l,es,es*opened*clamp(val('openL'),.02,1.25));self.show_r=self.project_ring(self.eye_r,es,es*opened*clamp(val('openR'),.02,1.25))
        self.mouth_alpha=clamp(ch['mouthA']['p'],0,1)
        self.show_m=self.mouth_alpha>.02 and self.project_ring(self.mouth,es,es*clamp(val('mo'),.15,1.8))
    def project_ring(self,ring,scale_x,scale_y)->bool:
        cy,sy,cp,sp,cr,sr=self._rot;cur=ring.cur;flat=ring.flat;cx0,cy0=ring.centre;front=False;sin=math.sin;cos=math.cos;hypot=math.hypot
        for i in range(ring.N):
            fx=cx0+(cur[i*2]-cx0)*scale_x;fy=cy0+(cur[i*2+1]-cy0)*scale_y;d=hypot(fx,fy)
            if d>1e-5:s=sin(d)/d;vx=fx*s;vy=fy*s;vz=cos(d)
            else:vx=vy=0.0;vz=1.0
            x1=vx*cy+vz*sy;z1=-vx*sy+vz*cy;y1=vy*cp+z1*sp;z2=-vy*sp+z1*cp;x2=x1*cr-y1*sr;y2=x1*sr+y1*cr
            if z2<0:
                # Behind the horizon: pinned to the rim so the contour folds away.
                n=hypot(x2,y2) or 1;x2/=n;y2/=n;self.folded=True
            else:front=True
            flat[i*2]=x2*R;flat[i*2+1]=y2*R
        return front
    def step_ribbons(self,dt):
        ease=1-math.exp(-dt*5);self.ribbon_lines=[]
        for slot in self.slots:
            if slot.ttl>0:
                slot.ttl-=dt
                if slot.ttl<=0:slot.aT=0
            slot.a+=(slot.aT-slot.a)*(1-math.exp(-dt*(7 if slot.aT else 3.4)))
            if slot.a<.012 and slot.aT==0:continue
            if slot.cfg:
                for k in ('r','inc','tilt','y','speed','len','w'):setattr(slot,k,getattr(slot,k)+(slot.cfg[k]-getattr(slot,k))*ease)
            slot.angle+=slot.speed*dt;slot.ph+=dt*.7
            si=math.sin(slot.inc);ci=math.cos(slot.inc);st=math.sin(slot.tilt);ct=math.cos(slot.tilt);direction=1 if slot.speed>=0 else -1;reach=slot.r*R*(.9+.1*slot.a);px=py=pz=0.0;bx=self.bx;by=self.by
            for s in range(SEGMENTS+1):
                u=s/SEGMENTS;a=slot.angle-direction*slot.len*u;wob=1+.05*math.sin(2*a+slot.ph);ox=math.cos(a)*reach*wob;oz=math.sin(a)*reach*wob;oy=math.sin(3*a+slot.ph)*R*.035
                y1=oy*ci+oz*si;z1=oz*ci-oy*si;x=bx+ox*ct-y1*st;y=by+slot.y*R+ox*st+y1*ct
                if s>0:
                    depth=1+z1/(reach or 1)*.28;width=max(.1,slot.w*(1-u*u)*depth*(.55+.45*slot.a))
                    self.ribbon_lines.append(((z1+pz)*.5>=0,px,py,x,y,width,slot.a,u))
                px=x;py=y;pz=z1
    def step_confetti(self,dt):
        drag=math.exp(-dt*1.5)
        for p in self.confetti[:]:
            p['life']+=dt;p['vy']+=420*dt;p['vx']*=drag;p['vy']*=drag;p['x']+=p['vx']*dt;p['y']+=p['vy']*dt;p['rot']+=p['vr']*dt;p['flip']+=p['vf']*dt
            if p['life']>=p['max']:self.confetti.remove(p)
    def step_floaters(self,dt):
        for f in self.floaters[:]:
            f['age']+=dt
            if f['age']>=MARKS[f['name']][4]:self.floaters.remove(f)
EYE_BOOST=1.25   # the ball is small on screen; the original suggests a larger face below 80px

def mark_path(name)->QPainterPath:
    p=QPainterPath()
    if name=='zzz':p.moveTo(-6,-6.5);p.lineTo(6,-6.5);p.lineTo(-6,6.5);p.lineTo(6,6.5)
    elif name=='question':p.moveTo(-5,-5.4);p.cubicTo(-5,-11.4,6,-11.4,6,-4.4);p.cubicTo(6,.8,0,.4,0,5);p.moveTo(0,10);p.lineTo(0,10.1)
    elif name=='bang':p.moveTo(0,-10);p.lineTo(0,3.4);p.moveTo(0,10);p.lineTo(0,10.1)
    elif name=='dots':
        for x in (-8,0,8):p.moveTo(x,0);p.lineTo(x,.1)
    elif name=='check':p.moveTo(-8,0);p.lineTo(-2.4,6.4);p.lineTo(8.4,-6.4)
    elif name in ('sweat','tear'):p.moveTo(0,-9);p.cubicTo(5,-2,7,1,7,4);p.arcTo(-7,-3,14,14,0,-180);p.cubicTo(-7,1,-5,-2,0,-9);p.closeSubpath()
    elif name=='sparkle':
        p.moveTo(0,-10);p.cubicTo(1,-3,3,-1,10,0);p.cubicTo(3,1,1,3,0,10);p.cubicTo(-1,3,-3,1,-10,0);p.cubicTo(-3,-1,-1,-3,0,-10);p.closeSubpath()
    elif name=='heart':p.moveTo(0,8.5);p.cubicTo(-10,1,-9,-7,-4,-7);p.cubicTo(-1.8,-7,-.6,-5.6,0,-4.2);p.cubicTo(.6,-5.6,1.8,-7,4,-7);p.cubicTo(9,-7,10,1,0,8.5);p.closeSubpath()
    elif name=='steam':p.moveTo(-8,4);p.cubicTo(-11,-1,-5,-5,-2,-3);p.cubicTo(0,-8,8,-6,7,-1);p.cubicTo(11,0,9,5,6,5);p.lineTo(-6,5);p.cubicTo(-7,5,-7.6,4.6,-8,4);p.closeSubpath()
    return p
MARK_PATHS={}
def ring_path(flat,N)->QPainterPath:
    p=QPainterPath();p.moveTo((flat[N*2-2]+flat[0])/2,(flat[N*2-1]+flat[1])/2)
    for i in range(N):
        j=(i+1)%N;x=flat[i*2];y=flat[i*2+1];p.quadTo(x,y,(x+flat[j*2])/2,(y+flat[j*2+1])/2)
    p.closeSubpath();return p

class MochiFace(QWidget):
    """The ball. Effects use difference blending, so they read on the ball and on the page behind it in both themes."""
    poked=Signal();hovered=Signal(bool)
    FPS=30
    def __init__(self,parent=None,emotion='idle',idle=(30000,90000),follow=True):
        super().__init__(parent);self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground,True);self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True);self.follow=follow
        self.engine=FaceEngine(emotion,reduced=not ui_kit.motion(),idle=idle);self.timer=QTimer(self);self.timer.setTimerType(Qt.TimerType.PreciseTimer);self.timer.setInterval(1000//self.FPS);self.timer.timeout.connect(self.tick);self.last=0.0
        self.blank=False;self.squash=0.0;self._layer=None;self.setCursor(Qt.CursorShape.PointingHandCursor)
    def set_emotion(self,key):
        ok=self.engine.set_emotion(key)
        if not self.timer.isActive():self.engine.settle();self.update()
        return ok
    @property
    def emotion(self)->str:return self.engine.key
    def showEvent(self,event):
        self.engine.reduced=not ui_kit.motion();self.last=time.perf_counter();self.timer.start();super().showEvent(event)
    def hideEvent(self,event):
        self.timer.stop();super().hideEvent(event)
    def enterEvent(self,event):
        self.hovered.emit(True);super().enterEvent(event)
    def leaveEvent(self,event):
        self.hovered.emit(False);super().leaveEvent(event)
    def tick(self):
        if self.window().isMinimized():return
        now=time.perf_counter();dt=clamp(now-self.last,.001,.05);self.last=now
        if self.follow:self.read_pointer()
        self.engine.frame(dt);self.update()
    def read_pointer(self):
        pos=self.mapFromGlobal(QCursor.pos());reach=max(180,self.width()*1.6);g=self.engine.gaze
        g[2]=clamp((pos.x()-self.width()/2)/reach,-1,1);g[3]=clamp((pos.y()-self.height()/2)/reach,-1,1)
    def on_body(self,pos)->bool:
        s=min(self.width(),self.height())/200;dx=pos.x()-self.width()/2;dy=pos.y()-self.height()/2
        return math.hypot(dx,dy)<=100*s   # the whole round spot: it is also the send button
    def mousePressEvent(self,event):
        if self.on_body(event.position()):self.engine.poke();self.poked.emit();event.accept()
        else:event.ignore()
    def set_squash(self,amount:float):
        """Wider and flatter when positive, narrower and taller when negative, on top of what the face is doing."""
        self.squash=amount;self.update()
    def effect_tone(self,value=255)->QColor:return QColor(value,value,value)
    def paintEvent(self,event):
        if self.blank:return   # while its surroundings are photographed
        e=self.engine;p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing,True)
        s=min(self.width(),self.height())/200;p.translate(self.width()/2,self.height()/2);p.scale(s,s)
        body=QColor(ui_kit.FACE_BODY);feature=QColor(ui_kit.FACE_FEATURE)
        lines=getattr(e,'ribbon_lines',())
        blend=QPainter.CompositionMode.CompositionMode_Difference
        if lines:
            self.paint_ribbons(p,lines,False,s)
        p.save();p.translate(e.bx,e.by+e.ground);p.scale(e.sx*(1+self.squash),e.sy*(1-self.squash));p.setPen(Qt.PenStyle.NoPen);p.setBrush(body);p.drawEllipse(QRectF(-R,-R,R*2,R*2))
        if e.folded:
            clip=QPainterPath();clip.addEllipse(QRectF(-R,-R,R*2,R*2));p.setClipPath(clip)
        for show,ring,weight,alpha in ((e.show_l,e.eye_l,1.8,1),(e.show_r,e.eye_r,1.8,1),(e.show_m,e.mouth,1.1,e.mouth_alpha)):
            if show:
                p.setOpacity(alpha);p.setPen(QPen(feature,weight,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin));p.setBrush(feature);p.drawPath(ring_path(ring.flat,ring.N))
        p.restore();p.setOpacity(1)
        if lines:self.paint_ribbons(p,lines,True,s)
        p.setCompositionMode(blend)
        self.paint_confetti(p,e);self.paint_floaters(p,e);p.end()
    def paint_ribbons(self,p,lines,front,scale):
        """Drawn on a layer and blended once: overlapping round caps would otherwise cancel into beads."""
        ratio=self.devicePixelRatioF();size=self.size()*ratio;layer=self._layer
        if layer is None or layer.size()!=size:layer=self._layer=QImage(size,QImage.Format.Format_ARGB32_Premultiplied);layer.setDevicePixelRatio(ratio)
        layer.fill(0);q=QPainter(layer);q.setRenderHint(QPainter.RenderHint.Antialiasing,True);q.translate(self.width()/2,self.height()/2);q.scale(scale,scale)
        for is_front,x0,y0,x1,y1,width,alpha,u in lines:
            if is_front==front:
                shade=int(255-u*190);q.setOpacity(alpha);q.setPen(QPen(QColor(shade,shade,shade),width,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap));q.drawLine(QPointF(x0,y0),QPointF(x1,y1))
        q.end();p.save();p.resetTransform();p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference);p.drawImage(0,0,layer);p.restore()
    def paint_confetti(self,p,e):
        tones=(255,189,138);p.setPen(Qt.PenStyle.NoPen)
        for c in e.confetti:
            p.save();p.translate(c['x'],c['y']);p.rotate(c['rot']);p.scale(1,math.cos(c['flip']));p.setOpacity(clamp((c['max']-c['life'])/.45,0,1));p.setBrush(self.effect_tone(tones[c['tone']]))
            rect=QRectF(-c['w']/2,-c['h']/2,c['w'],c['h'])
            if c['round']:p.drawEllipse(rect)
            else:p.drawRect(rect)
            p.restore()
    def paint_floaters(self,p,e):
        for f in e.floaters:
            stroke,_from,vy,vx,life,size,_scatter=MARKS[f['name']];u=f['age']/life;pop=clamp(u*5,0,1);scale=lerp(size[0],size[1],u)*(1-(1-pop)**3)
            p.save();p.translate(f['x']+f['side']*vx*f['age']+math.sin(f['age']*2.6+f['sway'])*2,f['y']+vy*f['age']);p.scale(scale,scale);p.setOpacity(clamp((1-u)*3,0,1)*clamp(u*8,0,1))
            path=MARK_PATHS.get(f['name'])
            if path is None:path=MARK_PATHS[f['name']]=mark_path(f['name'])
            tone=self.effect_tone()
            if stroke:p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(tone,3,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
            else:p.setPen(Qt.PenStyle.NoPen);p.setBrush(tone)
            p.drawPath(path);p.restore()
