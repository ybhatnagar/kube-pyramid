"""Phase B.6–7 — PriorityClass integer + QoS class assignment (pure functions).

PriorityClass integer is **score-proportional**: `base + step * weighted_score`
(score in 0..1), clamped below k8s's reserved system band, so relative score gaps are
preserved (talk: scores x, 2x -> 100, 200) (docs/05). QoS class splits the sorted
group into thirds -> Guaranteed / Burstable / BestEffort (equal thirds default,
percentiles configurable) (docs/01 Phase B.7).
"""
from __future__ import annotations

QOS_GUARANTEED = "Guaranteed"
QOS_BURSTABLE = "Burstable"
QOS_BESTEFFORT = "BestEffort"
# Order for deriving change direction (BestEffort < Burstable < Guaranteed), docs/04 §E.
QOS_ORDER = {QOS_BESTEFFORT: 0, QOS_BURSTABLE: 1, QOS_GUARANTEED: 2}


def priority_integer(weighted_score: float, cfg) -> int:
    """Map a 0..1 weighted score to a clamped PriorityClass integer."""
    raw = cfg.priority_base + cfg.priority_step * float(weighted_score)
    val = int(round(raw))
    return max(cfg.priority_min, min(val, cfg.priority_max))


def qos_for_positions(n: int, split: tuple = (1 / 3, 1 / 3, 1 / 3)) -> list[str]:
    """QoS class for each rank position 0..n-1 (0 = top / most important).

    Equal thirds by default. The top slice is Guaranteed, the middle Burstable, the
    rest BestEffort. Rounding keeps the split stable; the top position is always at
    least Guaranteed when the group is non-empty.
    """
    if n <= 0:
        return []
    g, b = split[0], split[1]
    g_end = max(1, round(g * n))
    b_end = min(n, max(g_end, round((g + b) * n)))
    out = []
    for i in range(n):
        if i < g_end:
            out.append(QOS_GUARANTEED)
        elif i < b_end:
            out.append(QOS_BURSTABLE)
        else:
            out.append(QOS_BESTEFFORT)
    return out


def change_direction(current_qos, recommended_qos) -> str:
    """'raise' | 'lower' | 'unchanged' from current vs recommended QoS (docs/04 §E)."""
    if current_qos not in QOS_ORDER or recommended_qos not in QOS_ORDER:
        return "unchanged"
    delta = QOS_ORDER[recommended_qos] - QOS_ORDER[current_qos]
    return "raise" if delta > 0 else ("lower" if delta < 0 else "unchanged")
