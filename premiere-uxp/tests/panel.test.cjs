const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ROOT = path.resolve(__dirname, '..');
const MAIN = readFileSync(path.join(ROOT, 'main.js'), 'utf8');
const MANIFEST = JSON.parse(readFileSync(path.join(ROOT, 'manifest.json'), 'utf8'));
async function flush() { for (let i=0; i<5; i++) await new Promise(r=>setImmediate(r)); }
function setup({ hasProject=true, folder='/tmp/resync', exportOK=true } = {}) {
  const ids=['status','refresh','export-xml','project-name','sequence-name','video-count','audio-count'];
  const elements=new Map(ids.map(id=>[id,{id,textContent:'',className:'',disabled:false,listeners:{},addEventListener(event,fn){this.listeners[event]=fn}}]));
  const calls=[];
  const sequence={name:'Entrevista 01',async getVideoTrackCount(){return 3},async getAudioTrackCount(){return 5}};
  const project={name:'Teste.prproj',async getActiveSequence(){return sequence}};
  let current=hasProject?project:null,entries;
  const premiere={Project:{async getActiveProject(){return current}},ProjectConverter:{async exportAsFinalCutProXML(seq,output,suppressUI){calls.push({seq,output,suppressUI});return exportOK}}};
  const uxp={entrypoints:{setup(obj){entries=obj}},storage:{localFileSystem:{async getFolder(){return folder?{nativePath:folder}:null}}}};
  vm.runInNewContext(MAIN,{
    require(id){if(id==='premierepro')return premiere;if(id==='uxp')return uxp;throw Error('Unexpected module '+id)},
    document:{getElementById(id){assert.ok(elements.has(id));return elements.get(id)}},
    console,Date
  },{filename:'main.js'});
  return {elements,calls,getEntries(){return entries},setProject(value){current=value}};
}
test('UXP manifest targets Premiere 26.2+ with user-approved filesystem access',()=>{
  assert.equal(MANIFEST.manifestVersion,5);
  assert.equal(MANIFEST.host.app,'premierepro');
  assert.equal(MANIFEST.host.minVersion,'26.2.0');
  assert.equal(MANIFEST.requiredPermissions.localFileSystem,'request');
  assert.equal(MANIFEST.entrypoints[0].id,'resyncPanel');
});
test('Panel reads active project, sequence and track counts',async()=>{
  const app=setup();await flush();
  assert.equal(app.elements.get('sequence-name').textContent,'Entrevista 01');
  assert.equal(app.elements.get('video-count').textContent,'3');
  assert.equal(app.elements.get('audio-count').textContent,'5');
  assert.equal(app.elements.get('export-xml').disabled,false);
  assert.equal(app.calls.length,0);
  assert.equal(typeof app.getEntries().panels.resyncPanel.show,'function');
});
test('XML export uses Premiere ProjectConverter and selected folder',async()=>{
  const app=setup();await flush();
  await app.elements.get('export-xml').listeners.click();
  assert.equal(app.calls.length,1);
  assert.equal(app.calls[0].suppressUI,true);
  assert.match(app.calls[0].output,/^\/tmp\/resync\/Entrevista_01_ReSync_[0-9]{14}\.xml$/);
  assert.match(app.elements.get('status').textContent,/XML criado:/);
});
test('Canceling picker does not export files',async()=>{
  const app=setup({folder:null});await flush();
  await app.elements.get('export-xml').listeners.click();
  assert.equal(app.calls.length,0);
});
test('No project means export disabled',async()=>{
  const app=setup({hasProject:false});await flush();
  assert.equal(app.elements.get('export-xml').disabled,true);
});
