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
const JOB_TIMEOUT_MS = 90 * 60 * 1000;

let busy = false;
let activeSequence = null;
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

function setBusy(value) {
  busy = Boolean(value);
  el('refresh').disabled = busy;
  el('export-xml').disabled = busy || !activeSequence;
  el('sync').disabled = busy || !activeSequence;
  el('change-workspace').disabled = busy;
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

async function waitForSync(folder, statusName, id, timeout=JOB_TIMEOUT_MS) {
  const deadline = Date.now() + timeout;
  let lastMessage = '';
  while (Date.now() < deadline) {
    const state = await readJobStatus(folder, statusName, id);
    if (state) {
      if (state.message && state.message !== lastMessage) {
        lastMessage = state.message;
        status('Sincronizando: ' + lastMessage);
      }
      progress(state.percent);
      if (state.state === 'error') throw Error(state.message || 'Falha no motor ReSync.');
      if (state.state === 'done') return state;
    }
    await new Promise(resolve => setTimeout(resolve, POLL_MS));
  }
  throw Error('O ReSync não respondeu. Verifique se a versão compatível do ReSync.app está instalada em Aplicativos.');
}

async function importNewSequence(project, xmlPath, expectedName) {
  const before = await project.getSequences();
  const beforeNames = new Set(before.map(seq => String(seq.name)));
  // Premiere's UXP Project.importFiles accepts null for the optional bin.
  // Unlike undefined, null is supported in Premiere 26.5.x.
  const imported = await project.importFiles([xmlPath], true, null, false);
  if (!imported) throw Error('O Premiere recusou importar o XML sincronizado. O XML permanece disponível na pasta escolhida.');
  const after = await project.getSequences();
  const created = after.filter(seq => !beforeNames.has(String(seq.name)));
  const matched = created.find(seq => String(seq.name) === String(expectedName))
    || (created.length === 1 ? created[0] : null);
  if (!matched) {
    throw Error('XML importado, mas não foi possível identificar uma nova sequência. Confira o painel Projeto. A original foi preservada.');
  }
  const opened = await project.openSequence(matched);
  if (!opened) {
    status('Nova sequência criada: ' + matched.name + '. Abra-a pelo painel Projeto.', 'success');
    return matched;
  }
  await project.setActiveSequence(matched);
  return matched;
}

async function syncSequence() {
  if (busy || !activeSequence) return;
  setBusy(true);
  progress(0);
  try {
    const {project, sequence} = await getSequenceContext();
    if (!project || !sequence) throw Error('Abra uma sequência no Premiere primeiro.');

    const folder = await workspaceFolder();
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
    if (!ok) throw Error('O Premiere não conseguiu exportar a sequência para análise.');

    const jobFile = await folder.createFile(jobName);
    await jobFile.write(JSON.stringify({
      protocol:1, job_id:id, reference:'auto', sequence_name:sequence.name
    }));
    if (!jobFile.nativePath) throw Error('Não foi possível criar o arquivo de trabalho.');

    status('Iniciando motor do ReSync em segundo plano…');
    const url = 'resync://sync?job=' + encodeURIComponent(jobFile.nativePath);
    const launched = await shell.openExternal(
      url, 'O ReSync precisa abrir seu motor local para sincronizar os áudios desta sequência.'
    );
    if (launched) throw Error('O macOS não conseguiu iniciar o ReSync: ' + launched);

    const result = await waitForSync(folder, statusName, id);
    if (result.output !== outputName || !result.sequence_name) {
      throw Error('Resultado da sincronização não corresponde à solicitação.');
    }

    const current = await premiere.Project.getActiveProject();
    if (!current || String(current.path) !== String(project.path)) {
      throw Error('O projeto ativo mudou durante a análise. O XML sincronizado está na pasta: ' + folder.nativePath);
    }

    progress(100);
    status('Importando a nova sequência no Premiere…');
    const newSequence = await importNewSequence(project,
      joinFile(folder.nativePath, outputName), result.sequence_name);
    status('Nova sequência pronta: ' + newSequence.name +
      '. A sequência original foi preservada.', 'success');
  } catch (error) {
    console.error('ReSync: sincronização pelo painel', error);
    status('Não foi possível concluir: ' + (error.message || error), 'error');
  } finally {
    setBusy(false);
  }
}

el('refresh').addEventListener('click', refreshSequence);
el('export-xml').addEventListener('click', exportSequenceXML);
el('sync').addEventListener('click', syncSequence);
el('change-workspace').addEventListener('click', changeWorkspace);

entrypoints.setup({
  panels: {resyncPanel: {show() { refreshSequence().catch(e => console.error('ReSync:', e)); }}}
});
refreshSequence().catch(e => console.error('ReSync:', e));
