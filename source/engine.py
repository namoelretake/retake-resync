"""ReSync - automatic graph-based Final Cut Pro 7 XML audio sync engine.

Keeps original media untouched. Repositions timeline clipitems in a fresh XML tree.
Requires Python 3.10+, numpy and ffmpeg in PATH.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np


FEATURE_FPS = 50
AUDIO_RATE = 16000
HOP = AUDIO_RATE // FEATURE_FPS
NFFT = 1024
MUSIC_IDENTITY_BINS = 12
# Valores internos: o editor não precisa configurar graus de confiança.
AUTO_STRONG_SCORE = 0.46
AUTO_IDENTITY_MIN_SCORE = 0.65
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
        """Export moved clips into the fewest compatible collision-free tracks.

        Interval-partition video/audio independently across ALL source tracks,
        reusing free lanes globally. Preserve per-track audio routing, other
        track metadata, opaque transitions, and FCP7 link track/clip indices.
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
        self.tracks_compacted = 0
        clip_position = {}  # uid -> (mediatype, 1-based track, 1-based clip index)
        for kind in ('video', 'audio'):
            media = self.seq.find(f'./media/{kind}')
            if media is None:
                continue

            original_tracks = list(media.findall('track'))
            kind_clips = [c for c in self.clips if c.kind == kind]
            parsed_element_ids = {id(c.element) for c in kind_clips}
            # Distinct source tracks can share a destination lane when they
            # have equivalent channel/enable/lock/format metadata. In
            # particular, do NOT merge L/R audio channel tracks with distinct
            # outputchannelindex values.
            def metadata_key(track):
                template = copy.deepcopy(track)
                for child in list(template):
                    if child.tag in ('clipitem', 'transitionitem'):
                        template.remove(child)
                for node in template.iter():
                    if node.text is not None and not node.text.strip():
                        node.text = None
                    if node.tail is not None and not node.tail.strip():
                        node.tail = None
                return ET.tostring(template, encoding='utf-8')

            keys = {}
            protected = set()
            for index, track in enumerate(original_tracks, 1):
                # Unparsed clipitems and transition XML must stay on the
                # original track. Isolate these tracks from global reuse so
                # their special content is never dropped or moved elsewhere.
                opaque = any(
                    node.tag == 'transitionitem' or
                    (node.tag == 'clipitem' and id(node) not in parsed_element_ids)
                    for node in track
                )
                if opaque:
                    protected.add(index)
                    keys[index] = ('protected', index)
                else:
                    keys[index] = ('compatible', metadata_key(track))

            # Interval-partition ALL compatible source tracks together, not
            # each old track individually. With half-open [start,end) frame
            # intervals, one track is created only when every compatible
            # destination lane is occupied. This minimizes track count per
            # metadata class while keeping each clip's timecode unchanged.
            lanes = []
            for c in sorted(kind_clips, key=lambda c: (
                    int(c.element.findtext('start')),
                    int(c.element.findtext('end')), c.track_no, c.uid)):
                st = int(c.element.findtext('start'))
                en = int(c.element.findtext('end'))
                key = keys[c.track_no]
                reusable = [
                    lane for lane in lanes
                    if lane['key'] == key and lane['end'] <= st
                ]
                # Prefer the lane previously used by this original source
                # track when possible, for a more familiar editing layout.
                lane = next((lane for lane in reusable
                             if lane['last_source'] == c.track_no), None)
                if lane is None and reusable:
                    lane = min(reusable, key=lambda lane: lane['index'])
                if lane is None:
                    lane = {'key': key, 'index': len(lanes),
                            'source': c.track_no, 'last_source': c.track_no,
                            'end': en, 'clips': []}
                    lanes.append(lane)
                lane['clips'].append(c)
                lane['end'] = en
                lane['last_source'] = c.track_no

            # Keep transition-only or other nonstandard original tracks
            # without parsed clips; silently deleting them would lose XML.
            for i in sorted(protected):
                if not any(lane['source'] == i for lane in lanes):
                    lanes.append({'key': keys[i], 'index': len(lanes),
                                  'source': i, 'last_source': i,
                                  'end': 0, 'clips': []})

            new_tracks = []
            protected_written = set()
            for lane in lanes:
                original_index = lane['source']
                original_track = original_tracks[original_index - 1]
                track = copy.deepcopy(original_track)
                first_protected_lane = (
                    original_index in protected and
                    original_index not in protected_written
                )
                if first_protected_lane:
                    protected_written.add(original_index)
                # Strip old parsed clipitems, preserving any *unparsed*
                # clipitems/transitionitems only in their protected lane.
                for original_node, copied_node in zip(
                        list(original_track), list(track)):
                    if original_node.tag == 'clipitem':
                        if (id(original_node) in parsed_element_ids or
                                not first_protected_lane):
                            track.remove(copied_node)
                    elif original_node.tag == 'transitionitem':
                        if not first_protected_lane:
                            track.remove(copied_node)
                # Special protected lanes may retain opaque clipitems that
                # precede the moved clips. They count toward FCP7 clipindex.
                retained_count = len(track.findall('clipitem'))
                for clip_idx, c in enumerate(lane['clips'], retained_count + 1):
                    track.append(c.element)
                    # FCP7 link references must point to the newly assigned
                    # lane/clip index; linked video/audio stays connected.
                    clip_position[c.uid] = (kind, len(new_tracks) + 1, clip_idx)
                new_tracks.append(track)

            # Preserve any non-track <media> children (including format).
            old_children = list(media)
            first = next((i for i, node in enumerate(old_children)
                          if node.tag == 'track'), len(old_children))
            for track in original_tracks:
                media.remove(track)
            for offset, track in enumerate(new_tracks):
                media.insert(first + offset, track)
            self.lanes_added += max(0, len(new_tracks) - len(original_tracks))
            self.tracks_compacted += max(0, len(original_tracks) - len(new_tracks))

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
    # Bump this whenever feature extraction changes. Files contain signatures,
    # never decoded audio, and can always be recreated from the source media.
    CACHE_VERSION = 3
    MAX_CACHE_BYTES = 512 * 1024 * 1024

    def __init__(self, report: Callable[[str], None] = lambda x: None,
                 cache_dir: str | Path | bool | None = None):
        self.cache: dict[tuple, np.ndarray | None] = {}
        self._verification: dict[int, np.ndarray] = {}
        self.report = report
        self._disk_cache_bytes: int | None = None
        self._disk_cache_lock = threading.Lock()
        self._cache_dir: Path | None = None
        if cache_dir is not False and os.environ.get('RESYNC_DISABLE_CACHE') != '1':
            try:
                default = (Path.home() / 'Library' / 'Caches' if sys.platform == 'darwin'
                           else Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))))
                self._cache_dir = Path(cache_dir or os.environ.get('RESYNC_CACHE_DIR')
                                       or default / 'ReSync' / 'audio-features')
            except (TypeError, ValueError, OSError):
                pass
        decoder = shutil.which('ffmpeg')
        self._decoder_identity = self._source_key(decoder, 0, 0) if decoder else None
        self.window = np.hanning(NFFT).astype(np.float32)
        # Log-spaced bands from voices through cymbals; old version discarded
        # everything above 3.5 kHz and compressed the sound into only 5 bands.
        edges = np.geomspace(100, 7400, 15)
        bins = np.round(edges / AUDIO_RATE * NFFT).astype(int)
        self.band_slices = [(max(1,int(x)), max(int(x)+1,int(y)))
                            for x,y in zip(bins[:-1], bins[1:])]
        # Keep raw musical identity separate from the gain-normalized onset
        # features. Different songs can share attacks/rhythm without sharing
        # their pitch content. Reuse the existing FFT rather than decode again.
        frequencies = np.fft.rfftfreq(NFFT, 1 / AUDIO_RATE)
        self._identity_mapping = np.zeros((len(frequencies), MUSIC_IDENTITY_BINS), dtype=np.float32)
        valid = (frequencies >= 180) & (frequencies <= 4000)
        indices = np.flatnonzero(valid)
        notes = np.round(MUSIC_IDENTITY_BINS * np.log2(frequencies[valid] / 440)).astype(int) % MUSIC_IDENTITY_BINS
        self._identity_mapping[indices, notes] = 1

    def verification_features(self, feat: np.ndarray | None) -> np.ndarray | None:
        """Return time-aligned tonal identity without changing public Nx15 features."""
        return self._verification.get(id(feat)) if feat is not None else None

    @staticmethod
    def _source_key(path: str, sec: float, duration: float) -> tuple | None:
        try:
            st = os.stat(path)
            if not os.path.isfile(path):
                return None
            # Match the actual seek/duration arguments sent to FFmpeg. Include
            # ctime as well as mtime so replacing or editing a file invalidates
            # its signatures even if a copy operation preserves its mtime.
            return (os.path.realpath(path), st.st_dev, st.st_ino, st.st_size,
                    st.st_mtime_ns, st.st_ctime_ns, f'{max(0, sec):.4f}',
                    f'{duration:.4f}')
        except (OSError, ValueError, OverflowError):
            return None

    def _cache_path(self, key: tuple) -> Path | None:
        if self._cache_dir is None:
            return None
        identity = (self.CACHE_VERSION, np.__version__, AUDIO_RATE, FEATURE_FPS, NFFT, HOP,
                    MUSIC_IDENTITY_BINS, tuple(self.band_slices), self._decoder_identity, key)
        digest = hashlib.sha256(repr(identity).encode('utf-8', 'surrogatepass')).hexdigest()
        return self._cache_dir / (digest + '.npy')

    def _load_features(self, path: Path | None, duration: float) -> np.ndarray | None:
        if path is None:
            return None
        try:
            with path.open('rb') as stream:
                # Inspect a bounded header before NumPy can allocate according
                # to an untrusted/corrupted shape stored on disk.
                if np.lib.format.read_magic(stream) != (1, 0):
                    return None
                shape, fortran, dtype = np.lib.format.read_array_header_1_0(
                    stream, max_header_size=1024)
                feature_columns = len(self.band_slices) + 1
                total_columns = feature_columns + MUSIC_IDENTITY_BINS
                # Cap before multiplying a very large but finite duration.
                max_frames = int(min(duration, self.MAX_CACHE_BYTES / 4 / total_columns / FEATURE_FPS)
                                 * FEATURE_FPS) + 2
                if (dtype != np.dtype(np.float32) or fortran or len(shape) != 2
                        or shape[1] != total_columns
                        or not 100 <= shape[0] <= max_frames):
                    return None
                payload_bytes = math.prod(shape) * dtype.itemsize
                if (payload_bytes > self.MAX_CACHE_BYTES
                        or os.fstat(stream.fileno()).st_size != stream.tell() + payload_bytes + 32):
                    return None
                stream.seek(0)
                combined = np.load(stream, allow_pickle=False)
                checksum = stream.read(32)
            # A finite bit-flip can still change the best correlation peak.
            # Verify the exact payload, rather than trusting finite values alone.
            if (checksum == hashlib.sha256(memoryview(combined).cast('B')).digest()
                    and np.isfinite(combined).all()):
                # Return contiguous arrays: a strided Nx15 view would preserve
                # values but add copying to subsequent correlation FFTs.
                feat = combined[:, :feature_columns].copy()
                fingerprint = combined[:, feature_columns:].copy()
                self._verification[id(feat)] = fingerprint
                return feat
        except (OSError, ValueError, TypeError, OverflowError, EOFError):
            pass
        return None

    def _trim_disk_cache(self):
        entries = []
        total = 0
        abandoned_before = time.time() - 24 * 60 * 60
        try:
            for item in self._cache_dir.iterdir():
                if item.is_symlink():
                    continue
                if item.name.startswith('resync-') and item.name.endswith('.tmp'):
                    try:
                        if item.is_file() and item.stat().st_mtime < abandoned_before:
                            item.unlink()
                    except OSError:
                        pass
                    continue
                if not re.fullmatch(r'[0-9a-f]{64}\.npy', item.name):
                    continue
                try:
                    st = item.stat()
                    entries.append((st.st_mtime_ns, item, st.st_size))
                    total += st.st_size
                except OSError:
                    continue
            for _, item, size in sorted(entries):
                if total <= self.MAX_CACHE_BYTES:
                    break
                try:
                    item.unlink()
                    total -= size
                except OSError:
                    continue
            self._disk_cache_bytes = total
        except OSError:
            self._disk_cache_bytes = 0

    def _save_features(self, path: Path | None, feat: np.ndarray, fingerprint: np.ndarray):
        if path is None or feat.nbytes + fingerprint.nbytes + 160 > self.MAX_CACHE_BYTES:
            return
        temporary = None
        try:
            combined = np.column_stack((feat, fingerprint))
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Each worker/process gets a distinct temporary file. Readers only
            # see a complete .npy after its atomic replacement.
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='resync-', suffix='.tmp',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                np.save(stream, combined, allow_pickle=False)
                stream.write(hashlib.sha256(memoryview(combined).cast('B')).digest())
            os.replace(temporary, path)
            temporary = None
            with self._disk_cache_lock:
                if self._disk_cache_bytes is None:
                    self._trim_disk_cache()
                else:
                    self._disk_cache_bytes += path.stat().st_size
                    if self._disk_cache_bytes > self.MAX_CACHE_BYTES:
                        self._trim_disk_cache()
        except (OSError, ValueError):
            # Cache availability must never prevent a sync (read-only disks,
            # disconnected volumes, full disks and concurrent eviction).
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def features(self, path: str, sec: float, duration: float) -> np.ndarray | None:
        if not math.isfinite(sec) or not math.isfinite(duration) or duration < 2:
            return None
        key = self._source_key(path, sec, duration)
        if key is None:
            return None
        if key in self.cache:
            return self.cache[key]
        cache_path = self._cache_path(key)
        cached = self._load_features(cache_path, duration)
        if cached is not None:
            self.cache[key] = cached
            self.report(f'Áudio reutilizado: {os.path.basename(path)}')
            return cached
        self.report(f'Analisando áudio: {os.path.basename(path)}')
        args = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-ss', f'{max(0,sec):.4f}',
                '-i', path, '-t', f'{duration:.4f}', '-map', '0:a:0', '-vn', '-ac', '1',
                '-ar', str(AUDIO_RATE), '-f', 'f32le', '-']
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Process audio in 4MB chunks. A 3-hour reference remains memory efficient.
        remain = np.empty(0, dtype=np.float32)
        segments = []
        identity_segments = []
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
                chroma = np.sqrt(spec @ self._identity_mapping)
                chroma = np.maximum(chroma - np.median(chroma, axis=1, keepdims=True), 0)
                chroma /= np.linalg.norm(chroma, axis=1, keepdims=True) + 1.e-8
                identity_segments.append(chroma)
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
        fingerprint = np.concatenate(identity_segments, axis=0)
        self._verification[id(feat)] = fingerprint
        if self._source_key(path, sec, duration) == key:
            self._save_features(cache_path, feat, fingerprint)
        return feat


