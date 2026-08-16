"""Runner dispatch: pick the recommender head by `run_type`.

This standalone repo only ships the QoS head, but the vendored polymorphic shape is
kept (dispatch on `run_type`) so the plumbing matches the shared contract and a
second head could slot in without changing callers.
"""
from __future__ import annotations

from typing import Any, Optional

from .analysis_core.io.statestore import StateStore
from .recommenders.qos.runner import RunResult, run_qos_analysis


def run_analysis(
    store: StateStore,
    *,
    cluster: Any,
    scope: Any = "all",
    config_overrides: Optional[dict] = None,
    ttl_hours: int = 24,
    name: Optional[str] = None,
    run_type: str = "qos",
    **kwargs,
) -> RunResult:
    """Dispatch to the recommender head for `run_type` (default: qos)."""
    if run_type == "qos":
        return run_qos_analysis(
            store, cluster=cluster, scope=scope, config_overrides=config_overrides,
            ttl_hours=ttl_hours, name=name, **kwargs,
        )
    raise ValueError(f"unknown run_type: {run_type!r} (this repo ships only 'qos')")


__all__ = ["run_analysis", "RunResult"]
