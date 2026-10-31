"""Execute the real ExtendScript template against an isolated Node VM host.

This checks getter call counts and cancellation before mutations, not AE ABI.
Node is a development-only dependency; no AE or user files are accessed.
"""
from pathlib import Path
import json,re,shutil,subprocess,unittest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'

def chained_ternaries(source):
    """Lines where a ?: starts in the else branch of another ?: at the same nesting level.

    ExtendScript evaluates a?b:c?d:e as (a?b:c)?d:e (AE QA results 2018-2026 all show it), so such chains
    must be parenthesized or written as if/else.
    """
    text=re.sub(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*[\s\S]*?\*/',lambda m:'""'+'\n'*m.group().count('\n'),source)
    found,stack,line=[],[[0,False]],1
    for char in text:
        state=stack[-1]
        if char in '([{':stack.append([0,False])
        elif char in ')]}':
            if len(stack)>1:stack.pop()
        elif char=='?':
            if state[1]:found.append(line)
            state[0]+=1
        elif char==':' and state[0]:state[0]-=1;state[1]=True
        elif char in ';,\n':state[:]=[0,False]
        if char=='\n':line+=1
    return found

class TernaryTests(unittest.TestCase):
    def test_detector(self):
        self.assertEqual(chained_ternaries("var a=x?1:y?2:3;\nvar b=x?1:(y?2:3);\nvar c={k:x?1:2,m:y?3:4};\nvar d='a?b:c?d';"),[1])
    def test_scripts_have_no_chained_ternary(self):
        for path in sorted(SCRIPTS.glob('*.jsx')):
            with self.subTest(path.name):self.assertEqual(chained_ternaries(path.read_text(encoding='utf8')),[])

RUNNER=r'''
const fs=require('fs'),vm=require('vm');const q=JSON.parse(fs.readFileSync(0,'utf8'));
let reads=0,permit=true,events=[],files={},project;
function File(path){this.fsName=path;this.path=path;Object.defineProperty(this,'exists',{get:()=>path==='permit'?permit:Object.hasOwn(files,path)});this.open=()=>true;this.write=text=>files[path]=text;this.close=()=>{};}
function CompItem(){}
const scalar={matchName:'ADBE Gaussian Blur 2-0001',setValue:()=>events.push('setValue')};
const effects={addProperty:name=>{events.push('effect:'+name);return {enabled:true,property:()=>scalar};}};
const layer={name:'QA Effects',property:()=>effects};
const comp=Object.assign(new CompItem(),{name:'qa',numLayers:3,layers:{addSolid:()=>{events.push('solid');return layer;}},layer:()=>layer,openInViewer:()=>{}});
project={file:q.userProject?new File('user.aep'):null,numItems:q.userProject?2:0,renderQueue:{rendering:false},items:{addComp:()=>{events.push('addComp');project.numItems=5;return comp;}},item:()=>comp,save:file=>{events.push('save');project.file=file;files[file.path]='project';},close:()=>{events.push('close');project.file=null;project.numItems=0;}};
let available=Array.from({length:200},(_,i)=>({matchName:'Other'+i}));
if(!q.noFixture)available.push({matchName:'ADBE Gaussian Blur 2'},{matchName:'CC Drizzle'});
const app={version:'23.0x59',project};Object.defineProperty(app,'effects',{get:()=>{reads++;if(q.expireOnRead)permit=false;return available;}});
const request={action:'prepare',requestId:'request-123',hostVersion:'23.0x59',project:'qa.aep',second:'second.aep',comp:'qa',permit:'permit',output:'result',progress:'progress',expires:Date.now()+60000};
vm.runInNewContext(q.source.replace('@@REQUEST@@',JSON.stringify(request)),{app,File,CompItem,CloseOptions:{DO_NOT_SAVE_CHANGES:0}},{timeout:3000});
process.stdout.write(JSON.stringify({result:JSON.parse(files.result),progress:JSON.parse(files.progress),reads,events}));
'''

@unittest.skipUnless(shutil.which('node'),'Node is needed for the isolated JSX regression tests')
class QAScriptTests(unittest.TestCase):
    def run_script(self,**options):
        source=(SCRIPTS/'adapter_qa.jsx').read_text(encoding='utf8')
        result=subprocess.run([shutil.which('node'),'-e',RUNNER],input=json.dumps(dict(source=source,**options)),text=True,encoding='utf8',capture_output=True,check=True)
        return json.loads(result.stdout)
    def test_prepare_reads_whole_catalog_once(self):
        result=self.run_script();self.assertEqual(result['reads'],1);self.assertTrue(result['result']['prepared']);self.assertIn('save',result['events'])
        self.assertIn('effect:ADBE Gaussian Blur 2',result['events']);self.assertEqual(result['result']['fixture']['testEffect'],'ADBE Gaussian Blur 2')
    def test_expiry_after_catalog_prevents_project_mutations(self):
        result=self.run_script(expireOnRead=True);self.assertIn('取消',result['result']['error']);self.assertEqual(result['events'],[])
    def test_user_project_is_never_closed_or_changed(self):
        result=self.run_script(userProject=True);self.assertEqual(result['reads'],0);self.assertEqual(result['events'],[]);self.assertIn('error',result['result'])
    def test_missing_fixture_does_not_create_project(self):
        result=self.run_script(noFixture=True);self.assertEqual(result['events'],[]);self.assertIn('error',result['result'])
    def test_response_and_phase_echo_request_identity(self):
        result=self.run_script();self.assertEqual(result['result']['requestId'],'request-123');self.assertEqual(result['result']['action'],'prepare');self.assertEqual(result['progress']['requestId'],'request-123')
        self.assertEqual(result['progress']['phase'],'保存自有 QA 工程');self.assertLessEqual(result['result']['startedAt'],result['result']['finishedAt'])
