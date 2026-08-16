"""Edge cases: single-member group, allocation-only, ties, sparse allocations."""
from engine.recommenders.qos import cluster as clusterer
from engine.recommenders.qos.runner import run_qos_analysis
from engine.synth import qos_edgecase_cluster, seed_qos_cluster


def _run_edge(store):
    cl = qos_edgecase_cluster()
    cid = seed_qos_cluster(store, cl)
    r = run_qos_analysis(store, cluster=cid, scope="all", k=3,
                         config_overrides={"comparison_scope": "within_group"})
    groups = store.get_qos_groups(r.run_id)
    recs = store.get_qos_recommendations(r.run_id)
    by_group = {g["id"]: [x for x in recs if x["group_id"] == g["id"]] for g in groups}
    return cl, groups, by_group, recs


def test_three_groups_including_a_singleton(store):
    _, groups, by_group, _ = _run_edge(store)
    assert len(groups) == 3
    assert sorted(g["member_count"] for g in groups) == [1, 2, 5]
    singleton = next(g for g in groups if g["member_count"] == 1)
    only = by_group[singleton["id"]][0]
    assert only["workload_name"] == "lonely"
    assert only["confidence"] == "low"            # nothing to rank against


def test_allocation_only_workload_is_low_confidence(store):
    _, _, _, recs = _run_edge(store)
    noutil = next(x for x in recs if x["workload_name"] == "big-noutil")
    assert noutil["confidence"] == "low"          # no utilization series


def test_tied_apps_get_equal_scores(store):
    _, _, _, recs = _run_edge(store)
    t1 = next(x for x in recs if x["workload_name"] == "big-tie1")
    t2 = next(x for x in recs if x["workload_name"] == "big-tie2")
    assert t1["weighted_score"] == t2["weighted_score"]
    assert t1["recommended_priority"] == t2["recommended_priority"]


def test_sparse_allocation_vector_fills_missing_dim_with_zero():
    feats = [{"cpu": 1.0, "memory": 1.0}, {"cpu": 1.0}]   # second workload lacks memory
    m = clusterer.build_feature_matrix(feats, ["cpu", "memory"])
    assert m.shape == (2, 2)
    assert m[1, 1] == 0.0
