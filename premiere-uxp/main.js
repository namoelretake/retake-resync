/* ReSync — native Premiere UXP workflow.
 *
 * Premiere exports its active sequence; ReSync's installed macOS bridge
 * analyzes audio in a separate background process. The panel imports the
 * completed FCP7 XML into the same Premiere project as a NEW sequence.
 * No mutations are applied to the source sequence.
 */
'use strict';

const premiere = require('premierepro');
const { entrypoints, storage, shell } = require('uxp');

const FOLDER_KEY = 'resync.premiere.workspace.v1';
const POLL_MS = 1300;
const BRIDGE_LAUNCH_TIMEOUT_MS = 25_000;
const BRIDGE_START_TIMEOUT_MS = 35_000;
const JOB_TIMEOUT_MS = 90 * 60 * 1000;

let busy = false;
let activeSequence = null;
let currentSync = null;

function newSyncRun() {
  const run = {cancelled:false, rejectCancel:null};
  run.cancelledPromise = new Promise((_, reject) => {run.rejectCancel = reject;});
  // The user may interrupt before the first await. Keep the cancellation
  // Promise handled even when no race is currently awaiting it.
  run.cancelledPromise.catch(() => {});
  run.stop = () => {
    if (run.cancelled) return;
    run.cancelled = true;
    run.rejectCancel(Error('Espera interrompida. A sequência original está intacta. Se o motor já iniciou, ele pode continuar analisando em segundo plano.'));
  };
  return run;
}

function checkCancelled(run) {
  if (run.cancelled) throw Error('Espera interrompida. A sequência original está intacta.');
}

async function withTimeout(operation, run, milliseconds, message) {
  checkCancelled(run);
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(Error(message)), milliseconds);
  });
  try {
    return await Promise.race([Promise.resolve().then(() => {
      checkCancelled(run);
      return operation();
    }), run.cancelledPromise, deadline]);
  } finally {
    clearTimeout(timer);
  }
}

function stopWaiting() {
  if (!currentSync) return;
  currentSync.stop();
  status('Interrompendo a espera…');
}
const el = id => document.getElementById(id);

function status(message, kind) {
  const node = el('status');
  node.textContent = message;
  node.className = 'status' + (kind ? ' ' + kind : '');
}

function progress(percent) {
  const track = el('progress-track');
  const fill = el('progress-fill');
  if (!track || !fill) return;
  track.hidden = false;
  fill.style.width = Math.max(0, Math.min(100, Number(percent) || 0)) + '%';
}

function safeFilename(text) {
  return String(text || 'Sequencia')
    .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^\w.-]+/g, '_').replace(/^[_\.]+|[_\.]+$/g, '')
    .slice(0, 75) || 'Sequencia';
}

function jobId() {
  // Nonce for distinct filenames; not an authentication credential.
  return Array.from({length:24}, () => Math.floor(Math.random() * 16).toString(16)).join('');
}

function joinFile(directory, filename) {
  return directory.replace(/[\\/]$/, '') + '/' + filename;
}

