"""Optional offset refinement for a component of verified audio matches."""

from collections.abc import Hashable, Iterable, Mapping
import math
from typing import TypeVar

import numpy as np


Node = TypeVar("Node", bound=Hashable)
# The baseline is already usable. Bound the dense solve on unusually large
# components rather than slowing down export for an optional refinement.
_MAX_NODES = 128


def _bridge_groups(size, a, b):
    """Return connectivity, bridge indices, and groups connected without bridges."""
    graph = [[] for _ in range(size)]
    for i, (u, v) in enumerate(zip(a, b)):
        graph[u].append((v, i))
        graph[v].append((u, i))
    seen = [-1] * size
    low = [0] * size
    bridges = set()
    visited = 0

    def visit(u, incoming):
        nonlocal visited
        seen[u] = low[u] = visited
        visited += 1
        for v, edge in graph[u]:
            if edge == incoming:
                continue
            if seen[v] == -1:
                visit(v, edge)
                low[u] = min(low[u], low[v])
                if low[v] > seen[u]:
                    bridges.add(edge)
            else:
                low[u] = min(low[u], seen[v])

    visit(0, None)
    labels = [-1] * size
    for start in range(size):
        if labels[start] != -1:
            continue
        labels[start] = start
        stack = [start]
        while stack:
            for other, edge in graph[stack.pop()]:
                if edge not in bridges and labels[other] == -1:
                    labels[other] = start
                    stack.append(other)
    return visited == size, bridges, labels


