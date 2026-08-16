"""QoS synthetic fixtures with known ground truth.

Builds `k` well-separated **allocation** clusters (so k-means recovers them), and
within each cluster a set of apps with a **designed within-group rank order**: cpu-
and memory-utilization medians and interaction sums all increase with an app's
`level`, so the expected importance order, percentile ranks, priority integers, and
QoS thirds are all deterministic and hand-checkable. Deterministic (flat series ->
exact medians); no randomness, no cluster needed.

Layout (defaults): two groups —
  * "batch"   : small allocation (0.25 cpu / 256Mi), levels 1..6
  * "serving" : large allocation (4 cpu / 8Gi / 1 gpu), levels 1..6
Clustering is on allocations (incl. the custom `nvidia.com/gpu` dim on "serving",
exercising free-text/custom allocation vectors); ranking is on utilization +
interactions, which vary *within* a group. All apps start deployed "Guaranteed"
(the priority paradox), so the tool's job is to demote the lower-ranked ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

Points = list  # list[tuple[datetime, float]]

DEFAULT_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
MI = 1024 ** 2
GI = 1024 ** 3


def _flat_series(level_value: float, hours: int = 168, start: datetime = DEFAULT_START) -> Points:
    """A constant utilization series — median is exactly `level_value`.

    168 hourly points = a full 7-day window, so confidence coverage is complete for
    the default window; the constant value keeps the median exact for assertions.
    """
    return [(start + timedelta(hours=h), float(level_value)) for h in range(hours)]


@dataclass
class QoSSynthWorkload:
    uid: str
    namespace: str
    kind: str
    name: str
    group_label: str
    level: int                                   # designed importance within the group (higher = more important)
    allocations: dict = field(default_factory=dict)   # resource -> {requested, limit, unit, resource_kind, is_custom}
    utilization: dict = field(default_factory=dict)   # resource -> Points
    current_qos: str = "Guaranteed"
    current_priority: int = 1000


@dataclass
class QoSSynthCluster:
    name: str
    workloads: list = field(default_factory=list)
    interactions: list = field(default_factory=list)   # {src_uid, dst_uid, avg_count}
    groups: dict = field(default_factory=dict)          # group_label -> [uids] (ground-truth membership)

    def by_uid(self, uid: str) -> QoSSynthWorkload:
        return next(w for w in self.workloads if w.uid == uid)

    def expected_order(self, group_label: str) -> list:
        """Ground-truth within-group importance order (uids), most important first."""
        members = [w for w in self.workloads if w.group_label == group_label]
        return [w.uid for w in sorted(members, key=lambda w: -w.level)]


def _uid(ns: str, name: str) -> str:
    return f"{ns}/Deployment/{name}"


def _make_group(label: str, namespace: str, per_group: int, *,
                cpu_req: float, mem_req: float, gpu_req: float = 0.0,
                cpu_util_step: float, mem_util_step: float) -> tuple:
    """One allocation cluster of `per_group` apps with monotonically increasing
    utilization + interactions by level. Returns (workloads, interactions)."""
    workloads = []
    for level in range(1, per_group + 1):
        name = f"{label}-{level}"
        uid = _uid(namespace, name)
        allocations = {
            "cpu": {"requested": cpu_req, "limit": cpu_req, "unit": "cores", "resource_kind": "standard", "is_custom": False},
            "memory": {"requested": mem_req, "limit": mem_req, "unit": "bytes", "resource_kind": "standard", "is_custom": False},
        }
        if gpu_req:
            allocations["nvidia.com/gpu"] = {
                "requested": gpu_req, "limit": gpu_req, "unit": "count",
                "resource_kind": "custom", "is_custom": True,
            }
        utilization = {
            "cpu": _flat_series(cpu_util_step * level),
            "memory": _flat_series(mem_util_step * level),
        }
        workloads.append(QoSSynthWorkload(
            uid=uid, namespace=namespace, kind="Deployment", name=name,
            group_label=label, level=level, allocations=allocations, utilization=utilization,
        ))

    # Interactions: each app of level L (>=2) calls the group's level-1 app L*10 times.
    # Outgoing-sum per app is then strictly increasing in level (level-1 app -> 0),
    # so the interactions pseudo-resource ranks in the same order as utilization.
    hub = _uid(namespace, f"{label}-1")
    interactions = [
        {"src_uid": _uid(namespace, f"{label}-{level}"), "dst_uid": hub, "avg_count": float(level * 10)}
        for level in range(2, per_group + 1)
    ]
    return workloads, interactions


def qos_synthetic_cluster(name: str = "synth-qos", per_group: int = 6, seed: int = 0) -> QoSSynthCluster:
    """A two-group fixture with known cluster membership and within-group rank order."""
    batch_w, batch_i = _make_group(
        "batch", "team-batch", per_group,
        cpu_req=0.25, mem_req=256 * MI,
        cpu_util_step=0.05, mem_util_step=40 * MI,
    )
    serving_w, serving_i = _make_group(
        "serving", "team-serving", per_group,
        cpu_req=4.0, mem_req=8 * GI, gpu_req=1.0,
        cpu_util_step=0.8, mem_util_step=1.2 * GI,
    )
    workloads = batch_w + serving_w
    groups = {
        "batch": [w.uid for w in batch_w],
        "serving": [w.uid for w in serving_w],
    }
    return QoSSynthCluster(name=name, workloads=workloads,
                           interactions=batch_i + serving_i, groups=groups)


# --- edge-case fixture (M2): ties, single-member group, allocation-only, sparse ---

def _wl(ns, name, group, level, *, cpu_alloc, mem_alloc, gpu_alloc=0.0,
        cpu_util=None, mem_util=None) -> QoSSynthWorkload:
    allocations = {}
    if cpu_alloc is not None:
        allocations["cpu"] = {"requested": cpu_alloc, "limit": cpu_alloc, "unit": "cores",
                              "resource_kind": "standard", "is_custom": False}
    if mem_alloc is not None:
        allocations["memory"] = {"requested": mem_alloc, "limit": mem_alloc, "unit": "bytes",
                                "resource_kind": "standard", "is_custom": False}
    if gpu_alloc:
        allocations["nvidia.com/gpu"] = {"requested": gpu_alloc, "limit": gpu_alloc, "unit": "count",
                                        "resource_kind": "custom", "is_custom": True}
    utilization = {}
    if cpu_util is not None:
        utilization["cpu"] = _flat_series(cpu_util)
    if mem_util is not None:
        utilization["memory"] = _flat_series(mem_util)
    return QoSSynthWorkload(uid=_uid(ns, name), namespace=ns, kind="Deployment", name=name,
                            group_label=group, level=level, allocations=allocations, utilization=utilization)


def qos_edgecase_cluster(name: str = "synth-qos-edge") -> QoSSynthCluster:
    """Three cleanly separable allocation clusters (force k=3) exercising edge cases:

      * "big"       — 5 apps incl. a TIE (big-tie1 == big-tie2, identical util + no
                      interactions) and an ALLOCATION-ONLY app (big-noutil: allocations
                      but no utilization series);
      * "mid"       — 2 apps with full allocations;
      * "singleton" — 1 app (its own cluster -> confidence 'low').

    (Sparse allocation vectors — a workload missing a dimension — are covered by a
    pure build_feature_matrix test rather than here, so k-means membership stays
    deterministic.)
    """
    ns = "edge"
    big = [
        _wl(ns, "big-hi", "big", 4, cpu_alloc=4.0, mem_alloc=8 * GI, cpu_util=3.0, mem_util=6 * GI),
        _wl(ns, "big-tie1", "big", 3, cpu_alloc=4.0, mem_alloc=8 * GI, cpu_util=1.0, mem_util=2 * GI),
        _wl(ns, "big-tie2", "big", 3, cpu_alloc=4.0, mem_alloc=8 * GI, cpu_util=1.0, mem_util=2 * GI),
        _wl(ns, "big-lo", "big", 2, cpu_alloc=4.0, mem_alloc=8 * GI, cpu_util=0.5, mem_util=1 * GI),
        _wl(ns, "big-noutil", "big", 1, cpu_alloc=4.0, mem_alloc=8 * GI),  # allocation-only
    ]
    mid = [
        _wl(ns, "mid-a", "mid", 2, cpu_alloc=1.0, mem_alloc=1 * GI, cpu_util=0.7, mem_util=700 * MI),
        _wl(ns, "mid-b", "mid", 1, cpu_alloc=1.0, mem_alloc=1 * GI, cpu_util=0.3, mem_util=300 * MI),
    ]
    singleton = [
        _wl(ns, "lonely", "singleton", 1, cpu_alloc=0.05, mem_alloc=32 * MI, cpu_util=0.01, mem_util=8 * MI),
    ]
    workloads = big + mid + singleton
    interactions = [
        {"src_uid": _uid(ns, "big-hi"), "dst_uid": _uid(ns, "big-lo"), "avg_count": 50.0},
    ]
    groups = {
        "big": [w.uid for w in big],
        "mid": [w.uid for w in mid],
        "singleton": [w.uid for w in singleton],
    }
    return QoSSynthCluster(name=name, workloads=workloads, interactions=interactions, groups=groups)


# --- DB seeding ------------------------------------------------------------

_RATE = {"cpu", "net_tx", "net_rx"}


def seed_qos_cluster(store, cluster: QoSSynthCluster) -> int:
    """Seed tiers 2–3 (disc_workloads, allocations, metric_samples, interactions)
    from a fixture; returns the cluster id. Store is duck-typed (any StateStore)."""
    cid = store.ensure_cluster(cluster.name)
    metric_rows, alloc_rows = [], []
    for w in cluster.workloads:
        store.upsert_workload(
            cid, w.uid, w.namespace, w.kind, w.name,
            current_qos=w.current_qos, current_priority=w.current_priority,
        )
        for res, spec in w.allocations.items():
            alloc_rows.append({
                "cluster_id": cid, "workload_uid": w.uid, "resource": res,
                "resource_kind": spec.get("resource_kind", "standard"),
                "requested": spec.get("requested"), "limit": spec.get("limit"),
                "unit": spec.get("unit"), "is_custom": spec.get("is_custom", False), "source": "synth",
            })
        for res, points in w.utilization.items():
            for ts, val in points:
                metric_rows.append({
                    "cluster_id": cid, "workload_uid": w.uid, "resource": res,
                    "resource_kind": "standard", "ts": ts, "value": val,
                    "unit": "cores" if res == "cpu" else "bytes", "is_rate": res in _RATE,
                })
    store.insert_allocations(alloc_rows)
    store.insert_metric_samples(metric_rows)
    if cluster.interactions:
        store.insert_interactions([
            {"cluster_id": cid, "src_workload_uid": e["src_uid"], "dst_workload_uid": e["dst_uid"],
             "avg_count": e["avg_count"]}
            for e in cluster.interactions
        ])
    return cid
