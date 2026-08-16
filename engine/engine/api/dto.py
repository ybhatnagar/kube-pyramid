"""DTO assembly for the REST surface (docs/04 §E). Pure dict builders over store rows."""
from __future__ import annotations

from typing import Optional

from ..analysis_core.io.statestore import StateStore, _iso, _parse_dt
from ..recommenders.qos.assign import change_direction
from ..recommenders.qos.representative import INTERACTIONS_KEY

_UNITS = {"cpu": "cores", "memory": "bytes", "net_tx": "bytes/s", "net_rx": "bytes/s", "ephemeral_storage": "bytes"}


def parse_rec_id(rec_id: str) -> int:
    s = str(rec_id)
    return int(s[4:]) if s.startswith("rec_") else int(s)


# --- config / discovery ----------------------------------------------------

def cluster_dto(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": str(row["id"]), "name": row["name"], "api_url": row.get("api_url"),
        "auth_method": row.get("auth_method"), "status": row.get("status"),
        "created_at": _iso(_parse_dt(row.get("created_at"))),
        "last_connected_at": _iso(_parse_dt(row.get("last_connected_at"))),
    }


def data_source_dto(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": str(row["id"]), "cluster_id": row.get("cluster_id"), "type": row["type"],
        "name": row["name"], "endpoint": row.get("endpoint"), "enabled": bool(row.get("enabled")),
        "settings": row.get("settings"), "health": row.get("health"),
        "last_checked_at": _iso(_parse_dt(row.get("last_checked_at"))),
    }


def settings_dto(row: Optional[dict]) -> dict:
    row = row or {}
    return {k: row.get(k) for k in (
        "metric_ttl_hours", "discovery_ttl_min", "result_ttl_hours",
        "default_resources", "default_window", "thresholds")}


def collection_dto(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": str(row["id"]), "status": row["status"],
        "progress": 100 if row["status"] in ("success", "failed", "partial") else 0,
        "data_as_of": _iso(_parse_dt(row.get("data_as_of"))),
        "rows_written": row.get("rows_written"), "error": row.get("error"),
    }


def workload_dto(row: dict) -> dict:
    return {
        "kind": row["kind"], "name": row["name"], "namespace": row["namespace"],
        "workload_uid": row.get("workload_uid"), "replicas": row.get("replicas"),
        "current_qos": row.get("current_qos"), "current_priority": row.get("current_priority"),
        "requests_cpu_m": row.get("requests_cpu_m"), "requests_mem_bytes": row.get("requests_mem_bytes"),
    }


# --- runs ------------------------------------------------------------------

def run_summary_dto(row: dict) -> dict:
    return {
        "id": str(row["id"]), "name": row["name"], "cluster_id": row.get("cluster_id"),
        "run_type": row.get("run_type", "qos"), "status": row["status"],
        "stale": bool(row.get("stale")), "data_as_of": _iso(_parse_dt(row.get("data_as_of"))),
        "created_at": _iso(_parse_dt(row.get("created_at"))), "completed_at": _iso(_parse_dt(row.get("completed_at"))),
    }


def run_status_dto(store: StateStore, run_id: int) -> Optional[dict]:
    run = store.get_run(run_id)
    if not run:
        return None
    return {
        "id": str(run["id"]), "run_type": run.get("run_type", "qos"), "status": run["status"],
        "data_as_of": _iso(_parse_dt(run.get("data_as_of"))), "stale": bool(run["stale"]),
        "progress": 100 if run["status"] in ("completed", "failed") else 0, "error": run.get("error"),
    }


