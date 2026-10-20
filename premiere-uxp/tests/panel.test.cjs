const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const MAIN = readFileSync(path.join(ROOT, 'main.js'), 'utf8');
const MANIFEST = JSON.parse(readFileSync(path.join(ROOT, 'manifest.json'), 'utf8'));
async function flush() { for(let i=0;i<12;i++)await new Promise(r=>setImmediate(r)); }
const PROJECT_GUID = 'a1111111-1111-4111-8111-111111111111';
const SOURCE_GUID = 'b2222222-2222-4222-8222-222222222222';
const NEW_GUID = 'c3333333-3333-4333-8333-333333333333';
const OTHER_GUID = 'd4444444-4444-4444-8444-444444444444';
function guid(value) { return {toString(){return value}}; }
function deferred() {
  let resolve;
  const promise = new Promise(r=>{resolve=r});
  return {promise,resolve};
}

function setup({hasProject=true,folder='/tmp/resync',exportOK=true,importOK=true,jobError=false,
  neverStarts=false,launchHangs=false,projectPath='/tmp/original.prproj',projectGuid=guid(PROJECT_GUID),
  resultName,importedName,extraImported=[],openOK=true,activateOK=true,
  summary={total:8,synced:7,unmatched:0},onPicker,onExport,onCreateFile,onJobWrite,onStatus,onSequences}={}) {
  let simulatedClock = Date.now();
  class MockDate extends Date { static now() { return simulatedClock; } }
  const ids=['status','refresh','export-xml','sync','change-workspace','stop-wait','progress-track','progress-fill',
    'project-name','sequence-name','video-count','audio-count'];
  const elements=new Map(ids.map(id=>[id,{
    id,textContent:'',className:'',disabled:false,hidden:true,style:{width:'0%'},
    listeners:{},addEventListener(event,fn){this.listeners[event]=fn}
  }]));
  const calls=[], jobs=new Map(), appSource={name:'Entrevista 01',guid:guid(SOURCE_GUID),
    async getVideoTrackCount(){return 3},async getAudioTrackCount(){return 5}};
  let currentSequence=appSource, imported=[];
  const project={
    path:projectPath,name:'Teste.prproj',guid:projectGuid,
    async getActiveSequence(){return currentSequence},
    async getSequences(){if(onSequences)await onSequences();return [appSource,...imported]},
    async importFiles(paths,suppress,bin,stills){
      calls.push({action:'import',paths,suppress,bin,stills});
      if(!importOK)return false;
      const id=/resync_([0-9a-f]{24})\.synced\.xml/.exec(paths[0])[1];
      imported.push(...extraImported,{guid:guid(NEW_GUID),name:importedName || resultName || 'Entrevista 01 — ReSync '+id.slice(0,6)});
      return true;
    },
    async openSequence(seq){calls.push({action:'open',seq});return openOK},
    async setActiveSequence(seq){if(activateOK)currentSequence=seq;return activateOK}
  };
  let current=hasProject?project:null, entries;
  const premiere={
    Project:{async getActiveProject(){return current}},
    ProjectConverter:{async exportAsFinalCutProXML(seq,output,suppressUI){
      calls.push({action:'export',seq,output,suppressUI});if(onExport)await onExport();return exportOK
    }}
  };
  const folderObj={
    isFolder:true,nativePath:folder,
    async createFile(name) { if(onCreateFile)await onCreateFile();return {
      nativePath:folder+'/'+name,async write(value){ jobs.set(name,JSON.parse(value));if(onJobWrite)await onJobWrite(); }
    }},
    async getEntry(name){
      if(name.endsWith('.status.json')){
        if (neverStarts) throw Error('Worker not registered');
        const id=/resync_([0-9a-f]{24})\.status\.json/.exec(name)[1];
        const job=jobs.get('resync_'+id+'.job.json');
        if(!job)throw Error('Job not ready');
        const state=jobError?'error':'done';
        return {async read(){if(onStatus)await onStatus();return JSON.stringify({
          job_id:id,state,percent:state==='done'?100:12,
          message:jobError?'Falha simulada':'Concluído',
          sequence_name:resultName || 'Entrevista 01 — ReSync '+id.slice(0,6),
          output:'resync_'+id+'.synced.xml',summary
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
      async getFolder(){if(onPicker)await onPicker();return folder?folderObj:null},
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
          getImported(){return imported},getSource(){return appSource},getActive(){return currentSequence},
          getJobs(){return jobs}};
}

test('Manifest declares Premiere UXP and explicit local bridge launch permission',()=>{
  assert.equal(MANIFEST.manifestVersion,5);
  assert.equal(MANIFEST.version,'0.5.0');
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
  assert.match(app.elements.get('status').textContent,/versão 0.6.8 ou posterior/);
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
  assert.match(html,/BETA 0\.5/);
});

test('A new sequence with the same name as the original is identified by its Guid',async()=>{
  const app=setup({resultName:'Entrevista 01'});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.getImported().length,1);
  assert.equal(app.getActive(),app.getImported()[0]);
  assert.notEqual(app.getActive().guid.toString(),app.getSource().guid.toString());
  assert.equal(app.getSource().name,'Entrevista 01');
});

test('Import opens only the newly created sequence matching the expected result',async()=>{
  const unrelated={name:'Montagem criada em paralelo',guid:guid(OTHER_GUID)};
  const app=setup({extraImported:[unrelated]});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.getImported().length,2);
  assert.equal(app.getActive(),app.getImported()[1]);
  assert.notEqual(app.getActive(),unrelated);
});

test('An unexpected imported name does not open an unrelated sequence',async()=>{
  const app=setup({importedName:'Outra montagem'});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.getImported().length,1);
  assert.equal(app.calls.some(c=>c.action==='open'),false);
  assert.equal(app.getActive(),app.getSource());
  assert.match(app.elements.get('status').textContent,/não foi possível identificar uma nova sequência/);
});

test('Duplicate new result names are reported as ambiguous and are not opened',async()=>{
  const resultName='Resultado ReSync';
  const app=setup({resultName,extraImported:[{name:resultName,guid:guid(OTHER_GUID)}]});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.calls.some(c=>c.action==='open'),false);
  assert.equal(app.getActive(),app.getSource());
});

for(const projectPath of ['/tmp/original.prproj','']) {
  test('A different project Guid blocks import even with the same path '+JSON.stringify(projectPath),async()=>{
    let app;
    app=setup({projectPath,onStatus(){app.setProject({...app.project,guid:guid(OTHER_GUID)})}});await flush();
    await app.elements.get('sync').listeners.click();await flush();
    assert.equal(app.calls.some(c=>c.action==='import'),false);
    assert.equal(app.getActive(),app.getSource());
    assert.match(app.elements.get('status').textContent,/projeto ativo mudou/);
  });
}

test('Project Guid conversion accepts new wrappers and canonical case/braces',async()=>{
  let app;
  app=setup({onStatus(){app.setProject({...app.project,guid:guid('{'+PROJECT_GUID.toUpperCase()+'}')})}});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.getImported().length,1);
  assert.equal(app.getActive(),app.getImported()[0]);
});

test('Project identity is captured before analysis when saveAs mutates the original wrapper',async()=>{
  let app;
  app=setup({onStatus(){app.project.path='/tmp/copy.prproj'}});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.calls.some(c=>c.action==='import'),false);
  assert.match(app.elements.get('status').textContent,/projeto ativo mudou/);
});

