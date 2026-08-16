"""API tests for the /api/v1 QoS surface (FastAPI TestClient over a seeded SQLite DB)."""
import pytest
from fastapi.testclient import TestClient

from engine.analysis_core.io.statestore import StateStore
from engine.api.app import create_app
from engine.synth import qos_synthetic_cluster, seed_qos_cluster


@pytest.fixture
def client(tmp_path):
    dsn = str(tmp_path / "api.db")
    # Seed the synthetic cluster into the DB the app will read.
    seed_store = StateStore(driver="sqlite", dsn=dsn)
    seed_store.apply_schema()
    cluster = qos_synthetic_cluster()
    seed_qos_cluster(seed_store, cluster)
    seed_store.close()

    app = create_app(lambda: StateStore(driver="sqlite", dsn=dsn))
    return TestClient(app), cluster


def _run(c):
    r = c.post("/api/v1/runs", json={"cluster": "synth-qos", "k": 2,
                                     "config": {"comparison_scope": "within_group"}})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def test_health_and_cluster_crud(client):
    c, _ = client
    assert c.get("/api/v1/healthz").json()["status"] == "ok"
    created = c.post("/api/v1/clusters", json={"name": "prod-1", "api_url": "https://x:6443"})
    assert created.status_code == 201
    assert any(cl["name"] == "prod-1" for cl in c.get("/api/v1/clusters").json()["clusters"])
    # duplicate name -> 409
    assert c.post("/api/v1/clusters", json={"name": "prod-1"}).status_code == 409


def test_data_source_crud_incl_interactions_type(client):
    c, _ = client
    cid = c.post("/api/v1/clusters", json={"name": "c-ds"}).json()["id"]
    made = c.post(f"/api/v1/clusters/{cid}/data_sources",
                  json={"type": "interactions", "name": "hubble", "endpoint": "http://prom:9090",
                        "settings": {"source": "hubble"}})
    assert made.status_code == 201, made.text
    assert made.json()["type"] == "interactions"
    sid = made.json()["id"]
    upd = c.put(f"/api/v1/data_sources/{sid}", json={"enabled": False})
    assert upd.json()["enabled"] is False


def test_run_then_groups_recommendations_evidence(client):
    c, cluster = client
    run_id = _run(c)
    assert c.get(f"/api/v1/runs/{run_id}").json()["status"] == "completed"

    groups = c.get(f"/api/v1/runs/{run_id}/groups").json()
    assert len(groups["groups"]) == 2
    assert all(len(g["recommendations"]) == 6 for g in groups["groups"])
    # nested recs carry the derived change direction + priority int
    top = max(groups["groups"][0]["recommendations"], key=lambda r: r["recommended_priority_int"])
    assert top["recommended_priority_int"] == 1000
    assert top["change"] in ("raise", "lower", "unchanged")

    cards = c.get(f"/api/v1/runs/{run_id}/recommendations").json()
    assert len(cards["recommendations"]) == len(cluster.workloads)
    assert cards["run"]["comparison_scope"] == "within_group"

    rec_id = cards["recommendations"][0]["recommendation_id"]
    ev = c.get(f"/api/v1/runs/{run_id}/recommendations/{rec_id}/evidence").json()
    resources = {p["resource"] for p in ev["per_resource"]}
    assert {"cpu", "memory"} <= resources
    assert "interactions" not in resources          # interactions surfaced separately
    assert ev["interaction_sum"] is not None
    assert ev["current_vs_recommended"]["recommended_qos"] in ("Guaranteed", "Burstable", "BestEffort")


def test_export_yaml_priorityclass_active_qos_commented(client):
    c, _ = client
    run_id = _run(c)
    y = c.get(f"/api/v1/runs/{run_id}/export", params={"scope": "all"}).text
    assert "kind: PriorityClass" in y
    assert "priorityClassName: qos-rec-" in y
    # QoS-class change is commented guidance, never an active edit
    assert "QoS RECOMMENDATION" in y or "QoS class unchanged" in y
    assert "NOT applied" in y

    # per-workload export
    cards = c.get(f"/api/v1/runs/{run_id}/recommendations").json()["recommendations"]
    uid = cards[0]["workload_uid"]
    one = c.get(f"/api/v1/runs/{run_id}/export", params={"scope": "workload", "uid": uid}).text
    assert "kind: PriorityClass" in one
    assert one.count("kind: PriorityClass") == 1


def test_cross_group_run_single_group(client):
    c, cluster = client
    r = c.post("/api/v1/runs", json={"cluster": "synth-qos",
                                     "config": {"comparison_scope": "cross_group"}})
    run_id = r.json()["run_id"]
    groups = c.get(f"/api/v1/runs/{run_id}/groups").json()["groups"]
    assert len(groups) == 1
    assert groups[0]["member_count"] == len(cluster.workloads)


def test_collections_graceful_when_collector_down(client):
    c, _ = client
    # No collector service running → 503, surfaced (the UI treats this as non-fatal).
    r = c.post("/api/v1/collections", json={"cluster": "synth-qos"})
    assert r.status_code == 503


def test_cluster_test_live_probe(client):
    c, _ = client
    cid = c.get("/api/v1/clusters").json()["clusters"][0]["id"]
    # saved-cluster probe returns a structured live-probe result (unreachable in the
    # test env: synth cluster has no api_url and we're not running in a k8s pod).
    r = c.post(f"/api/v1/clusters/{cid}:test")
    assert r.status_code == 200, r.text
    assert r.json()["reachable"] is False and "detail" in r.json()
    # unknown cluster -> 404
    assert c.post("/api/v1/clusters/999999:test").status_code == 404
    # test-before-save (no persistence) against an unreachable endpoint -> structured False
    r2 = c.post("/api/v1/clusters:test", json={"api_url": "https://127.0.0.1:1", "auth_method": "token"})
    assert r2.status_code == 200 and r2.json()["reachable"] is False
    # and it did NOT create a cluster record
    assert all(cl["name"] != "127.0.0.1" for cl in c.get("/api/v1/clusters").json()["clusters"])
    # empty input must NOT silently probe the engine's own cluster -> guarded, not reachable
    empty = c.post("/api/v1/clusters:test", json={})
    assert empty.status_code == 200 and empty.json()["reachable"] is False
    assert "enter an API" in empty.json()["detail"].lower() or "credential" in empty.json()["detail"].lower()


def test_settings_roundtrip(client):
    c, _ = client
    assert c.get("/api/v1/settings").json()["default_window"] == "7d"
    upd = c.put("/api/v1/settings", json={"default_window": "14d"})
    assert upd.json()["default_window"] == "14d"
