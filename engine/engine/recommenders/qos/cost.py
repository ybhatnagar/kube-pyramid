"""Optional cost / savings estimate (pure).

Ties $ to over-provisioned "gold-tier" apps: a workload deployed Guaranteed but
ranked low (recommended Burstable/BestEffort) reserves cpu/memory it doesn't use.
When OpenCost is wired (collector milestone) its per-workload cost is used directly;
until then a **static node-price fallback** estimates the monthly cost of the
*unused* reserved fraction. Returns None whenever no price/OpenCost is configured, so
runs succeed with `estimated_savings = null` (docs/04 §D, docs/05).
"""
from __future__ import annotations

from typing import Optional


def estimate_savings(
    *,
    effective_alloc: dict,     # resource -> effective allocation (cpu cores, memory bytes)
    median_util: dict,         # resource -> median utilization (same units)
    recommended_qos: str,
    current_qos: Optional[str],
    cfg,
) -> Optional[float]:
    """Monthly $ estimate of the unused reservation for a to-be-demoted gold-tier app.

    Only produces a number when cost is enabled AND a node price is set AND the app is
    currently Guaranteed but recommended below it. Otherwise None.
    """
    if not getattr(cfg, "enable_cost", False) or cfg.node_hourly_cost <= 0:
        return None
    if current_qos != "Guaranteed" or recommended_qos == "Guaranteed":
        return None

    cpu_alloc = float(effective_alloc.get("cpu", 0.0) or 0.0)
    mem_alloc = float(effective_alloc.get("memory", 0.0) or 0.0)
    cpu_used = float(median_util.get("cpu", 0.0) or 0.0)
    mem_used = float(median_util.get("memory", 0.0) or 0.0)

    cpu_unused_frac = _frac(cpu_alloc - cpu_used, cfg.node_cpu_cores)
    mem_unused_frac = _frac(mem_alloc - mem_used, cfg.node_mem_bytes)
    node_frac = max(cpu_unused_frac, mem_unused_frac)  # bottleneck resource
    if node_frac <= 0:
        return None
    return round(node_frac * cfg.node_hourly_cost * cfg.hours_per_month, 2)


def _frac(unused: float, capacity: float) -> float:
    if capacity <= 0:
        return 0.0
    return max(0.0, unused) / capacity
