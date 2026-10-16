(function(){
    // saveFrameToPng writes asynchronously; wait for a complete file before changing the scene again.
    function savedFrame(comp,file){
        if(file.exists)file.remove();comp.saveFrameToPng(0,file);var last=-1;
        for(var waited=0;waited<30000;waited+=100){$.sleep(100);if(file.exists&&file.length>0&&file.length===last)return file.length;last=file.exists?file.length:-1;}
        throw new Error("Frame was not written: "+file.fsName);
    }
    var root=new Folder(@@OUTPUT_DIR@@);var report={aeVersion:app.version};
    try{
        var expected=new File(root.fsName+"/AE_rename_QA.aep");
        if(!app.project.file||app.project.file.fsName.toLowerCase()!==expected.fsName.toLowerCase())throw new Error("Only the owned QA project can be changed");
        var comp=app.project.activeItem;
        if(!(comp instanceof CompItem)||comp.name!=="AE\u540d\u79f0\u6c49\u5316_\u9a8c\u6536")throw new Error("Expected the isolated QA composition");
        var effect=comp.layer(1).property("ADBE Effect Parade").property(1);
        if(effect.matchName!=="S_Glow")throw new Error("Wrong effect identity");
        report.effectName=effect.name;report.matchName=effect.matchName;report.parameterCount=effect.numProperties;
        var brightness=effect.property("Brightness");var original=brightness.value;
        brightness.setValue(original*.75);report.parameterEditVerified=brightness.value===original*.75;brightness.setValue(original);
        report.frameBytes=savedFrame(comp,new File(root.fsName+"/@@FRAME@@.png"));
        app.project.save(expected);
        report.projectSaved=true;
    }catch(e){report.error=String(e);}
    var file=new File(root.fsName+"/@@FRAME@@.json");file.encoding="UTF-8";file.open("w");var pairs=[];
    for(var key in report)if(report.hasOwnProperty(key)){var value=report[key];pairs.push('"'+key+'":'+(typeof value==="string"?'"'+value.replace(/\\/g,"\\\\").replace(/"/g,'\\"')+'"':String(value)));}file.write('{'+pairs.join(',')+'}');file.close();
})();
