# Priority ranking — deep dive

How Kube Pyramid decides which workload gets `Guaranteed` and priority
1,000, which gets `BestEffort` and priority 100, and how it renders that as
YAML you can (partially) `kubectl apply` without accidentally OOM-killing
production.

## Contents

- [The problem](#the-problem)
- [The pipeline at a glance](#the-pipeline-at-a-glance)
- [Phase A — cluster peers on allocations](#phase-a--cluster-peers-on-allocations)
- [Phase B — rank within each group](#phase-b--rank-within-each-group)
- [Assignment — QoS class + PriorityClass integer](#assignment--qos-class--priorityclass-integer)
- [Cross-group mode](#cross-group-mode)
- [Confidence](#confidence)
- [Safe YAML export](#safe-yaml-export)
- [Every knob](#every-knob)

## The problem

Kubernetes lets you signal workload importance two ways, both static and
absolute:

- **QoS class** — implicit, node-level, decided by the kubelet from
  `requests`/`limits`. `request == limit` → **Guaranteed** (last killed);
  `request < limit` → **Burstable**; neither set → **BestEffort** (first
  killed).
- **PriorityClass integer** — explicit, cluster-wide, used by the scheduler
  for preemption. Higher = higher priority.

**The paradox**: priority is inherently *relative* (it only means something
against every other workload competing for the same resources), but you set
it *absolutely* at deploy time, before knowing what else runs in the cluster.
So developers default to the safest option (Guaranteed + high priority) —
and clusters end up over-provisioned, with everyone at the "gold tier" and
nobody willing to be preempted.

Kube Pyramid looks at what actually runs in your cluster and recommends
priorities that are relative to reality.

## The pipeline at a glance

For a single run, per selected workload:

```
Phase A ────────────────────────────────
  allocations vector  →  scale  →  k-means  →  peer group

Phase B ────────────────────────────────
  utilization series  →  median   ┐
  outgoing interactions  →  sum   ├→ representative vector
                                  │
                          per-resource percentile rank (within group)
                                  │
                          weighted aggregate
                                  │
                          sort → rank position

Assignment ─────────────────────────────
  weighted score  →  base + step·score  →  PriorityClass integer (clamped)
  rank position   →  top / mid / bottom third  →  QoS class
```

## Phase A — cluster peers on allocations

**Why cluster first?** Because ranking apples-to-oranges is a mistake. A
stateful database and an ML training job both consume "cpu and memory", but
comparing their utilization percentiles directly would be misleading — the
DB is *supposed* to hold memory; the ML job is *supposed* to burn CPU.
Clustering on the allocation vector groups apps by their **developer intent**
(what they asked for) so the ranking runs peer-vs-peer.

**Feature building.** For each workload, build the allocation vector across
the union of resource dimensions seen anywhere in the run. For a missing
value, the **effective allocation** rule is:

```
requested ?? limit ?? max-utilization-over-last-3-cycles ?? 0
```

This works for any resource — cpu, memory, `nvidia.com/gpu`,
`example.com/hadoop-slots`, whatever. The collector's `allocations` table is
free-text on the resource name.

**Scaling.** Allocation dimensions have wildly different scales and units —
cpu in millicores, memory in bytes, gpu in integer counts. The default
strategy is **log-then-standardize** (log1p to tame heavy tails on big-app
outliers, then per-column z-score). `zscore` and `minmax` are available.

**k-selection.** Default is a **silhouette sweep** over `k ∈ [k_min, k_max]`
(defaults `[2, 8]`), picking the k with the highest score. Degenerate inputs
(< 3 rows, fewer distinct rows than k_min) fall back to the heuristic
`round(sqrt(n/2))` or `k=1`. Fixed k via `--k <N>` on the CLI or `k` in the
run config.

**k-means** — deterministic with a fixed `random_state` and `n_init=10`.
Output: labels + per-group centroids (in both scaled and original units,
for the group-header label).

## Phase B — rank within each group

For each group, for each member workload:

**Representative value per resource.** For each *ranked* resource (defaults
`cpu`, `memory`; excludes monotonic counters like `ephemeral_storage`), the
representative is the **median** of the prepared utilization series
(hourly-resampled from the raw samples, gap-filled). Median is robust to
spikes in a way the mean isn't.

**Interactions as a pseudo-resource.** Sum of the app's outgoing edges to
*peers in the same group*. This surfaces "hub" services that lots of the
group's other members call — they matter more even when their own CPU is
modest. Cross-group edges are ignored so the metric stays comparable to the
utilization percentiles.

**Percentile rank per resource.** Standard: for each app in the group, the
percentile rank on resource *r* is `|{peer : rep(peer, r) ≤ rep(self, r)}| / |group| × 100`.

**Weighted aggregate.** Normalize the per-resource weights to sum 1, then
combine the percentiles: `score = Σ_r w_r × pct(self, r) / 100`. Default
weights are equal across ranked resources + interactions. The UI has weight
sliders that write these.

**Sort** by descending weighted score, tie-break by uid for determinism.
Result: an explicit rank order within each group.

## Assignment — QoS class + PriorityClass integer

**PriorityClass integer** (score-proportional):

```
priority = clamp(base + step × weighted_score, priority_min, priority_max)
```

Defaults `base=0, step=1000, priority_min=0, priority_max=1e9`. So a score
of 1.0 → priority 1000, score 0.5 → priority 500. This preserves the
**relative delta** between apps: two apps at scores 0.6 and 0.3 land at
priorities 600 and 300 (the busier one is exactly twice as important as the
quieter one, not just "higher").

The clamp keeps everything below k8s's reserved system band
(2 × 10⁹), so nothing here can preempt the system priority classes.

**QoS class** — split the sorted group into thirds:

| Rank position | Class |
|---|---|
| Top third | Guaranteed |
| Middle third | Burstable |
| Bottom third | BestEffort |

Configurable via `qos_split: [0.34, 0.33, 0.33]` (in run config or global
settings). Not equal weights — top always at least 1 slot when the group is
non-empty, and rounding never leaves a class empty on the low end unless the
group is smaller than three.

## Cross-group mode

The default is `within_group`, which is what you almost always want — comparing
peers of similar allocation profiles. But when the question is *"across all
clusters combined, who's the most over-provisioned relative to what they
asked for?"*, the answer is the **cross-group** mode.

Instead of a median of raw utilization, the representative becomes the
**utilization/allocation fraction**: `median(util_r) / effective_alloc_r`.
Everything else in Phase B is unchanged. The effect: a small `batch-hot` that
sits at 80% of its 50m allocation outranks a large `serving-hot` that sits at
50% of its 500m allocation, because the small app is closer to being
resource-starved even in absolute terms.

Turn it on: `--scope cross_group` on the CLI, or the "Across all groups"
toggle at the top of the UI.

## Confidence

Every recommendation carries `high | medium | low`, derived from three signals:

1. **Coverage** — did we see enough utilization samples relative to the
   configured window? A run over a 7d window on only 1h of collected data is
   low-coverage. Threshold `confidence_min_coverage` (default 0.6).
2. **Boundary proximity** — is this app's weighted score within
   `confidence_boundary_gap` (default 0.05) of an app in a *different* QoS
   class? A workload one slot from a class cut deserves a soft warning.
3. **Allocation realness** — did we have real `requests`/`limits`, or did we
   fall through to `max-utilization-over-last-3-cycles`? A fallback allocation
   is a weaker input.

A single-member group or a workload with no utilization is always **low**.
Zero issues → **high**. One issue → **medium**. Two or more → **low**.

## Safe YAML export

A QoS-class change is not a metadata edit. In Kubernetes **the QoS class is
derived from `requests`/`limits`**, so "recommend Burstable" means rewriting
the resource block — which changes scheduling reservation, CPU throttling,
and eviction/OOM-kill order. That means naively `kubectl apply`-ing a
recommended QoS downgrade can, for real, take your app down.

Kube Pyramid's export solves this by treating the two knobs asymmetrically:

- **PriorityClass integer** — independent of QoS, only affects scheduler
  preemption order. Low risk. **Applied actively.**
- **QoS class** — derived from `requests`/`limits`; downgrades are
  behavior-affecting. **Never an active edit.** Rendered as inert commented
  guidance next to the workload's *current, unchanged* resources.

Structure of a per-workload export:

1. A `PriorityClass` object with the recommended integer.
2. A deployment patch setting `priorityClassName`.
3. The container's **current** `resources` block, shown unchanged.
4. A **commented** QoS-target block:
   - **→ Guaranteed**: commented `requests` and `limits` with the **same**
     values, sized to observed **P95 + headroom** (Guaranteed requires
     `requests == limits`).
   - **→ Burstable**: commented `requests = P50`, `limits = P95`
     (`request < limit`).
   - **→ BestEffort**: **no example values** — instead a comment telling
     the user to *remove* the entire `resources` block, plus a warning that
     Best-Effort pods are the first to be OOM-killed and to consider a canary.

The P50/P95 numbers come from the same `metric_samples` the engine ranked on.
For memory the P95 hint is floored at the observed peak (OOM guard).

The whole file is intentionally partly-inert. It's a diff you're supposed to
*review*, not a manifest you're supposed to *apply*.

## Every knob

Every threshold, weight, and behavior is config-driven. See
[`engine/engine/analysis_core/config.py`](../engine/engine/analysis_core/config.py)
for the full `EngineConfig` dataclass; the essentials:

| Knob | Default | What it does |
|---|---|---|
| `resources` | `["cpu", "memory"]` | Utilization dimensions to rank on. |
| `excluded_resources` | `["ephemeral_storage"]` | Monotonic counters skipped by default. |
| `include_interactions` | `true` | Add interactions as a pseudo-resource. |
| `k` / `k_strategy` | `0` / `"silhouette"` | Explicit k, or `"silhouette"`/`"fixed"`. |
| `scaling` | `"log_standardize"` | Feature scaling — also `zscore` or `minmax`. |
| `weights` | equal | Per-resource weights (UI writes these). |
| `qos_split` | `(1/3, 1/3, 1/3)` | Top / middle / bottom fractions. |
| `priority_base` / `priority_step` | `0` / `1000` | Score-to-integer mapping. |
| `priority_max` | `1_000_000_000` | Ceiling — below the k8s system band. |
| `comparison_scope` | `"within_group"` | Or `"cross_group"` for fraction mode. |
| `enable_cost` / `node_hourly_cost` | `false` / `0.0` | Optional savings estimate. |
| `confidence_min_coverage` | `0.6` | Coverage threshold for confidence scoring. |
| `confidence_boundary_gap` | `0.05` | Score gap for "near boundary". |

Global defaults come from the `settings` row in the state DB; per-run
overrides via `POST /runs` body or CLI flags.
