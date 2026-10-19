/* ReSync UXP panel prototype for Premiere Pro 26.5.2.
 * Safe initial scope: inspect the active sequence and export Final Cut Pro 7 XML.
 * Does not modify the project, touch clips, or invoke the synchronization engine.
 */
'use strict';

const premiere = require('premierepro');
const { entrypoints, storage } = require('uxp');
let busy = false;
let activeSequence = null;

const el = id => document.getElementById(id);

function status(message, kind) {
  const node = el('status');
  node.textContent = message;
  node.className = 'status' + (kind ? ' ' + kind : '');
}

function safeFilename(text) {
  return String(text || 'Sequencia')
    .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^\w.-]+/g, '_').replace(/^[_\.]+|[_\.]+$/g, '')
    .slice(0, 75) || 'Sequencia';
}

function setBusy(value) {
  busy = Boolean(value);
  el('refresh').disabled = busy;
  el('export-xml').disabled = busy || !activeSequence;
}

async function getSequenceContext() {
  const project = await premiere.Project.getActiveProject();
  if (!project) return { project: null, sequence: null };
  const sequence = await project.getActiveSequence();
  return { project, sequence };
}

async function refreshSequence() {
  if (busy) return;
  setBusy(true);
  status('Consultando o Premiere…');
  try {
    const { project, sequence } = await getSequenceContext();
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
      sequence.getVideoTrackCount(),
      sequence.getAudioTrackCount()
    ]);
    el('video-count').textContent = String(videos);
    el('audio-count').textContent = String(audios);
    status('Sequência reconhecida. Pronto para exportar o XML.', 'success');
  } catch (error) {
    activeSequence = null;
    el('sequence-name').textContent = 'Não foi possível conectar';
    el('video-count').textContent = '—';
    el('audio-count').textContent = '—';
    console.error('ReSync: erro ao ler sequência', error);
    status('Erro ao consultar a sequência: ' + (error.message || String(error)), 'error');
  } finally {
    setBusy(false);
  }
}

async function exportSequenceXML() {
  if (busy || !activeSequence) return;
  setBusy(true);
  status('Preparando exportação do XML…');
  try {
    // Re-fetch on click: user may have switched the active sequence.
    const { project, sequence } = await getSequenceContext();
    if (!project || !sequence) throw new Error('Nenhuma sequência ativa.');
    activeSequence = sequence;
    el('sequence-name').textContent = sequence.name;
    el('project-name').textContent = project.name;

    // The folder picker gives explicit access. Premiere's exporter receives
    // a native filesystem path (not a UXP session token).
    const folder = await storage.localFileSystem.getFolder();
    if (!folder) {
      status('Exportação cancelada. Nenhum arquivo foi criado.');
      return;
    }
    if (!folder.nativePath) throw new Error('A pasta selecionada não tem um caminho válido.');
    const timestamp = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14);
    const filename = safeFilename(sequence.name) + '_ReSync_' + timestamp + '.xml';
    const filePath = folder.nativePath.replace(/[\\/]$/, '') + '/' + filename;

    const ok = await premiere.ProjectConverter.exportAsFinalCutProXML(sequence, filePath, true);
    if (!ok) throw new Error('O Premiere não conseguiu exportar o XML. Verifique os arquivos de mídia vinculados.');
    status('XML criado: ' + filePath + '. Abra o ReSync.app e importe esse arquivo.', 'success');
  } catch (error) {
    console.error('ReSync: erro na exportação XML', error);
    status('Exportação não concluída: ' + (error.message || String(error)), 'error');
  } finally {
    setBusy(false);
  }
}

el('refresh').addEventListener('click', refreshSequence);
el('export-xml').addEventListener('click', exportSequenceXML);

entrypoints.setup({
  panels: {
    resyncPanel: {
      show() { refreshSequence().catch(e => console.error('ReSync:', e)); }
    }
  }
});

// UXP may not call show() again when a docked panel is restored. The explicit
// Refresh button handles sequence switches. Initial attempt is harmless.
refreshSequence().catch(e => console.error('ReSync startup:', e));
