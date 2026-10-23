"""Execute the production JSX instance branch against an isolated mock host."""
from pathlib import Path
import json,shutil,subprocess,unittest

RUNNER=r'''
const fs=require('fs'),vm=require('vm'),q=JSON.parse(fs.readFileSync(0,'utf8'));
let files={},events=[],permit=true;
function File(path){this.fsName=path;Object.defineProperty(this,'exists',{get:()=>permit});this.open=()=>true;this.write=t=>files[path]=t;this.close=()=>{};}
function CompItem(){}
const effects=[1,2].map(index=>{let name='CC Drizzle';const fx={matchName:'CC Drizzle',value:10};Object.defineProperty(fx,'name',{get:()=>name,set:v=>{events.push([index,v]);if(q.failSecond&&index===2&&v==='细雨')throw Error('controlled setter failure');name=v;}});return fx;});
const parade={numProperties:2,property:i=>effects[i-1]};
const layer={name:'Solid',property:()=>parade};if(!q.noLayerId)layer.id=10;
const comp=Object.assign(new CompItem(),{id:1,numLayers:1,layer:()=>layer});
const app={version:'24.2.1x2',project:{file:{fsName:'test.aep'},numItems:1,item:()=>comp,renderQueue:{rendering:false}},beginUndoGroup:()=>events.push('begin'),endUndoGroup:()=>events.push('end')};
let hash=2166136261;const source='test.aep|1';for(let j=0;j<source.length;j++){hash^=source.charCodeAt(j);hash+=(hash<<1)+(hash<<4)+(hash<<7)+(hash<<8)+(hash<<24);}
const token=(hash>>>0).toString(16),request={action:q.catalog?'instanceCatalog':'instanceNames',requestId:'test',hostVersion:'24.2.1x2',projectToken:q.wrongProject?'wrong':token,permit:'permit',output:'out',expires:Date.now()+(q.expired?-1:60000),operations:[1,2].map(i=>({compId:1,layerIndex:1,layerId:q.noLayerId?null:10,layerName:'Solid',effectIndex:i,matchName:'CC Drizzle',before:i===2&&q.wrongName?'Custom':'CC Drizzle',after:'细雨'}))};
request.targetHost={pid:3,creationTime:'0000000000000004',image:'C:/AE/AfterFX.exe'};request.identityHelper='identity';
vm.runInNewContext(q.template.replace('@@REQUEST@@',JSON.stringify(request)),{app,File,CompItem,ExternalObject:function(){return {identity:()=>JSON.stringify(request.targetHost)};}},{timeout:3000});
process.stdout.write(JSON.stringify({result:JSON.parse(files.out),names:effects.map(f=>f.name),values:effects.map(f=>f.value),events}));
'''

@unittest.skipUnless(shutil.which('node'),'Node is needed for isolated JSX tests')
class InstanceBridgeTests(unittest.TestCase):
    def run_script(self,**options):
        source=(Path(__file__).resolve().parents[1]/'scripts'/'parameter_bridge.jsx').read_text(encoding='utf8')
        result=subprocess.run([shutil.which('node'),'-e',RUNNER],input=json.dumps(dict(template=source,**options)),text=True,encoding='utf8',capture_output=True,check=True)
        return json.loads(result.stdout)
    def test_script_synchronizes_all_instances_without_values_or_native_calls(self):
        result=self.run_script();self.assertEqual(result['names'],['细雨','细雨']);self.assertEqual(result['values'],[10,10]);self.assertEqual(result['result']['changed'],2)
        self.assertEqual(result['result']['requestId'],'test');self.assertEqual(result['events'].count('begin'),1);self.assertEqual(result['events'].count('end'),1)
    def test_catalog_has_no_writes(self):
        result=self.run_script(catalog=True);self.assertEqual(len(result['result']['instances']),2);self.assertEqual(result['events'],[])
    def test_all_targets_are_preflighted_before_first_write(self):
        result=self.run_script(wrongName=True);self.assertIn('error',result['result']);self.assertEqual(result['events'],[])
    def test_project_switch_and_expiry_prevent_any_writes(self):
        for options in ({'wrongProject':True},{'expired':True}):
            result=self.run_script(**options);self.assertIn('error',result['result']);self.assertEqual(result['events'],[])
    def test_failed_setter_rolls_back_prior_titles(self):
        result=self.run_script(failSecond=True);self.assertEqual(result['names'],['CC Drizzle','CC Drizzle']);self.assertIn('error',result['result']);self.assertEqual(result['result']['rollbackFailed'],0)
    def test_older_host_without_layer_id_uses_guarded_index_and_name(self):
        result=self.run_script(noLayerId=True);self.assertEqual(result['names'],['细雨','细雨'])
