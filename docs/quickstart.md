# Quickstart — Kube Pyramid in 5 minutes

Kube Pyramid ships a synthetic-cluster generator, so you can go from zero to
real recommendations without a Kubernetes cluster or a Prometheus. This
walk-through takes about 5 minutes.

## What you'll need

- **Python ≥ 3.9** (to run the engine + API).
- No Kubernetes cluster, no Prometheus. Everything runs against SQLite locally.

## 1. Install the engine

```bash
cd engine
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

The engine bundles `numpy`, `pandas`, `scikit-learn`, and FastAPI. Install
takes about a minute from cold.

## 2. Seed a synthetic cluster and get recommendations

```bash
# Ranks 12 synthetic workloads (2 allocation groups × 6 apps) and writes a
# fresh SQLite DB at ./demo.db.
./.venv/bin/kubepyramid-engine run --synthetic --k 2 --db-dsn ./demo.db
```

You should see two allocation groups recovered, each with the designed clean
ordering — hot → Guaranteed, warm → Burstable, idle → BestEffort — at priority
1000 / 833 / 667 / 500 / 333 / 167. All at **high** confidence.

```
■ group 0: allocation: cpu 4.00, memory 8.00Gi, +nvidia.com/gpu  (6 apps)
  workload         rec QoS        prio  score  conf
  serving-6        Guaranteed     1000   1.00  high
  serving-5        Guaranteed      833   0.83  high
  serving-4        Burstable       667   0.67  high
  serving-3        Burstable       500   0.50  high
  serving-2        BestEffort      333   0.33  high
  serving-1        BestEffort      167   0.17  high

■ group 1: allocation: cpu 0.25, memory 256Mi  (6 apps)
  batch-6          Guaranteed     1000   1.00  high
  batch-5          Guaranteed      833   0.83  high
  batch-4          Burstable       667   0.67  high
  batch-3          Burstable       500   0.50  high
  batch-2          BestEffort      333   0.33  high
  batch-1          BestEffort      167   0.17  high
```

Try `--json` for machine-readable output, or `--scope cross_group` to rank on
the **utilization ÷ allocation fraction** (small hot apps outrank big idle
ones, across groups).

## 3. Compare with cross-group (fraction) mode

```bash
./.venv/bin/kubepyramid-engine run --synthetic --scope cross_group --db-dsn ./demo.db
```

Now the small `batch-6` (fully utilized inside its tiny allocation) tops the
list ahead of `serving-6` (large allocation, similarly-hot in absolute terms
but a smaller fraction). This is the mode to use when comparing apps
across allocation clusters.

## 4. Try the "priority paradox" story

The default synthetic fixture also seeds *current* QoS classes and
PriorityClass integers for each workload, so you can see the transitions the
tool would recommend:

- `serving-hot`: **Burstable(100) → Guaranteed(1000)** — under-prioritized, needs a raise.
- `serving-idle`: **Guaranteed(1,000,000) → BestEffort(333)** — over-provisioned "gold-tier" pod, safe to demote.

The corresponding YAML export **actively edits only the PriorityClass**; the
QoS-class change is rendered as *commented* guidance so `kubectl apply`
cannot silently damage the workload. See [priority-ranking.md](priority-ranking.md#safe-yaml-export).

## 5. Serve the API + UI (walk the wizard)

```bash
# From the engine/ folder:
KUBEPYRAMID_UI_DIR=../ui ./.venv/bin/kubepyramid-engine serve \
    --db-dsn ./demo.db --port 8000
```

Open <http://localhost:8000/> and step through the 4-step wizard:

1. **Connect cluster** → cards for existing clusters + an "Add cluster" modal
   with a live "Test connection" button.
2. **Select workloads** → the discovery tree with per-namespace and
   per-workload checkboxes.
3. **Data sources & run** → confirm the interaction source (Hubble / Istio /
   OTel), weights, and cluster count *k*; click "Collect & generate".
4. **Recommendations** → grouped by allocation cluster, with the current →
   recommended transition per workload, a "Why?" evidence panel, and per-row
   or bulk YAML export.

The API's OpenAPI docs are auto-generated at
<http://localhost:8000/docs>. Full REST reference: [`api.md`](api.md).

## 6. What next?

- **Deploy to a cluster**: [`deployment.md`](deployment.md) — Helm chart,
  kind/minikube, external Postgres, ingress, RBAC.
- **Understand the algorithm**: [`priority-ranking.md`](priority-ranking.md) — the
  clustering + ranking pipeline, cross-group mode, confidence, and the
  safe-YAML export model.
- **Learn the architecture**: [`architecture.md`](architecture.md) — the shared
  analysis core, the state-DB contract, and how the collector fits in.

## Something didn't work?

The engine is deterministic on the synthetic fixture — same seed, same output.
If you see different numbers or a failure, please [file an issue](../CONTRIBUTING.md#reporting-bugs)
with the command you ran and the output, and we'll take a look.
