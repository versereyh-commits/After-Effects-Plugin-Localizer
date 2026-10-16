(function () {
    // Never touches plugin files or project effects. Only tests the documented catalog object.
    var outputPath = @@OUTPUT@@;
    var runMutation = @@MUTATE@@;
    var targetMatchName = "S_Glow";
    var desiredName = "\u8f89\u5149";
    function encode(v) {
        if (v === null || v === undefined) return "null";
        if (typeof v === "string") return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\r/g, "\\r").replace(/\n/g, "\\n").replace(/\t/g, "\\t").replace(/[\x00-\x1f]/g, function(c) { return "\\u" + ("0000" + c.charCodeAt(0).toString(16)).slice(-4); }) + '"';
        if (typeof v === "number" || typeof v === "boolean") return String(v);
        var out = [], i, k;
        if (v instanceof Array) { for (i = 0; i < v.length; i++) out.push(encode(v[i])); return "[" + out.join(",") + "]"; }
        for (k in v) if (v.hasOwnProperty(k)) out.push(encode(k) + ":" + encode(v[k]));
        return "{" + out.join(",") + "}";
    }
    function entries() {
        var list = app.effects, result = [], i;
        for (i = 0; i < list.length; i++) result.push({displayName:String(list[i].displayName), matchName:String(list[i].matchName), category:String(list[i].category), version:String(list[i].version)});
        return result;
    }
    function find(list) { var result = [], i; for(i=0;i<list.length;i++) if(String(list[i].matchName) === targetMatchName) result.push(list[i]); return result; }
    var report = {schemaVersion:1, aeVersion:String(app.version), targetMatchName:targetMatchName, requestedName:desiredName, mutationRequested:runMutation, hostChangeObserved:false, pluginFilesModified:false, projectModified:false};
    var originalObject = null, originalName = null;
    try {
        if (parseFloat(app.version) !== 26.0) throw new Error("This probe only supports AE 26.0.");
        report.catalog = entries();
        report.projectState = {itemCount:app.project.numItems, rendering:app.project.renderQueue.rendering};
        report.panelCommands = {};
        var commandNames = ["Refresh List", "\u5237\u65b0\u5217\u8868", "Effects & Presets", "\u6548\u679c\u548c\u9884\u8bbe"];
        for (var commandIndex=0;commandIndex<commandNames.length;commandIndex++) report.panelCommands[commandNames[commandIndex]]=app.findMenuCommandId(commandNames[commandIndex]);
        report.effectCount = report.catalog.length;
        report.before = find(report.catalog);
        if (report.before.length !== 1) throw new Error("Expected exactly one installed S_Glow effect; found " + report.before.length);
        if (runMutation) {
            var objects = find(app.effects);
            originalObject = objects[0];
            originalName = String(originalObject.displayName);
            try {
                originalObject.displayName = desiredName;
                report.returnedObjectAfterAssignment = String(originalObject.displayName);
            } catch (assignmentError) { report.assignmentError = String(assignmentError); }
            report.afterFreshQuery = find(entries());
            report.hostChangeObserved = report.afterFreshQuery.length === 1 && report.afterFreshQuery[0].displayName === desiredName;
            report.matchNamePreserved = report.afterFreshQuery.length === 1 && report.afterFreshQuery[0].matchName === targetMatchName;
        }
    } catch (err) { report.error = String(err); }
    finally {
        if (originalObject !== null) {
            try { originalObject.displayName = originalName; }
            catch (restoreError) { report.restoreError = String(restoreError); }
            try {
                report.afterRestore = find(entries());
                report.restoreVerified = report.afterRestore.length === 1 && report.afterRestore[0].displayName === originalName && report.afterRestore[0].matchName === targetMatchName;
            } catch (restoreQueryError) { report.restoreError = String(restoreQueryError); report.restoreVerified = false; }
        }
    }
    report.verdict = report.error ? "probe_error" : (!runMutation ? "catalog_read" : (report.hostChangeObserved ? "requires_ui_verification" : "catalog_assignment_does_not_update_host"));
    var payload = encode(report);
    var file = new File(outputPath); file.encoding = "UTF-8";
    if (!file.open("w")) { $.writeln("AE_LOCALIZER_REPORT " + payload); alert("AE Localizer probe cannot write its result: " + file.error); return; }
    file.write(payload); file.close();
})();
