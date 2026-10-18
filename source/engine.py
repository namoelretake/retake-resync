"""ReSync - automatic graph-based Final Cut Pro 7 XML audio sync engine.

Keeps original media untouched. Repositions timeline clipitems in a fresh XML tree.
Requires Python 3.10+, numpy and ffmpeg in PATH.
"""
from __future__ import annotations

import copy
import csv
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np


FEATURE_FPS = 50
AUDIO_RATE = 16000
HOP = AUDIO_RATE // FEATURE_FPS
NFFT = 1024
# Valores internos: o editor não precisa configurar graus de confiança.
AUTO_STRONG_SCORE = 0.46
AUTO_CONSENSUS_SCORE = 0.38
MIN_VERIFIED_OVERLAP = 1.0
# Gap between distinct audio-verified recording blocks on the Premiere timeline.
# Never add this inside a block: member clips must preserve their relative sync.
VERIFIED_BLOCK_GAP_SECONDS = 10.0


def fps_from_rate(element: ET.Element | None, default: float = 30.0) -> float:
    if element is None:
        return default
    timebase = element.findtext('timebase')
    if not timebase:
        return default
    base = float(timebase)
    ntsc = (element.findtext('ntsc') or '').strip().upper() == 'TRUE'
    return base * 1000 / 1001 if ntsc else base


def rate_for_clip(clip: ET.Element, file_element: ET.Element | None, seqfps: float) -> float:
    rate = clip.find('rate')
    if rate is not None:
        return fps_from_rate(rate, seqfps)
    if file_element is not None:
        return fps_from_rate(file_element.find('rate'), seqfps)
    return seqfps


def decode_file_uri(value: str) -> str:
    value = value.strip()
    if value.startswith('file:'):
        parts = urllib.parse.urlsplit(value)
        path = urllib.parse.unquote(parts.path)
        if parts.netloc not in ('', 'localhost'):
            path = f'//{parts.netloc}{path}'
        if re.match(r'^/[a-zA-Z]:/', path):
            path = path[1:]
        return path
    return urllib.parse.unquote(value)


@dataclass
class Clip:
    uid: str
    element: ET.Element
    kind: str
    track_no: int
    name: str
    path: str
    source_in: float
    start: float
    end: float
    file_rate: float
    seq_rate: float
    links: tuple[str, ...] = ()
    new_start: float | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def track_key(self) -> str:
        return f'{self.kind}-{self.track_no}'


@dataclass
class Group:
    gid: int
    members: list[Clip]
    start: float
    duration: float
    path: str = ''
    source_in: float = 0.0
    assigned: float | None = None
    match_score: float = 0.0
    matched_to: str = ''
    reason: str = ''
    status: str = 'pending'
    verified_partner_ids: set[int] = field(default_factory=set)

    @property
    def title(self):
        return next((c.name for c in self.members if c.kind == 'video'), self.members[0].name)


