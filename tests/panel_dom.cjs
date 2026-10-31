// Real DOM contract test; jsdom is a development-only fixture, not shipped at runtime.
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {JSDOM} = require('../build/panel-dom/node_modules/jsdom');
const script = fs.readFileSync(path.join(__dirname,'../scripts/panel_overlay.js'),'utf8')
  .replace('/*DICTIONARY*/{}',JSON.stringify({'Apply':'应用','Select mode':'选择模式','Alpha':'甲','Tooltip':'提示','New text':'新文字','User':'用户','Code':'代码','Key':'键','English (中文)':'英文（中文）'}));
const dom = new JSDOM(`<!doctype html><body>
<button id="b" title="Tooltip">Apply</button><select id="s"><option value="stable">Alpha</option></select>
<input id="i" placeholder="Tooltip" value="User"><textarea id="ta" placeholder="Tooltip">User</textarea><div contenteditable="true"><span>User</span></div>
<div class="monaco-editor"><span>Code</span></div><pre><span>Code</span></pre>
<span id="key" data-i18n="Key">English (中文)</span><div id="dynamic"></div></body>`,{runScripts:'outside-only'});
const w=dom.window,d=w.document;
const original={setAttribute:w.Element.prototype.setAttribute,text:Object.getOwnPropertyDescriptor(w.Node.prototype,'textContent').set};
let clicks=0;d.getElementById('b').addEventListener('click',()=>clicks++);
w.eval(script);
const pause=()=>new Promise(resolve=>setTimeout(resolve,90));
(async()=>{
  await pause();
  assert.equal(d.getElementById('b').textContent,'应用');assert.equal(d.getElementById('b').title,'提示');
  d.getElementById('b').click();assert.equal(clicks,1);
  assert.equal(d.getElementById('s').value,'stable');assert.equal(d.getElementById('s').options[0].textContent,'甲');
  assert.equal(d.getElementById('i').value,'User');
  assert.equal(d.getElementById('i').placeholder,'提示');assert.equal(d.getElementById('ta').placeholder,'提示');assert.equal(d.getElementById('ta').value,'User');
  assert.equal(d.querySelector('[contenteditable] span').textContent,'User');
  assert.equal(d.querySelector('.monaco-editor span').textContent,'Code');assert.equal(d.querySelector('pre span').textContent,'Code');
  assert.equal(d.getElementById('key').getAttribute('data-i18n'),'Key');assert.equal(d.getElementById('key').textContent,'英文（中文）');
  assert.strictEqual(w.Element.prototype.setAttribute,original.setAttribute);
  assert.strictEqual(Object.getOwnPropertyDescriptor(w.Node.prototype,'textContent').set,original.text);
  const el=d.createElement('button');el.textContent='New text';d.getElementById('dynamic').appendChild(el);
  await pause();assert.equal(el.textContent,'新文字');
  el.textContent='Apply';el.setAttribute('title','Tooltip');await pause();assert.equal(el.textContent,'应用');assert.equal(el.title,'提示');
  dom.window.close();console.log('PASS: DOM text, attributes, dynamic updates, callbacks, values, ancestor exclusions, unchanged prototypes');
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});
