(function(){
    // saveFrameToPng writes asynchronously; wait for a complete file before changing the scene again.
    function savedFrame(comp,file){
        if(file.exists)file.remove();comp.saveFrameToPng(0,file);var last=-1;
        for(var waited=0;waited<30000;waited+=100){$.sleep(100);if(file.exists&&file.length>0&&file.length===last)return file.length;last=file.exists?file.length:-1;}
        throw new Error("Frame was not written: "+file.fsName);
    }
    var root=new Folder(@@OUTPUT_DIR@@);var report={};
    try{
        var expected=new File(root.fsName+"/AE_rename_QA.aep");
        if(!app.project.file||app.project.file.fsName.toLowerCase()!==expected.fsName.toLowerCase())throw new Error("Only the owned QA project can be changed");
        var comp=null;for(var i=1;i<=app.project.numItems;i++)if(app.project.item(i) instanceof CompItem&&app.project.item(i).name==="AE\u540d\u79f0\u6c49\u5316_\u9a8c\u6536")comp=app.project.item(i);
        if(!comp)throw new Error("QA composition not found");
        var adjustment;
        if(comp.numLayers===2){
            var oldEffect=comp.layer(1).property("ADBE Effect Parade").property(1);
            if(oldEffect&&oldEffect.matchName==="S_Glow")oldEffect.remove();
            adjustment=comp.layers.addSolid([1,1,1],"QA Glow Adjustment",320,180,1);adjustment.adjustmentLayer=true;
            adjustment.property("ADBE Effect Parade").addProperty("S_Glow");
        }else if(comp.numLayers===3&&comp.layer(1).name==="QA Glow Adjustment")adjustment=comp.layer(1);
        else throw new Error("Unexpected QA layer structure");
        var effect=adjustment.property("ADBE Effect Parade").property(1);
        if(effect.matchName!=="S_Glow")throw new Error("Unexpected effect identity");
        effect.property("Brightness").setValue(1.5);effect.enabled=true;
        report.baselineBytes=savedFrame(comp,new File(root.fsName+"/baseline-glow.png"));
        try{effect.enabled=false;report.disabledBytes=savedFrame(comp,new File(root.fsName+"/disabled-glow.png"));}finally{effect.enabled=true;}
        comp.openInViewer();adjustment.selected=true;app.project.save(expected);
        report.effectName=effect.name;report.matchName=effect.matchName;report.parameterCount=effect.numProperties;report.prepared=true;
    }catch(e){report.error=String(e);}
    var file=new File(root.fsName+"/fixture.json");file.encoding="UTF-8";file.open("w");var pairs=[];
    for(var key in report)if(report.hasOwnProperty(key)){var value=report[key];pairs.push('"'+key+'":'+(typeof value==="string"?'"'+value.replace(/\\/g,"\\\\").replace(/"/g,'\\"')+'"':String(value)));}file.write('{'+pairs.join(',')+'}');file.close();
})();