class CorrelationMatch(tuple):
    """(relative offset, correlation) plus evidence of a distinct unique peak."""
    def __new__(cls, lag, score, margin, runnerup):
        obj=super().__new__(cls, (lag,score))
        obj.margin=margin
        obj.runnerup=runnerup
        return obj


class _CorrelationCache:
    """Bounded, per-job transforms of immutable audio feature arrays.

    Keep the source reference with each entry so a Python object ID cannot be
    reused for a different recording during the session. Entries are bounded by
    both array bytes and count; long recordings still work when their transform
    is too large to retain.
    """
    def __init__(self, max_bytes: int, max_entries: int = 256):
        self.max_bytes = max(0, max_bytes)
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.bytes = 0

    def get(self, source, fft_size=None):
        key = (id(source), fft_size)
        if key in self.entries:
            _, value = self.entries[key]
            self.entries.move_to_end(key)
            return value
        if fft_size is None:
            value = np.r_[0., np.cumsum((source * source).sum(axis=1),
                                       dtype=np.float64)]
        else:
            value = np.fft.rfft(source, n=fft_size, axis=0)
        if value.nbytes <= self.max_bytes:
            while self.entries and (self.bytes + value.nbytes > self.max_bytes or
                                    len(self.entries) >= self.max_entries):
                _, (_, evicted) = self.entries.popitem(last=False)
                self.bytes -= evicted.nbytes
            self.entries[key] = (source, value)
            self.bytes += value.nbytes
        return value


