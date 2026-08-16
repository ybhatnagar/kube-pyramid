# Kube Pyramid

_Auto QoS / Priority-Class Recommender for Kubernetes._

An open-source tool that analyzes a Kubernetes cluster's **allocated resources,
historical utilization, and inter-pod interactions** and recommends a *relative*
**PriorityClass integer** and **QoS class** (Guaranteed / Burstable / BestEffort) for
every workload — so admins can fix mis-set priorities that cause over-provisioning and
starvation of critical apps. **Recommend-only: the tool never mutates the cluster.**

Design docs live in [`docs/`](docs/) (`CLAUDE.md` + `docs/01`–`08`). This is a
**standalone repo** that vendors (copies + adapts) the sibling Job Recommender's Go
collector and Python `analysis_core`, and adds a new QoS recommender head. It runs
**fully independently** of the Job Recommender: its own code copy, its own state DB
(`./kubepyramid.db`, `KUBEPYRAMID_*` env), its own `qos_*` result tables.

## Status — Milestone 5 complete (packaging + deploy)

All five milestones are built and tested: the state-DB schema, the full `recommenders/qos`
engine (silhouette clustering, ranking/assignment, cross-cluster fraction mode, confidence,
optional cost, template "Why"), the Go data collector (allocations from KSM incl. custom
resources, arbitrary-resource utilization, hubble/istio/otel interaction sources), the
`/api/v1` REST surface + YAML export, the 4-step UI wizard, **and the deployment artifacts**
— small images (distroless Go collector, slim Python engine+API, nginx UI) and the
`deploy/helm/kubepyramid` Helm chart (postgres + migrate hook + collector CronJob/trigger-service
+ engine-API + UI + RBAC). **Validated live** on a 3-node Docker Desktop cluster: helm install →
migrate → collect from real Prometheus → the engine recovered both allocation groups and ranked
`*-hot/warm/idle → Guaranteed/Burstable/BestEffort`, served through the UI's `/api` proxy. (Plus
`helm lint`/`template` + `docker build`.) Nothing reads or writes anything outside this repo.

```
$ cd engine && kubepyramid-engine run --synthetic --k 2

run gentle-quokka-2510 (id=1) — completed; 12 recommendations; stale=False

■ group 0: allocation: cpu 4.00, memory 8.00Gi, +nvidia.com/gpu  (6 apps)
  workload               rec QoS       prio  score  current     conf
  serving-6              Guaranteed    1000   1.00  Guaranteed  high
  serving-5              Guaranteed     833   0.83  Guaranteed  high
  serving-4              Burstable      667   0.67  Guaranteed  high
  serving-3              Burstable      500   0.50  Guaranteed  high
  serving-2              BestEffort     333   0.33  Guaranteed  high
  serving-1              BestEffort     167   0.17  Guaranteed  high
■ group 1: allocation: cpu 0.25, memory 256Mi  (6 apps)
  batch-6 … batch-1  (same 1000→167 / Guaranteed→BestEffort pattern)
```

Every app starts deployed `Guaranteed` (the "priority paradox"); the tool ranks each
group and demotes the lower-ranked apps.

## Repo layout
```
collector/   # Go — VENDORED from Job Recommender (renamed jobrec→kubepyramid), extended for QoS
  internal/connectors/prometheus/   # metrics + allocations (KSM) + interaction sources
  internal/steps/                   # metrics.go, allocations.go, interactions step
  internal/store/migrations/{sqlite,postgres}/
    0001_init.sql   # vendored base schema, verbatim
    0002_qos.sql    # ADDITIVE: allocations + qos_* tables + run_type + current-state cols
    0003_freetext_and_interactions.sql  # metric_samples free-text; data_sources += interactions
engine/      # Python — core engine
  engine/analysis_core/   # VENDORED: io/statestore, prepare, interaction_graph, types, config
  engine/recommenders/qos/
    cluster.py         # Phase A — effective-allocation feature build → log-standardize → k-means
    representative.py  # Phase B.1–2 — median utilization + interaction sum
    ranking.py         # Phase B.3–5 — percentile rank + weighted aggregate
    assign.py          # Phase B.6–7 — score-proportional priority + equal-thirds QoS
    runner.py          # orchestrates the above; reads DB, writes Tier-4 results
    types.py           # QoS DTOs
    export.py          # YAML export renderer (docs/08 safety model)
  engine/synth/        # QoS synthetic generator (k separable clusters, designed rank order)
  engine/api/          # FastAPI /api/v1 (app.py) + DTO builders (dto.py)
  engine/{runner,cli,collector}.py
  tests/
ui/index.html          # 4-step wizard (static; calls /api/v1); served via KUBEPYRAMID_UI_DIR
docs/  CLAUDE.md  README.md
```

