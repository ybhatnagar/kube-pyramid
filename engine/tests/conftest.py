import pytest

from engine.analysis_core.io.statestore import StateStore
from engine.synth import qos_synthetic_cluster, seed_qos_cluster


@pytest.fixture
def store(tmp_path):
    s = StateStore(driver="sqlite", dsn=str(tmp_path / "qos.db"))
    s.apply_schema()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def seeded(store):
    """A fresh store seeded with the deterministic 2-group QoS fixture."""
    cluster = qos_synthetic_cluster()
    cid = seed_qos_cluster(store, cluster)
    return store, cluster, cid
