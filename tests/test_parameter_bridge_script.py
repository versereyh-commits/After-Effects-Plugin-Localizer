"""Run the actual label bridge's failure rollback in an isolated Node VM."""
from pathlib import Path
import json,shutil,subprocess,unittest

RUNNER=r'''
const fs=require('fs'),vm=require('vm');let source=fs.readFileSync(0,'utf8'),files={},state={pf:' ',display:'重复边缘像素'},calls=[];
const path='qa.aep';let hash=2166136261;for(let j=0;j<path.length;j++){hash^=path.charCodeAt(j);hash+=(hash<<1)+(hash<<4)+(hash<<7)+(hash<<8)+(hash<<24);}
const request={action:'labels',hostVersion:'24.2.1x2',projectToken:(hash>>>0).toString(16),permit:'permit',output:'output',expires:Date.now()+100000,helper:'test',operations:[
{address:'good',guid:'g',parent:'p',before:' ',after:'边缘重复',afterPf:'边缘重复',beforeDisplay:'重复边缘像素'},
{address:'bad',guid:'g',parent:'p',before:'x',after:'y',afterPf:'y',beforeDisplay:'x'}]};
function File(path){this.fsName=path;this.exists=path==='permit';this.open=()=>true;this.write=text=>files[path]=text;this.close=()=>{};}
function ExternalObject(){return {setLabel:(address,guid,parent,before,after,afterPf,displayBefore)=>{
calls.push([address,before,after,afterPf,displayBefore]);if(address==='bad')throw Error('controlled failure');
if(state.pf!==before||state.display!==displayBefore)throw Error('guard mismatch');state={pf:afterPf,display:after};}};}
const app={version:'24.2.1x2',project:{file:{fsName:path},numItems:0,renderQueue:{rendering:false}}};
vm.runInNewContext(source.replace('@@REQUEST@@',JSON.stringify(request)),{app,File,ExternalObject},{timeout:3000});
process.stdout.write(JSON.stringify({state,calls,result:JSON.parse(files.output)}));
'''

@unittest.skipUnless(shutil.which('node'),'Node is required for isolated JSX tests')
class BridgeRollbackTests(unittest.TestCase):
    def test_failure_restores_distinct_display_and_blank_pf_metadata(self):
        source=(Path(__file__).resolve().parents[1]/'scripts'/'parameter_bridge.jsx').read_text(encoding='utf8')
        result=subprocess.run([shutil.which('node'),'-e',RUNNER],input=source,text=True,encoding='utf8',capture_output=True,check=True)
        data=json.loads(result.stdout)
        self.assertEqual(data['state'],{'pf':' ','display':'重复边缘像素'})
        self.assertEqual(data['result']['rollbackFailed'],0);self.assertIn('controlled failure',data['result']['error'])
        self.assertEqual(data['calls'][-1],['good','边缘重复','重复边缘像素',' ','边缘重复'])