## Algorithm (docs/01, docs/05)
- **Phase A — Cluster Generator:** k-means over each workload's *allocated-resource*
  vector (effective allocation = `requested ?? limit ?? max-util ?? 0`), after
  **log-then-standardize** scaling. `k` is auto-selected by a **silhouette sweep**
  (`k_min..k_max`), with a heuristic/fixed fallback for degenerate inputs. Ranking is
  always *within a cluster*.
- **Phase B — Priority Generation (per cluster):** representative value = **median**
  utilization per resource; interactions as a pseudo-resource = **sum** of the app's
  interaction counts; **percentile rank** per resource; **weighted aggregate** (equal
  weights by default); sort → within-group importance order; **PriorityClass integer**
  = `base + step·score` (score 0..1), clamped; **QoS class** = equal thirds
  (Guaranteed / Burstable / BestEffort).
- **Cross-cluster mode** (`--scope cross_group`): merge all workloads and rank on the
  **utilization/allocation fraction** `x/y` — a small app running hot outranks a big app
  idling.
- **Confidence** (high/medium/low) from utilization coverage, distance to a QoS-third
  boundary, and whether allocations were real vs a max-util fallback.
- **Cost** (optional): monthly $ of the unused reservation for over-provisioned
  gold-tier apps recommended for demotion — null unless a node price / OpenCost is set.
- **"Why"**: deterministic per-recommendation templates + a downsampled per-resource
  series stored in evidence (LLM hook scaffolded, off).

All thresholds/weights are config-driven (`EngineConfig`, defaults from the DB
`settings` row, per-run overrides via CLI/API).

## Run / test locally (no cluster needed)
```bash
# Engine (Python) — runs entirely on the synthetic generator
cd engine
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
./.venv/bin/pytest                                       # unit + e2e tests
./.venv/bin/kubepyramid-engine run --synthetic --k auto       # silhouette k-selection, human table
./.venv/bin/kubepyramid-engine run --synthetic --scope cross_group          # fraction mode
./.venv/bin/kubepyramid-engine run --synthetic --cost --node-hourly-cost 0.10   # + savings
./.venv/bin/kubepyramid-engine run --synthetic --k 2 --json   # machine-readable

# API + UI — serve /api/v1 and the wizard from the same origin
cd engine
KUBEPYRAMID_UI_DIR=../ui ./.venv/bin/kubepyramid-engine serve --port 8000 --db-dsn ./kubepyramid.db
#   → API at http://localhost:8000/api/v1, UI at http://localhost:8000/
#   POST /runs → GET /runs/{id}/groups | /recommendations/{id}/evidence | /export

# Collector (Go) — connectors, steps, store, migrations
cd collector && go test ./...
# Real ingest from a Prometheus endpoint into the state DB (CronJob/Job entrypoint):
#   collector ingest --all --prom-url http://prometheus.monitoring:9090 \
#       --interaction-source hubble --resources cpu,memory --db-dsn ./kubepyramid.db
# then: kubepyramid-engine run --cluster default --db-dsn ./kubepyramid.db
```
Each repo uses its **own venv**; the QoS engine and the Job Recommender engine share the
package name `engine` but never the same environment or database.