test('A project switch during the sequence list read is caught by the final import guard',async()=>{
  let app;
  app=setup({onSequences(){app.setProject({...app.project,guid:guid(OTHER_GUID)})}});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.deepEqual(app.calls.map(c=>c.action),['export','launch']);
  assert.match(app.elements.get('status').textContent,/projeto ativo mudou/);
});

test('A missing original sequence Guid blocks analysis before export',async()=>{
  const app=setup();app.getSource().guid={};await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.calls.length,0);
  assert.match(app.elements.get('status').textContent,/identificação válida da sequência original/);
});

for(const projectGuid of [undefined,{},guid(''),guid('00000000-0000-0000-0000-000000000000')]) {
  test('An invalid project identity cannot export or launch '+JSON.stringify(projectGuid),async()=>{
    const app=setup();app.project.guid=projectGuid;await flush();
    await app.elements.get('sync').listeners.click();await flush();
    assert.equal(app.calls.length,0);
    assert.match(app.elements.get('status').textContent,/identificação válida do projeto/);
  });
}

for(const [hook,actions,jobs] of [
  ['onPicker',[],0],['onExport',['export'],0],['onCreateFile',['export'],0],['onJobWrite',['export'],1]
]) {
  test('Interrupting during '+hook+' prevents later export, worker launch and import',async()=>{
    const gate=deferred(),entered=deferred();
    const app=setup({[hook]:async()=>{entered.resolve();await gate.promise}});await flush();
    const waiting=app.elements.get('sync').listeners.click();await entered.promise;
    app.elements.get('stop-wait').listeners.click();gate.resolve();
    await waiting;await flush();
    assert.deepEqual(app.calls.map(c=>c.action),actions);
    assert.equal(app.getJobs().size,jobs);
    assert.equal(app.getActive(),app.getSource());
    assert.equal(app.elements.get('sync').disabled,false);
    assert.match(app.elements.get('status').textContent,/Espera interrompida/);
  });
}

