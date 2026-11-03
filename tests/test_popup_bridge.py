import json,shutil,subprocess,unittest
from pathlib import Path

RUNNER=r'''
const fs=require('fs'),vm=require('vm');let files={},calls=[],text='A|B';let path='qa.aep',hash=2166136261;
for(let j=0;j<path.length;j++){hash^=path.charCodeAt(j);hash+=(hash<<1)+(hash<<4)+(hash<<7)+(hash<<8)+(hash<<24);}
let request={action:'popups',hostVersion:'26.5x89',projectToken:(hash>>>0).toString(16),permit:'permit',output:'output',expires:Date.now()+10000,helper:'helper',identityHelper:'identity',targetHost:{pid:3,creationTime:'0000000000000004',image:'C:/AE/AfterFX.exe'},operations:[{address:'good',guid:'g',parent:'p',pointer:'ptr',before:'A|B',after:'C|D',pfName:'Method',flags:'a42',default:'1'},{address:'bad',guid:'g',parent:'p',pointer:'ptr',before:'X|Y',after:'Z|Q',pfName:'Method',flags:'a42',default:'1'}]};
function File(path){this.fsName=path;this.exists=path==='permit';this.open=()=>true;this.write=s=>files[path]=s;this.close=()=>{};}
function ExternalObject(path){if(path==='lib:identity')return {identity:()=>JSON.stringify(request.targetHost)};return {setPopup:(a,g,p,ptr,before,after,name,flags,def)=>{calls.push([a,ptr,before,after,name,flags,def]);if(a==='bad')throw Error('fail');if(text!==before)throw Error('mismatch');text=after;return 1;},setLabel:()=>{throw Error('Must not write popup as label');}};}
let app={version:'26.5x89',project:{file:{fsName:path},numItems:0,renderQueue:{rendering:false}}};
vm.runInNewContext(fs.readFileSync(0,'utf8').replace('@@REQUEST@@',JSON.stringify(request)),{File,app,ExternalObject},{timeout:3000});
process.stdout.write(JSON.stringify({text,calls,result:JSON.parse(files.output)}));
'''
@unittest.skipUnless(shutil.which('node'),'Node required')
class PopupBridgeTests(unittest.TestCase):
    def test_failure_rolls_back_text_with_same_pointer_and_guards(self):
        source=(Path(__file__).resolve().parents[1]/'scripts/parameter_bridge.jsx').read_text(encoding='utf8')
        r=json.loads(subprocess.run([shutil.which('node'),'-e',RUNNER],input=source,text=True,encoding='utf8',capture_output=True,check=True).stdout)
        self.assertEqual(r['text'],'A|B');self.assertEqual(r['result']['rollbackFailed'],0);self.assertIn('fail',r['result']['error'])
        self.assertEqual(r['calls'][-1],['good','ptr','C|D','A|B','Method','a42','1'])
    def test_native_helper_reuses_verified_host_configuration(self):
        source=(Path(__file__).resolve().parents[1]/'native/ae_popup.c').read_text(encoding='utf8')
        self.assertIn('return LabelConfigure(a,n,out)',source);self.assertNotIn('FLT_POPUP',source);self.assertIn('pf_encode(wide,after,8192)',source)
        self.assertIn('strlen(after)+1!=capacity',source)
        self.assertNotIn('VirtualProtect(',source);self.assertNotIn('VirtualAlloc(',source)

if __name__=='__main__':unittest.main()
