"""Schema contract: allocations table, free-text/custom resources, run_type='qos'."""
from engine.analysis_core.io.statestore import StateStore


def test_allocations_accepts_free_text_and_custom_resources(store):
    cid = store.ensure_cluster("c1")
    store.insert_allocations([
        {"cluster_id": cid, "workload_uid": "ns/Deployment/a", "resource": "cpu",
         "resource_kind": "standard", "requested": 0.5, "limit": 1.0, "unit": "cores"},
        {"cluster_id": cid, "workload_uid": "ns/Deployment/a", "resource": "nvidia.com/gpu",
         "resource_kind": "custom", "requested": 2.0, "limit": 2.0, "unit": "count", "is_custom": True},
        {"cluster_id": cid, "workload_uid": "ns/Deployment/a", "resource": "example.com/hadoop-slots",
         "resource_kind": "custom", "requested": 8.0, "unit": "slots", "is_custom": True},
    ])
    rows = {r["resource"]: r for r in store.load_allocations(cid, "ns/Deployment/a")}
    assert set(rows) == {"cpu", "nvidia.com/gpu", "example.com/hadoop-slots"}
    assert rows["nvidia.com/gpu"]["requested"] == 2.0
    assert rows["example.com/hadoop-slots"]["lim"] is None  # limit unset -> null


def test_allocations_upsert_is_idempotent(store):
    cid = store.ensure_cluster("c1")
    row = {"cluster_id": cid, "workload_uid": "ns/Deployment/a", "resource": "cpu", "requested": 0.5}
    store.insert_allocations([row])
    store.insert_allocations([{**row, "requested": 0.9}])
    rows = store.load_allocations(cid, "ns/Deployment/a")
    assert len(rows) == 1 and rows[0]["requested"] == 0.9  # updated in place on the natural key


def test_analysis_run_defaults_to_qos_run_type(store):
    cid = store.ensure_cluster("c1")
    run_id = store.create_analysis_run(
        name="test-run-1", cluster_id=cid, scope="all", config={},
        data_as_of=None, stale=True, ttl_hours=24,
    )
    assert store.get_run(run_id)["run_type"] == "qos"


def test_apply_schema_creates_qos_tables(store):
    # store fixture already applied the schema; assert the QoS tables exist.
    names = {r["name"] for r in store._fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"allocations", "qos_groups", "qos_recommendations", "qos_evidence", "qos_peers"} <= names
