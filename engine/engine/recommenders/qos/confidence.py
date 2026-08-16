"""Confidence scoring (pure).

`high | medium | low` from three signals (docs/05):
  * utilization **coverage** vs the analysis window,
  * within-group **separation** — is the app clearly ranked or near a QoS third cut,
  * whether allocations were **real** (request/limit) or a max-utilization fallback.
A single-member group or a workload with no utilization is always `low`.
"""
from __future__ import annotations

import re

_WINDOW_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([hdw]?)\s*$")
_UNIT_HOURS = {"h": 1.0, "d": 24.0, "w": 168.0, "": 1.0}


def window_hours(window: str, default: float = 168.0) -> float:
    """Parse '7d' / '24h' / '2w' to hours (default 7d)."""
    if not window:
        return default
    m = _WINDOW_RE.match(str(window))
    if not m:
        return default
    return float(m.group(1)) * _UNIT_HOURS[m.group(2)]


def coverage(n_points: int, window: str, freq_hours: float = 1.0) -> float:
    """Fraction of the window actually covered by samples (clamped 0..1)."""
    expected = max(1.0, window_hours(window) / max(freq_hours, 1e-9))
    return max(0.0, min(1.0, n_points / expected))


def near_boundary(score: float, neighbor_scores: list[float], gap: float) -> bool:
    """True if any adjacent *cross-class* app's score is within `gap` (0..1)."""
    return any(abs(score - ns) < gap for ns in neighbor_scores)


def score_confidence(*, member_count: int, has_util: bool, cov: float,
                     boundary: bool, alloc_fallback: bool, cfg) -> str:
    """Combine the signals into high/medium/low."""
    if member_count <= 1 or not has_util:
        return "low"
    issues = 0
    if cov < cfg.confidence_min_coverage:
        issues += 1
    if boundary:
        issues += 1
    if alloc_fallback:
        issues += 1
    if issues == 0:
        return "high"
    if issues == 1:
        return "medium"
    return "low"
