"""QoS recommender runner — orchestrates Phase A + B and persists Tier-4 results.

The one code path shared by the CLI (`engine run`) and (later) the API. Reads
allocations + utilization + interactions from the state DB, clusters workloads by
their allocation vector (Phase A, within-group mode) or merges them and ranks on the
utilization/allocation fraction (cross-group mode), assigns a PriorityClass integer +
QoS class with a confidence + optional cost estimate (Phase B), then writes
`qos_groups` / `qos_recommendations` / `qos_evidence` / `qos_peers`. Only this module
and analysis_core/io touch the DB; the stage functions stay pure.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Optional

from ...analysis_core.config import EngineConfig
from ...analysis_core.interaction_graph import interaction_sum
from ...analysis_core.io.statestore import StateStore, _iso, _now
from ...analysis_core.prepare import prepare_series
from ...why import series as why_series
from ...why.templates import qos_summary
from . import assign
from . import cluster as clusterer
from . import confidence as conf
from . import cost as cost_mod
from . import ranking
from .crosscluster import fraction_representation
from .representative import INTERACTIONS_KEY, build_representation
from .types import QoSEvidenceItem, QoSGroup, QoSPeer, QoSRecommendation

_ADJ = ["brave", "calm", "clever", "eager", "gentle", "jolly", "keen", "lively",
        "merry", "noble", "proud", "swift", "witty", "zesty", "amber", "bold"]
_ANIMALS = ["otter", "falcon", "lynx", "panda", "koala", "heron", "ibis", "marmot",
            "narwhal", "quokka", "raven", "stoat", "tapir", "viper", "yak", "wren"]


@dataclass
class RunResult:
    run_id: int
    name: str
    status: str
    recommendations: int
    groups: list = field(default_factory=list)
    data_as_of: Optional[str] = None
    stale: bool = False


def generate_name(rng: random.Random) -> str:
    return f"{rng.choice(_ADJ)}-{rng.choice(_ANIMALS)}-{rng.randint(1000, 9999)}"


def run_qos_analysis(
    store: StateStore,
    *,
    cluster: Any,
    scope: Any = "all",
    config_overrides: Optional[dict] = None,
    ttl_hours: int = 24,
    name: Optional[str] = None,
    k: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> RunResult:
    rng = rng or random.Random()
    cluster_id = cluster if isinstance(cluster, int) else store.ensure_cluster(str(cluster))

    settings = store.get_settings()
    cfg = EngineConfig.from_settings(settings).with_overrides(**_map_overrides(config_overrides))

    data_as_of = store.max_collected_at(cluster_id)
    metric_ttl_h = int((settings or {}).get("metric_ttl_hours", 24))
    stale = data_as_of is None or data_as_of < _now() - timedelta(hours=metric_ttl_h)

    run_name = name or _unique_name(store, rng)
    run_id = store.create_analysis_run(
        name=run_name, cluster_id=cluster_id, scope=scope, config=cfg.to_config_dict(),
        data_as_of=data_as_of, stale=stale, ttl_hours=ttl_hours, run_type="qos",
    )

    try:
        uids = store.list_allocation_uids(cluster_id, scope)
        loaded = {uid: _load_workload(store, cluster_id, uid, cfg) for uid in uids}

        if cfg.comparison_scope == "cross_group":
            groups = {0: uids}
            centroids = {0: _mean_centroid(loaded, uids)}
        else:
            resource_dims = sorted({res for w in loaded.values() for res in w["feature"]})
            feature_dicts = [loaded[uid]["feature"] for uid in uids]
            clustering = clusterer.cluster_workloads(feature_dicts, resource_dims, cfg, k=k)
            groups = {}
            for uid, label in zip(uids, clustering["labels"]):
                groups.setdefault(int(label), []).append(uid)
            centroids = clustering["centroids"]

        n_recs = 0
        group_summaries = []
        for gindex in sorted(groups):
            member_uids = groups[gindex]
            n_recs += _process_group(
                store, run_id, cluster_id, cfg, gindex, member_uids, loaded,
                centroid=centroids.get(gindex, {}),
            )
            group_summaries.append({"group_index": gindex, "members": member_uids})

        store.finish_analysis_run(run_id, "completed")
        status = "completed"
    except Exception as exc:  # pragma: no cover - defensive; surfaced to caller
        store.finish_analysis_run(run_id, "failed", error=str(exc))
        raise

    return RunResult(
        run_id=run_id, name=run_name, status=status, recommendations=n_recs,
        groups=group_summaries, data_as_of=_iso(data_as_of), stale=stale,
    )


def _ranked_resources(cfg: EngineConfig) -> list[str]:
    """Utilization resources to rank on: configured resources minus excluded (monotonic)."""
    excluded = set(cfg.excluded_resources or [])
    return [r for r in cfg.resources if r not in excluded]


def _load_workload(store: StateStore, cluster_id: int, uid: str, cfg: EngineConfig) -> dict:
    """Load one workload's allocation feature vector + prepared utilization + medians."""
    ranked = _ranked_resources(cfg)

    util_series: dict = {}
    median_util: dict = {}
    max_util: dict = {}
    n_points = 0
    for res in set(ranked):
        points = store.load_series(cluster_id, uid, res)
        if points:
            s = prepare_series(points, cfg.resample_freq)
            util_series[res] = s
            median_util[res] = float(s.median()) if len(s) else 0.0
            n_points = max(n_points, len(s))
            tail = [v for _, v in points[-3:]]
            max_util[res] = max(tail) if tail else None

    feature: dict = {}
    alloc_fallback = False
    for row in store.load_allocations(cluster_id, uid):
        res = row["resource"]
        if row.get("requested") is None and row.get("lim") is None:
            alloc_fallback = True
        feature[res] = clusterer.effective_value(row.get("requested"), row.get("lim"), max_util.get(res))

    return {
        "feature": feature, "util_series": util_series, "median_util": median_util,
        "has_util": bool(util_series), "alloc_fallback": alloc_fallback, "n_points": n_points,
    }


