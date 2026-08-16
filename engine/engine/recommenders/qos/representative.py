"""Phase B.1–2 — representative value per (app, resource).

Utilization representative = **median** of the series (robust to spikes, docs/01
Phase B.1). Interactions enter ranking as a pseudo-resource whose representative is
the **sum** of the app's interaction counts (docs/01 Phase B.2), computed by
`analysis_core.interaction_graph.interaction_sum` and folded in here. Pure functions.
"""
from __future__ import annotations

import pandas as pd

INTERACTIONS_KEY = "interactions"


def median_representative(series: pd.Series) -> float:
    """Median of a prepared utilization series (0.0 if empty)."""
    if series is None or len(series) == 0:
        return 0.0
    return float(series.median())


def build_representation(
    util_series_by_resource: dict,
    interaction_total: float,
    ranked_resources: list[str],
    include_interactions: bool = True,
) -> dict:
    """Assemble one app's representative vector over the ranked dimensions.

    `util_series_by_resource` maps resource -> prepared pd.Series. `ranked_resources`
    is the ordered list of utilization dims to rank on (already excludes monotonic /
    user-excluded resources). Adds the interactions pseudo-resource when enabled.
    """
    reps: dict = {}
    for res in ranked_resources:
        reps[res] = median_representative(util_series_by_resource.get(res))
    if include_interactions:
        reps[INTERACTIONS_KEY] = float(interaction_total)
    return reps
