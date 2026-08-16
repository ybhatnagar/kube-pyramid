"""Template-based Why text + downsampled evidence series."""
from engine.recommenders.qos.runner import run_qos_analysis


def test_summary_text_states_rank_qos_and_priority(seeded):
    store, cluster, cid = seeded
    r = run_qos_analysis(store, cluster=cid, scope="all", k=2)
    recs = store.get_qos_recommendations(r.run_id)
    top = next(x for x in recs if x["recommended_priority"] == 1000)   # rank #1 in its group
    s = top["summary_text"]
    assert "Guaranteed" in s
    assert "priority 1000" in s
    assert "score 1.00" in s
    assert "Top drivers" in s


def test_evidence_carries_downsampled_series_for_utilization_only(seeded):
    store, cluster, cid = seeded
    r = run_qos_analysis(store, cluster=cid, scope="all", k=2)
    recs = store.get_qos_recommendations(r.run_id)
    ev = {e["resource"]: e for e in store.get_qos_evidence(recs[0]["id"])}
    assert isinstance(ev["cpu"]["series"], list) and ev["cpu"]["series"]
    assert set(ev["cpu"]["series"][0]) == {"t", "v"}      # downsampled point shape
    assert ev["interactions"]["series"] is None            # pseudo-resource has no series
