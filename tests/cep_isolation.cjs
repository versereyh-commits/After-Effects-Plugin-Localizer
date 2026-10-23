const fs=require('fs'),path=require('path'),os=require('os'),vm=require('vm'),assert=require('assert');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'cep-isolation-'));
try {
 const ext=path.join(tmp,'ext');fs.mkdirSync(ext);fs.writeFileSync(path.join(ext,'parameter_bridge.jsx'),'var request=@@REQUEST@@;');
 const user=path.join(tmp,'user');fs.mkdirSync(user);const calls=[[],[]],ticks=[[],[]];
 const identities=[{pid:3,creationTime:'0000000000000004',image:'C:/Program Files/Same/AfterFX.exe'},{pid:4,creationTime:'0000000000000005',image:'C:/Program Files/Same/AfterFX.exe'}];
 function client(index){
   const identity=identities[index],api={};
   for(const [method,run] of Object.entries({readFile:p=>({data:fs.readFileSync(p,'utf8')}),writeFile:(p,d)=>{fs.writeFileSync(p,d);return {};},makedir:p=>{fs.mkdirSync(p);return {};},readdir:p=>({data:fs.readdirSync(p)}),deleteFile:p=>{fs.unlinkSync(p);return {};},rename:(a,b)=>{if(fs.existsSync(b))throw Error('CEP cannot replace');fs.renameSync(a,b);return {};}}))api[method]=(...args)=>{try{return {err:0,...run(...args)};}catch(e){return {err:3};}};
   const host={getSystemPath:k=>k==='extension'?ext:user,getHostEnvironment:()=>JSON.stringify({appVersion:'24.2.1'}),evalScript:(s,cb)=>{
     if(s.includes('lib.identity()'))cb(JSON.stringify(identity));else {calls[index].push(JSON.parse(s.slice('var request='.length,-1)));cb('undefined');}
   }};
   vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8').replace('@@BRIDGE_ID@@','test'),{window:{__adobe_cep__:host,cep:{fs:api}},setInterval:f=>ticks[index].push(f)});
 }
 client(0);client(1);
 const base=path.join(user,'AE-Localizer','bridge');
 function submit(root,id,target){const folder=path.join(root,id.repeat(32));fs.mkdirSync(folder);fs.writeFileSync(path.join(folder,'request.json'),JSON.stringify({targetHost:target,action:'addEffect',output:folder+'/result.json',permit:folder+'/permit',expires:Date.now()+100000}));return folder;}
 const roots=identities.map(a=>path.join(base,'hosts',a.pid+'-'+a.creationTime));
 submit(roots[0],'a',identities[0]);submit(roots[1],'b',identities[1]);
 const wrong=submit(roots[0],'c',identities[1]),legacy=submit(base,'d',identities[0]);
 ticks.flat().forEach(f=>f());ticks.flat().forEach(f=>f());
 assert.equal(calls[0].length,1);assert.equal(calls[1].length,1);
 assert.equal(calls[0][0].targetHost.pid,3);assert.equal(calls[1][0].targetHost.pid,4);
 assert(fs.existsSync(wrong+'/request.json'));assert(fs.existsSync(legacy+'/request.json'));
 console.log(JSON.stringify({sameVersionHostsIsolated:true,wrongHostUnclaimed:true,legacyRootUnclaimed:true}));
}finally {fs.rmSync(tmp,{recursive:true,force:true});}
