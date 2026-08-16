"""YAML export renderer (docs/08 safety model).

The export **actively changes only the PriorityClass**; the QoS-class recommendation is
rendered as **inert commented guidance** next to the workload's current, unchanged
requests/limits, so nothing damaging applies on `kubectl apply`. Sizing hints (P50/P95
for cpu/memory) come from the same utilization series the engine ranked on; the memory
hint never falls below the observed peak (OOM guard). Read-only; there is no apply path.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ...analysis_core.prepare import prepare_series

_QOS_LONG = {"Guaranteed": "Guaranteed", "Burstable": "Burstable", "BestEffort": "Best-Effort"}


def render_export(store, run_id: int, scope: str = "all", uid: Optional[str] = None) -> str:
    """Render YAML for one workload (scope='workload') or all (scope='all')."""
    run = store.get_run(run_id)
    if not run:
        return ""
    cluster_id = int(run["cluster_id"])
    recs = store.get_qos_recommendations(run_id)
    if scope == "workload" and uid:
        recs = [r for r in recs if r["workload_uid"] == uid]

    docs = [_workload_yaml(store, cluster_id, r) for r in recs]
    if scope == "workload":
        return docs[0] if docs else ""
    header = (
        f"# Kube Pyramid export — {len(docs)} workload(s).\n"
        "# ACTIVE: PriorityClass changes only. QoS-class changes are COMMENTED guidance,\n"
        "# never applied, so nothing here can silently alter app behavior on kubectl apply.\n"
        "# =========================================================================\n\n"
    )
    return header + "\n---\n".join(docs)


def _workload_yaml(store, cluster_id: int, rec: dict) -> str:
    name = rec.get("workload_name") or rec["workload_uid"]
    ns = rec.get("namespace") or "default"
    uid = rec["workload_uid"]
    prio = rec.get("recommended_priority") or 0

    allocs = {a["resource"]: a for a in store.load_allocations(cluster_id, uid)}
    cur_cpu = _cpu(allocs.get("cpu"))
    cur_mem = _mem(allocs.get("memory"))
    hints = _sizing_hints(store, cluster_id, uid, allocs)

    return f"""apiVersion: scheduling.k8s.io/v1
kind: PriorityClass
metadata:
  name: qos-rec-{name}
value: {prio}
globalDefault: false
description: "Relative priority recommended by Kube Pyramid (was {rec.get('current_priority')})"
---
# ACTIVE CHANGE (safe): this export only sets the priorityClassName.
# {rec.get('workload_kind') or 'Deployment'}/{name} (namespace: {ns})
spec:
  template:
    spec:
      priorityClassName: qos-rec-{name}
      containers:
        - name: {name}
          resources:            # CURRENT values — left UNCHANGED by this export
            requests: {{cpu: "{cur_cpu[0]}", memory: "{cur_mem[0]}"}}
            limits:   {{cpu: "{cur_cpu[1]}", memory: "{cur_mem[1]}"}}
# ---------------------------------------------------------------------------
{_qos_comment(rec, hints)}"""


def _qos_comment(rec: dict, hints: dict) -> str:
    cur, target = rec.get("current_qos"), rec.get("recommended_qos")
    curL = _QOS_LONG.get(cur, cur or "unknown")
    if cur and cur == target:
        return f"# QoS class unchanged ({curL}). Only the priority integer changed above."
    if target == "Guaranteed":
        return (f"# QoS RECOMMENDATION: {curL} -> Guaranteed  (NOT applied — review, then edit yourself)\n"
                "#   Guaranteed requires requests == limits for cpu AND memory.\n"
                "#   Suggested (sized to observed P95 + headroom):\n"
                "#           resources:\n"
                f"#             requests: {{cpu: \"{hints['p95c']}\", memory: \"{hints['p95m']}\"}}\n"
                f"#             limits:   {{cpu: \"{hints['p95c']}\", memory: \"{hints['p95m']}\"}}")
    if target == "Burstable":
        return (f"# QoS RECOMMENDATION: {curL} -> Burstable  (NOT applied — review, then edit yourself)\n"
                "#   Burstable = request < limit. Suggested request=observed P50, limit=observed P95:\n"
                "#           resources:\n"
                f"#             requests: {{cpu: \"{hints['p50c']}\", memory: \"{hints['p50m']}\"}}   # P50 (typical)\n"
                f"#             limits:   {{cpu: \"{hints['p95c']}\", memory: \"{hints['p95m']}\"}}   # P95 (ceiling)")
    return ("# QoS RECOMMENDATION: {} -> Best-Effort  (NOT applied — review carefully)\n"
            "#   Best-Effort = NO requests or limits on any container.\n"
            "#   To apply, REMOVE the entire resources block above. No values are suggested\n"
            "#   on purpose. Best-Effort pods are the FIRST to be OOM-killed / evicted under\n"
            "#   node pressure — consider a canary before production."
            ).format(curL)


# --- sizing hints ----------------------------------------------------------

def _sizing_hints(store, cluster_id: int, uid: str, allocs: dict) -> dict:
    """P50/P95 for cpu (millicores) + memory (Mi) from the utilization series.
    Memory P95 is floored at the observed peak (OOM guard, docs/08)."""
    cpu_p50, cpu_p95 = _percentiles(store, cluster_id, uid, "cpu")
    mem_p50, mem_p95, mem_peak = _percentiles(store, cluster_id, uid, "memory", want_peak=True)
    if mem_peak is not None:
        mem_p95 = max(mem_p95 or 0.0, mem_peak)  # never below recent observed peak
    return {
        "p50c": _fmt_cpu(cpu_p50), "p95c": _fmt_cpu(cpu_p95),
        "p50m": _fmt_mem(mem_p50), "p95m": _fmt_mem(mem_p95),
    }


def _percentiles(store, cluster_id, uid, resource, want_peak=False):
    points = store.load_series(cluster_id, uid, resource)
    if not points:
        return (None, None, None) if want_peak else (None, None)
    s = prepare_series(points, "1h")
    if len(s) == 0:
        return (None, None, None) if want_peak else (None, None)
    arr = s.to_numpy()
    p50, p95 = float(np.percentile(arr, 50)), float(np.percentile(arr, 95))
    return (p50, p95, float(arr.max())) if want_peak else (p50, p95)


def _cpu(alloc: Optional[dict]) -> tuple:
    if not alloc:
        return ("unset", "unset")
    return (_fmt_cpu(alloc.get("requested")), _fmt_cpu(alloc.get("lim")))


def _mem(alloc: Optional[dict]) -> tuple:
    if not alloc:
        return ("unset", "unset")
    return (_fmt_mem(alloc.get("requested")), _fmt_mem(alloc.get("lim")))


def _fmt_cpu(cores: Optional[float]) -> str:
    return "unset" if cores is None else f"{max(1, round(float(cores) * 1000))}m"


def _fmt_mem(byts: Optional[float]) -> str:
    return "unset" if byts is None else f"{max(1, round(float(byts) / (1024 ** 2)))}Mi"