def _recommendation_dto(r: dict) -> dict:
    return {
        "recommendation_id": f"rec_{r['id']}",
        "workload": r.get("workload_name"), "namespace": r.get("namespace"), "kind": r.get("workload_kind"),
        "workload_uid": r.get("workload_uid"), "group_id": r.get("group_id"),
        "current_qos": r.get("current_qos"), "current_priority": r.get("current_priority"),
        "recommended_qos": r.get("recommended_qos"), "recommended_priority_int": r.get("recommended_priority"),
        "weighted_score": r.get("weighted_score"), "comparison_scope": r.get("comparison_scope"),
        "estimated_savings": r.get("estimated_savings"), "savings_currency": r.get("savings_currency"),
        "confidence": r.get("confidence"), "summary": r.get("summary_text"),
        "change": change_direction(r.get("current_qos"), r.get("recommended_qos")),
    }


def cards_dto(store: StateStore, run_id: int) -> Optional[dict]:
    """Flat QoS recommendation DTOs (GET /runs/{id}/recommendations)."""
    run = store.get_run(run_id)
    if not run:
        return None
    recs = store.get_qos_recommendations(run_id)
    return {"run": _run_head(run), "recommendations": [_recommendation_dto(r) for r in recs]}


def groups_dto(store: StateStore, run_id: int) -> Optional[dict]:
    """Group DTOs with nested recommendations (GET /runs/{id}/groups)."""
    run = store.get_run(run_id)
    if not run:
        return None
    recs_by_group: dict = {}
    for r in store.get_qos_recommendations(run_id):
        recs_by_group.setdefault(r.get("group_id"), []).append(_recommendation_dto(r))
    groups = []
    for g in store.get_qos_groups(run_id):
        groups.append({
            "group_id": g["id"], "group_index": g["group_index"], "label": g.get("label"),
            "centroid_summary": g.get("label"), "member_count": g.get("member_count"),
            "recommendations": recs_by_group.get(g["id"], []),
        })
    return {"run": _run_head(run), "groups": groups}


def evidence_dto(store: StateStore, run_id: int, rec_id: int, include_series: bool = True) -> Optional[dict]:
    """Evidence + peers DTO (lazy, per card)."""
    run = store.get_run(run_id)
    if not run:
        return None
    rec = next((r for r in store.get_qos_recommendations(run_id) if r["id"] == rec_id), None)
    if not rec:
        return None
    evs = store.get_qos_evidence(rec_id)
    peers = store.get_qos_peers(rec_id)

    per_resource, interaction_sum = [], None
    for e in evs:
        if e["resource"] == INTERACTIONS_KEY:
            interaction_sum = e.get("representative_value")
            continue
        item = {
            "resource": e["resource"], "representative_value": e.get("representative_value"),
            "percentile": e.get("percentile"), "weight": e.get("weight"),
            "unit": _UNITS.get(e["resource"], ""),
        }
        if include_series:
            item["series"] = e.get("series") or []
        per_resource.append(item)

    return {
        "recommendation_id": f"rec_{rec_id}", "summary": rec.get("summary_text"),
        "confidence": rec.get("confidence"),
        "per_resource": per_resource,
        "interaction_sum": interaction_sum,
        "current_vs_recommended": {
            "current_qos": rec.get("current_qos"), "current_priority": rec.get("current_priority"),
            "recommended_qos": rec.get("recommended_qos"), "recommended_priority_int": rec.get("recommended_priority"),
            "change": change_direction(rec.get("current_qos"), rec.get("recommended_qos")),
        },
        "peers": [
            {"peer_workload": p.get("peer_workload"), "peer_workload_uid": p.get("peer_workload_uid"),
             "relation": p.get("relation"), "affinity": p.get("affinity")}
            for p in peers
        ],
    }


def _run_head(run: dict) -> dict:
    config = run.get("config") or {}
    return {
        "id": str(run["id"]), "name": run["name"], "run_type": run.get("run_type", "qos"),
        "cluster": run.get("cluster_name"), "data_as_of": _iso(_parse_dt(run.get("data_as_of"))),
        "stale": bool(run["stale"]), "comparison_scope": config.get("comparison_scope"),
        "window": config.get("window"),
    }
