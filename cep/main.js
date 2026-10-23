/* AE汉化工具 background bridge.

   The program writes <bridge>/<request id>/request.json (data only). This extension claims it by renaming it to
   claimed.json, fills its own signed copy of parameter_bridge.jsx with that data and runs it with evalScript:
   no second AfterFX.exe, so AE's window keeps its state and focus. Arbitrary script is never run; the request
   must be JSON naming one of the known actions, with its permit and output inside its own request folder.
   A heartbeat tells the program the bridge is alive and which build of it is loaded. */
(function () {
    'use strict';
    var BRIDGE_ID = '@@BRIDGE_ID@@';
    var ACTIONS = ['catalog', 'selection', 'labels', 'codec', 'instanceCatalog', 'instanceNames', 'addEffect'];
    var host = window.__adobe_cep__, fs = window.cep && window.cep.fs;
    if (!host || !fs) return;

    function systemPath(kind) {
        var path = decodeURI(host.getSystemPath(kind));
        return path.replace(/^file:\/\/\//, '').replace(/^file:\/\//, '').replace(/\\/g,'/');
    }
    var base = systemPath('userData') + '/AE-Localizer/bridge', root = '', identity = null;
    var extension = systemPath('extension');
    var template = fs.readFile(systemPath('extension') + '/parameter_bridge.jsx');
    if (template.err !== 0 || template.data.indexOf('@@REQUEST@@') < 0) return;
    template = template.data;
    var version = '';
    try { version = JSON.parse(host.getHostEnvironment()).appVersion || ''; } catch (e) {}
    var busy = false, pulse = 0;

    function makeRoot() {
        var parent = base.slice(0, base.lastIndexOf('/'));
        fs.makedir(parent); fs.makedir(base);fs.makedir(base+'/hosts');if(root)fs.makedir(root);
    }
    function beat() {
        makeRoot();
        if(!identity)return;
        // CEP rename does not replace an existing destination on Windows. Alternate two slots so readers
        // always have the preceding complete heartbeat while the other is replaced.
        var target=root+'/heartbeat.'+(pulse++%2)+'.json',temp=target+'.tmp';
        if(fs.writeFile(temp,JSON.stringify({time:Date.now(),bridge:BRIDGE_ID,host:version,identity:identity})).err===0){
            fs.deleteFile(target);fs.rename(temp,target);
        }
    }
    function inside(path, folder) {
        return typeof path === 'string' && path.replace(/\\/g, '/').toLowerCase().indexOf(folder.toLowerCase() + '/') === 0;
    }
    function fail(folder, message) {
        fs.writeFile(folder + '/bridge-error.json', JSON.stringify({ error: message }));
    }
    function run(folder) {
        var preview=fs.readFile(folder+'/request.json'), checked;
        try{checked=JSON.parse(preview.data);}catch(e){return false;}
        if(!sameHost(checked.targetHost,identity)||checked.expires<=Date.now())return false;
        var claimed = folder + '/claimed.json';
        if (fs.rename(folder + '/request.json', claimed).err !== 0) return false;   // the program took it back
        var text = fs.readFile(claimed), request;
        try { request = JSON.parse(text.data); } catch (e) { fail(folder, 'request'); return true; }
        if (!request || !sameHost(request.targetHost,identity) || ACTIONS.indexOf(request.action) < 0 || !inside(request.output, folder) || !inside(request.permit, folder)) {
            fail(folder, 'request'); return true;
        }
        // JSON is a valid ExtendScript literal once the two JavaScript line separators are escaped.
        var literal = JSON.stringify(request).split(String.fromCharCode(0x2028)).join('\\u2028').split(String.fromCharCode(0x2029)).join('\\u2029');
        busy = true;
        host.evalScript(template.replace('@@REQUEST@@', function () { return literal; }), function (result) {
            busy = false;
            // AE refuses scripts while one of its dialogs waits for an answer; say so instead of timing out.
            if (result === 'EvalScript error.') fail(folder, 'evalScript');
        });
        return true;
    }
    function poll() {
        if (busy) return;
        var list = fs.readdir(root);
        if (list.err !== 0) return;
        for (var i = 0; i < list.data.length; i++) {
            var name = list.data[i];
            if (!/^[0-9a-f]{32}$/.test(name)) continue;
            if (run(root + '/' + name)) return;          // one request at a time
        }
    }
    function sameHost(a,b){return a&&b&&a.pid===b.pid&&a.creationTime===b.creationTime&&String(a.image).replace(/\\/g,'/').toLowerCase()===String(b.image).replace(/\\/g,'/').toLowerCase();}
    // The only startup script is a read-only helper shipped inside this signed extension.
    var helper=JSON.stringify(extension+'/ae_host_identity_v1.dll');
    host.evalScript('(function(){try{var lib=new ExternalObject("lib:"+'+helper+');return lib.identity();}catch(e){return "";}})()',function(text){
        try{identity=JSON.parse(text);}catch(e){return;}
        if(!identity||!(identity.pid>0)||!/^[0-9a-f]{16}$/.test(identity.creationTime)||typeof identity.image!=='string')return;
        root=base+'/hosts/'+identity.pid+'-'+identity.creationTime;
        beat();setInterval(beat,1000);setInterval(poll,100);
    });
})();