function entityGuid(entity, label) {
  // Premiere Project/Sequence.guid is a Guid, whose documented toString()
  // returns its identifier. Never compare wrapper objects or their generic
  // String(object) conversion, which can be identical for different projects.
  let value;
  try {
    const guid = entity && entity.guid;
    value = guid && typeof guid.toString === 'function' ? guid.toString() : '';
  } catch (_error) {
    value = '';
  }
  if (typeof value !== 'string') value = '';
  value = value.trim().replace(/^\{(.+)\}$/, '$1').toLowerCase();
  if (!/^(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/.test(value)
      || /^0+$/.test(value.replace(/-/g, ''))) {
    throw Error('O Premiere não forneceu uma identificação válida ' + label + '. Salve o projeto, reabra-o e tente novamente.');
  }
  return value.replace(/-/g, '');
}

async function validateActiveProject(identity, folderPath) {
  const current = await premiere.Project.getActiveProject();
  if (!current || entityGuid(current, 'do projeto ativo') !== identity.guid
      || (identity.path && current.path !== identity.path)) {
    throw Error('O projeto ativo mudou durante a análise. O XML sincronizado está na pasta: ' + folderPath);
  }
  return current;
}

function setBusy(value) {
  busy = Boolean(value);
  el('refresh').disabled = busy;
  el('export-xml').disabled = busy || !activeSequence;
  el('sync').disabled = busy || !activeSequence;
  el('change-workspace').disabled = busy;
  el('stop-wait').hidden = !busy || !currentSync;
}

async function getSequenceContext() {
  const project = await premiere.Project.getActiveProject();
  if (!project) return {project:null, sequence:null};
  return {project, sequence:await project.getActiveSequence()};
}

async function refreshSequence() {
  if (busy) return;
  setBusy(true);
  status('Consultando o Premiere…');
  try {
    const {project, sequence} = await getSequenceContext();
    activeSequence = sequence;
    el('project-name').textContent = project ? project.name : 'Nenhum projeto aberto';
    el('sequence-name').textContent = sequence ? sequence.name : 'Nenhuma sequência ativa';
    if (!sequence) {
      el('video-count').textContent = '—';
      el('audio-count').textContent = '—';
      status('Abra uma sequência no Premiere e clique em Atualizar.');
      return;
    }
    const [videos, audios] = await Promise.all([
      sequence.getVideoTrackCount(), sequence.getAudioTrackCount()
    ]);
    el('video-count').textContent = String(videos);
    el('audio-count').textContent = String(audios);
    status('Pronto. Clique em Sincronizar e criar sequência.', 'success');
  } catch (error) {
    activeSequence = null;
    el('sequence-name').textContent = 'Não foi possível conectar';
    el('video-count').textContent = '—';
    el('audio-count').textContent = '—';
    console.error('ReSync: erro ao ler sequência', error);
    status('Não foi possível consultar a sequência: ' + (error.message || error), 'error');
  } finally {
    setBusy(false);
  }
}

async function workspaceFolder(forceNew=false) {
  const fs = storage.localFileSystem;
  const existing = !forceNew && localStorage.getItem(FOLDER_KEY);
  if (existing) {
    try {
      const folder = await fs.getEntryForPersistentToken(existing);
      if (folder && folder.isFolder && folder.nativePath) return folder;
    } catch (e) {
      localStorage.removeItem(FOLDER_KEY);
    }
  }
  status('Escolha uma pasta para os arquivos temporários do ReSync. Só é necessário na primeira utilização.');
  const folder = await fs.getFolder();
  if (!folder) return null;
  if (!folder.nativePath || !folder.isFolder) throw Error('A pasta selecionada não pode ser utilizada.');
  const token = await fs.createPersistentToken(folder);
  localStorage.setItem(FOLDER_KEY, token);
  return folder;
}

async function changeWorkspace() {
  if (busy) return;
  try {
    const folder = await workspaceFolder(true);
    status(folder ? 'Pasta de trabalho configurada: ' + folder.nativePath : 'Alteração cancelada.',
           folder ? 'success' : '');
  } catch (error) {
    status('Não foi possível escolher a pasta: ' + (error.message || error), 'error');
  }
}

async function exportSequenceXML() {
  if (busy || !activeSequence) return;
  setBusy(true);
  status('Preparando exportação manual do XML…');
  try {
    const {project, sequence} = await getSequenceContext();
    if (!project || !sequence) throw Error('Nenhuma sequência ativa.');
    const folder = await storage.localFileSystem.getFolder();
    if (!folder) {
      status('Exportação cancelada. Nenhum arquivo foi criado.');
      return;
    }
    if (!folder.nativePath) throw Error('A pasta selecionada não tem caminho válido.');
    const stamp = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0,14);
    const name = safeFilename(sequence.name) + '_ReSync_' + stamp + '.xml';
    const output = joinFile(folder.nativePath, name);
    const ok = await premiere.ProjectConverter.exportAsFinalCutProXML(sequence, output, true);
    if (!ok) throw Error('Premiere não conseguiu exportar a sequência.');
    status('XML salvo em: ' + output, 'success');
  } catch (error) {
    console.error('ReSync: exportação XML', error);
    status('Erro ao exportar XML: ' + (error.message || error), 'error');
  } finally {
    setBusy(false);
  }
}

