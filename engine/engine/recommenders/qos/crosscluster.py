"""Cross-cluster comparison mode (optional).

To compare apps *across* allocation clusters, merge everything into one group and
replace raw utilization with the **utilization / allocation fraction** `x' = x / y`
(so a small app running hot outranks a big app idling), then run Phase B ranking on
those fractions (docs/01 "Cross-cluster comparison", docs/05). Pure functions; the
runner supplies loaded utilization + effective allocations.
"""
from __future__ import annotations

from .representative import INTERACTIONS_KEY, median_representative


def fraction_representation(
    util_series_by_resource: dict,
    effective_alloc_by_resource: dict,
    interaction_total: float,
    ranked_resources: list[str],
    include_interactions: bool = True,
) -> dict:
    """One app's representative vector using utilization/allocation fractions.

    For each ranked resource: fraction = median(utilization) / effective_allocation
    (0.0 when the allocation is unknown/zero). Interactions enter as the raw sum, as
    in within-group mode.
    """
    reps: dict = {}
    for res in ranked_resources:
        med = median_representative(util_series_by_resource.get(res))
        y = float(effective_alloc_by_resource.get(res, 0.0) or 0.0)
        reps[res] = (med / y) if y > 0 else 0.0
    if include_interactions:
        reps[INTERACTIONS_KEY] = float(interaction_total)
    return reps
