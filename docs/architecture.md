# Architecture

Kube Pyramid is three independently containerized modules coupled by a
single contract — the shape of the state database. This doc walks through
each module and the data flow between them.

## The big picture

```
                    ┌──────────────────────────────┐
                    │  Prometheus (KSM + cAdvisor  │
                    │   + Hubble / Istio / OTel)   │
                    └──────────────┬───────────────┘
                                   │  PromQL
                                   ▼
                    ┌──────────────────────────────┐
                    │  Collector (Go)              │
                    │  · allocations step          │
                    │  · metrics step              │
                    │  · interactions step         │
                    └──────────────┬───────────────┘
                                   │  UPSERT
                                   ▼
                    ┌──────────────────────────────┐
                    │  State DB (PostgreSQL / SQLite) │
                    │  Tier 1  clusters, sources, settings  │
                    │  Tier 2  disc_workloads (cache)  │
                    │  Tier 3  allocations, metric_samples, │
                    │          interactions             │
                    │  Tier 4  analysis_runs, qos_groups, │
                    │          qos_recommendations,     │
                    │          qos_evidence, qos_peers  │
                    └──────────────┬───────────────┘
                                   │  SELECT + INSERT
                                   ▼
                    ┌──────────────────────────────┐
                    │  Engine + API (Python)       │
                    │  · analysis_core             │
                    │  · recommenders/qos          │
                    │  · /api/v1 REST surface      │
                    │  · YAML export renderer      │
                    └──────────────┬───────────────┘
                                   │  HTTP
                                   ▼
                    ┌──────────────────────────────┐
                    │  UI (static HTML + JS)       │
                    └──────────────────────────────┘
```

The collector **only writes**, the engine **only reads** what the collector
wrote — they never call each other directly. That gives you three practical
properties:

- The engine can re-run on stored data after a collector crash without
  re-fetching.
- Adding a new data source is a new **connector + step** — one Go file, no
  changes to the engine or the schema.
- Both modules run as small, focused containers (distroless static Go for the
  collector, slim Python for the engine + API).

## The state database — the one contract

Four tiers with different lifetimes:

| Tier | Contents | Owner | Lifetime |
|---|---|---|---|
| **1. Config** | `clusters`, `data_sources`, `settings` | UI / API | Persistent. |
| **2. Discovery cache** | `disc_namespaces`, `disc_workloads`, `disc_pods` | Collector | Short TTL (~10 min); refreshable. |
| **3. Collected data** | `allocations`, `metric_samples`, `interactions`, `collection_runs` | Collector | TTL (~1 day, configurable). |
| **4. Runs + results** | `analysis_runs`, `qos_groups`, `qos_recommendations`, `qos_evidence`, `qos_peers` | Engine | TTL (~1 day, configurable). |

The single join key across everything is `workload_uid`, formatted
`namespace/Kind/name` — deterministic, human-readable, and stable across a
discovery-cache refresh.

Detailed schema: [`design-docs/04-schema-and-api.md`](../design-docs/04-schema-and-api.md).

## Module 1 — Collector (Go)

Location: [`collector/`](../collector).

**Responsibilities.**
- Discover the workloads in a cluster (from the Kubernetes API, cached in `disc_workloads`).
- Fetch **allocations** — the N-dimensional per-workload allocation vector
  including extended/custom resources (`nvidia.com/gpu`, `example.com/hadoop-slots`, …).
- Fetch **utilization** — arbitrary-resource time series (any resource name;
  `resource_kind` classifies them as `standard | network | custom`).
- Fetch **interactions** — src → dst edge counts, from Hubble / Istio / OTel.
- Upsert everything to the state DB, idempotently.

**Extension points.**
- **`Connector` interface + registry** — one interface per data type
  (`MetricsConnector`, `AllocationsConnector`, `InteractionConnector`).
  A new source is one Go file + a `init()` that registers it.
- **`Step` interface** — a step orchestrates (resolve connector → fetch window
  → normalize → upsert). Add a new data type = new table + new step + new connector.
- **`StateStore` interface** — the DB is swappable behind a repository
  interface. Ships with Postgres (`pgx`) and SQLite (`modernc.org/sqlite`).

**Triggering.**
- **Scheduled**: k8s CronJob, `collector.schedule` in values.yaml.
- **On demand**: `collector-svc` runs a tiny HTTP service exposing `POST /ingest`.
  The engine's `POST /collections` calls it when the UI triggers a run.
- **Headless**: `collector ingest --all` for cron/one-off use.

Details: [`design-docs/03-collector-design.md`](../design-docs/03-collector-design.md).

## Module 2 — Engine + API (Python)

Location: [`engine/`](../engine).

The engine is split into a **shared analysis core** and a **QoS recommender head**.

### `analysis_core/`

Pure, reusable functions (no DB access inside them):