class Project:
    def __init__(self, xml_bytes: bytes):
        if len(xml_bytes) > 30_000_000:
            raise ValueError('XML maior que 30 MB, limite desta versão.')
        self.root = ET.fromstring(xml_bytes)
        if self.root.tag != 'xmeml':
            raise ValueError('Formato não suportado: é necessário Final Cut Pro 7 XML (xmeml).')
        self.seq = self.root.find('./sequence')
        if self.seq is None:
            self.seq = self.root.find('.//sequence')
        if self.seq is None:
            raise ValueError('Nenhuma sequência encontrada no XML.')
        self.fps = fps_from_rate(self.seq.find('rate'))
        self.clips: list[Clip] = []
        self.groups: list[Group] = []
        self.has_doctype = b'<!DOCTYPE xmeml' in xml_bytes
        self.multi_sequence = len(self.root.findall('./sequence')) > 1
        self._export_cache: bytes | None = None
        self._parse()

    def _parse(self):
        all_files: dict[str, ET.Element] = {}
        for f in self.root.iter('file'):
            if f.get('id') and f.findtext('pathurl'):
                all_files[f.get('id')] = f
        index = 0
        for kind in ('video', 'audio'):
            media = self.seq.find(f'./media/{kind}')
            if media is None:
                continue
            for track_i, track in enumerate(media.findall('./track'), 1):
                for clip in track.findall('./clipitem'):
                    if (clip.findtext('start') or '') == '-1' or (clip.findtext('end') or '') == '-1':
                        continue  # transitions must not be rewritten
                    try:
                        st, en = int(clip.findtext('start')), int(clip.findtext('end'))
                        source = int(clip.findtext('in'))
                    except (ValueError, TypeError):
                        continue
                    if en <= st:
                        continue
                    file_node = clip.find('file')
                    if file_node is not None and not file_node.findtext('pathurl'):
                        file_node = all_files.get(file_node.get('id'), file_node)
                    path = decode_file_uri(file_node.findtext('pathurl') or '') if file_node is not None else ''
                    clip_rate = rate_for_clip(clip, file_node, self.fps)
                    if clip_rate <= 0:
                        clip_rate = self.fps
                    links = tuple(e.text.strip() for e in clip.findall('./link/linkclipref') if e.text)
                    index += 1
                    self.clips.append(Clip(
                        uid=clip.get('id') or f'clip-auto-{index}', element=clip,
                        kind=kind, track_no=track_i,
                        name=clip.findtext('name') or os.path.basename(path) or f'Clipe {index}',
                        path=path, source_in=source / clip_rate,
                        start=st / self.fps, end=en / self.fps,
                        file_rate=clip_rate, seq_rate=self.fps, links=links))
        if not self.clips:
            raise ValueError('Nenhum clipitem válido encontrado na sequência.')
        self._make_groups()

    def _make_groups(self):
        n = len(self.clips)
        par = list(range(n))
        def find(k):
            while par[k] != k:
                par[k] = par[par[k]]
                k = par[k]
            return k
        def union(a, b):
            a, b = find(a), find(b)
            if a != b:
                par[b] = a
        by_id = defaultdict(list)
        for i, clip in enumerate(self.clips):
            by_id[clip.uid].append(i)
        for i, clip in enumerate(self.clips):
            for link in clip.links:
                for j in by_id[link]:
                    other = self.clips[j]
                    if abs(clip.start - other.start) < 0.12 and abs(clip.duration - other.duration) < 0.12:
                        union(i, j)
        # Some Premiere XML exports omit link nodes, especially for dual mono tracks.
        same_source = {}
        for i, clip in enumerate(self.clips):
            if not clip.path:
                continue
            key = (clip.path, round(clip.start, 2), round(clip.source_in, 2), round(clip.duration, 2))
            if key in same_source:
                union(i, same_source[key])
            else:
                same_source[key] = i
        components = defaultdict(list)
        for i, clip in enumerate(self.clips):
            components[find(i)].append(clip)
        for gid, member_list in enumerate(components.values(), 1):
            primary = next((x for x in member_list if x.kind == 'video'), member_list[0])
            options = sorted(member_list, key=lambda x: (
                0 if x.kind == 'audio' and x.path.lower().endswith(('.wav','.mp3','.aif','.aiff','.flac','.m4a')) else 1,
                0 if x.kind == 'audio' else 1))
            candidate = next((x for x in options if x.path), primary)
            self.groups.append(Group(gid=gid, members=member_list,
                                    start=primary.start, duration=min(x.duration for x in member_list),
                                    path=candidate.path, source_in=candidate.source_in))

    def tracks(self):
        out = []
        for kind in ('video', 'audio'):
            for no in sorted({c.track_no for c in self.clips if c.kind == kind}):
                clips = [c for c in self.clips if c.kind == kind and c.track_no == no]
                out.append({'id': f'{kind}-{no}', 'name': f'{"V" if kind == "video" else "A"}{no}',
                            'clips': len(clips), 'minutes': round(sum(c.duration for c in clips) / 60, 1),
                            'examples': [c.name for c in clips[:3]]})
        return out

    def summary(self):
        return {'name': self.seq.findtext('name') or 'Sequência sem nome',
                'fps': round(self.fps, 3), 'clip_count': len(self.clips),
                'source_groups': len(self.groups), 'tracks': self.tracks(),
                'multi_sequence': self.multi_sequence}

    def to_xml(self):
        """Export all moved clips, creating new lanes if clips overlap within a track.

        For FCP7 XML, overlapping clipitems in a single track aren't reliably importable.
        Keep all original clips and their links. Additional tracks are inserted adjacent
        to the original and link trackindex / clipindex are updated.
        """
        if self._export_cache is not None:
            return self._export_cache
        min_start = min((c.new_start if c.new_start is not None else c.start) for c in self.clips)
        global_shift = max(0., -min_start)
        maxend = 0
        for c in self.clips:
            start = c.new_start if c.new_start is not None else c.start
            startframe = max(0, round((start + global_shift) * self.fps))
            duration_frames = max(1, round(c.duration * self.fps))
            endframe = startframe + duration_frames
            c.element.find('start').text = str(startframe)
            c.element.find('end').text = str(endframe)
            maxend = max(maxend, endframe)
        duration_node = self.seq.find('duration')
        if duration_node is not None:
            duration_node.text = str(maxend)

        self.lanes_added = 0
        clip_position = {}  # uid -> (mediatype, 1-based track, 1-based clip index)
        for kind in ('video', 'audio'):
            media = self.seq.find(f'./media/{kind}')
            if media is None:
                continue
            original_tracks = list(media.findall('track'))
            # Group by original track index. Do not rely on element equality for clip IDs.
            track_clips = defaultdict(list)
            for c in self.clips:
                if c.kind == kind:
                    track_clips[c.track_no].append(c)
            new_tracks = []
            for original_index, original_track in enumerate(original_tracks, 1):
                cs = sorted(track_clips.get(original_index, []),
                            key=lambda c: (int(c.element.findtext('start')), int(c.element.findtext('end'))))
                lanes = []
                lane_ends = []
                for c in cs:
                    st = int(c.element.findtext('start'))
                    en = int(c.element.findtext('end'))
                    lane_index = next((i for i, end in enumerate(lane_ends) if end <= st), None)
                    if lane_index is None:
                        lane_index = len(lanes)
                        lanes.append([])
                        lane_ends.append(en)
                    lanes[lane_index].append(c)
                    lane_ends[lane_index] = en
                # Retain unmodified track metadata (e.g. enabled / audio outputchannelindex).
                # Original transitions aren't rearranged; users should avoid edits/transitions.
                if not lanes:
                    lanes = [[]]
                for i, lane in enumerate(lanes):
                    if i == 0:
                        track = original_track
                    else:
                        track = copy.deepcopy(original_track)
                        for node in list(track):
                            if node.tag in ('clipitem', 'transitionitem'):
                                track.remove(node)
                        self.lanes_added += 1
                    if i == 0:
                        for node in list(track):
                            if node.tag == 'clipitem':
                                track.remove(node)
                    for c in lane:
                        track.append(c.element)
                        # Premiere uses one-based trackindex and clipindex in clipitem links.
                        clip_position[c.uid] = (kind, len(new_tracks) + 1,
                                                lane.index(c) + 1)
                    new_tracks.append(track)
            # Restore other media properties in place; replace only track siblings.
            existing = list(media)
            first = next((i for i, node in enumerate(existing) if node.tag == 'track'), len(existing))
            for t in original_tracks:
                media.remove(t)
            for offset, t in enumerate(new_tracks):
                media.insert(first + offset, t)

        for c in self.clips:
            for l in c.element.findall('./link'):
                refid = l.findtext('linkclipref')
                if refid not in clip_position:
                    continue
                media_kind, idx, clip_idx = clip_position[refid]
                for key, val in [('mediatype', media_kind),
                                 ('trackindex', str(idx)), ('clipindex', str(clip_idx))]:
                    node = l.find(key)
                    if node is not None:
                        node.text = val
        # FCP7 XML permits a label2 string on clipitem. Premiere has historically
        # recognized "Rose" as a red label, though custom label presets and
        # importer versions may override it. We also store an explicit comment.
        for group in self.groups:
            if group.status != 'unmatched':
                # A marca de falha feita pelo próprio ReSync não deve sobreviver
                # se o take for confirmado numa segunda análise.
                for clip in group.members:
                    comment = clip.element.findtext('./comments/clipcommenta') or ''
                    if comment.startswith('ReSync | NÃO SINCRONIZADO'):
                        label2 = clip.element.find('./labels/label2')
                        if label2 is not None and label2.text == 'Rose':
                            clip.element.find('labels').remove(label2)
                        node = clip.element.find('./comments/clipcommenta')
                        if node is not None:
                            clip.element.find('comments').remove(node)
                continue
            for clip in group.members:
                labels = clip.element.find('labels')
                if labels is None:
                    labels = ET.SubElement(clip.element, 'labels')
                label2 = labels.find('label2')
                if label2 is None:
                    label2 = ET.SubElement(labels, 'label2')
                label2.text = 'Rose'
                comments = clip.element.find('comments')
                if comments is None:
                    comments = ET.SubElement(clip.element, 'comments')
                comment = comments.find('clipcommenta')
                if comment is None:
                    comment = ET.SubElement(comments, 'clipcommenta')
                comment.text = 'ReSync | NÃO SINCRONIZADO — revisar manualmente'
        data = ET.tostring(self.root, encoding='utf-8', xml_declaration=True)
        if self.has_doctype:
            data = data.replace(b'?>', b'?>\n<!DOCTYPE xmeml>', 1)
        self._export_cache = data
        return data


