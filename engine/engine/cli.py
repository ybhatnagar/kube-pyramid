"""`engine` CLI — headless entrypoint (QoS recommender).

  engine run --synthetic [--k auto|N] [--scope within_group]   # seed synthetic data, rank, print
  engine run --cluster <id|name> [--k auto|N] [--resources cpu,memory]
  engine serve [--host 0.0.0.0 --port 8000]                    # run the FastAPI /api/v1 app
  engine init-db                                                # dev: create the SQLite schema
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .analysis_core.io.statestore import StateStore
from .recommenders.qos.runner import run_qos_analysis
from .synth import qos_synthetic_cluster, seed_qos_cluster


def _add_db_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db-driver", default="sqlite", help="sqlite|postgres")
    p.add_argument("--db-dsn", default="./kubepyramid.db", help="connection string / sqlite path")


def _open_store(args) -> StateStore:
    return StateStore(driver=args.db_driver, dsn=args.db_dsn)


def _resolve_k(raw: Optional[str]) -> Optional[int]:
    if not raw or raw == "auto":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def cmd_run(args) -> int:
    if args.scope not in ("within_group", "cross_group"):
        print(f"error: --scope must be within_group|cross_group (got {args.scope!r})", file=sys.stderr)
        return 2

    store = _open_store(args)
    try:
        if args.synthetic:
            if args.db_driver == "sqlite":
                store.apply_schema()  # dev convenience; Postgres schema comes from `collector db migrate`
            cluster = qos_synthetic_cluster()
            seed_qos_cluster(store, cluster)
            cluster_ref: object = cluster.name
        else:
            cluster_ref = int(args.cluster) if args.cluster and args.cluster.isdigit() else (args.cluster or "default")

        overrides = {"comparison_scope": args.scope, "enable_cost": args.cost}
        if args.resources:
            overrides["resources"] = [r.strip() for r in args.resources.split(",") if r.strip()]
        if args.node_hourly_cost is not None:
            overrides["node_hourly_cost"] = args.node_hourly_cost

        result = run_qos_analysis(
            store, cluster=cluster_ref, scope="all", config_overrides=overrides,
            ttl_hours=24, name=args.name, k=_resolve_k(args.k),
        )
        _print_run(store, result, as_json=args.json)
    finally:
        store.close()
    return 0


def cmd_serve(args) -> int:
    import os

    import uvicorn
    os.environ.setdefault("KUBEPYRAMID_DB_DRIVER", args.db_driver)
    os.environ.setdefault("KUBEPYRAMID_DB_DSN", args.db_dsn)
    uvicorn.run("engine.api.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_init_db(args) -> int:
    store = _open_store(args)
    try:
        store.apply_schema()
        print(f"schema ready in {args.db_dsn}")
    finally:
        store.close()
    return 0


def _print_run(store: StateStore, result, as_json: bool) -> None:
    recs = store.get_qos_recommendations(result.run_id)
    groups = {g["id"]: g for g in store.get_qos_groups(result.run_id)}

    if as_json:
        payload = {
            "run_id": result.run_id, "name": result.name, "status": result.status,
            "recommendations": result.recommendations, "data_as_of": result.data_as_of, "stale": result.stale,
            "groups": [
                {
                    "group_index": g["group_index"], "label": g["label"], "member_count": g["member_count"],
                    "recommendations": [
                        {k: r[k] for k in ("workload_name", "namespace", "recommended_qos",
                                           "recommended_priority", "weighted_score", "current_qos",
                                           "current_priority", "comparison_scope", "estimated_savings",
                                           "savings_currency", "confidence", "summary_text")}
                        for r in recs if r["group_id"] == g["id"]
                    ],
                }
                for g in store.get_qos_groups(result.run_id)
            ],
        }
        print(json.dumps(payload, indent=2))
        return

    show_cost = any(r["estimated_savings"] is not None for r in recs)
    print(f"run {result.name} (id={result.run_id}) — {result.status}; "
          f"{result.recommendations} recommendations; stale={result.stale}")
    for g in store.get_qos_groups(result.run_id):
        print(f"\n■ group {g['group_index']}: {g['label']}  ({g['member_count']} apps)")
        header = f"  {'workload':<22} {'rec QoS':<11} {'prio':>6}  {'score':>5}  {'current':<11} conf"
        if show_cost:
            header += "   savings/mo"
        print(header)
        for r in [r for r in recs if r["group_id"] == g["id"]]:
            cur = r["current_qos"] or "-"
            line = (f"  {(r['workload_name'] or r['workload_uid']):<22} {r['recommended_qos']:<11} "
                    f"{r['recommended_priority']:>6}  {r['weighted_score']:>5.2f}  {cur:<11} {r['confidence']}")
            if show_cost:
                s = r["estimated_savings"]
                line += f"   {('$' + format(s, '.2f')) if s is not None else '-':>10}"
            print(line)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engine", description="Kube Pyramid — core engine (QoS / Priority-Class Recommender)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="cluster + rank workloads and write qos_recommendations")
    run.add_argument("--synthetic", action="store_true", help="seed the built-in synthetic fixture and run on it")
    run.add_argument("--cluster", default="default", help="cluster id or name (ignored with --synthetic)")
    run.add_argument("--scope", default="within_group",
                     help="comparison scope: within_group | cross_group (util/allocation fraction)")
    run.add_argument("--k", default="auto", help="cluster count: 'auto' (silhouette) or an integer")
    run.add_argument("--resources", default=None, help="utilization resources to rank on (default cpu,memory)")
    run.add_argument("--cost", action="store_true", help="estimate savings (needs --node-hourly-cost or OpenCost)")
    run.add_argument("--node-hourly-cost", type=float, default=None,
                     help="static per-node price ($/hr) for the cost estimate")
    run.add_argument("--ttl", default="24h")
    run.add_argument("--name", default=None, help="override the generated run name")
    run.add_argument("--json", action="store_true", help="print machine-readable JSON")
    _add_db_flags(run)
    run.set_defaults(func=cmd_run)

    serve = sub.add_parser("serve", help="run the FastAPI /api/v1 app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    _add_db_flags(serve)
    serve.set_defaults(func=cmd_serve)

    init = sub.add_parser("init-db", help="create the SQLite schema (dev)")
    _add_db_flags(init)
    init.set_defaults(func=cmd_init_db)

    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