- **`io/statestore.py`** — the only DB code in the engine. Reads tiers 2–3,
  writes tier 4. SQLite + Postgres behind a common interface.
- **`config.py`** — `EngineConfig` dataclass: every threshold/weight is
  config-driven (`k` strategy, feature scaling, QoS split percentiles,
  priority base/step, comparison scope, cost model, etc.).
- **`prepare.py`** — resample raw `(ts, value)` points to a regular series,
  handle gaps, coverage checks.
- **`interaction_graph.py`** — the interactions pseudo-resource (sum of an
  app's outgoing edges to peers in the group).

### `recommenders/qos/`

The algorithm, six pure files that the runner composes:

- **`cluster.py`** — Phase A: build the allocation feature matrix, apply the
  effective-allocation rule (`requested ?? limit ?? max-util ?? 0`), scale
  (log-then-standardize by default), pick `k` (silhouette sweep or fixed),
  run k-means.
- **`representative.py`** — Phase B.1–2: median-utilization per resource,
  interaction sum as a pseudo-resource.
- **`ranking.py`** — Phase B.3–5: percentile rank per resource, weighted
  aggregate, sort within group.
- **`assign.py`** — Phase B.6–7: score → clamped PriorityClass integer
  (`base + step·score`), position → QoS class (equal thirds by default).
- **`crosscluster.py`** — the optional utilization/allocation fraction mode.
- **`cost.py`** — optional monthly-$ estimate for over-provisioned demoted apps.
- **`export.py`** — the safe YAML renderer (docs/08 model).

Full algorithm: [`priority-ranking.md`](priority-ranking.md).

### `api/`

FastAPI app in [`engine/engine/api/app.py`](../engine/engine/api/app.py):

- **Clusters** — CRUD, live `:test` connectivity probe.
- **Discovery** — namespaces / workloads (from the cache).
- **Data sources** — per-cluster metric sources + interaction source selection.
- **Collections** — trigger + poll on-demand collection.
- **Runs** — start an analysis, poll status, fetch grouped or flat
  recommendations, lazy per-card evidence, YAML export.

Full REST reference: [`api.md`](api.md).

### `kube.py`

A small stdlib-only Kubernetes connectivity probe (`urllib` + `ssl`, plus
PyYAML for kubeconfig parsing). No heavy k8s client dependency. Powers the
`/clusters:test` and `/clusters/{id}:test` endpoints:

- **No credential reference** → probe the cluster the engine runs in (via its
  own ServiceAccount).
- **With a credential reference** → read the referenced k8s Secret, resolve
  by auth method (`token` / `kubeconfig` / `client_cert` / `basic`), probe
  the target cluster's `/version`.

## Module 3 — UI

Location: [`ui/`](../ui).

A single-page static bundle (`index.html`), served by nginx in production.
No build step, no framework — vanilla JS wired to `/api/v1`. Four screens:

1. **Connect cluster** — card grid + "Add cluster" modal with live "Test
   connection" (no need to save first).
2. **Select workloads** — namespace / workload tree with per-workload
   checkboxes; feeds into `scope: {workload_uids: [...]}` on the run.
3. **Data sources & run** — interaction source picker (Hubble / Istio /
   OTel), utilization window, k strategy, resource weight sliders, optional
   cost estimate.
4. **Recommendations** — grouped by allocation cluster, with the
   current → recommended transition per workload, "Why?" evidence, and per-row
   or bulk YAML export.

In production the chart mounts an nginx config that also proxies `/api/` to
the engine service, so the browser hits a single origin.

## Why this shape?

**Separation of concerns.** The collector is stateless I/O — pull, normalize,
upsert. The engine is stateless compute — read, cluster, rank, write.
Nothing about the two modules needs to know about the other's runtime.

**Testability.** Every stage of the algorithm is a pure function over arrays
or dataframes. The engine's whole test suite (~47 tests today) runs against
the synthetic generator with no cluster and no Prometheus.

**Extensibility.** New data source = new `Connector`. New algorithm step =
new pure function. New DB backend = new `StateStore`. Because the schema is
the only contract, none of these ripple into the other modules.

**Deployability.** The static Go collector is a distroless ~26 MB image with
no libc. The engine is a slim Python image with only the wheels it needs.
The UI is nginx + a single HTML file. Nothing that isn't strictly required to
run this specific tool.

## Related reading

- [**REST API reference**](api.md) — every endpoint, DTOs, request bodies.
- [**Priority ranking deep dive**](priority-ranking.md) — the algorithm, the
  safe-YAML export model, and where the parameters come from.
- [**Deployment guide**](deployment.md) — Helm chart, external DB, ingress.
- **Design docs** in [`design-docs/`](../design-docs/) — the original design
  discussions this repo was built from (numbered 01–08, plus
  `build-prompts.md`). Kept as historical context.
