"""Phase A — Cluster Generator (pure functions).

Group "similar" apps by their *allocated-resource* vector so ranking compares like
with like (docs/01, docs/05 Phase A). Pipeline: effective-allocation feature build
-> per-dimension feature scaling (log-then-standardize by default) -> k-means. No DB
access here; the runner assembles the feature dicts and persists the groups.

k-selection is fixed/heuristic in M1 (`resolve_k`); silhouette-based auto selection
lands in M2 behind the same call.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np


def effective_value(requested: Optional[float], limit: Optional[float], max_util: Optional[float]) -> float:
    """Effective allocation for one (workload, resource): requested ?? limit ?? max-util ?? 0.

    The patent's fallback (docs/01 §Data.1, docs/03): use the request, else the limit,
    else max utilization over the last few cycles, else 0.
    """
    for v in (requested, limit, max_util):
        if v is not None:
            return float(v)
    return 0.0


def build_feature_matrix(feature_dicts: list[dict], resource_dims: list[str]) -> np.ndarray:
    """M×N matrix: row per workload, column per resource dimension (missing dim -> 0)."""
    if not feature_dicts:
        return np.zeros((0, len(resource_dims)), dtype=float)
    return np.array(
        [[float(fd.get(dim, 0.0)) for dim in resource_dims] for fd in feature_dicts],
        dtype=float,
    )


def scale_features(matrix: np.ndarray, strategy: str = "log_standardize") -> np.ndarray:
    """Standardize allocation dimensions before distance-based clustering.

    Allocation dims have wildly different scales/units (cpu millicores vs memory bytes
    vs gpu count), so an unscaled distance is dominated by the largest-magnitude dim.
    Default "log_standardize": log1p (tame heavy tails / big-app outliers) then z-score
    per column. Zero-variance columns collapse to 0 (they carry no separating signal).
    """
    if matrix.size == 0:
        return matrix
    x = matrix.astype(float)
    if strategy == "log_standardize":
        x = np.log1p(np.clip(x, 0.0, None))
    if strategy in ("log_standardize", "zscore"):
        mean = x.mean(axis=0)
        std = x.std(axis=0)
        std_safe = np.where(std > 0, std, 1.0)
        return (x - mean) / std_safe
    if strategy == "minmax":
        lo = x.min(axis=0)
        hi = x.max(axis=0)
        span = np.where(hi > lo, hi - lo, 1.0)
        return (x - lo) / span
    raise ValueError(f"unknown scaling strategy {strategy!r}")


def resolve_k(n_samples: int, cfg) -> int:
    """Heuristic (fixed-strategy / fallback) cluster count.

    Explicit `cfg.k` wins. Otherwise ~sqrt(n/2), clamped to [1, min(k_max, n)].
    """
    if n_samples <= 1:
        return 1
    if getattr(cfg, "k", 0):
        k = int(cfg.k)
    else:
        k = max(1, round(math.sqrt(n_samples / 2.0)))
    return max(1, min(k, cfg.k_max, n_samples))


def _n_unique_rows(scaled: np.ndarray) -> int:
    return len({tuple(round(v, 9) for v in row) for row in scaled}) if scaled.size else 0


def select_k_silhouette(scaled: np.ndarray, cfg) -> int:
    """Auto-select k by sweeping k in [k_min, k_max] and maximizing silhouette.

    Falls back to the heuristic `resolve_k` when the sample is too small or
    degenerate (fewer than 3 rows, or fewer distinct points than k_min) — silhouette
    is undefined there. Deterministic (fixed k-means seed). docs/05 "Decisions".
    """
    n = scaled.shape[0]
    uniq = _n_unique_rows(scaled)
    if n < 3 or uniq < 2:
        return 1 if uniq <= 1 else min(resolve_k(n, cfg), uniq)
    from sklearn.metrics import silhouette_score

    k_hi = min(cfg.k_max, n - 1, uniq)
    k_lo = max(2, cfg.k_min)
    best_k, best_score = 1, -1.0
    for k in range(k_lo, k_hi + 1):
        labels, _ = run_kmeans(scaled, k, cfg.kmeans_seed)
        if len(set(labels.tolist())) < 2:
            continue
        score = float(silhouette_score(scaled, labels))
        if score > best_score:
            best_k, best_score = k, score
    return best_k if best_k >= 2 else min(resolve_k(n, cfg), max(1, uniq))


def select_k(scaled: np.ndarray, cfg) -> int:
    """Dispatch on cfg.k_strategy to pick the cluster count."""
    if getattr(cfg, "k", 0):
        return resolve_k(scaled.shape[0], cfg)
    if cfg.k_strategy == "silhouette":
        return select_k_silhouette(scaled, cfg)
    return resolve_k(scaled.shape[0], cfg)


def run_kmeans(scaled: np.ndarray, k: int, seed: int = 0):
    """Assign each row to one of k clusters. Returns (labels, scaled_centroids).

    Deterministic: fixed random_state + n_init. k==1 (or degenerate) -> everyone in
    group 0. Imported lazily so importing the module doesn't require scikit-learn.
    """
    n = scaled.shape[0]
    if n == 0:
        return np.array([], dtype=int), np.zeros((0, scaled.shape[1]))
    if k <= 1:
        return np.zeros(n, dtype=int), scaled.mean(axis=0, keepdims=True)
    from sklearn.cluster import KMeans
    km = KMeans(n_clusters=k, random_state=seed, n_init=10)
    labels = km.fit_predict(scaled)
    return labels.astype(int), km.cluster_centers_


def cluster_workloads(feature_dicts: list[dict], resource_dims: list[str], cfg, k: Optional[int] = None) -> dict:
    """Full Phase A: returns labels + per-group centroids (scaled + original units).

    Result: {
      "labels": [int, ...] aligned to feature_dicts,
      "k": int,
      "resource_dims": [...],
      "centroids": {group_index: {resource: {"scaled": float, "original": float}}},
    }
    """
    matrix = build_feature_matrix(feature_dicts, resource_dims)
    scaled = scale_features(matrix, cfg.scaling)
    k_eff = k if k is not None else select_k(scaled, cfg)
    k_eff = max(1, min(k_eff, len(feature_dicts))) if feature_dicts else 1
    labels, _ = run_kmeans(scaled, k_eff, cfg.kmeans_seed)

    centroids: dict = {}
    for g in sorted(set(labels.tolist())):
        rows = matrix[labels == g]
        srows = scaled[labels == g]
        centroids[g] = {
            dim: {"scaled": float(srows[:, j].mean()), "original": float(rows[:, j].mean())}
            for j, dim in enumerate(resource_dims)
        }
    return {"labels": labels.tolist(), "k": k_eff, "resource_dims": resource_dims, "centroids": centroids}
