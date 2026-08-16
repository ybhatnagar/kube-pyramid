"""QoS recommender DTOs (the typed results the runner assembles and persists).

Each maps closely to a Tier-4 table in docs/04 §B: QoSGroup -> qos_groups,
QoSRecommendation -> qos_recommendations, QoSEvidenceItem -> qos_evidence,
QoSPeer -> qos_peers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class QoSEvidenceItem:
    """Per-resource ranking evidence for the Why panel (one qos_evidence row)."""
    resource: str
    representative_value: float   # median utilization, or the interaction sum
    percentile: float             # 0..100, within the group
    weight: float
    series: Optional[list] = None  # downsampled [{"t","v"}] for the chart (utilization only)


@dataclass
class QoSPeer:
    """An interacting peer, shown inside Why (one qos_peers row)."""
    peer_workload_uid: str
    peer_workload: str
    relation: str
    affinity: float               # normalized interaction strength 0..1


@dataclass
class QoSRecommendation:
    """One workload's QoS/priority recommendation (one qos_recommendations row)."""
    workload_uid: str
    workload_kind: Optional[str]
    workload_name: Optional[str]
    namespace: Optional[str]
    recommended_qos: str          # Guaranteed | Burstable | BestEffort
    recommended_priority: int
    weighted_score: float         # 0..1
    comparison_scope: str
    confidence: str
    summary_text: str
    current_qos: Optional[str] = None
    current_priority: Optional[int] = None
    estimated_savings: Optional[float] = None
    savings_currency: Optional[str] = None
    evidence: list = field(default_factory=list)   # list[QoSEvidenceItem]
    peers: list = field(default_factory=list)       # list[QoSPeer]


@dataclass
class QoSGroup:
    """A k-means peer group (one qos_groups row) + its ranked members."""
    group_index: int
    label: str
    centroid: dict                # {resource: {"scaled": .., "original": ..}}
    member_count: int
    recommendations: list = field(default_factory=list)  # list[QoSRecommendation], ranked