_correlation_cache = ContextVar('resync_correlation_cache', default=None)


@contextmanager
def correlation_session(max_bytes: int = 128 * 1024 * 1024):
    """Reuse FFTs/energies within one sync job, never between unrelated jobs.

    Feature arrays must remain unchanged inside the session. The public pair
    matcher keeps its original two-argument API for callers and test doubles.
    """
    cache = _CorrelationCache(max_bytes)
    token = _correlation_cache.set(cache)
    try:
        yield cache
    finally:
        _correlation_cache.reset(token)
        cache.entries.clear()
        cache.bytes = 0


def correlate_features(a: np.ndarray, b: np.ndarray) -> tuple[float, float] | None:
    """Returns (b-start minus a-start in seconds, normalized correlation score)."""
    n, m = len(a), len(b)
    if min(n, m) < FEATURE_FPS * 2:
        return None
    fft_size = 1 << (n + m - 2).bit_length()
    cache = _correlation_cache.get()
    fft_a = (cache.get(a, fft_size) if cache is not None else
             np.fft.rfft(a, n=fft_size, axis=0))
    fft_b = (cache.get(b, fft_size) if cache is not None else
             np.fft.rfft(b, n=fft_size, axis=0))
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
    sq_a = (cache.get(a) if cache is not None else
            np.r_[0., np.cumsum((a * a).sum(axis=1), dtype=np.float64)])
    sq_b = (cache.get(b) if cache is not None else
            np.r_[0., np.cumsum((b * b).sum(axis=1), dtype=np.float64)])
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


