(function(){
    var root=new Folder(@@OUTPUT_DIR@@);root.create();
    var report={aeVersion:app.version};
    var comp=null;
    try {
        comp=app.project.items.addComp("AE名称汉化_验收",320,180,1,1,24);
        var background=comp.layers.addSolid([0,0,0],"验收背景",320,180,1);
        var source=comp.layers.addSolid([1,1,1],"验收光源",36,36,1);
        source.property("ADBE Transform Group").property("ADBE Position").setValue([160,90]);
        report.canAddChineseName=source.property("ADBE Effect Parade").canAddProperty("\u8f89\u5149");
        if(@@ADD_EFFECT@@){
            var effect=source.property("ADBE Effect Parade").addProperty("S_Glow");
            report.effectName=effect.name;report.effectMatchName=effect.matchName;report.parameterCount=effect.numProperties;
            comp.saveFrameToPng(0,new File(root.fsName+"/@@FRAME@@.png"));
        }
        comp.openInViewer();source.selected=true;background.selected=false;
        report.compId=comp.id;report.sourceIndex=source.index;
    } catch(e){report.error=String(e);}
    var file=new File(root.fsName+"/@@FRAME@@.json");file.encoding="UTF-8";file.open("w");
    var pairs=[];for(var key in report)if(report.hasOwnProperty(key)){var value=report[key];pairs.push('"'+key+'":'+(typeof value==="string"?'"'+value.replace(/\\/g,"\\\\").replace(/"/g,'\\"').replace(/\r/g,"\\r").replace(/\n/g,"\\n")+'"':String(value)));}file.write('{'+pairs.join(',')+'}');file.close();
})();