test('Interrupting while the completed status is read prevents import',async()=>{
  const gate=deferred(),entered=deferred();
  const app=setup({onStatus:async()=>{entered.resolve();await gate.promise}});await flush();
  const waiting=app.elements.get('sync').listeners.click();await entered.promise;
  app.elements.get('stop-wait').listeners.click();gate.resolve();
  await waiting;await flush();
  assert.deepEqual(app.calls.map(c=>c.action),['export','launch']);
  assert.equal(app.getActive(),app.getSource());
});

test('Interrupting before import commits prevents project changes',async()=>{
  const gate=deferred(),entered=deferred();
  const app=setup({onSequences:async()=>{entered.resolve();await gate.promise}});await flush();
  const waiting=app.elements.get('sync').listeners.click();await entered.promise;
  assert.equal(app.elements.get('stop-wait').hidden,false);
  app.elements.get('stop-wait').listeners.click();gate.resolve();
  await waiting;await flush();
  assert.deepEqual(app.calls.map(c=>c.action),['export','launch']);
  assert.equal(app.getActive(),app.getSource());
});

test('All confirmed takes include references excluded from the synced counter',async()=>{
  const app=setup({summary:{total:8,synced:6,unmatched:0}});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.match(app.elements.get('status').textContent,/8\/8 takes com áudio confirmado/);
  assert.equal(app.elements.get('status').className,'status success');
});

for(const unmatched of [3,8]) {
  test('A created sequence reports '+unmatched+' unmatched takes without green sync success',async()=>{
    const app=setup({summary:{total:8,synced:Math.max(0,7-unmatched),unmatched}});await flush();
    await app.elements.get('sync').listeners.click();await flush();
    assert.equal(app.getImported().length,1);
    assert.match(app.elements.get('status').textContent,new RegExp((8-unmatched)+'/8 takes com áudio confirmado'));
    assert.match(app.elements.get('status').textContent,new RegExp(unmatched+' para revisão \\(vermelho\\)'));
    assert.equal(app.elements.get('status').className,'status');
  });
}

test('An unknown or invalid summary never claims successful audio verification',async()=>{
  for(const summary of [null,{total:8,unmatched:9}]) {
    const app=setup({summary});await flush();
    await app.elements.get('sync').listeners.click();await flush();
    assert.equal(app.getImported().length,1);
    assert.match(app.elements.get('status').textContent,/não informou a contagem de áudio confirmado/);
    assert.equal(app.elements.get('status').className,'status');
  }
});

test('A refused import does not attempt to open or activate any sequence',async()=>{
  const app=setup({importOK:false});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.deepEqual(app.calls.map(c=>c.action),['export','launch','import']);
  assert.equal(app.getActive(),app.getSource());
  assert.match(app.elements.get('status').textContent,/Premiere recusou importar/);
});

test('When Premiere cannot open the imported sequence the panel gives a manual opening instruction',async()=>{
  const app=setup({openOK:false});await flush();
  await app.elements.get('sync').listeners.click();await flush();
  assert.equal(app.getImported().length,1);
  assert.equal(app.getActive(),app.getSource());
  assert.match(app.elements.get('status').textContent,/Abra-a pelo painel Projeto/);
});

