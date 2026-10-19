const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const MAIN = readFileSync(path.join(ROOT, 'main.js'), 'utf8');
const MANIFEST = JSON.parse(readFileSync(path.join(ROOT, 'manifest.json'), 'utf8'));
async function flush() { for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r)); }

function setup({hasProject=true,folder='/tmp/resync',exportOK=true,importOK=true,jobError=false,
  neverStarts=false,launchHangs=false}={}) {
  let simulatedClock = Date.now();
  class MockDate extends Date { static now() { return simulatedClock; } }
  const ids=['status','refresh','export-xml','sync','change-workspace','stop-wait','progress-track','progress-fill',
    'project-name','sequence-name','video-count','audio-count'];
  const elements=new Map(ids.map(id=>[id,{
    id,textContent:'',className:'',disabled:false,hidden:true,style:{width:'0%'},
    listeners:{},addEventListener(event,fn){this.listeners[event]=fn}
  }]));
  const calls=[], jobs=new Map(), appSource={name:'Entrevista 01',guid:'original',
    async getVideoTrackCount(){return 3},async getAudioTrackCount(){return 5}};
  let currentSequence=appSource, imported=[];
  const project={
    path:'/tmp/original.prproj',name:'Teste.prproj',
    async getActiveSequence(){return currentSequence},
    async getSequences(){return [appSource,...imported]},
    async importFiles(paths,suppress,bin,stills){
      calls.push({action:'import',paths,suppress,bin,stills});
      if(!importOK)return false;
      const id=/resync_([0-9a-f]{24})\.synced\.xml/.exec(paths[0])[1];
      imported.push({guid:'new-'+id,name:'Entrevista 01 — ReSync '+id.slice(0,6)});
      return true;
    },
    async openSequence(seq){calls.push({action:'open',seq});return true},
    async setActiveSequence(seq){currentSequence=seq;return true}
  };
  let current=hasProject?project:null, entries;
  const premiere={
    Project:{async getActiveProject(){return current}},
    ProjectConverter:{async exportAsFinalCutProXML(seq,output,suppressUI){
      calls.push({action:'export',seq,output,suppressUI});return exportOK
    }}
  };
  const folderObj={
    isFolder:true,nativePath:folder,
    async createFile(name) { return {
      nativePath:folder+'/'+name,async write(value){ jobs.set(name,JSON.parse(value)); }
    }},
    async getEntry(name){
      if(name.endsWith('.status.json')){
        if (neverStarts) throw Error('Worker not registered');
        const id=/resync_([0-9a-f]{24})\.status\.json/.exec(name)[1];
        const job=jobs.get('resync_'+id+'.job.json');
        if(!job)throw Error('Job not ready');
        const state=jobError?'error':'done';
        return {async read(){return JSON.stringify({
          job_id:id,state,percent:state==='done'?100:12,
          message:jobError?'Falha simulada':'Concluído',
          sequence_name:'Entrevista 01 — ReSync '+id.slice(0,6),
          output:'resync_'+id+'.synced.xml'
        })}};
      }
      throw Error('Missing '+name);
    }
  };
  const localValues=new Map(), localStorage={
    getItem(k){return localValues.get(k)||null},setItem(k,v){localValues.set(k,v)},
    removeItem(k){localValues.delete(k)}
  };
  const uxp={
    entrypoints:{setup(obj){entries=obj}},
    storage:{localFileSystem:{
      async getFolder(){return folder?folderObj:null},
      async createPersistentToken(){return 'token'},
      async getEntryForPersistentToken(){return folderObj}
    }},
    shell:{async openExternal(url,reason){
      calls.push({action:'launch',url,reason});
      if(launchHangs) return new Promise(()=>{});
      return '';
    }}
  };
  vm.runInNewContext(MAIN,{
    require(id){if(id==='premierepro')return premiere;if(id==='uxp')return uxp;
      throw Error('Unexpected module '+id)},
    document:{getElementById(id){assert.ok(elements.has(id),'Missing element '+id);return elements.get(id)}},
    localStorage,console,Date:MockDate,Math,
    setTimeout(fn,ms){
      if(ms > 2000) return global.setTimeout(fn, ms);
      simulatedClock += ms;
      return setImmediate(fn);
    },
    clearTimeout(handle){global.clearTimeout(handle);clearImmediate(handle);}
  },{filename:'main.js'});
  return {elements,calls,project,getEntries(){return entries},setProject(x){current=x},
          getImported(){return imported},getSource(){return appSource},getActive(){return currentSequence}};
}

