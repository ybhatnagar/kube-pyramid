"""Phase A M2: silhouette-based k-selection (with degenerate fallbacks)."""
import numpy as np

from engine.analysis_core.config import EngineConfig
from engine.recommenders.qos import cluster as clusterer
from engine.synth import qos_synthetic_cluster


def _scaled(cl, cfg):
    uids = [w.uid for w in cl.workloads]
    feats = [{r: s["requested"] for r, s in cl.by_uid(u).allocations.items()} for u in uids]
    dims = sorted({r for f in feats for r in f})
    return clusterer.scale_features(clusterer.build_feature_matrix(feats, dims), cfg.scaling)


def test_silhouette_picks_two_for_two_group_fixture():
    cfg = EngineConfig()
    assert clusterer.select_k_silhouette(_scaled(qos_synthetic_cluster(), cfg), cfg) == 2


def test_select_k_dispatches_to_silhouette_by_default():
    cfg = EngineConfig()  # k_strategy="silhouette", k=0
    assert clusterer.select_k(_scaled(qos_synthetic_cluster(), cfg), cfg) == 2


def test_explicit_k_bypasses_silhouette():
    cfg = EngineConfig(k=3)
    assert clusterer.select_k(_scaled(qos_synthetic_cluster(), cfg), cfg) == 3


def test_degenerate_inputs_fall_back_to_one():
    cfg = EngineConfig()
    assert clusterer.select_k_silhouette(np.zeros((0, 2)), cfg) == 1     # empty
    assert clusterer.select_k_silhouette(np.array([[1.0, 1.0]]), cfg) == 1  # single row
    assert clusterer.select_k_silhouette(np.ones((5, 2)), cfg) == 1      # all identical