def _build_members(cfg, member_uids, loaded, interaction_totals) -> list:
    """Representative vectors for the ranker — median utilization (within) or the
    utilization/allocation fraction (cross-group)."""
    ranked = _ranked_resources(cfg)
    members = []
    for uid in member_uids:
        L = loaded[uid]
        if cfg.comparison_scope == "cross_group":
            reps = fraction_representation(L["util_series"], L["feature"], interaction_totals[uid],
                                          ranked, cfg.include_interactions)
        else:
            reps = build_representation(L["util_series"], interaction_totals[uid],
                                       ranked, cfg.include_interactions)
        members.append({"uid": uid, "reps": reps})
    return members


def _process_group(store, run_id, cluster_id, cfg, gindex, member_uids, loaded, centroid) -> int:
    """Rank one group, assign priority + QoS + confidence + cost, and persist."""
    group_set = set(member_uids)
    interaction_totals = {
        uid: interaction_sum(store.get_outgoing_interactions(cluster_id, uid), peer_uids=group_set)
        for uid in member_uids
    }
    members = _build_members(cfg, member_uids, loaded, interaction_totals)
    ranked = ranking.rank_group(members, cfg.weights)
    n = len(ranked)
    qos_positions = assign.qos_for_positions(n, cfg.qos_split)
    scores = [r["weighted_score"] for r in ranked]

    label = _group_label(centroid) if cfg.comparison_scope == "within_group" else "cross-cluster: all workloads"
    group = QoSGroup(group_index=gindex, label=label, centroid=centroid, member_count=n)
    group_id = store.insert_qos_group(run_id, group)

    identities = {uid: (store.get_identity(cluster_id, uid) or {}) for uid in member_uids}
    for pos, entry in enumerate(ranked):
        uid = entry["uid"]
        L = loaded[uid]
        identity = identities[uid]
        recommended_qos = qos_positions[pos]
        recommended_priority = assign.priority_integer(entry["weighted_score"], cfg)

        evidence = _evidence(entry, L)
        peers = _peers(store, cluster_id, uid, group_set, identities)
        confidence = conf.score_confidence(
            member_count=n, has_util=L["has_util"],
            cov=conf.coverage(L["n_points"], cfg.window),
            boundary=_near_boundary(pos, scores, qos_positions, cfg),
            alloc_fallback=L["alloc_fallback"], cfg=cfg,
        )
        savings = cost_mod.estimate_savings(
            effective_alloc=L["feature"], median_util=L["median_util"],
            recommended_qos=recommended_qos, current_qos=identity.get("current_qos"), cfg=cfg,
        )
        summary = qos_summary(
            pos=pos, n=n, entry=entry, recommended_qos=recommended_qos, priority=recommended_priority,
            identity=identity, peers=peers, comparison_scope=cfg.comparison_scope,
        )

        rec = QoSRecommendation(
            workload_uid=uid,
            workload_kind=identity.get("kind"), workload_name=identity.get("name"),
            namespace=identity.get("namespace"),
            recommended_qos=recommended_qos, recommended_priority=recommended_priority,
            weighted_score=entry["weighted_score"], comparison_scope=cfg.comparison_scope,
            confidence=confidence, summary_text=summary,
            current_qos=identity.get("current_qos"), current_priority=identity.get("current_priority"),
            estimated_savings=savings, savings_currency=(cfg.currency if savings is not None else None),
            evidence=evidence, peers=peers,
        )
        store.insert_qos_recommendation(run_id, group_id, rec)

    return n


