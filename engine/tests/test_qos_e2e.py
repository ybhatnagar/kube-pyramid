"""End-to-end on the synthetic fixture: known rank order, priority integers, QoS thirds.

The fixture designs each group's within-group order so cpu/memory utilization medians
and interaction sums all increase with `level`; with equal weights every ranked
dimension agrees, so the expected weighted score for the app at rank position `pos`
(0 = top) in a 6-member group is (6-pos)/6, giving priority round(1000*(6-pos)/6).
"""
import pytest

from engine.recommenders.qos.runner import run_qos_analysis

EXPECTED_PRIORITIES = [1000, 833, 667, 500, 333, 167]
EXPECTED_QOS = ["Guaranteed", "Guaranteed", "Burstable", "Burstable", "BestEffort", "BestEffort"]


def _run(seeded):
    store, cluster, cid = seeded
    result = run_qos_analysis(store, cluster=cid, scope="all", k=2,
                              config_overrides={"comparison_scope": "within_group"})
    assert result.status == "completed"
    assert result.recommendations == len(cluster.workloads)  # every workload ranked
    assert result.stale is False

    groups = store.get_qos_groups(result.run_id)
    recs = store.get_qos_recommendations(result.run_id)
    by_group = {g["id"]: [r for r in recs if r["group_id"] == g["id"]] for g in groups}
    return store, cluster, result, groups, by_group


def _match_group(cluster, group_recs):
    """Map a result group to a ground-truth group label by member-set equality."""
    names = {r["workload_name"] for r in group_recs}
    for label, uids in cluster.groups.items():
        if names == {cluster.by_uid(u).name for u in uids}:
            return label
    raise AssertionError(f"group members {names} match no synthetic group")


def test_two_groups_recovered(seeded):
    _, cluster, _, groups, _ = _run(seeded)
    assert len(groups) == 2
    assert all(g["member_count"] == 6 for g in groups)


def test_known_rank_order_priorities_and_qos_thirds(seeded):
    _, cluster, _, groups, by_group = _run(seeded)
    for g in groups:
        group_recs = by_group[g["id"]]        # already ordered by insertion = ranked desc
        label = _match_group(cluster, group_recs)

        ordered_names = [r["workload_name"] for r in group_recs]
        expected_names = [cluster.by_uid(u).name for u in cluster.expected_order(label)]
        assert ordered_names == expected_names, f"rank order wrong for {label}"

        assert [r["recommended_priority"] for r in group_recs] == EXPECTED_PRIORITIES
        assert [r["recommended_qos"] for r in group_recs] == EXPECTED_QOS

        scores = [r["weighted_score"] for r in group_recs]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == pytest.approx(1.0)


def test_current_state_and_change_direction(seeded):
    _, cluster, _, groups, by_group = _run(seeded)
    for g in groups:
        group_recs = by_group[g["id"]]
        assert all(r["current_qos"] == "Guaranteed" for r in group_recs)   # priority paradox
        # top two stay Guaranteed; the rest are demoted (the tool's value)
        assert group_recs[0]["recommended_qos"] == "Guaranteed"
        assert group_recs[-1]["recommended_qos"] == "BestEffort"


def test_evidence_written_per_ranked_resource(seeded):
    store, _, _, groups, by_group = _run(seeded)
    top = by_group[groups[0]["id"]][0]
    ev = {e["resource"] for e in store.get_qos_evidence(top["id"])}
    assert {"cpu", "memory", "interactions"} <= ev


def test_confidence_high_for_full_groups(seeded):
    _, _, _, groups, by_group = _run(seeded)
    for g in groups:
        assert all(r["confidence"] == "high" for r in by_group[g["id"]])
