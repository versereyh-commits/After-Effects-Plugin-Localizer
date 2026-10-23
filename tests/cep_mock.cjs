// Runs cep/main.js against mock CEP APIs (tests/test_cep_bridge.py): catches script errors and checks the heartbeat,
// the claim and the script it hands to evalScript.
const fs = require('fs'), path = require('path'), os = require('os');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cep-'));
const ext = path.join(tmp, 'ext'); fs.mkdirSync(ext);
fs.writeFileSync(path.join(ext, 'parameter_bridge.jsx'), 'var request=@@REQUEST@@;');
const userData = path.join(tmp, 'Roaming');
fs.mkdirSync(userData);
// CEP hands paths over as URL-encoded file URLs (CSInterface decodes them the same way main.js does).
const url = p => 'file:///' + encodeURI(p.replace(/\\/g, '/'));
const calls = [];const identity={pid:3,creationTime:'0000000000000004',image:'C:/AE/AfterFX.exe'};
global.window = {
  __adobe_cep__: {
    getSystemPath: k => ({ userData: url(userData), extension: url(ext) })[k],
    getHostEnvironment: () => JSON.stringify({ appVersion: '24.2.1' }),
    evalScript: (s, cb) => { calls.push(s); cb(s.includes('lib.identity()')?JSON.stringify(identity):'undefined'); },
  },
  cep: { fs: {
    readFile: p => { try { return { err: 0, data: fs.readFileSync(p, 'utf8') }; } catch (e) { return { err: 3 }; } },
    writeFile: (p, d) => { try { fs.writeFileSync(p, d); return { err: 0 }; } catch (e) { return { err: 3 }; } },
    makedir: p => { try { fs.mkdirSync(p); return { err: 0 }; } catch (e) { return { err: 3 }; } },
    readdir: p => { try { return { err: 0, data: fs.readdirSync(p) }; } catch (e) { return { err: 3 }; } },
    rename: (a, b) => { try { fs.renameSync(a, b); return { err: 0 }; } catch (e) { return { err: 3 }; } },
    deleteFile: p => {try{fs.unlinkSync(p);return {err:0};}catch(e){return {err:3};}},
  } },
};
global.setInterval = (f, ms) => { global.__ticks = global.__ticks || []; global.__ticks.push(f); };
const source = fs.readFileSync(process.argv[2], 'utf8').replace('@@BRIDGE_ID@@', 'test');
eval(source);
const root = path.join(userData, 'AE-Localizer', 'bridge','hosts','3-0000000000000004');
const beat = fs.existsSync(path.join(root, 'heartbeat.0.json')) ? JSON.parse(fs.readFileSync(path.join(root, 'heartbeat.0.json'), 'utf8')) : null;
const id = 'a'.repeat(32), folder = path.join(root, id); fs.mkdirSync(folder);
const p = f => path.join(folder, f).replace(/\\/g, '/');
fs.writeFileSync(path.join(folder, 'request.json'), JSON.stringify({ action: 'codec',targetHost:identity,expires:Date.now()+100000, output: p('result.json'), permit: p('permit') }));
global.__ticks.forEach(f => f());
console.log(JSON.stringify({ heartbeat: beat, claimed: fs.existsSync(path.join(folder, 'claimed.json')), scripts: calls }));
fs.rmSync(tmp, { recursive: true, force: true });
