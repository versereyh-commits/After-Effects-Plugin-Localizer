(function(){var q=@@REQUEST@@,r={requestId:q.requestId,action:q.action,startedAt:new Date().getTime()},building=false;
function quote(s){return '"'+String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/[\x00-\x1f]/g,function(c){return '\\u'+('0000'+c.charCodeAt(0).toString(16)).slice(-4);})+'"';}
function json(v){if(v===null||typeof v==='undefined')return 'null';if(typeof v==='string')return quote(v);if(typeof v==='number'||typeof v==='boolean')return String(v);var a=[],k;if(v instanceof Array){for(k=0;k<v.length;k++)a.push(json(v[k]));return '['+a.join(',')+']';}for(k in v)if(v.hasOwnProperty(k))a.push(quote(k)+':'+json(v[k]));return '{'+a.join(',')+'}';}
function same(path){return app.project.file&&app.project.file.fsName.toLowerCase()===new File(path).fsName.toLowerCase();}
function permitted(){return new File(q.permit).exists&&new Date().getTime()<q.expires;}
function requirePermit(){if(!permitted())throw new Error('QA 请求已取消或过期，停止后续操作');}
function phase(text){r.phase=text;if(q.progress){var pf=new File(q.progress);pf.encoding='UTF-8';if(pf.open('w')){pf.write(json({requestId:q.requestId,phase:text,time:new Date().getTime()}));pf.close();}}}
function owned(){return same(q.project)||same(q.second);}
function comp(){for(var i=1;i<=app.project.numItems;i++){var c=app.project.item(i);if(c instanceof CompItem&&c.name===q.comp&&c.numLayers===3&&c.layer(1).name==='QA Effects')return c;}throw new Error('QA fixture identity changed');}
try{
 requirePermit();phase('已进入 AE 脚本');
 if(q.hostVersion&&String(app.version)!==q.hostVersion)throw new Error('QA host version mismatch');
 if(app.project.renderQueue.rendering)throw new Error('Stop rendering before validation');
 r.hostVersion=String(app.version);r.project=app.project.file?app.project.file.fsName:null;r.items=app.project.numItems;
 if(q.action==='status'){
  if(q.diagnoseCatalog){var start=new Date().getTime(),sample=app.effects;r.catalogCount=sample.length;r.singleCatalogReadMs=new Date().getTime()-start;start=new Date().getTime();for(var di=0;di<8;di++){requirePermit();sample=app.effects;}r.eightCatalogReadsMs=new Date().getTime()-start;}
 }
 else if(q.action==='catalog'){
  phase('读取效果目录');r.effects=[];var available=app.effects;for(var ai=0;ai<available.length;ai++){if(ai%64===0)requirePermit();r.effects.push({displayName:String(available[ai].displayName),matchName:String(available[ai].matchName),category:String(available[ai].category)});}
 }else if(q.action==='prepare'){
  if(app.project.file||app.project.numItems)throw new Error('请先自行保存并关闭当前工程；验证仅从空工程开始');
  if(new File(q.project).exists||new File(q.second).exists)throw new Error('QA files already exist');
  phase('读取一次效果目录，选择测试效果');
  var ids={},available=app.effects;for(var ai=0;ai<available.length;ai++){if(ai%64===0)requirePermit();ids[available[ai].matchName]=true;}
  // AE's own Gaussian Blur first: every AE has it and it does not depend on a third-party plugin's GPU renderer.
  var test=ids['ADBE Gaussian Blur 2']?'ADBE Gaussian Blur 2':ids.S_Glow?'S_Glow':null;
  if(!test)throw new Error('No supported QA fixture effect is installed');
  requirePermit();phase('创建自有测试合成');building=true;
  // The QA project renders on the CPU: graphics drivers that crash AE (seen with AE 25.0 on an AMD card) must not end a self-check.
  r.softwareRender=false;try{app.project.gpuAccelType=GpuAccelType.SOFTWARE;r.softwareRender=app.project.gpuAccelType===GpuAccelType.SOFTWARE;}catch(gpuError){}
  var c=app.project.items.addComp(q.comp,320,180,1,1,24);
  c.layers.addSolid([0,0,0],'QA Background',320,180,1);c.layers.addSolid([1,1,1],'QA Source',50,30,1);
  var layer=c.layers.addSolid([1,1,1],'QA Effects',320,180,1);layer.adjustmentLayer=true;
  requirePermit();phase('添加测试效果');var effects=layer.property('ADBE Effect Parade'),fx=effects.addProperty(test);
  if(test==='S_Glow')fx.property('Brightness').setValue(1.5);else fx.property(1).setValue(25);
  var parameterEffect=ids['CC Drizzle']?'CC Drizzle':test;
  requirePermit();if(parameterEffect==='CC Drizzle')effects.addProperty(parameterEffect).enabled=false;
  requirePermit();phase('保存自有 QA 工程');c.openInViewer();layer.selected=true;app.project.save(new File(q.project));
  r.fixture={testEffect:test,parameterEffect:parameterEffect,parameterMatch:parameterEffect==='CC Drizzle'?'CC Drizzle-0001':effects.property(1).property(1).matchName};r.prepared=true;
 }else{
  requirePermit();if(!owned())throw new Error('验证只能操作软件自有 QA 工程');phase('已核对自有 QA 工程');
  var c=comp(),effects=c.layer(1).property('ADBE Effect Parade');
  if(q.action==='snapshot'){
   r.rows=[];for(var e=1;e<=effects.numProperties;e++){var fx=effects.property(e);for(var i=1;i<=fx.numProperties;i++){
    var p=fx.property(i),row={effect:fx.matchName,matchName:p.matchName,name:p.name,index:i};
    try{row.keys=p.numKeys;row.expression=p.expression;}catch(ignore){}
    try{var v=p.value;if(typeof v==='number'||typeof v==='string'||typeof v==='boolean'||v instanceof Array)row.value=v;}catch(ignoreValue){}
    r.rows.push(row);
   }}
  }else if(q.action==='render'){
   var fx=effects.property(1),enabled=fx.enabled;
   try{
    requirePermit();phase('渲染测试帧');
    if(q.disabled)fx.enabled=false;
    var frame=new File(q.frame);if(frame.exists)throw new Error('QA render target already exists');
    c.saveFrameToPng(0,frame);var last=-1,stable=0;
    for(var waited=0;waited<40000;waited+=100){$.sleep(100);if(!permitted())throw new Error('Render permit expired');if(frame.exists&&frame.length>0&&frame.length===last)stable++;else stable=0;if(stable>=3){r.bytes=frame.length;break;}last=frame.exists?frame.length:-1;}
    if(!r.bytes)throw new Error('Frame was not written');
   }finally{fx.enabled=enabled;}
  }else if(q.action==='edit'){
   var fx=null;for(var e=1;e<=effects.numProperties;e++)if(effects.property(e).matchName===q.parameterEffect){fx=effects.property(e);break;}
   if(!fx)throw new Error('QA parameter effect missing');requirePermit();phase('调节并还原测试参数');var p=fx.property(q.parameterMatch);
   r.before=p.value;r.nameBefore=p.name;r.matchName=p.matchName;
   try{p.setValue(r.before+1);r.changed=fx.property(q.parameterMatch).value;r.nameAfter=fx.property(q.parameterMatch).name;}
   finally{fx.property(q.parameterMatch).setValue(r.before);}
   r.restored=fx.property(q.parameterMatch).value;
  }else if(q.action==='switch'){
   if(q.target!==q.project&&q.target!==q.second)throw new Error('QA switch target not owned');
   requirePermit();phase('切换软件 QA 工程');app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);app.open(new File(q.target));r.opened=same(q.target);
  }else if(q.action==='show'){
   c.openInViewer();c.layer(1).selected=true;r.shown=true;
  }else if(q.action==='close'){
   requirePermit();app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);r.closed=true;
  }else throw new Error('Unknown QA action');
 }
}catch(e){r.error=String(e);
 if(building&&!r.prepared&&!app.project.file){try{if(app.project.numItems===5&&comp())app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);}catch(cleanup){r.cleanupPending=true;}}
}
r.finishedAt=new Date().getTime();
var f=new File(q.output);f.encoding='UTF-8';if(f.open('w')){f.write(json(r));f.close();}
})();
