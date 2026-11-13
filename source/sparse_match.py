"""Suggest audio pairs worth comparing, without decoding or accepting a match.

The input is the existing 50 Hz analysis data. Repeated rhythm alone is weak
evidence: common keys are discarded, and a pair needs several distinct events
agreeing on a time shift. This is an approximate index; the full matcher must
verify each suggestion and retain an exhaustive fallback.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np


_FPS = 50
_MIN_FRAMES = 6 * _FPS
_MAX_FRAMES = 160 * _FPS
_MAX_CLIPS = 1024
_MAX_LANDMARKS = 440
_MAX_ENTRIES = 100_000
_MAX_BUCKET = 10
_MAX_DENSITY = .30
_MIN_COVERAGE = .70
_LAG_BUCKET = 5
_MIN_VOTES = 3

# Two independent rank patterns. Comparing energy at nearby instants instead
# of quantizing its absolute amplitude tolerates level changes and mic EQ.
# Deliberately leave the anchor and its immediate neighborhood out: by
# construction an anchor is a peak, so those comparisons carry little entropy.
_PATTERNS = np.array([
    [(-30, -20), (-27, 11), (-23, 18), (-19, -9), (-18, 29), (-15, 5),
     (-13, 25), (-9, 15), (-6, 23), (5, 17), (9, 28), (15, 25)],
    [(-29, 20), (-26, -15), (-23, -8), (-21, 9), (-18, -7), (-17, 26),
     (-12, 16), (-10, 27), (-7, 6), (6, 13), (10, 22), (18, 30)],
], dtype=np.int32)
_KEY_BITS = _PATTERNS.shape[1]
_BIT_WEIGHTS = 1 << np.arange(_KEY_BITS, dtype=np.int32)
_RADIUS = int(np.abs(_PATTERNS).max())
_SMOOTHING = np.array([1, 2, 3, 2, 1], dtype=np.float32) / 9


def _landmarks(coarse: np.ndarray, tonal: np.ndarray | None):
    if (not isinstance(coarse, np.ndarray) or coarse.ndim != 2 or coarse.shape[1] != 15
            or not _MIN_FRAMES <= len(coarse) <= _MAX_FRAMES
            or not isinstance(tonal, np.ndarray) or tonal.shape != (len(coarse), 12)
            or not np.isfinite(coarse).all() or not np.isfinite(tonal).all()):
        return None
    envelope = np.convolve(coarse[:, :14].mean(axis=1), _SMOOTHING, mode='same')
    if float(np.std(envelope)) < .12:
        return None
    center = envelope[_RADIUS:-_RADIUS]
    peaks = ((center > envelope[_RADIUS-2:-_RADIUS-2])
             & (center > envelope[_RADIUS-1:-_RADIUS-1])
             & (center >= envelope[_RADIUS+1:-_RADIUS+1])
             & (center >= envelope[_RADIUS+2:-_RADIUS+2]))
    positions = np.flatnonzero(peaks) + _RADIUS
    if len(positions) < 10:
        return None
    if len(positions) > _MAX_LANDMARKS:
        # Keep landmarks spread across the entire clip rather than putting
        # every retained peak in its loudest passage.
        positions = positions[np.linspace(0, len(positions) - 1, _MAX_LANDMARKS, dtype=int)]
    # Isolated pitched energy is informative when confidently concentrated
    # in one class. Ordinary broadband and harmonically mixed frames use 0.
    notes = tonal[positions]
    strongest = notes.argmax(axis=1)
    dominant = notes[np.arange(len(positions)), strongest]
    runnerup = np.partition(notes, -2, axis=1)[:, -2]
    tone = np.where((dominant >= .72) & (dominant - runnerup >= .20), strongest + 1, 0)
    bits = (envelope[positions[:, None, None] + _PATTERNS[None, :, :, 0]]
            > envelope[positions[:, None, None] + _PATTERNS[None, :, :, 1]])
    signatures = (bits * _BIT_WEIGHTS).sum(axis=2, dtype=np.int32)
    namespace = tone[:, None] + 13 * np.arange(len(_PATTERNS), dtype=np.int32)
    keys = (signatures + (namespace << _KEY_BITS)).ravel()
    return keys, np.repeat(positions, len(_PATTERNS))


def _vote(keys, clips, frames, count):
    order = np.argsort(keys, kind='stable')
    keys, clips, frames = keys[order], clips[order], frames[order]
    _, frequency = np.unique(keys, return_counts=True)
    keep = np.repeat((frequency >= 2) & (frequency <= _MAX_BUCKET), frequency)
    keys, clips, frames = keys[keep], clips[keep], frames[keep]
    # Enumerate equal-key pairs in bulk by traversing at most _MAX_BUCKET
    # offsets in sorted order. Pathological repetitions cannot explode work.
    packed = []
    for offset in range(1, _MAX_BUCKET):
        if len(keys) <= offset:
            break
        same_key = (keys[:-offset] == keys[offset:]) & (clips[:-offset] != clips[offset:])
        if not same_key.any():
            continue
        ai, bi = clips[:-offset][same_key], clips[offset:][same_key]
        af, bf = frames[:-offset][same_key], frames[offset:][same_key]
        pair = np.minimum(ai, bi) * count + np.maximum(ai, bi)
        left, right = np.where(ai < bi, af, bf), np.where(ai < bi, bf, af)
        packed.append((pair.astype(np.int64) * _MAX_FRAMES + left) * _MAX_FRAMES + right)
    if not packed:
        return np.empty(0, dtype=np.int64)
    # One event may produce two keys. Deduplicate before counting agreement.
    events = np.unique(np.concatenate(packed))
    right = events % _MAX_FRAMES
    left = events // _MAX_FRAMES % _MAX_FRAMES
    pair = events // (_MAX_FRAMES * _MAX_FRAMES)
    lag = left - right + _MAX_FRAMES
    bucket_count = 2 * _MAX_FRAMES // _LAG_BUCKET + 2
    matches = []
    # Shift a second grid to reduce losses near an arbitrary lag boundary.
    for shift in (0, _LAG_BUCKET // 2):
        bins, votes = np.unique(pair * bucket_count + (lag + shift) // _LAG_BUCKET,
                                return_counts=True)
        matches.append(bins[votes >= _MIN_VOTES] // bucket_count)
    return np.unique(np.concatenate(matches))


def sparse_candidates(coarse: Sequence[np.ndarray], tonal: Sequence[np.ndarray | None], *,
                      min_clips: int = 24) -> list[tuple[int, int]] | None:
    """Return sorted unique zero-based pairs, or ``None`` for exhaustive matching.

    Short, long or ambiguous recordings are protected with all their pairs.
    Small, dense, and poorly indexed jobs also keep exhaustive matching.
    A suggestion is never evidence that two recordings match.
    """
    count = len(coarse)
    if not min_clips <= count <= _MAX_CLIPS or len(tonal) != count:
        return None
    pair_count = count * (count - 1) // 2
    if pair_count == 0:
        return None
    key_blocks, clip_blocks, frame_blocks = [], [], []
    protected = []
    entries = 0
    for index, (features, identity) in enumerate(zip(coarse, tonal)):
        result = _landmarks(features, identity)
        if result is None:
            protected.append(index)
            continue
        keys, frames = result
        entries += len(keys)
        if entries > _MAX_ENTRIES:
            return None
        key_blocks.append(keys)
        clip_blocks.append(np.full(len(keys), index, dtype=np.int32))
        frame_blocks.append(frames)
    unprotected = count - len(protected)
    protected_count = pair_count - unprotected * (unprotected - 1) // 2
    if not key_blocks or protected_count > _MAX_DENSITY * pair_count:
        return None
    candidates = _vote(np.concatenate(key_blocks), np.concatenate(clip_blocks),
                       np.concatenate(frame_blocks), count)
    if (len(candidates) + protected_count > _MAX_DENSITY * pair_count
            or len(np.unique(np.r_[candidates // count, candidates % count])) < _MIN_COVERAGE * unprotected):
        return None
    pairs = {(int(value // count), int(value % count)) for value in candidates}
    for left in protected:
        pairs.update((min(left, right), max(left, right)) for right in range(count) if left != right)
    return sorted(pairs)
