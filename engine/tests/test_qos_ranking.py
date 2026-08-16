"""Phase B pure-function tests: percentile rank, weighted aggregate, assignment."""
import pytest

from engine.analysis_core.config import EngineConfig
from engine.analysis_core.interaction_graph import interaction_sum
from engine.recommenders.qos import assign, ranking


def test_percentile_ranks_monotonic():
    assert ranking.percentile_ranks([10, 20, 30]) == pytest.approx([100 / 3, 200 / 3, 100.0])
    # ties share the higher rank (<=), lowest value still counts itself
    assert ranking.percentile_ranks([5, 5]) == pytest.approx([100.0, 100.0])
    assert ranking.percentile_ranks([]) == []


def test_normalized_weights_equal_by_default():
    w = ranking.normalized_weights(["cpu", "memory", "interactions"], None)
    assert w == pytest.approx({"cpu": 1 / 3, "memory": 1 / 3, "interactions": 1 / 3})
    assert sum(w.values()) == pytest.approx(1.0)


def test_rank_group_orders_by_weighted_score():
    # three apps, all resources agree on order a3 > a2 > a1
    members = [
        {"uid": "a1", "reps": {"cpu": 1.0, "memory": 1.0, "interactions": 0.0}},
        {"uid": "a2", "reps": {"cpu": 2.0, "memory": 2.0, "interactions": 10.0}},
        {"uid": "a3", "reps": {"cpu": 3.0, "memory": 3.0, "interactions": 20.0}},
    ]
    ranked = ranking.rank_group(members)
    assert [r["uid"] for r in ranked] == ["a3", "a2", "a1"]
    assert [round(r["weighted_score"], 4) for r in ranked] == [1.0, round(2 / 3, 4), round(1 / 3, 4)]


def test_interaction_sum_within_group_only():
    edges = [
        {"dst_workload_uid": "peer-in", "avg_count": 20.0},
        {"dst_workload_uid": "peer-out", "avg_count": 99.0},
    ]
    assert interaction_sum(edges, peer_uids={"peer-in"}) == 20.0
    assert interaction_sum(edges, peer_uids=None) == 119.0
    assert interaction_sum([], peer_uids={"x"}) == 0.0


def test_priority_integer_is_score_proportional_and_clamped():
    cfg = EngineConfig()  # base 0, step 1000, clamp [0, 1e9]
    assert assign.priority_integer(1.0, cfg) == 1000
    assert assign.priority_integer(5 / 6, cfg) == 833
    assert assign.priority_integer(4 / 6, cfg) == 667
    assert assign.priority_integer(0.5, cfg) == 500
    assert assign.priority_integer(1 / 6, cfg) == 167
    # clamp
    hi = EngineConfig(priority_step=10_000_000_000.0)
    assert assign.priority_integer(1.0, hi) == hi.priority_max


def test_qos_equal_thirds():
    assert assign.qos_for_positions(6) == [
        "Guaranteed", "Guaranteed", "Burstable", "Burstable", "BestEffort", "BestEffort",
    ]
    assert assign.qos_for_positions(3) == ["Guaranteed", "Burstable", "BestEffort"]
    assert assign.qos_for_positions(1) == ["Guaranteed"]
    assert assign.qos_for_positions(0) == []


def test_change_direction():
    assert assign.change_direction("Guaranteed", "BestEffort") == "lower"
    assert assign.change_direction("BestEffort", "Guaranteed") == "raise"
    assert assign.change_direction("Burstable", "Burstable") == "unchanged"
    assert assign.change_direction(None, "Guaranteed") == "unchanged"
