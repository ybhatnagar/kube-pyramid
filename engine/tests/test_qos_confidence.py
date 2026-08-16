"""Confidence scoring: coverage, boundary proximity, allocation-realness."""
import pytest

from engine.analysis_core.config import EngineConfig
from engine.recommenders.qos import confidence as conf


def test_window_hours_parsing():
    assert conf.window_hours("7d") == 168
    assert conf.window_hours("24h") == 24
    assert conf.window_hours("2w") == 336
    assert conf.window_hours("garbage") == 168   # default


def test_coverage_clamped():
    assert conf.coverage(168, "7d") == pytest.approx(1.0)
    assert conf.coverage(84, "7d") == pytest.approx(0.5)
    assert conf.coverage(1000, "7d") == pytest.approx(1.0)   # clamp high
    assert conf.coverage(0, "7d") == 0.0


def test_near_boundary():
    assert conf.near_boundary(0.50, [0.52], 0.05) is True
    assert conf.near_boundary(0.50, [0.70], 0.05) is False
    assert conf.near_boundary(0.50, [], 0.05) is False


def test_score_confidence_levels():
    cfg = EngineConfig()
    hi = dict(member_count=6, has_util=True, cov=1.0, boundary=False, alloc_fallback=False, cfg=cfg)
    assert conf.score_confidence(**hi) == "high"
    assert conf.score_confidence(**{**hi, "member_count": 1}) == "low"      # singleton
    assert conf.score_confidence(**{**hi, "has_util": False}) == "low"      # no utilization
    assert conf.score_confidence(**{**hi, "cov": 0.2}) == "medium"          # one issue
    assert conf.score_confidence(**{**hi, "alloc_fallback": True}) == "medium"
    assert conf.score_confidence(**{**hi, "cov": 0.2, "boundary": True}) == "low"  # two issues