# --- small helpers ---------------------------------------------------------

def _evidence(entry, loaded_wl) -> list:
    items = []
    for res, pr in entry["per_resource"].items():
        s = loaded_wl["util_series"].get(res) if res != INTERACTIONS_KEY else None
        items.append(QoSEvidenceItem(
            resource=res, representative_value=pr["value"], percentile=pr["percentile"],
            weight=pr["weight"], series=(why_series.downsample(s) if s is not None else None),
        ))
    return items


def _near_boundary(pos, scores, qos_positions, cfg) -> bool:
    """True if an adjacent app in a different QoS class has a score within the gap."""
    neighbors = []
    for j in (pos - 1, pos + 1):
        if 0 <= j < len(scores) and qos_positions[j] != qos_positions[pos]:
            neighbors.append(scores[j])
    return conf.near_boundary(scores[pos], neighbors, cfg.confidence_boundary_gap)


def _peers(store, cluster_id, uid, group_set, identities) -> list:
    edges = [e for e in store.get_outgoing_interactions(cluster_id, uid)
             if e["dst_workload_uid"] in group_set]
    if not edges:
        return []
    max_c = max((e.get("avg_count") or 0.0) for e in edges) or 1.0
    peers = []
    for e in edges:
        peer_uid = e["dst_workload_uid"]
        pid = identities.get(peer_uid) or store.get_identity(cluster_id, peer_uid) or {}
        peers.append(QoSPeer(
            peer_workload_uid=peer_uid, peer_workload=pid.get("name") or peer_uid,
            relation="interacts_with", affinity=round((e.get("avg_count") or 0.0) / max_c, 4),
        ))
    return peers


def _mean_centroid(loaded, uids) -> dict:
    dims = sorted({res for u in uids for res in loaded[u]["feature"]})
    out = {}
    for d in dims:
        vals = [loaded[u]["feature"].get(d, 0.0) for u in uids]
        out[d] = {"scaled": 0.0, "original": (sum(vals) / len(vals) if vals else 0.0)}
    return out


def _group_label(centroid: dict) -> str:
    if not centroid:
        return "group"
    parts = []
    for res in ("cpu", "memory"):
        if res in centroid:
            parts.append(f"{res} {_fmt(centroid[res].get('original', 0.0), res)}")
    extra = [r for r in centroid if r not in ("cpu", "memory") and centroid[r].get("original", 0.0) > 0]
    if extra:
        parts.append("+" + ",".join(extra))
    return "allocation: " + ", ".join(parts) if parts else "group"


def _fmt(v: float, res: str) -> str:
    if res == "memory":
        return f"{v / (1024 ** 3):.2f}Gi" if v >= 1024 ** 3 else f"{v / (1024 ** 2):.0f}Mi"
    return f"{v:.2f}"


def _unique_name(store: StateStore, rng: random.Random) -> str:
    for _ in range(20):
        candidate = generate_name(rng)
        if store._fetchone("SELECT 1 AS x FROM analysis_runs WHERE name = ?", (candidate,)) is None:
            return candidate
    return generate_name(rng)


def _map_overrides(overrides: Optional[dict]) -> dict:
    """Map a run request's `config` body / CLI flags onto EngineConfig fields.

    Accepts both flat keys and the docs/04 §C nested shape
    (`priority: {base, step}`, `outputs: {cost}`).
    """
    if not overrides:
        return {}
    out: dict[str, Any] = {}
    for kk in ("resources", "window", "resample_freq", "k", "k_strategy", "scaling",
               "weights", "qos_split", "comparison_scope", "excluded_resources",
               "include_interactions", "concurrency", "enable_cost", "node_hourly_cost",
               "priority_base", "priority_step"):
        if overrides.get(kk) is not None:
            out[kk] = overrides[kk]
    prio = overrides.get("priority") or {}
    if prio.get("base") is not None:
        out["priority_base"] = prio["base"]
    if prio.get("step") is not None:
        out["priority_step"] = prio["step"]
    outputs = overrides.get("outputs") or {}
    if outputs.get("cost") is not None:
        out["enable_cost"] = outputs["cost"]
    return out
