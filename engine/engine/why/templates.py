"""Deterministic "why" templates for QoS recommendations (docs/05 §Why).

Explains, per workload: which resources drove the rank (top percentiles), its peer
group, interaction weight, and the delta from its current QoS/priority. No LLM — the
optional hook in `why/llm.py` is scaffolded but off.
"""
from __future__ import annotations

from ..recommenders.qos import assign

_LABEL = {"interactions": "interactions"}


def _label(res: str) -> str:
    return _LABEL.get(res, res)


def qos_summary(*, pos, n, entry, recommended_qos, priority, identity,
                peers, comparison_scope) -> str:
    """One-line summary stored in qos_recommendations.summary_text."""
    drivers = sorted(entry["per_resource"].items(), key=lambda kv: -kv[1]["percentile"])
    top = ", ".join(f"{_label(r)} p{int(round(pr['percentile']))}" for r, pr in drivers[:2])

    cur = identity.get("current_qos")
    delta = ""
    if cur:
        d = assign.change_direction(cur, recommended_qos)
        delta = f" (was {cur}: {d})" if d != "unchanged" else f" (unchanged from {cur})"

    scope_note = "vs its allocation cluster" if comparison_scope == "within_group" else "across all clusters (util/alloc fraction)"
    peer_note = ""
    if peers:
        peer_note = f" Interacts with {len(peers)} peer(s), e.g. {peers[0].peer_workload}."
    return (f"Ranked #{pos + 1} of {n} {scope_note} (score {entry['weighted_score']:.2f}); "
            f"recommend {recommended_qos}, priority {priority}{delta}. "
            f"Top drivers: {top}.{peer_note}")
