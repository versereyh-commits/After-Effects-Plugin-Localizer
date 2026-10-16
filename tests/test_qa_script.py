"""Execute the real ExtendScript template against an isolated Node VM host.

This checks getter call counts and cancellation before mutations, not AE ABI.
Node is a development-only dependency; no AE or user files are accessed.
"""
from pathlib import Path
import json,shutil,subprocess,unittest

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
        source=(Path(__file__).resolve().parents[1]/'scripts'/'adapter_qa.jsx').read_text(encoding='utf8')
        result=subprocess.run([shutil.which('node'),'-e',RUNNER],input=json.dumps(dict(source=source,**options)),text=True,encoding='utf8',capture_output=True,check=True)
        return json.loads(result.stdout)
    def test_prepare_reads_whole_catalog_once(self):
        result=self.run_script();self.assertEqual(result['reads'],1);self.assertTrue(result['result']['prepared']);self.assertIn('save',result['events'])
    def test_expiry_after_catalog_prevents_project_mutations(self):
        result=self.run_script(expireOnRead=True);self.assertIn('取消',result['result']['error']);self.assertEqual(result['events'],[])
    def test_user_project_is_never_closed_or_changed(self):
        result=self.run_script(userProject=True);self.assertEqual(result['reads'],0);self.assertEqual(result['events'],[]);self.assertIn('error',result['result'])
    def test_missing_fixture_does_not_create_project(self):
        result=self.run_script(noFixture=True);self.assertEqual(result['events'],[]);self.assertIn('error',result['result'])
    def test_response_and_phase_echo_request_identity(self):
        result=self.run_script();self.assertEqual(result['result']['requestId'],'request-123');self.assertEqual(result['result']['action'],'prepare');self.assertEqual(result['progress']['requestId'],'request-123')
        self.assertEqual(result['progress']['phase'],'保存自有 QA 工程');self.assertLessEqual(result['result']['startedAt'],result['result']['finishedAt'])