async function readJobStatus(folder, name, id) {
  try {
    const file = await folder.getEntry(name);
    const data = JSON.parse(await file.read());
    return data && data.job_id === id ? data : null;
  } catch (_error) {
    // Worker may not have produced the status file yet; an atomic replace
    // may also occur between checking and reading the file.
    return null;
  }
}

async function waitForSync(folder, statusName, id, run, timeout=JOB_TIMEOUT_MS) {
  const began = Date.now();
  const deadline = began + timeout;
  let lastMessage = '';
  let hasStarted = false;
  while (Date.now() < deadline) {
    checkCancelled(run);
    const state = await readJobStatus(folder, statusName, id);
    checkCancelled(run);
    if (state) {
      hasStarted = true;
      if (state.message && state.message !== lastMessage) {
        lastMessage = state.message;
        status('Sincronizando: ' + lastMessage);
      }
      progress(state.percent);
      if (state.state === 'error') throw Error(state.message || 'Falha no motor ReSync.');
      if (state.state === 'done') return state;
    } else if (!hasStarted && Date.now() - began > BRIDGE_START_TIMEOUT_MS) {
      throw Error('O motor não iniciou. Instale o ReSync.app compatível em Aplicativos (versão 0.6.8 ou posterior). Não escolha um arquivo JSON na janela do macOS; ela indica que o endereço resync:// não foi associado ao aplicativo.');
    }
    await Promise.race([
      new Promise(resolve => setTimeout(resolve, POLL_MS)),
      run.cancelledPromise
    ]);
  }
  throw Error('A sincronização excedeu o limite de tempo. Confira a pasta de trabalho e os arquivos de mídia.');
}

async function importNewSequence(project, xmlPath, expectedName, run, identity, folderPath) {
  const before = await project.getSequences();
  checkCancelled(run);
  const beforeIds = new Set(before.map(seq => entityGuid(seq, 'da sequência')));
  project = await validateActiveProject(identity, folderPath);
  checkCancelled(run);
  // importFiles is asynchronous and is not a CompoundAction transaction.
  // Once it starts, do not advertise cancellation or a rollback we cannot do.
  currentSync = null;
  el('stop-wait').hidden = true;
  // Premiere's UXP Project.importFiles accepts null for the optional bin.
  // Unlike undefined, null is supported in Premiere 26.5.x.
  const imported = await project.importFiles([xmlPath], true, null, false);
  if (!imported) throw Error('O Premiere recusou importar o XML sincronizado. O XML permanece disponível na pasta escolhida.');
  const after = await project.getSequences();
  const created = after.filter(seq => !beforeIds.has(entityGuid(seq, 'da sequência')));
  const candidates = created.filter(seq => seq.name === expectedName);
  const matched = candidates.length === 1 ? candidates[0] : null;
  if (!matched) {
    throw Error('XML importado, mas não foi possível identificar uma nova sequência. Confira o painel Projeto. A original foi preservada.');
  }
  const opened = await project.openSequence(matched);
  const activated = opened ? await project.setActiveSequence(matched) : false;
  return {sequence:matched, opened:Boolean(opened && activated)};
}