## Deploy to a cluster (Helm)
Small images (distroless Go collector, slim Python engine+API, nginx UI) + the
`deploy/helm/kubepyramid` chart (bundled demo Postgres, a migrate hook, the collector CronJob +
on-demand trigger service, engine-API, UI, read-only RBAC). Quickstart on kind + kube-
prometheus-stack (Cilium optional — needed only for interaction edges):
```bash
# 1. build + load images into kind
for m in collector engine ui; do docker build -t kubepyramid/$m:0.1.0 ./$m; done
kind create cluster --name qos
for m in collector engine ui; do kind load docker-image kubepyramid/$m:0.1.0 --name qos; done

# 2. metrics stack (kube-state-metrics + cAdvisor via Prometheus)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
  -f deploy/demo/kube-prometheus-stack-values.yaml

# 3. the recommender + a synthetic workload set to analyze
helm install qr deploy/helm/kubepyramid -n qos --create-namespace \
  --set collector.promUrl=http://kps-kube-prometheus-stack-prometheus.monitoring:9090
kubectl apply -f deploy/demo/synthetic-workloads.yaml

# 4. collect now, then open the UI (or GET the API)
kubectl -n qos create job --from=cronjob/qr-kubepyramid-collector collect-now
kubectl -n qos port-forward svc/qr-kubepyramid-ui 8080:80     # → http://localhost:8080/
```
The synthetic set has two allocation groups (serving/large, batch/small) with graded CPU
utilization, so the run should recover both groups and rank `*-hot → Guaranteed`,
`*-warm → Burstable`, `*-idle → BestEffort`. See [`deploy/README.md`](deploy/README.md).

## State DB (this repo's own DB — never shared)
Four tiers behind a `StateStore` interface (PostgreSQL prod, SQLite dev). QoS additions
are **purely additive** over the vendored base (new `allocations` + `qos_groups` /
`qos_recommendations` / `qos_evidence` / `qos_peers` tables, a `run_type` discriminator
defaulting to `qos`, and nullable current-state columns) — no vendored table's data or
constraints are rewritten. Full contract: [`docs/04-schema-and-api.md`](docs/04-schema-and-api.md).

### Schema evolution (migrations)
`0001_init` is the Job-Recommender base vendored **verbatim**; `0002_qos` adds the QoS
layer additively (new `allocations` + `qos_*` tables, `run_type`, nullable columns);
`0003_freetext_and_interactions` completes the two relaxations that were deferred through
M1–M2 (now that the collector ingests arbitrary/custom-resource utilization and an
`interactions` data source): `metric_samples.resource` → free-text, and
`data_sources.type` gains `'interactions'`. On Postgres these are one-line constraint
edits; SQLite (which can't alter a CHECK in place) rebuilds the two tables — a no-op copy
on a fresh DB.

## Roadmap
- **M1 ✓** — thin end-to-end on synthetic data (schema, clustering, ranking, assignment).
- **M2 ✓** — silhouette k-selection, cross-cluster fraction mode, confidence scoring,
  optional cost head, template-based "Why", expanded edge-case fixtures.
- **M3 ✓** — collector: `allocations` step (KSM, incl. extended/custom resources),
  arbitrary-resource utilization (completed the deferred `metric_samples` relaxation),
  `InteractionSource` registry (hubble/istio/otel), real collector→engine handoff.
- **M4 ✓** — `/api/v1` (`run_type='qos'`): clusters/discovery/data_sources/collections/
  runs/groups/recommendations/evidence/**export**; 4-step UI wizard with Why + Export YAML.
- **M5 ✓** — small images (distroless collector, slim engine+API, nginx UI) + the
  `deploy/helm/kubepyramid` Helm chart (postgres, migrate hook, collector CronJob + trigger
  service, engine-API, UI, RBAC); demo synthetic-workloads manifest + kube-prometheus-stack
  values. `helm lint`/`template` + `docker build` validated; kind quickstart below.