def confirm_audio_identity(cache: AudioCache, a: np.ndarray, b: np.ndarray,
                           lag: float) -> bool:
    """Confirm that an envelope match also shares spectral/pitch identity.

    Per-band standardization is useful for finding offsets through different
    microphones, but can make different notes with the same rhythm look alike.
    The secondary descriptor retains the spectral identity of each instant and
    is checked only at the candidate offset. Silence supplies no evidence.
    """
    if not math.isfinite(lag):
        return False
    left, right = cache.verification_features(a), cache.verification_features(b)
    if left is None or right is None:
        return False
    shift = round(lag * FEATURE_FPS)
    first_a, first_b = max(0, shift), max(0, -shift)
    count = min(len(left) - first_a, len(right) - first_b)
    if count <= 0:
        return False
    x, y = left[first_a:first_a + count], right[first_b:first_b + count]
    active = ((x * x).sum(axis=1) > .25) & ((y * y).sum(axis=1) > .25)
    if np.count_nonzero(active) < 3:
        return False
    score = float(np.mean((x[active] * y[active]).sum(axis=1)))
    return math.isfinite(score) and score >= AUTO_IDENTITY_MIN_SCORE


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
    # Worker callbacks share the same status writer (including the headless
    # Premiere worker). Serialize callbacks so status JSON remains atomic.
    progress_lock = threading.Lock()

    def report(percent, message):
        with progress_lock:
            progress(percent, message)

    cache = AudioCache(lambda msg: report(-1, msg))
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

    source_paths = {}
    for g in groups:
        try:
            source_paths[g.gid] = os.path.realpath(g.path) if g.path else ''
        except (OSError, ValueError):
            # A malformed media URL is unavailable, not a reason to abort all
            # other takes while precomputing their file identities.
            source_paths[g.gid] = g.path

    def independent(a, b):
        # Premiere tracks are editing lanes, not unique physical recording sources.
        # In particular, an independent .wav soundtrack may share A1 with the
        # camera's scratch audio, and videos from several cameras may share V1.
        # Only clips taken from the SAME underlying file are non-independent.
        # Linked video/scratch audio has already been consolidated into a Group.
        # Never count a file against itself merely because it occurs twice in XML.
        if not a.path or not b.path:
            return False
        return source_paths[a.gid] != source_paths[b.gid]

    # Decode each recording once. Missing/silent media will be explicitly
    # reported instead of showing an incorrect green reference clip.
    features = {}
    invalid = {}
    progress(3, 'Lendo o áudio das gravações...')
    # A segment may occur in several unlinked XML clips. Decode it once and
    # share the read-only features; only independent files count as evidence.
    segments = defaultdict(list)
    for g in groups:
        key = (source_paths[g.gid], f'{max(0, g.source_in):.4f}', f'{g.duration:.4f}')
        segments[key].append(g)
    try:
        requested_workers = int(os.environ.get('RESYNC_WORKERS', '4'))
    except ValueError:
        requested_workers = 4
    workers = min(len(segments), max(1, os.cpu_count() or 1),
                  max(1, min(4, requested_workers)))
    completed = 0

    def accept(segment_groups, value):
        nonlocal completed
        for g in segment_groups:
            features[g.gid] = value
            if value is None:
                invalid[g.gid] = ('Arquivo de mídia não localizado' if not os.path.isfile(g.path)
                                  else 'Áudio indisponível, silencioso ou muito curto')
        completed += len(segment_groups)
        report(3 + int(17 * completed / len(groups)),
               f'Extraindo assinaturas de áudio · {completed}/{len(groups)}')

    if workers == 1:
        for segment_groups in segments.values():
            g = segment_groups[0]
            accept(segment_groups, cache.features(g.path, g.source_in, g.duration))
    else:
        # Bounded concurrency speeds independent FFmpeg/NumPy work without
        # starting one process per take or changing graph comparison order.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='resync-audio') as pool:
            pending = {}
            for segment_groups in segments.values():
                g = segment_groups[0]
                pending[pool.submit(cache.features, g.path, g.source_in, g.duration)] = segment_groups
            for future in as_completed(pending):
                accept(pending[future], future.result())

    # All distinct recording sources, not just comparisons to one track.
    # A1/V1 are containers and cannot identify a physical camera or recorder.
    # Independent WAV references on A1 are eligible even when A1 also contains
    # the cameras' linked scratch audio. Transitive bridges allow A ↔ B ↔ C.
    edges = []
    comparisons = 0
    identity_rejected = set()
    eligible = [(a,b) for i,a in enumerate(groups) for b in groups[i+1:]
                if independent(a,b) and features[a.gid] is not None and features[b.gid] is not None]
    with correlation_session():
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
                    if confirm_audio_identity(cache, features[a.gid], features[b.gid], lag):
                        edges.append((weight, score, a.gid, b.gid, lag))
                    else:
                        identity_rejected.update((a.gid, b.gid))
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
        elif g.gid in identity_rejected:
            g.reason='Ritmo parecido, mas áudio diferente: sincronismo não confirmado'
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

