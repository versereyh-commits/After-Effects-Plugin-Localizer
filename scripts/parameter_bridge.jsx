(function(){
    var request=@@REQUEST@@,result={changed:0},lib=null,done=[],instanceDone=[];
    // Tells the program AE entered this script and how far it got, so a timeout can name the step it stopped at.
    function stage(name){try{var mark=new File(new File(request.output).parent.fsName+'/started');if(mark.open('w')){mark.write(name);mark.close();}}catch(markError){}}
    stage('start');
    function permitted(){return new File(request.permit).exists&&new Date().getTime()<request.expires;}
    function quoted(s){return '"'+String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/\r/g,'\\r').replace(/\n/g,'\\n').replace(/[\x00-\x1f]/g,function(c){return '\\u'+('0000'+c.charCodeAt(0).toString(16)).slice(-4);})+'"';}
    function projectToken(){var source=app.project.file?app.project.file.fsName:'unsaved',hash=2166136261;for(var i=1;i<=app.project.numItems;i++)source+='|'+app.project.item(i).id;for(var j=0;j<source.length;j++){hash^=source.charCodeAt(j);hash+=(hash<<1)+(hash<<4)+(hash<<7)+(hash<<8)+(hash<<24);}return (hash>>>0).toString(16);}
    function selectionState(){
        var c=app.project.activeItem,rows=[];
        if(!(c instanceof CompItem))return {compId:null,layers:[]};
        var layers=c.selectedLayers;
        for(var i=0;i<layers.length;i++){var l=layers[i];rows.push({index:l.index,id:typeof l.id==='number'?l.id:null,name:String(l.name)});}
        rows.sort(function(a,b){return a.index-b.index;});return {compId:c.id,layers:rows};
    }
    function selectionJson(s){var rows=[];for(var i=0;i<s.layers.length;i++){var l=s.layers[i];rows.push('{"index":'+l.index+',"id":'+l.id+',"name":'+quoted(l.name)+'}');}return '{"compId":'+s.compId+',"layers":['+rows.join(',')+']}';}
    function collectParameters(layer){
        if(!permitted())throw new Error('请求已取消');
        var effects=layer.property('ADBE Effect Parade');if(!effects)return;
        for(var effectIndex=1;effectIndex<=effects.numProperties;effectIndex++){
            if(!permitted())throw new Error('请求已取消');
            var effect=effects.property(effectIndex);
            for(var paramIndex=1;paramIndex<=effect.numProperties;paramIndex++){
                var param=effect.property(paramIndex);
                if(param.name)result.parameters.push({id:'['+quoted(effect.matchName)+','+quoted(param.matchName)+']',name:String(param.name)});
            }
        }
    }
    try{
        if(!permitted())throw new Error('请求已过期或取消，未执行');
        if(String(app.version)!==request.hostVersion)throw new Error('AE 构建不匹配');
        if(request.qaProjects){var owned=false;for(var qi=0;qi<request.qaProjects.length;qi++)if(app.project.file&&app.project.file.fsName.toLowerCase()===new File(request.qaProjects[qi]).fsName.toLowerCase())owned=true;if(!owned)throw new Error('验证只能操作软件自有QA工程');}
        if(app.project.renderQueue.rendering)throw new Error('请停止渲染后再修改参数名称');
        if(request.action==='instanceCatalog'||request.action==='instanceNames'){
            stage('instances');result.projectToken=projectToken();result.instances=[];
            for(var ci=1;ci<=app.project.numItems;ci++){
                if(!permitted())throw new Error('请求已取消');
                var c=app.project.item(ci);if(!(c instanceof CompItem))continue;
                for(var li=1;li<=c.numLayers;li++){
                    var layer=c.layer(li),parade=layer.property('ADBE Effect Parade');if(!parade)continue;
                    for(var ei=1;ei<=parade.numProperties;ei++){
                        var fx=parade.property(ei),lid=typeof layer.id==='number'?layer.id:null;
                        result.instances.push({compId:c.id,layerIndex:li,layerId:lid,layerName:String(layer.name),effectIndex:ei,matchName:String(fx.matchName),name:String(fx.name)});
                    }
                }
            }
            if(request.action==='instanceNames'){
                if(!request.projectToken||request.projectToken!==result.projectToken)throw new Error('工程已切换，请重新应用效果名称');
                var targets=[];
                for(var oi=0;oi<request.operations.length;oi++){
                    var op=request.operations[oi],target=null,hostLayer=null;
                    for(var ic=1;ic<=app.project.numItems;ic++)if(app.project.item(ic).id===op.compId){
                        var hostComp=app.project.item(ic);
                        for(var il=1;il<=hostComp.numLayers;il++){
                            var candidate=hostComp.layer(il);
                            if(op.layerId!==null?candidate.id===op.layerId:(il===op.layerIndex&&String(candidate.name)===op.layerName)){hostLayer=candidate;break;}
                        }
                        break;
                    }
                    if(hostLayer){var hostParade=hostLayer.property('ADBE Effect Parade');if(hostParade&&op.effectIndex<=hostParade.numProperties)target=hostParade.property(op.effectIndex);}
                    if(!target||String(target.matchName)!==op.matchName||String(target.name)!==op.before)throw new Error('效果实例已移动或被改名，未覆盖');
                    targets.push(target);
                }
                try{
                    if(targets.length)app.beginUndoGroup('AE 效果标题汉化');
                    for(var ti=0;ti<targets.length;ti++){
                        if(!permitted()||projectToken()!==request.projectToken)throw new Error('请求已取消或工程已切换');
                        var change=request.operations[ti];
                        if(String(targets[ti].name)!==change.before||String(targets[ti].matchName)!==change.matchName)throw new Error('效果标题在写入前发生变化');
                        instanceDone.push({target:targets[ti],before:change.before,after:change.after});targets[ti].name=change.after;
                        if(String(targets[ti].name)!==change.after)throw new Error('效果标题写入回读不一致');
                        result.changed++;
                    }
                }finally{if(targets.length)app.endUndoGroup();}
            }
        }else if(request.action==='catalog'||request.action==='selection'){
            stage('collect');result.projectToken=projectToken();
            result.readScope=request.readScope||'project';
            if(result.readScope!=='selected'&&result.readScope!=='project')throw new Error('未知参数读取范围');
            result.parameters=[];
            if(result.readScope==='selected'){
                result.selectionSnapshot=selectionState();
                if(request.action==='catalog'&&result.selectionSnapshot.compId!==null){
                    var selectedComp=app.project.activeItem;
                    for(var si=0;si<result.selectionSnapshot.layers.length;si++)collectParameters(selectedComp.layer(result.selectionSnapshot.layers[si].index));
                }
                if(projectToken()!==result.projectToken||selectionJson(selectionState())!==selectionJson(result.selectionSnapshot))throw new Error('读取期间工程或选中图层发生变化，请重新读取参数');
            }else if(request.action==='catalog'){
                for(var itemIndex=1;itemIndex<=app.project.numItems;itemIndex++){
                    var comp=app.project.item(itemIndex);if(!(comp instanceof CompItem))continue;
                    for(var layerIndex=1;layerIndex<=comp.numLayers;layerIndex++)collectParameters(comp.layer(layerIndex));
                }
            }
        }else{
          if(request.action!=='codec'&&(!request.projectToken||request.projectToken!==projectToken()))throw new Error('工程已切换，请重新读取参数后再应用');
          if(request.action==='codec'&&request.operations.length)throw new Error('编码查询不能修改参数');
          stage('loadHelper');lib=new ExternalObject('lib:'+request.helper);
          stage('configure');
          if(request.adapter){var a=request.adapter;result.codec=lib.setLabel('configure',a.flt,a.tdb,a.layout,a.codec);if(request.action!=='codec'&&result.codec!==(a.encoding==='utf8'?1:2))throw new Error('编码核验不匹配');}
          if(request.operations.length)stage('labels');
          for(var i=0;i<request.operations.length;i++){
            if(!permitted())throw new Error('请求已取消');
            var op=request.operations[i];
            if(typeof op.afterPf==='string'&&typeof op.beforeDisplay==='string')lib.setLabel(op.address,op.guid,op.parent,op.before,op.after,op.afterPf,op.beforeDisplay);
            else lib.setLabel(op.address,op.guid,op.parent,op.before,op.after);
            done.push(op);result.changed++;
          }
        }
    }catch(e){
        result.error=String(e);result.rollbackFailed=0;
        for(var ij=instanceDone.length-1;ij>=0;ij--){var prior=instanceDone[ij];try{var current=String(prior.target.name);if(current!==prior.after&&current!==prior.before)throw new Error('实例回滚守卫不符');prior.target.name=prior.before;if(String(prior.target.name)!==prior.before)throw new Error('实例回滚回读不符');}catch(instanceRollback){result.rollbackFailed++;}}
        for(var j=done.length-1;j>=0;j--){var prev=done[j];try{if(typeof prev.afterPf==='string'&&typeof prev.beforeDisplay==='string')lib.setLabel(prev.address,prev.guid,prev.parent,prev.afterPf,prev.beforeDisplay,prev.before,prev.after);else lib.setLabel(prev.address,prev.guid,prev.parent,prev.after,prev.before);}catch(rollback){result.rollbackFailed++;}}
    }
    stage('result');
    var file=new File(request.output);file.encoding='UTF-8';
    if(file.open('w')){
        var entries=[];if(result.parameters)for(var r=0;r<result.parameters.length;r++)entries.push('{"id":'+quoted(result.parameters[r].id)+',"name":'+quoted(result.parameters[r].name)+'}');
        var instanceEntries=[];if(result.instances)for(var ri=0;ri<result.instances.length;ri++){var row=result.instances[ri];instanceEntries.push('{"compId":'+row.compId+',"layerIndex":'+row.layerIndex+',"layerId":'+row.layerId+',"layerName":'+quoted(row.layerName)+',"effectIndex":'+row.effectIndex+',"matchName":'+quoted(row.matchName)+',"name":'+quoted(row.name)+'}');}
        file.write('{"changed":'+result.changed+',"requestId":'+quoted(request.requestId||'')+',"action":'+quoted(request.action)+(result.codec?',"codec":'+result.codec:'')+(result.projectToken?',"projectToken":'+quoted(result.projectToken):'')+(result.readScope?',"readScope":'+quoted(result.readScope):'')+(result.selectionSnapshot?',"selectionSnapshot":'+selectionJson(result.selectionSnapshot):'')+(result.instances?',"instances":['+instanceEntries.join(',')+']':'')+(result.parameters?',"parameters":['+entries.join(',')+']':'')+(result.error?',"error":'+quoted(result.error)+',"rollbackFailed":'+result.rollbackFailed:'')+'}');file.close();
    }
})();