def fit_offsets(
    coordinates: Mapping[Node, float],
    edges: Iterable[tuple[Node, Node, float, float]],
    *,
    anchor: Node | None = None,
    tolerance: float = 0.12,
) -> dict[Node, float]:
    """Refine one already accepted component, keeping its anchor at the old position.

    Edges are (a, b, lag_seconds, positive_confidence); each measures b - a.
    Pass only independently verified audio edges. A repeated pair gets one
    vote, and disagreeing measurements of that same pair are ignored.

    Trees, disconnected inputs, and ambiguous cycles keep their old offsets.
    Replacing a previously supported edge needs two edge-disjoint alternate
    paths in the new support graph, more supported pairs, and more weighted
    support. Sole bridges always retain their previous relative offsets. This
    changes positions only; it cannot split, merge, or verify components.
    """
    original = dict(coordinates)
    ids = list(original)
    size = len(ids)
    if not (3 <= size <= _MAX_NODES and math.isfinite(tolerance) and tolerance > 0):
        return original
    index = {gid: i for i, gid in enumerate(ids)}
    if anchor is not None and anchor not in index:
        return original
    fixed = index[anchor] if anchor is not None else 0
    base = np.array(list(original.values()), dtype=float)
    if not np.isfinite(base).all():
        return original

    pairs = {}
    for u, v, lag, confidence in edges:
        if u == v or u not in index or v not in index:
            continue
        if not (math.isfinite(lag) and math.isfinite(confidence) and confidence > 0):
            continue
        i, j = index[u], index[v]
        if i > j:
            i, j, lag = j, i, -lag
        pairs.setdefault((i, j), []).append((float(confidence), float(lag)))
    data = []
    for (i, j), measurements in sorted(pairs.items()):
        lags = [value for _, value in measurements]
        if max(lags) - min(lags) <= tolerance:
            weight = max(w for w, _ in measurements)
            best_lags = [lag for w, lag in measurements if w == weight]
            lag = min(best_lags) / 2 + max(best_lags) / 2
            data.append((i, j, lag, weight))
    if len(data) < size:
        return original  # A spanning tree has no redundant measurement.

    a, b = np.asarray([(i, j) for i, j, _, _ in data], dtype=int).T
    measurement = np.array([lag for _, _, lag, _ in data], dtype=float)
    confidence = np.array([weight for _, _, _, weight in data], dtype=float)
    # Pair scores are a relative confidence, not a calibrated variance. Cap
    # their ratio so no single high score can drown out several other cameras.
    confidence = np.maximum(confidence / confidence.max(), 0.25)
    with np.errstate(over="ignore", invalid="ignore"):
        target = measurement - (base[b] - base[a])
    if not np.isfinite(target).all():
        return original
    connected, bridges, _ = _bridge_groups(size, a, b)
    old_support = np.abs(target) <= tolerance
    baseline_connected, _, baseline_labels = _bridge_groups(size, a[old_support], b[old_support])
    if not connected or not baseline_connected:
        return original

    free = np.arange(size) != fixed
    scale = max(float(np.max(np.abs(target))), tolerance)
    unit_target = target / scale
    unit_tol = tolerance / scale
    if unit_tol < np.finfo(float).eps:
        return original
    bridge_target = unit_target.copy()
    bridge_target[list(bridges)] = 0.0

    def solve(weights, mask, goals):
        src, dest = a[mask], b[mask]
        val = weights * goals
        matrix = np.zeros((size, size), dtype=float)
        rhs = np.zeros(size, dtype=float)
        np.add.at(matrix, (src, src), weights)
        np.add.at(matrix, (dest, dest), weights)
        np.add.at(matrix, (src, dest), -weights)
        np.add.at(matrix, (dest, src), -weights)
        np.add.at(rhs, src, -val)
        np.add.at(rhs, dest, val)
        correction = np.zeros(size, dtype=float)
        correction[free] = np.linalg.solve(matrix[np.ix_(free, free)], rhs[free])
        return correction

    def residual(x):
        return x[b] - x[a] - unit_target

    def loss(error):
        # Cap the contribution of outliers when comparing with the baseline.
        return float(confidence @ np.minimum(np.abs(error) / unit_tol, 1.0) ** 2)

    all_edges = np.ones(len(data), dtype=bool)
    old_loss = loss(-unit_target)
    if old_loss <= 1e-6:
        return original
    old_weight = float(confidence[old_support].sum())
    old_count = int(old_support.sum())
    winner = None
    winner_rank = None
    hypotheses = [old_support]
    try:
        # Use both confidence-weighted and balanced starting points. The
        # Cauchy reweighting finds candidate inlier sets; the final fit below
        # uses the real pair weights on the selected inliers.
        for prior in (confidence, np.ones(len(data), dtype=float)):
            x = solve(prior, all_edges, bridge_target)
            for width in (2.0, 2.0, 1.0, 1.0, 1.0, 0.5, 0.5, 0.5):
                weights = prior / (1.0 + (residual(x) / (width * unit_tol)) ** 2)
                x = solve(weights, all_edges, bridge_target)
            mask = np.abs(residual(x)) <= unit_tol
            if not any(np.array_equal(mask, other) for other in hypotheses):
                hypotheses.append(mask)

        for mask in hypotheses:
            if mask.sum() < size:
                continue
            positions = np.flatnonzero(mask)
            inliers_connected, weak_links, _ = _bridge_groups(size, a[mask], b[mask])
            if not inliers_connected:
                continue
            goals = bridge_target[mask].copy()
            goals[list(weak_links)] = 0.0
            x = solve(confidence[mask], mask, goals)
            error = residual(x)
            support = np.abs(error) <= unit_tol
            # Do not accept a fit that disqualifies an edge used to compute it.
            if not support[positions].all():
                continue
            connected, _, labels = _bridge_groups(size, a[support], b[support])
            if not connected:
                continue
            count = int(support.sum())
            weight = float(confidence[support].sum())
            fit_loss = loss(error)
            if fit_loss >= old_loss - 1e-6:
                continue
            lost = old_support & ~support
            if lost.any():
                if count <= old_count or weight <= old_weight + 1e-9:
                    continue
                # In an undirected graph, sharing a group after removing all
                # bridges means these endpoints have at least two independent
                # paths. A lone alternative around a triangle cannot pick a
                # winner when the measurements contradict one another.
                if any(labels[u] != labels[v] for u, v in zip(a[lost], b[lost])):
                    continue
            else:
                added = mask & ~old_support
                # With no previously accepted edge outvoted, new input to a
                # fit must already have two paths of baseline corroboration.
                # In particular, a borderline conflicting triangle may not
                # "confirm" itself just by spreading one disputed lag.
                if any(baseline_labels[u] != baseline_labels[v] for u, v in zip(a[added], b[added])):
                    continue
                if np.any(support & ~old_support & (np.abs(target) > 2 * tolerance)):
                    continue
            rank = (weight, count, -fit_loss)
            if winner_rank is None or rank > winner_rank:
                winner, winner_rank = x, rank
    except np.linalg.LinAlgError:
        return original

    if winner is None:
        return original
    with np.errstate(over="ignore", invalid="ignore"):
        fitted = base + winner * scale
    if not np.isfinite(fitted).all():
        return original
    fitted[fixed] = base[fixed]
    return {gid: float(fitted[i]) for i, gid in enumerate(ids)}