test('Manifest declares Premiere UXP and explicit local bridge launch permission',()=>{
  assert.equal(MANIFEST.manifestVersion,5);
  assert.equal(MANIFEST.host.app,'premierepro');
  assert.equal(MANIFEST.host.minVersion,'26.2.0');
  assert.equal(MANIFEST.requiredPermissions.localFileSystem,'request');
  assert.deepEqual(MANIFEST.requiredPermissions.launchProcess.schemes,['resync']);
  assert.equal(MANIFEST.entrypoints[0].id,'resyncPanel');
});
test('Reads active project, sequence and track counts without mutation',async()=>{
  const app=setup();await flush();
  assert.equal(app.elements.get('sequence-name').textContent,'Entrevista 01');
  assert.equal(app.elements.get('video-count').textContent,'3');
  assert.equal(app.elements.get('audio-count').textContent,'5');
  assert.equal(app.elements.get('sync').disabled,false);
  assert.equal(app.calls.length,0);
  assert.equal(typeof app.getEntries().panels.resyncPanel.show,'function');
});
test('Manual XML export remains available',async()=>{
  const app=setup();await flush();
  await app.elements.get('export-xml').listeners.click();await flush();
  const c=app.calls.find(x=>x.action==='export');
  assert.equal(c.suppressUI,true);
  assert.match(c.output,/^\/tmp\/resync\/Entrevista_01_ReSync_[0-9]{14}\.xml$/);
});
test('One click exports, launches motor, imports NEW synced sequence and opens it',async()=>{
  const app=setup();await flush();
  await app.elements.get('sync').listeners.click();await flush();
  const actions=app.calls.map(c=>c.action);
  assert.deepEqual(actions,['export','launch','import','open']);
  const [exportCall,launch,importCall]=app.calls;
  assert.match(exportCall.output,/resync_[0-9a-f]{24}\.source\.xml$/);
  assert.match(launch.url,/^resync:\/\/sync\?job=/);
  assert.match(decodeURIComponent(launch.url),/resync_[0-9a-f]{24}\.job\.json$/);
  assert.equal(importCall.suppress,true);
  assert.equal(importCall.bin,null);
  assert.equal(importCall.stills,false);
  assert.equal(app.getImported().length,1);
  assert.equal(app.getSource().name,'Entrevista 01');
  assert.notEqual(app.getActive(),app.getSource());
  assert.match(app.elements.get('status').textContent,/original foi preservada/);
});
test('A failed sync NEVER imports or changes the original sequence',async()=>{
  const app=setup({jobError:true});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.deepEqual(app.calls.map(c=>c.action),['export','launch']);
  assert.equal(app.getImported().length,0);
  assert.equal(app.getActive(),app.getSource());
  assert.match(app.elements.get('status').textContent,/Não foi possível concluir/);
});
test('A canceled workspace picker does not export or import',async()=>{
  const app=setup({folder:null});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.calls.length,0);
  assert.equal(app.getImported().length,0);
});
test('No active project means synchronization is disabled',async()=>{
  const app=setup({hasProject:false});await flush();
  assert.equal(app.elements.get('sync').disabled,true);
});

test('If the native bridge is missing, it errors promptly instead of waiting 90 minutes',async()=>{
  const app=setup({neverStarts:true});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.calls.filter(c=>c.action==='import').length,0);
  assert.equal(app.elements.get('sync').disabled,false);
  assert.equal(app.elements.get('stop-wait').hidden,true);
  assert.match(app.elements.get('status').textContent,/versão pública 0.6.6 não possui essa integração/);
  assert.equal(app.getActive(),app.getSource());
});

test('User can interrupt waiting for the bridge without altering project',async()=>{
  const app=setup({launchHangs:true});await flush();
  const waiting=app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.elements.get('stop-wait').hidden,false);
  app.elements.get('stop-wait').listeners.click();
  await waiting;await flush();
  assert.equal(app.elements.get('sync').disabled,false);
  assert.equal(app.getImported().length,0);
  assert.equal(app.getActive(),app.getSource());
  assert.match(app.elements.get('status').textContent,/Espera interrompida/);
});

test('Docked UXP panel can scroll down to status and footer without page clipping',()=>{
  const css=readFileSync(path.join(ROOT,'styles.css'),'utf8');
  const html=readFileSync(path.join(ROOT,'index.html'),'utf8');
  assert.match(css,/html,body\s*\{[^}]*height:100%[^}]*overflow:hidden/s);
  assert.match(css,/\.plugin-panel\s*\{[^}]*height:100%[^}]*min-height:0[^}]*overflow-y:auto/s);
  assert.match(css,/\.plugin-panel::-webkit-scrollbar-thumb/);
  assert.match(css,/@media\s*\(max-height:680px\)/);
  assert.match(html,/id="status"/);
  assert.match(html,/id="sync"/);
  assert.match(html,/BETA 0\.4/);
});
