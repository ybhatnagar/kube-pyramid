"""Optional cost head: savings only for enabled + priced + demoted gold-tier apps."""
from engine.analysis_core.config import EngineConfig
from engine.recommenders.qos import cost as cost_mod

GI = 1024 ** 3
_ALLOC = {"cpu": 4.0, "memory": 8 * GI}
_UTIL = {"cpu": 0.4, "memory": 1 * GI}


def test_none_when_cost_disabled():
    cfg = EngineConfig()  # enable_cost False by default
    assert cost_mod.estimate_savings(
        effective_alloc=_ALLOC, median_util=_UTIL,
        recommended_qos="BestEffort", current_qos="Guaranteed", cfg=cfg) is None


def test_none_when_no_price():
    cfg = EngineConfig(enable_cost=True, node_hourly_cost=0.0)
    assert cost_mod.estimate_savings(
        effective_alloc=_ALLOC, median_util=_UTIL,
        recommended_qos="BestEffort", current_qos="Guaranteed", cfg=cfg) is None


def test_positive_when_enabled_priced_and_demoted():
    cfg = EngineConfig(enable_cost=True, node_hourly_cost=0.10)
    s = cost_mod.estimate_savings(
        effective_alloc=_ALLOC, median_util=_UTIL,
        recommended_qos="BestEffort", current_qos="Guaranteed", cfg=cfg)
    # bottleneck = cpu unused (4.0-0.4)/4 cores = 0.9 node-fraction * $0.10 * 730h
    assert s == round(0.9 * 0.10 * 730.0, 2)


def test_none_when_not_demoted_or_not_currently_guaranteed():
    cfg = EngineConfig(enable_cost=True, node_hourly_cost=0.10)
    assert cost_mod.estimate_savings(
        effective_alloc=_ALLOC, median_util=_UTIL,
        recommended_qos="Guaranteed", current_qos="Guaranteed", cfg=cfg) is None
    assert cost_mod.estimate_savings(
        effective_alloc=_ALLOC, median_util=_UTIL,
        recommended_qos="BestEffort", current_qos="Burstable", cfg=cfg) is None
