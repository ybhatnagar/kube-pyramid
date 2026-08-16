# REST API reference

Every endpoint under `/api/v1`, served by `kubepyramid-engine serve` (or
`engine serve` from a checkout). FastAPI also generates interactive
OpenAPI docs at `/docs` and a raw spec at `/openapi.json`.

## Conventions

- All request/response bodies are JSON, unless noted.
- Timestamps are ISO-8601 UTC (`2026-08-16T14:30:00Z`).
- IDs are returned as strings even when the underlying storage is integer.
- Errors follow FastAPI's default shape: `{"detail": "…"}` with the
  appropriate HTTP status code.
- The `POST /clusters:test` endpoint uses the request body directly; other
  test endpoints take path-only.

## Contents

- [Health](#health)
- [Clusters](#clusters)
- [Discovery](#discovery)
- [Data sources](#data-sources)
- [Settings](#settings)
- [Collections](#collections)
- [Runs](#runs)
- [Recommendations + evidence](#recommendations--evidence)
- [YAML export](#yaml-export)
- [DTOs](#dtos)

## Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/healthz` | Liveness/readiness. Returns `{"status": "ok"}`. |

## Clusters

Manage the set of Kubernetes clusters Kube Pyramid knows about. Adding a
cluster only records metadata + a credential reference (a k8s Secret name);
raw credentials are never stored in the DB.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/clusters` | Create a cluster record. Body: `{name, api_url?, auth_method?, credential_ref?, ca_cert?}`. Returns the created cluster DTO. |
| GET | `/api/v1/clusters` | List all clusters. |
| GET | `/api/v1/clusters/{id}` | Single cluster by id. |
| DELETE | `/api/v1/clusters/{id}` | Delete a cluster (and cascade its runs). |
| POST | `/api/v1/clusters:test` | **Live connectivity probe** on a form's fields — does NOT save. Body: `{api_url?, auth_method?, credential_ref?, ca_cert?}`. Requires at least an `api_url` or `credential_ref`. Returns `{reachable, server?, server_version?, detail?}`. |
| POST | `/api/v1/clusters/{id}:test` | Live probe for a saved cluster (reads its stored fields). Persists the outcome to `clusters.status` + `last_connected_at`. |

The auth methods supported by the probe:

| `auth_method` | Where the credential comes from |
|---|---|
| *(unset)* / `"in_cluster"` | The engine's own mounted SA token → probes the cluster the engine runs in. |
| `"token"` | Secret key `token` → Bearer. |
| `"kubeconfig"` | Secret key `kubeconfig` or `config` → server + token/cert from `current-context`. |
| `"client_cert"` | Secret keys `tls.crt` + `tls.key` → mutual TLS. |
| `"basic"` | Secret keys `username` + `password` → Basic auth. |

## Discovery

Namespaces and workloads. Served from the collector's cache (`disc_workloads`);
live-refresh (`?refresh=true`) requires the k8s client and currently returns
`501`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/clusters/{id}/namespaces` | List namespaces we've seen workloads in. |
| GET | `/api/v1/clusters/{id}/namespaces/{ns}/workloads` | Workloads in a namespace, including `current_qos` and `current_priority`. |

## Data sources

Per-cluster metric sources (Prometheus, OpenCost, mesh interactions).

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/clusters/{id}/data_sources` | Create. Body: `{type, name, endpoint?, auth_config?, settings?, enabled?}`. |
| GET | `/api/v1/clusters/{id}/data_sources` | List sources for a cluster. |
| PUT | `/api/v1/data_sources/{id}` | Partial update (any of the fields). |
| DELETE | `/api/v1/data_sources/{id}` | Delete a source. |
| POST | `/api/v1/data_sources/{id}:test` | Health-probe a source (Prometheus / OpenCost). |

Recognized types: `prometheus`, `custom_api`, `file`, `opencost`, `mesh`,
`interactions`.

## Settings

Global defaults and TTLs. Single-row.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/settings` | Read current settings. |
| PUT | `/api/v1/settings` | Partial update. Fields: `metric_ttl_hours`, `discovery_ttl_min`, `result_ttl_hours`, `default_resources`, `default_window`, `thresholds` (JSON object). |

## Collections

On-demand collection via the collector's `POST /ingest` trigger service. The
engine calls it and returns immediately; you poll the `collection_runs` row.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/collections` | Trigger a collection. Body: `{cluster_id?, cluster?, scope?, resources?, window?, interaction_source?}`. Returns `{collection_id, status}`. Returns `503` if the collector service is unreachable. |
| GET | `/api/v1/collections/{id}` | Status: `{status, progress, data_as_of, rows_written, error}`. |

## Runs

Start an analysis, poll its status, and read results.

### Start a run

`POST /api/v1/runs`

```jsonc
{
  "cluster_id": 1,               // or "cluster": "in-cluster"
  "scope": "all",                // or {"workload_uids": ["ns/Kind/name", ...]}
  "config": {
    "comparison_scope": "within_group",   // or "cross_group"
    "window": "7d",
    "resample_freq": "1h",
    "resources": ["cpu", "memory"],
    "weights": {"cpu": 1.0, "memory": 1.0, "interactions": 1.0},
    "qos_split": [0.34, 0.33, 0.33],
    "priority": {"base": 0, "step": 1000},
    "outputs": {"cost": false},
    "include_interactions": true
  },
  "collectData": false,          // if true, trigger a collection first (non-fatal)
  "k": 2,                        // or null for auto (silhouette)
  "ttl": "24h",
  "run_type": "qos"              // only accepted value in this repo
}
```

Returns `{run_id, name, status}`. The `name` is a generated slug like
`gentle-otter-4821` — human-memorable, unique per run.

### Read runs

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/runs` | Run history. Query: `cluster_id?`, `limit?` (default 50). |
| GET | `/api/v1/runs/{id}` | Status + freshness (`status`, `data_as_of`, `stale`, `progress`, `error`). |
| GET | `/api/v1/runs/{id}/groups` | Groups + nested recommendations (see [GroupDTO](#groupdto)). |
| GET | `/api/v1/runs/{id}/recommendations` | Flat recommendation list (see [QoSRecommendationDTO](#qosrecommendationdto)). |
| GET | `/api/v1/runs/{id}/recommendations/{recId}/evidence` | Evidence + peers (see [EvidenceDTO](#evidencedto)). Add `?series=false` to skip the downsampled utilization series. |

Recommendation IDs are returned as `"rec_<n>"` in DTOs; both `rec_5` and `5`
are accepted in path parameters.

## YAML export

Render the docs/08 safe-by-default YAML for a whole run or one workload.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/runs/{id}/export` | Query: `scope=all` (default) or `scope=workload&uid=<workload_uid>`. `Content-Type: text/plain`. |

Response body is YAML. Every workload block has an active PriorityClass +
priorityClassName patch and inert commented QoS guidance next to the
unchanged current resource block. Details: [`priority-ranking.md#safe-yaml-export`](priority-ranking.md#safe-yaml-export).

## DTOs

### ClusterDTO

```json
{
  "id": "1",
  "name": "prod-eu",
  "api_url": "https://10.20.0.1:6443",
  "auth_method": "kubeconfig",
  "status": "reachable",
  "created_at": "2026-08-01T12:00:00Z",
  "last_connected_at": "2026-08-16T09:12:00Z"
}
```

### WorkloadDTO

```json
{
  "kind": "Deployment",
  "name": "payments",
  "namespace": "prod",
  "workload_uid": "prod/Deployment/payments",
  "replicas": 3,
  "current_qos": "Burstable",
  "current_priority": 100,
  "requests_cpu_m": 500,
  "requests_mem_bytes": 536870912
}
```

### GroupDTO

Returned inside `GET /runs/{id}/groups`:

```jsonc
{
  "group_id": 12,
  "group_index": 0,
  "label": "allocation: cpu 4.00, memory 8.00Gi, +nvidia.com/gpu",
  "centroid_summary": "allocation: cpu 4.00, memory 8.00Gi, +nvidia.com/gpu",
  "member_count": 6,
  "recommendations": [ /* QoSRecommendationDTO */ ]
}
```

### QoSRecommendationDTO

The card payload:

```jsonc
{
  "recommendation_id": "rec_42",
  "workload": "serving-hot",
  "namespace": "demo-kubepyramid",
  "kind": "Deployment",
  "workload_uid": "demo-kubepyramid/Deployment/serving-hot",
  "group_id": 12,
  "current_qos": "Burstable",
  "current_priority": 100,
  "recommended_qos": "Guaranteed",
  "recommended_priority_int": 1000,
  "weighted_score": 1.0,
  "comparison_scope": "within_group",
  "estimated_savings": null,           // only when cost is enabled
  "savings_currency": null,
  "confidence": "high",
  "summary": "Ranked #1 of 6 vs its allocation cluster …",
  "change": "raise"                    // "raise" | "lower" | "unchanged"
}
```

`change` is derived from the QoS class order (BestEffort < Burstable < Guaranteed).

### EvidenceDTO

```jsonc
{
  "recommendation_id": "rec_42",
  "summary": "Ranked #1 of 6 vs its allocation cluster (score 1.00); …",
  "confidence": "high",
  "per_resource": [
    {
      "resource": "cpu",
      "unit": "cores",
      "representative_value": 0.99,
      "percentile": 100.0,
      "weight": 0.5,
      "series": [                     // downsampled ~200 points; omitted when ?series=false
        {"t": "2026-08-16T14:00:00Z", "v": 0.98},
        // …
      ]
    },
    {
      "resource": "memory",
      "unit": "bytes",
      "representative_value": 269484032,
      "percentile": 100.0,
      "weight": 0.5,
      "series": [ /* … */ ]
    }
  ],
  "interaction_sum": 0.0,              // pseudo-resource; may be null
  "current_vs_recommended": {
    "current_qos": "Burstable",
    "current_priority": 100,
    "recommended_qos": "Guaranteed",
    "recommended_priority_int": 1000,
    "change": "raise"
  },
  "peers": [                           // interacting peers in the same group
    {
      "peer_workload": "serving-warm",
      "peer_workload_uid": "demo-kubepyramid/Deployment/serving-warm",
      "relation": "primary upstream",
      "affinity": 0.72
    }
  ]
}
```

### CollectionDTO

```jsonc
{
  "id": "17",
  "status": "success",                 // "pending" | "running" | "success" | "failed" | "partial"
  "progress": 100,
  "data_as_of": "2026-08-16T15:00:00Z",
  "rows_written": 384,
  "error": null
}
```

### SettingsDTO

```jsonc
{
  "metric_ttl_hours": 24,
  "discovery_ttl_min": 10,
  "result_ttl_hours": 24,
  "default_resources": "cpu,memory",
  "default_window": "7d",
  "thresholds": {
    "k_strategy": "silhouette",
    "qos_split": [0.3333, 0.3333, 0.3334],
    "priority_base": 0,
    "priority_step": 1000,
    "scaling": "log_standardize"
  }
}
```

### Probe result (from `:test` endpoints)

```jsonc
{
  "reachable": true,
  "server": "https://10.96.0.1:443",
  "server_version": "v1.36.1",
  "detail": null                       // present on reachable=false, e.g. "connection refused"
}
```

## HTTP status codes at a glance

| Code | When |
|---|---|
| `200` | Success. |
| `201` | Cluster or data source created. |
| `400` | Bad body — malformed config, wrong scope value, invalid duration, etc. |
| `404` | Cluster / source / run / recommendation not found. |
| `409` | Duplicate cluster name on create. |
| `501` | Live discovery refresh (`?refresh=true`) — not wired yet. |
| `503` | Collector trigger service unreachable (`POST /collections`). |
