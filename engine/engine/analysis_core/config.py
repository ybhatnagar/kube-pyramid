"""EngineConfig — all QoS algorithm thresholds/weights are config-driven.

Adapted from the vendored Job Recommender `analysis_core.config` (same
``with_overrides`` / ``from_settings`` pattern), but the job seasonality/band/jump
thresholds are replaced by the QoS knobs (docs/05 "Decisions"): k strategy, feature
scaling, per-resource weights, QoS split, priority base/step, comparison scope.
Defaults come from the state-DB `settings` row; per-run overrides arrive via the
`POST /runs` body / CLI flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional

# Resources excluded from *utilization* ranking by default: monotonic counters
# (storage) aren't a meaningful "how busy is it" signal (docs/01 Phase B / docs/03).
DEFAULT_EXCLUDED = ("ephemeral_storage",)


@dataclass(frozen=True)
class EngineConfig:
    # --- scope / windowing ---
    resources: list[str] = field(default_factory=lambda: ["cpu", "memory"])
    window: str = "7d"
    resample_freq: str = "1h"
    excluded_resources: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED))
    include_interactions: bool = True   # add the interactions pseudo-resource to the ranking

    # --- Phase A: clustering ---
    k: int = 0                          # fixed cluster count; 0 => derive via k_strategy
    k_strategy: str = "silhouette"      # "silhouette" (auto, default) | "fixed" (heuristic fallback)
    k_min: int = 2
    k_max: int = 8
    scaling: str = "log_standardize"    # "log_standardize" | "zscore" | "minmax"
    kmeans_seed: int = 0                # deterministic k-means

    # --- Phase B: ranking ---
    weights: Optional[dict] = None      # per-resource weight; None => equal across ranked dims

    # --- assignment ---
    # equal thirds by default: top -> Guaranteed, middle -> Burstable, bottom -> BestEffort
    qos_split: tuple = (1 / 3, 1 / 3, 1 / 3)
    priority_base: float = 0.0          # priority = base + step * weighted_score (score in 0..1)
    priority_step: float = 1000.0
    priority_min: int = 0
    priority_max: int = 1_000_000_000   # stay below k8s's reserved system band (2e9)

    # --- mode ---
    comparison_scope: str = "within_group"   # "within_group" | "cross_group" (util/allocation fraction)

    # --- confidence (docs/05) ---
    confidence_min_coverage: float = 0.6     # utilization coverage vs window below this counts as an issue
    confidence_boundary_gap: float = 0.05    # score-gap (0..1) to an adjacent cross-class app; below => "near boundary"

    # --- cost (optional; null unless a price/OpenCost source is configured) ---
    enable_cost: bool = False
    currency: str = "USD"
    node_hourly_cost: float = 0.0            # static per-node price fallback ($/hr); 0 => no cost estimate
    node_cpu_cores: float = 4.0              # node capacity for the over-provision fraction
    node_mem_bytes: float = 16 * 1024 ** 3
    hours_per_month: float = 730.0

    # --- runtime ---
    concurrency: int = 1

    def with_overrides(self, **kw: Any) -> "EngineConfig":
        """Return a copy with the given fields overridden (ignores None values)."""
        clean = {k: v for k, v in kw.items() if v is not None and k in self.__dataclass_fields__}
        return replace(self, **clean)

    @classmethod
    def from_settings(cls, settings: dict | None) -> "EngineConfig":
        """Build defaults from a state-DB `settings` row (thresholds JSON).

        Only QoS-relevant keys are read; unrelated keys (e.g. vendored job
        thresholds that may still sit in `settings`) are ignored.
        """
        if not settings:
            return cls()
        thr = settings.get("thresholds") or {}
        kw: dict[str, Any] = {}
        if settings.get("default_resources"):
            kw["resources"] = [r.strip() for r in str(settings["default_resources"]).split(",") if r.strip()]
        if settings.get("default_window"):
            kw["window"] = settings["default_window"]
        mapping = {
            "k": "k", "k_strategy": "k_strategy", "scaling": "scaling",
            "priority_base": "priority_base", "priority_step": "priority_step",
            "comparison_scope": "comparison_scope",
            "enable_cost": "enable_cost", "node_hourly_cost": "node_hourly_cost",
        }
        for src, dst in mapping.items():
            if thr.get(src) is not None:
                kw[dst] = thr[src]
        if thr.get("qos_split") is not None:
            kw["qos_split"] = tuple(thr["qos_split"])
        if thr.get("weights") is not None:
            kw["weights"] = dict(thr["weights"])
        return cls(**kw)

    def to_config_dict(self) -> dict:
        """The JSON persisted to analysis_runs.config (docs/04 §B)."""
        return {
            "resources": self.resources,
            "window": self.window,
            "excluded_resources": self.excluded_resources,
            "k_strategy": self.k_strategy,
            "k": self.k,
            "scaling": self.scaling,
            "weights": self.weights,
            "qos_split": list(self.qos_split),
            "priority": {"base": self.priority_base, "step": self.priority_step},
            "comparison_scope": self.comparison_scope,
            "outputs": {"cost": self.enable_cost},
        }