async function syncSequence() {
  if (busy || !activeSequence) return;
  const run = newSyncRun();
  currentSync = run;
  setBusy(true);
  progress(0);
  try {
    const {project, sequence} = await getSequenceContext();
    checkCancelled(run);
    if (!project || !sequence) throw Error('Abra uma sequência no Premiere primeiro.');
    // Capture primitive values now: saveAs() may mutate a Project wrapper.
    const identity = {guid:entityGuid(project, 'do projeto'), path:project.path};
    entityGuid(sequence, 'da sequência original');

    const folder = await workspaceFolder();
    checkCancelled(run);
    if (!folder) {
      status('Operação cancelada. A sequência original não foi modificada.');
      return;
    }

    const id = jobId();
    const name = 'resync_' + id;
    const sourceName = name + '.source.xml';
    const jobName = name + '.job.json';
    const statusName = name + '.status.json';
    const outputName = name + '.synced.xml';
    const sourcePath = joinFile(folder.nativePath, sourceName);

    status('Exportando sequência para análise de áudio…');
    progress(2);
    const ok = await premiere.ProjectConverter.exportAsFinalCutProXML(sequence, sourcePath, true);
    checkCancelled(run);
    if (!ok) throw Error('O Premiere não conseguiu exportar a sequência para análise.');

    const jobFile = await folder.createFile(jobName);
    checkCancelled(run);
    await jobFile.write(JSON.stringify({
      protocol:1, job_id:id, reference:'auto', sequence_name:sequence.name
    }));
    checkCancelled(run);
    if (!jobFile.nativePath) throw Error('Não foi possível criar o arquivo de trabalho.');

    status('Iniciando motor do ReSync em segundo plano…');
    const url = 'resync://sync?job=' + encodeURIComponent(jobFile.nativePath);
    const launched = await withTimeout(
      () => shell.openExternal(url,
        'Autorize iniciar o motor local do ReSync. NÃO selecione um arquivo JSON nem outro programa.'),
      run, BRIDGE_LAUNCH_TIMEOUT_MS,
      'O macOS não confirmou a abertura do ReSync. Verifique se o ReSync.app está instalado em Aplicativos. Feche qualquer janela pedindo para escolher um arquivo JSON.'
    );
    checkCancelled(run);
    if (launched) throw Error('O macOS não conseguiu iniciar o ReSync: ' + launched);

    status('Aguardando a confirmação do motor ReSync…');
    const result = await waitForSync(folder, statusName, id, run);
    if (result.output !== outputName || !result.sequence_name) {
      throw Error('Resultado da sincronização não corresponde à solicitação.');
    }

    const current = await validateActiveProject(identity, folder.nativePath);
    checkCancelled(run);
    progress(100);
    status('Importando a nova sequência no Premiere…');
    const imported = await importNewSequence(current,
      joinFile(folder.nativePath, outputName), result.sequence_name, run, identity, folder.nativePath);
    const newSequence = imported.sequence;
    const tracks = result.summary || {};
    const trackInfo = Number.isInteger(tracks.video_tracks) && Number.isInteger(tracks.audio_tracks)
      ? ' · V' + tracks.video_tracks + ' / A' + tracks.audio_tracks
      : '';
    const savedInfo = Number.isInteger(tracks.tracks_compacted) && tracks.tracks_compacted > 0
      ? ' · ' + tracks.tracks_compacted + ' pistas desnecessárias eliminadas'
      : '';
    const hasCounts = Number.isInteger(tracks.total) && tracks.total >= 0
      && Number.isInteger(tracks.unmatched) && tracks.unmatched >= 0 && tracks.unmatched <= tracks.total;
    // The engine's synced counter excludes reference takes. total-unmatched
    // includes every take with confirmed audio, including those references.
    const syncInfo = hasCounts
      ? ' · ' + (tracks.total - tracks.unmatched) + '/' + tracks.total + ' takes com áudio confirmado'
        + (tracks.unmatched > 0 ? ' · ' + tracks.unmatched + ' para revisão (vermelho)' : '')
      : ' · O motor não informou a contagem de áudio confirmado; revise os takes';
    const openInfo = imported.opened ? '' : ' Abra-a pelo painel Projeto.';
    status('Nova sequência criada: ' + newSequence.name + syncInfo + trackInfo + savedInfo +
      '. A sequência original foi preservada.' + openInfo,
      hasCounts && tracks.total > 0 && tracks.unmatched === 0 ? 'success' : '');
  } catch (error) {
    console.error('ReSync: sincronização pelo painel', error);
    status('Não foi possível concluir: ' + (error.message || error), 'error');
  } finally {
    if (currentSync === run) currentSync = null;
    setBusy(false);
  }
}

el('refresh').addEventListener('click', refreshSequence);
el('export-xml').addEventListener('click', exportSequenceXML);
el('sync').addEventListener('click', syncSequence);
el('stop-wait').addEventListener('click', stopWaiting);
el('change-workspace').addEventListener('click', changeWorkspace);

entrypoints.setup({
  panels: {resyncPanel: {show() { refreshSequence().catch(e => console.error('ReSync:', e)); }}}
});
refreshSequence().catch(e => console.error('ReSync:', e));