class AudioCache:
    def __init__(self, report: Callable[[str], None] = lambda x: None):
        self.cache: dict[tuple, np.ndarray | None] = {}
        self.report = report
        self.window = np.hanning(NFFT).astype(np.float32)
        # Log-spaced bands from voices through cymbals; old version discarded
        # everything above 3.5 kHz and compressed the sound into only 5 bands.
        edges = np.geomspace(100, 7400, 15)
        bins = np.round(edges / AUDIO_RATE * NFFT).astype(int)
        self.band_slices = [(max(1,int(x)), max(int(x)+1,int(y)))
                            for x,y in zip(bins[:-1], bins[1:])]

    def features(self, path: str, sec: float, duration: float) -> np.ndarray | None:
        key = (path, round(sec, 3), round(duration, 3))
        if key in self.cache:
            return self.cache[key]
        if not os.path.isfile(path):
            self.cache[key] = None
            return None
        if duration < 2:
            self.cache[key] = None
            return None
        self.report(f'Analisando áudio: {os.path.basename(path)}')
        args = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-ss', f'{max(0,sec):.4f}',
                '-i', path, '-t', f'{duration:.4f}', '-map', '0:a:0', '-vn', '-ac', '1',
                '-ar', str(AUDIO_RATE), '-f', 'f32le', '-']
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Process audio in 4MB chunks. A 3-hour reference remains memory efficient.
        remain = np.empty(0, dtype=np.float32)
        segments = []
        try:
            while True:
                raw = p.stdout.read(4 * 1024 * 1024)
                if not raw:
                    break
                raw = raw[:len(raw) - (len(raw) % 4)]
                if not raw:
                    continue
                x = np.concatenate((remain, np.frombuffer(raw, dtype='<f4')))
                count = (len(x) - NFFT) // HOP + 1
                if count <= 0:
                    remain = x
                    continue
                view = np.lib.stride_tricks.sliding_window_view(x, NFFT)[::HOP][:count]
                spec = np.abs(np.fft.rfft(view * self.window, axis=1)).astype(np.float32)
                spec **= 2
                bands = np.stack([np.log1p(spec[:, lo:hi].mean(axis=1)) for lo, hi in self.band_slices], axis=1)
                segments.append(bands)
                remain = x[count * HOP:].copy()
        finally:
            if p.stdout:
                p.stdout.close()
        stderr = p.stderr.read().decode('utf-8', errors='ignore') if p.stderr else ''
        rc = p.wait()
        if p.stderr:
            p.stderr.close()
        if rc or not segments:
            self.report(f'Áudio indisponível: {os.path.basename(path)} ({stderr.strip()[:80]})')
            self.cache[key] = None
            return None
        feat = np.concatenate(segments, axis=0)
        if len(feat) < 100:
            self.cache[key] = None
            return None
        # Spectral flux explicitly represents attacks (claps, kicks, consonants).
        # In combination with the wider frequency bands it helps with concert
        # recordings where each camera has a very different tonal balance.
        flux = np.maximum(np.diff(feat, axis=0, prepend=feat[:1]), 0)
        onset = flux.mean(axis=1, keepdims=True)
        onset = onset - np.median(onset)
        feat = np.column_stack((feat, onset * 1.7))
        # High-pass remove slow response/gain differences across microphones.
        size = 151
        cs = np.concatenate((np.repeat(feat[[0]], size // 2, axis=0),
                             feat, np.repeat(feat[[-1]], size // 2, axis=0)), axis=0)
        cs = np.concatenate((np.zeros((1, feat.shape[1]), dtype=np.float64),
                             np.cumsum(cs, axis=0, dtype=np.float64)), axis=0)
        trend = (cs[size:] - cs[:-size]) / size
        feat = feat - trend.astype(np.float32)
        scale = np.sqrt((feat ** 2).mean(axis=0)).clip(min=1.e-4)
        feat = np.clip(feat / scale, -5., 5.).astype(np.float32)
        # If entirely silent, treat it as unsynchronizable.
        if float(np.std(feat)) < 0.06:
            self.cache[key] = None
            return None
        self.cache[key] = feat
        return feat


class CorrelationMatch(tuple):
    """(relative offset, correlation) plus evidence of a distinct unique peak."""
    def __new__(cls, lag, score, margin, runnerup):
        obj=super().__new__(cls, (lag,score))
        obj.margin=margin
        obj.runnerup=runnerup
        return obj


def correlate_features(a: np.ndarray, b: np.ndarray) -> tuple[float, float] | None:
    """Returns (b-start minus a-start in seconds, normalized correlation score)."""
    n, m = len(a), len(b)
    if min(n, m) < FEATURE_FPS * 2:
        return None
    fft_size = 1 << (n + m - 2).bit_length()
    fft_a = np.fft.rfft(a, n=fft_size, axis=0)
    fft_b = np.fft.rfft(b, n=fft_size, axis=0)
    cross = np.fft.irfft((fft_a * fft_b.conj()).sum(axis=1), n=fft_size)
    shifts = np.arange(-(m - 1), n, dtype=np.int64)
    corr = np.concatenate((cross[-(m - 1):], cross[:n]))
    first_a = np.maximum(shifts, 0)
    last_a = np.minimum(n, shifts + m)
    first_b = np.maximum(-shifts, 0)
    last_b = first_b + (last_a - first_a)
    overlap = last_a - first_a
    # Reject coincidences of tiny portions of recordings.
    minimum = min(int(FEATURE_FPS * 6), int(min(n,m) * 0.55))
    valid = overlap >= minimum
    if not np.any(valid):
        return None
    sq_a = np.r_[0., np.cumsum((a * a).sum(axis=1), dtype=np.float64)]
    sq_b = np.r_[0., np.cumsum((b * b).sum(axis=1), dtype=np.float64)]
    energy_a = sq_a[last_a] - sq_a[first_a]
    energy_b = sq_b[last_b] - sq_b[first_b]
    norm = np.sqrt(np.maximum(energy_a * energy_b, 1.e-9))
    scores = corr / norm
    scores[~valid] = -np.inf
    best = int(np.argmax(scores))
    # At a concert, two DIFFERENT songs can have the same tempo and strong
    # recurring kick/snare hits. A true shared performance should produce a
    # markedly sharper alignment peak than all other candidate offsets.
    alternate = np.abs(shifts-shifts[best]) >= int(FEATURE_FPS * .7)
    runnerup = float(np.max(scores[alternate])) if np.any(alternate) else -1.
    margin = float(scores[best]) - runnerup
    return CorrelationMatch(float(shifts[best]) / FEATURE_FPS, float(scores[best]), margin, runnerup)


def locate_media(project: Project, media_folder: str, report=lambda x: None):
    """Resolve missing FCP7 path URLs by searching local media folder by filename."""
    missing = [g for g in project.groups if not os.path.isfile(g.path)]
    if not missing or not media_folder:
        return
    folder = Path(os.path.expanduser(media_folder)).resolve()
    if not folder.is_dir():
        report(f'Pasta de mídias não encontrada: {folder}')
        return
    targets = {Path(g.path).name.lower() for g in missing}
    index = {}
    duplicates = set()
    report(f'Procurando arquivos na pasta {folder}...')
    for base, dirs, filenames in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in filenames:
            if f.lower() in targets:
                if f.lower() in index:
                    duplicates.add(f.lower())
                else:
                    index[f.lower()] = str(Path(base, f))
    replacements = {}
    for g in missing:
        basename = Path(g.path).name.lower()
        candidate = index.get(basename) if basename not in duplicates else None
        if candidate:
            replacements[g.path] = candidate
            g.path = candidate
    if duplicates:
        report('Aviso: arquivos com nomes repetidos não foram vinculados automaticamente: ' + ', '.join(sorted(duplicates)[:6]))
    # Relink exported XML too, not only FFmpeg analysis, when an unambiguous match is found.
    for file_node in project.root.iter('file'):
        urlnode = file_node.find('pathurl')
        if urlnode is None or not urlnode.text:
            continue
        old = decode_file_uri(urlnode.text)
        if old in replacements:
            urlnode.text = Path(replacements[old]).resolve().as_uri()
    resolved = sum(os.path.isfile(g.path) for g in missing)
    report(f'{resolved}/{len(missing)} fontes de análise localizadas por nome.')


def sync(project: Project, reference_track: str = 'auto', media_folder: str = '',
         threshold: float = AUTO_STRONG_SCORE,
         progress: Callable[[int, str], None] = lambda pct, msg: None) -> dict:
    """Sync independent takes using a pairwise audio-evidence graph.

    Each graph edge supplies a measured relative start time. Connected components
    are validated against same-camera chronology and packed in output order.
    There is no compulsory main camera and no end-user confidence setting.
    Takes without a verified independent overlap are kept in the red tail.
    """
    if not shutil.which('ffmpeg'):
        raise RuntimeError('FFmpeg não encontrado. Instale com: brew install ffmpeg')
    available = [t['id'] for t in project.tracks()]
    cache = AudioCache(lambda msg: progress(-1, msg))
    locate_media(project, media_folder, lambda msg: progress(-1, msg))
    project._export_cache = None
    groups = project.groups
    for clip in project.clips:
        clip.new_start = None
    for g in groups:
        g.assigned = None
        g.status = 'pending'
        g.match_score = 0.0
        g.matched_to = ''
        g.reason = ''
        g.verified_partner_ids.clear()
    if not groups:
        raise ValueError('O XML não contém takes para sincronizar.')

    # Keep old API fields for reports, without allowing selection in the UI.
    track_groups = defaultdict(list)
    for g in groups:
        for track in {c.track_key for c in g.members}:
            track_groups[track].append(g)
    ranking = sorted(track_groups, key=lambda track: (
        sum(g.duration for g in track_groups[track]), track.startswith('video-')), reverse=True)
    reference_track = (reference_track if reference_track in available else
                       next((t for t in ranking if t.startswith('video-')), ranking[0]))

    def independent(a, b):
        # Premiere tracks are editing lanes, not unique physical recording sources.
        # In particular, an independent .wav soundtrack may share A1 with the
        # camera's scratch audio, and videos from several cameras may share V1.
        # Only clips taken from the SAME underlying file are non-independent.
        # Linked video/scratch audio has already been consolidated into a Group.
        # Never count a file against itself merely because it occurs twice in XML.
        if not a.path or not b.path:
            return False
        return os.path.realpath(a.path) != os.path.realpath(b.path)

    # Decode each recording once. Missing/silent media will be explicitly
    # reported instead of showing an incorrect green reference clip.
    features = {}
    invalid = {}
    progress(3, 'Lendo o áudio das gravações...')
    for i,g in enumerate(groups):
        features[g.gid] = cache.features(g.path, g.source_in, g.duration)
        if features[g.gid] is None:
            invalid[g.gid] = ('Arquivo de mídia não localizado' if not os.path.isfile(g.path)
                              else 'Áudio indisponível, silencioso ou muito curto')
        progress(3 + int(17 * (i+1)/len(groups)), f'Extraindo assinaturas de áudio · {i+1}/{len(groups)}')

    # All distinct recording sources, not just comparisons to one track.
    # A1/V1 are containers and cannot identify a physical camera or recorder.
    # Independent WAV references on A1 are eligible even when A1 also contains
    # the cameras' linked scratch audio. Transitive bridges allow A ↔ B ↔ C.
    edges = []
    comparisons = 0
    eligible = [(a,b) for i,a in enumerate(groups) for b in groups[i+1:]
                if independent(a,b) and features[a.gid] is not None and features[b.gid] is not None]
    for i,(a,b) in enumerate(eligible):
        answer = correlate_features(features[a.gid], features[b.gid])
        comparisons += 1
        if answer is not None:
            lag, score = answer
            unique = getattr(answer,'margin',1.)
            if math.isfinite(lag) and math.isfinite(score) and score >= threshold \
                    and (unique >= .065 or (score >= .90 and unique >= .03)):
                # Correlations with at least 6 s of overlap are intrinsically
                # safer than short chance alignments, so use duration as tie-break.
                length = max(0., min(a.duration, b.duration))
                weight = score + min(0.08, 0.018 * math.log1p(length / 6))
                edges.append((weight, score, a.gid, b.gid, lag))
        if i % 10 == 0 or i == len(eligible)-1:
            progress(20 + int(50*(i+1)/max(1,len(eligible))),
                     f'Comparando áudio entre todas as câmeras · {i+1}/{len(eligible)}')

    ids = [g.gid for g in groups]
    by_id = {g.gid:g for g in groups}
    components = {gid:{gid:0.0} for gid in ids}  # positions relative to component origin
    parent = {gid:gid for gid in ids}
    evidence = defaultdict(list)
    blocked_chronology = set()
    contradictory = set()

    def root_of(gid):
        while parent[gid] != gid:
            parent[gid] = parent[parent[gid]]
            gid = parent[gid]
        return gid

    def chrono_compatible(left, right):
        """Don't reverse/displace takes originally sequenced on one track.

        Takes from the same track can be consecutively placed in the source XML
        even when originally recorded at unrelated times. This constraint only
        applies when the graph proposes that they overlap inside one concert.
        """
        for u, pu in left.items():
            for v, pv in right.items():
                gu, gv = by_id[u], by_id[v]
                if independent(gu, gv):
                    continue
                # Non-independent can also mean identical source URL; those
                # should never count as corroborating audio evidence.
                if gu.start <= gv.start:
                    if pu + gu.duration > pv + .25:
                        return False
                else:
                    if pv + gv.duration > pu + .25:
                        return False
        return True

    for weight,score,aid,bid,lag in sorted(edges, reverse=True):
        ar,br=root_of(aid),root_of(bid)
        acoords,bcoords=components[ar],components[br]
        # b-start = a-start + lag, according to correlate_features.
        shift = acoords[aid] + lag - bcoords[bid]
        if ar == br:
            if abs(shift) <= .35:
                evidence[aid].append((bid,score))
                evidence[bid].append((aid,score))
            else:
                contradictory.add(aid);contradictory.add(bid)
            continue
        shifted = {k:v+shift for k,v in bcoords.items()}
        if not chrono_compatible(acoords,shifted):
            blocked_chronology.add(aid);blocked_chronology.add(bid)
            continue
        parent[br]=ar
        acoords.update(shifted)
        components.pop(br)
        evidence[aid].append((bid,score))
        evidence[bid].append((aid,score))

    progress(81, 'Validando pares e alinhando os takes...')
    # Build position-consistent verified components. Each take must have a
    # direct accepted edge to another independent recorded take.
    verified = [coords for coords in components.values() if len(coords) > 1]
    verified.sort(key=lambda comp: min(by_id[k].start for k in comp))
    cursor = 0.0
    for block_index, coords in enumerate(verified):
        # A component is a complete multicam block. Calculate its bounding
        # interval from EVERY real clip (video, linked scratch audio, etc.),
        # not just the Group anchor / nominal duration. Linked clips may
        # begin slightly before or finish after their group's main video.
        block_start = min(
            relative + clip.start - by_id[gid].start
            for gid, relative in coords.items()
            for clip in by_id[gid].members
        )
        block_end = max(
            relative + clip.start - by_id[gid].start + clip.duration
            for gid, relative in coords.items()
            for clip in by_id[gid].members
        )
        # Preserve the first block's original timecode when possible. Every
        # subsequent independent block starts only after the previous block's
        # actual final clip plus 10 seconds; synchronized offsets inside each
        # block stay untouched.
        if block_index == 0:
            origin = next((g for g in groups if g.gid in coords and
                           any(c.track_key == reference_track for c in g.members)),
                          by_id[next(iter(coords))])
            shift = max(origin.start - coords[origin.gid], -block_start)
        else:
            shift = cursor + VERIFIED_BLOCK_GAP_SECONDS - block_start
        for gid, relative in coords.items():
            g = by_id[gid]
            g.assigned = relative + shift
            g.status = 'synced'
            partners = evidence[gid]
            if partners:
                g.match_score = max(v for _, v in partners)
                peer = by_id[max(partners, key=lambda x: x[1])[0]]
                g.matched_to = peer.title
                g.reason = 'Sincronizado: áudio confirmado por outra gravação'
                g.verified_partner_ids.update(x for x, _ in partners)
        cursor = max(cursor, block_end + shift)

    matched_end=cursor
    tail_start=max(0.0,matched_end)+2 if verified else 0.0
    cursor=tail_start
    isolated=0
    for g in sorted((g for g in groups if g.status!='synced'),key=lambda x:(x.start,x.gid)):
        g.assigned=cursor
        cursor += max(c.duration for c in g.members)+1
        g.status='unmatched'
        isolated += 1
        if g.gid in invalid:
            g.reason=invalid[g.gid]
        elif g.gid in blocked_chronology:
            g.reason='Posição incompatível com a ordem dos clipes'
        elif g.gid in contradictory:
            g.reason='Áudio ambíguo entre diferentes takes'
        elif not any(independent(g,other) for other in groups if other is not g):
            g.reason='Take isolado: nenhuma outra fonte independente disponível'
        else:
            g.reason='Sem sobreposição de áudio confirmada com outra gravação'

    for g in groups:
        for clip in g.members:
            clip.new_start = clip.start + (g.assigned - g.start)

    collisions=[]
    for track in available:
        clips=sorted((c for c in project.clips if c.track_key==track),key=lambda c:c.new_start)
        last=-float('inf')
        for clip in clips:
            if clip.new_start < last - 1/project.fps:
                collisions.append(f'{track}: {clip.name}')
            last=max(last,clip.new_start+clip.duration)
    missing=[g.title for g in groups if not os.path.isfile(g.path)]
    verified_count=sum(g.status=='synced' for g in groups)
    base_count=sum(g.status=='synced' and any(c.track_key==reference_track for c in g.members) for g in groups)
    failed_count=len(groups)-verified_count
    clip_status={id(c):g.status for g in groups for c in g.members}
    progress(100,f'{verified_count}/{len(groups)} takes com áudio confirmado · {failed_count} para revisão (vermelho)')
    return {'synced':verified_count-base_count,'reference_groups':base_count,
            'total':len(groups),'comparisons':comparisons,'missing':missing[:30],
            'collisions':collisions[:100],'reference':reference_track,
            'unmatched':failed_count,'isolated':isolated,
            'verified_components':len(verified),'tail_start':round(tail_start,3) if failed_count else None,
            'timeline':[{'id':c.uid,'name':c.name,'kind':c.kind,'track':c.track_key,
                         'start':round(c.new_start,3),'duration':round(c.duration,3),
                         'status':clip_status[id(c)]} for c in project.clips[:1200]],
            'timeline_truncated':len(project.clips)>1200,
            'groups':[{'name':g.title,'track':', '.join(sorted({c.track_key for c in g.members})),
                       'old':round(g.start,3),'new':round(g.assigned,3),
                       'duration':round(max(c.duration for c in g.members),3),
                       'score':round(g.match_score,3),'status':g.status,
                       'result':g.reason,'matched_to':g.matched_to} for g in groups]}

def report_csv(results: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Arquivo','Faixa','Início original (s)','Início sincronizado (s)','Confiança','Status','Correspondência'])
    for g in results['groups']:
        w.writerow([g['name'], g['track'], g['old'], g['new'], g['score'], g['result'], g['matched_to']])
    return ('\ufeff' + buf.getvalue()).encode('utf-8')
