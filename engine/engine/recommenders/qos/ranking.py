"""Phase B.3–5 — percentile rank, weighted aggregate, sort (pure functions).

Within a group: for each ranked resource, an app's percentile rank is the fraction
of group members whose representative value is <= its own (docs/01 Phase B.3). The
weighted rank is the weighted sum of those percentiles across resources (weights
default equal, per-resource configurable, docs/01 Phase B.4). Sorting by weighted
rank gives the within-group importance order (B.5).
"""
from __future__ import annotations

from typing import Optional


def percentile_ranks(values: list[float]) -> list[float]:
    """Per-element percentile rank: |{v_j <= v_i}| / n * 100, for each i (0..100)."""
    n = len(values)
    if n == 0:
        return []
    return [100.0 * sum(1 for v in values if v <= vi) / n for vi in values]


def normalized_weights(resources: list[str], weights: Optional[dict]) -> dict:
    """Per-resource weights normalized to sum 1 (equal by default; missing -> equal share)."""
    if not resources:
        return {}
    raw = {r: (float(weights[r]) if weights and r in weights and weights[r] is not None else 1.0)
           for r in resources}
    total = sum(raw.values())
    if total <= 0:
        return {r: 1.0 / len(resources) for r in resources}
    return {r: w / total for r, w in raw.items()}


def rank_group(members: list[dict], weights: Optional[dict] = None) -> list[dict]:
    """Rank a group's members by weighted percentile.

    `members`: [{"uid": str, "reps": {resource: value}}, ...]. All members must share
    the same ranked resource keys. Returns a list sorted by descending weighted score,
    each entry augmented with:
      weighted_rank  (0..100), weighted_score (0..1),
      per_resource   {resource: {"value", "percentile", "weight"}}
    """
    if not members:
        return []
    resources = list(members[0]["reps"].keys())
    w = normalized_weights(resources, weights)

    # Percentile rank per resource, across the group.
    pcts_by_res: dict = {}
    for res in resources:
        col = [m["reps"].get(res, 0.0) for m in members]
        pcts_by_res[res] = percentile_ranks(col)

    ranked = []
    for i, m in enumerate(members):
        per_resource = {
            res: {"value": m["reps"].get(res, 0.0), "percentile": pcts_by_res[res][i], "weight": w[res]}
            for res in resources
        }
        weighted_rank = sum(w[res] * pcts_by_res[res][i] for res in resources)  # 0..100
        ranked.append({
            "uid": m["uid"],
            "weighted_rank": weighted_rank,
            "weighted_score": weighted_rank / 100.0,
            "per_resource": per_resource,
        })

    # Descending importance; tie-break by uid for determinism.
    ranked.sort(key=lambda r: (-r["weighted_rank"], r["uid"]))
    return ranked
