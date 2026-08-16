# Deployment guide

Kube Pyramid ships as three small container images and a Helm chart that runs
them all together. This guide covers: building the images, installing the
chart with the bundled demo Postgres, moving to an external database,
exposing it through an Ingress, and running RBAC-scoped in the cluster it
analyzes.

## Contents

- [What gets deployed](#what-gets-deployed)
- [Prerequisites](#prerequisites)
- [Building the images](#building-the-images)
- [Installing the chart](#installing-the-chart)
- [Try it on kind or minikube](#try-it-on-kind-or-minikube)
- [External Postgres](#external-postgres)
- [Ingress](#ingress)
- [RBAC](#rbac)
- [Uninstalling](#uninstalling)

## What gets deployed

Chart location: [`deploy/helm/kubepyramid`](../deploy/helm/kubepyramid).

| Component | Kind | What it does |
|---|---|---|
| **collector** | CronJob | Scheduled ingestion (default: hourly) — pulls allocations from KSM, CPU/memory/custom-resource utilization from Prometheus, and inter-pod interactions from Hubble / Istio / OTel. |
| **collector-svc** | Deployment | On-demand trigger service — the engine's `POST /collections` calls it to collect right now. |
| **engine** | Deployment + Service | The `/api/v1` REST surface, analysis pipeline, YAML export. |
| **ui** | Deployment + Service | Static wizard front-end, served via nginx. |
| **postgres** | Deployment + Service (optional) | Demo-grade Postgres, bundled for convenience. **Not production-hardened.** |
| **migrate** | Job (helm hook) | Applies the schema on install/upgrade; idempotent. |
| **RBAC** | ClusterRole + Role | Read-only cluster discovery + namespaced `secrets` get for the connectivity probe. |

## Prerequisites

- **Kubernetes ≥ 1.28** with `kubectl` context set.
- **Helm ≥ 3.13**.
- **Prometheus** reachable from inside the cluster (e.g. `kube-prometheus-stack` in the `monitoring` namespace).
- Optional: **Cilium/Hubble**, **Istio**, or **OpenTelemetry Collector** for
  interaction edges. Cpu/memory ranking works without any of these.

## Building the images

Three small images, each built from its own directory:

```bash
docker build -t kubepyramid/collector:0.1.0 collector/   # ~26 MB, distroless static Go
docker build -t kubepyramid/engine:0.1.0    engine/      # slim Python + PG driver
docker build -t kubepyramid/ui:0.1.0        ui/          # nginx + static bundle
```

Push to whatever registry your cluster can pull from and set
`images.*.repository` in `values.yaml`. Or, for a **local cluster**, load
them straight into the nodes — the chart uses `imagePullPolicy: IfNotPresent`
so no registry is needed:

```bash
# kind
kind load docker-image kubepyramid/collector:0.1.0 kubepyramid/engine:0.1.0 kubepyramid/ui:0.1.0

# Docker Desktop Kubernetes (kind-style nodes)
docker save kubepyramid/collector:0.1.0 kubepyramid/engine:0.1.0 kubepyramid/ui:0.1.0 \
  | for n in $(kubectl get nodes -o name | sed 's|node/||'); do
      docker exec -i "$n" ctr -n k8s.io images import -
    done

# minikube
minikube image load kubepyramid/collector:0.1.0 kubepyramid/engine:0.1.0 kubepyramid/ui:0.1.0
```

## Installing the chart

The quickest path — bundled Postgres, chart migrates the schema, everything
comes up on defaults:

```bash
helm install kp deploy/helm/kubepyramid \
  --namespace kubepyramid --create-namespace \
  --set collector.promUrl=http://prometheus.monitoring:9090
```

Reach the UI (via port-forward — see [Ingress](#ingress) for a proper URL):

```bash
kubectl -n kubepyramid port-forward svc/kp-kubepyramid-ui 8080:80
# → http://localhost:8080/
```

To collect **right now** rather than waiting for the CronJob:

```bash
kubectl -n kubepyramid create job --from=cronjob/kp-kubepyramid-collector collect-now
```

## Try it on kind or minikube

A complete kind quickstart with kube-prometheus-stack and the synthetic
workload set:

```bash
# 1. kind + kube-prometheus-stack
kind create cluster --name kp
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kps prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f deploy/demo/kube-prometheus-stack-values.yaml

# 2. build + load images
for m in collector engine ui; do docker build -t kubepyramid/$m:0.1.0 ./$m; done
for m in collector engine ui; do kind load docker-image kubepyramid/$m:0.1.0 --name kp; done

# 3. install Kube Pyramid, scoped to the demo namespace
helm install kp deploy/helm/kubepyramid -n kubepyramid --create-namespace \
  --set collector.promUrl=http://kps-kube-prometheus-stack-prometheus.monitoring:9090 \
  --set collector.namespaces=demo-kubepyramid

# 4. deploy the synthetic workload set
kubectl apply -f deploy/demo/synthetic-workloads.yaml

# 5. wait ~5 min for CPU history, then collect + open the UI
kubectl -n kubepyramid create job --from=cronjob/kp-kubepyramid-collector collect-now
kubectl -n kubepyramid port-forward svc/kp-kubepyramid-ui 8080:80
```

The synthetic set has 6 pods across two allocation groups (serving/large,
batch/small) with graded CPU utilization, so a full-hour collection should
recover both groups and rank `*-hot → Guaranteed`, `*-warm → Burstable`,
`*-idle → BestEffort`.

## External Postgres

**Don't ship the bundled Postgres to production.** It has no persistence
tuning, backup, or HA. Provide a real database and pass its DSN via a Secret:

```bash
kubectl -n kubepyramid create secret generic kubepyramid-db \
  --from-literal=dsn='postgres://user:pass@pg.internal:5432/kubepyramid?sslmode=require'

helm install kp deploy/helm/kubepyramid -n kubepyramid \
  --set postgres.enabled=false \
  --set database.existingSecret=kubepyramid-db \
  --set collector.promUrl=http://prometheus.monitoring:9090 \
  --set engine.corsOrigins=https://kubepyramid.your-domain
```

The migrate hook applies the schema idempotently, so re-running `helm upgrade`
is safe. Kube Pyramid needs a database with `CREATE TABLE`, `CREATE INDEX`,
and `INSERT`/`UPDATE`/`DELETE` privileges on its own database or schema.

## Ingress

```bash
helm upgrade kp deploy/helm/kubepyramid -n kubepyramid --reuse-values \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set ingress.host=kubepyramid.your-domain
```

The Ingress routes `/` → UI and `/api` → engine on one host, so the browser
talks to a single origin and no CORS configuration is needed.

## RBAC

Two roles are installed with the chart:

1. **ClusterRole (read-only, global)** — `get list` on namespaces, pods, nodes,
   deployments, statefulsets, daemonsets, replicasets. Used for discovery
   against any cluster the engine analyzes. Kube Pyramid **never writes** to a
   cluster it analyzes.

2. **Role (release namespace only)** — `get secrets`. Used by the live
   connectivity probe (`POST /clusters:test`) to resolve credential Secrets
   referenced by cluster records.

The service account is created by default. To use an existing SA, set
`serviceAccount.create=false` and `serviceAccount.name=<yours>`, and make
sure it has the equivalent grants.

## Uninstalling

```bash
helm uninstall kp -n kubepyramid
kubectl delete ns kubepyramid
# and, if you deployed them:
kubectl delete ns demo-kubepyramid monitoring
```

The chart leaves the persistent volume claim behind by default (in case you
want the collected data), so also delete the PVC to reclaim disk:

```bash
kubectl -n kubepyramid delete pvc -l app.kubernetes.io/component=postgres
```
