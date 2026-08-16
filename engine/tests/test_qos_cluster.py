"""Phase A: k-means recovers the two separable allocation clusters."""
from engine.analysis_core.config import EngineConfig
from engine.recommenders.qos import cluster as clusterer
from engine.synth import qos_synthetic_cluster


def _feature_dicts(cl):
    """Effective allocation (= requested here) per workload, in a stable uid order."""
    uids = [w.uid for w in cl.workloads]
    feats = [{res: spec["requested"] for res, spec in cl.by_uid(u).allocations.items()} for u in uids]
    dims = sorted({res for f in feats for res in f})
    return uids, feats, dims


def test_effective_value_fallback_chain():
    assert clusterer.effective_value(0.25, 1.0, 9.0) == 0.25   # requested wins
    assert clusterer.effective_value(None, 1.0, 9.0) == 1.0    # then limit
    assert clusterer.effective_value(None, None, 9.0) == 9.0   # then max-util
    assert clusterer.effective_value(None, None, None) == 0.0  # then 0


def test_kmeans_recovers_two_groups():
    cl = qos_synthetic_cluster()
    uids, feats, dims = _feature_dicts(cl)
    cfg = EngineConfig()
    out = clusterer.cluster_workloads(feats, dims, cfg, k=2)

    labels = dict(zip(uids, out["labels"]))
    batch_labels = {labels[u] for u in cl.groups["batch"]}
    serving_labels = {labels[u] for u in cl.groups["serving"]}
    # each ground-truth group maps to exactly one cluster, and they differ
    assert len(batch_labels) == 1
    assert len(serving_labels) == 1
    assert batch_labels != serving_labels
    assert out["k"] == 2


def test_auto_k_picks_two_for_the_fixture():
    cl = qos_synthetic_cluster()
    _, feats, dims = _feature_dicts(cl)
    out = clusterer.cluster_workloads(feats, dims, EngineConfig(), k=None)  # silhouette (default)
    assert out["k"] == 2
