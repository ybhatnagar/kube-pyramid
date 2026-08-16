"""Cross-cluster fraction mode: small-hot outranks big-idle."""
import pandas as pd

from engine.recommenders.qos.crosscluster import fraction_representation
from engine.recommenders.qos.runner import run_qos_analysis


def test_fraction_representation_divides_by_allocation():
    idx = pd.date_range("2026-07-01", periods=3, freq="1h", tz="UTC")
    util = {"cpu": pd.Series([2.0, 2.0, 2.0], index=idx)}
    reps = fraction_representation(util, {"cpu": 4.0}, 5.0, ["cpu"], include_interactions=True)
    assert reps["cpu"] == 0.5           # median 2.0 / alloc 4.0
    assert reps["interactions"] == 5.0
    # zero / missing allocation -> 0.0 fraction (no division by zero)
    assert fraction_representation(util, {"cpu": 0.0}, 0, ["cpu"], False)["cpu"] == 0.0
    assert fraction_representation(util, {}, 0, ["cpu"], False)["cpu"] == 0.0


def test_cross_group_merges_and_small_hot_outranks_big_idle(seeded):
    store, cluster, cid = seeded
    r = run_qos_analysis(store, cluster=cid, scope="all",
                         config_overrides={"comparison_scope": "cross_group"})
    groups = store.get_qos_groups(r.run_id)
    assert len(groups) == 1                                   # everything merged
    assert groups[0]["member_count"] == len(cluster.workloads)

    recs = store.get_qos_recommendations(r.run_id)            # ordered by id = rank desc
    names = [x["workload_name"] for x in recs]
    # batch-6 has a tiny allocation but the highest util/alloc fraction — it tops the
    # ranking over serving-6, whose *absolute* cpu utilization is ~16x higher.
    assert names[0] == "batch-6"
    assert names.index("batch-6") < names.index("serving-6")
    assert all(x["comparison_scope"] == "cross_group" for x in recs)
