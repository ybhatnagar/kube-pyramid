# Contributing

Bug reports and PRs are welcome. This is a small project with clear
contracts; it's easy to make targeted contributions without needing to hold
the whole codebase in your head.

## Contents

- [Reporting bugs](#reporting-bugs)
- [Development setup](#development-setup)
- [Running the tests](#running-the-tests)
- [Layout of a change](#layout-of-a-change)
- [Contracts you must not break](#contracts-you-must-not-break)
- [Style](#style)
- [Security](#security)
- [Releases](#releases)

## Reporting bugs

Include:

1. **The command / API call** that triggered the bug.
2. **What you expected** and **what you saw**.
3. The relevant slice of logs or output. For engine bugs, `--json` output is
   more diagnostic than the human table.
4. Kube Pyramid version (git commit sha is fine).
5. If it's a deployment/RBAC issue, `helm get values` + a description of the
   auth setup (in-cluster vs external, kubeconfig vs SA token, etc.).

Please **don't** include cluster credentials, tokens, kubeconfigs, or any of
the raw contents of Secrets you might reference. The tool only ever stores
credential *references*; PRs and issues should follow the same discipline.

## Development setup

Local, no cluster:

```bash
# Engine (Python)
cd engine
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/pytest

# Collector (Go)
cd ../collector
go build ./...
go test ./...

# UI is static — open ui/index.html against a running engine on :8000.
```

With a cluster: see [`deployment.md`](deployment.md).

## Running the tests

```bash
# Engine tests (Python) — 47+ tests, seconds
cd engine && ./.venv/bin/pytest -q

# Just the API tests
cd engine && ./.venv/bin/pytest tests/test_api.py -q

# Collector tests (Go) — including an end-to-end ingest fixture
cd collector && go test ./...
```

The engine tests are all pure/deterministic — same input, same output. The
collector tests use `httptest` fake Prometheus servers and pure-Go SQLite;
there is nothing that requires a live cluster or a live Prometheus.

Helm chart lint + templating:

```bash
helm lint deploy/helm/kubepyramid
helm template kp deploy/helm/kubepyramid
```

## Layout of a change

Try to keep a PR focused on one of these buckets. It makes reviewing much
easier and avoids surprising cross-module churn:

- **A new data source** — one connector under `collector/internal/connectors/`
  (self-registering `init()`), one step under `collector/internal/steps/` if
  it's a new data type, unit tests for the connector, and README updates
  under `collector/`.
- **An algorithm change** — a pure function under
  `engine/engine/recommenders/qos/`, with tests in
  `engine/tests/test_qos_*.py`. If the change is a new stage, the runner
  wires it in.
- **An API change** — the endpoint under `engine/engine/api/app.py`, the DTO
  builder under `engine/engine/api/dto.py`, tests under
  `engine/tests/test_api.py`, and the reference table in
  [`docs/api.md`](api.md).
- **A UI change** — the static bundle in `ui/`. Test manually against a
  running engine.
- **A deployment change** — the Helm chart under `deploy/helm/kubepyramid/`,
  values documented in [`docs/deployment.md`](deployment.md).

## Contracts you must not break

Two things are the load-bearing contracts across modules; they need extra
care in a PR.

**The state-DB schema.** The migrations under
`collector/internal/store/migrations/` are the only cross-module contract.
The engine reads exactly what the collector writes and nothing else. If you
need a new column or table:

- Add a new migration file (numbered, e.g. `0004_<what>.sql`), for **both**
  the `postgres/` and `sqlite/` dialects.
- Make it purely additive when possible: new tables, new columns with
  defaults. Non-additive changes (dropping a constraint, changing a type)
  are fine but need corresponding logic in both the collector and the engine.
- Update `docs/architecture.md` if the tier / lifecycle model changes.

**The REST DTOs.** `docs/api.md` is the source of truth for what clients see.
- If you add a field: default-null on the response side so old clients keep
  working; document it in `docs/api.md`.
- If you rename or repurpose a field: bump the API base path
  (`/api/v2/…`) rather than break `/api/v1`.

Both contracts have test coverage — new endpoints and new schema shapes
should get corresponding tests.

## Style

- **Python**: type hints where they help; single-line where they don't. No
  hard style linter enforced, but the existing codebase leans concise and
  functional (pure functions, few classes, dataclasses for structured
  values). Follow that.
- **Go**: `gofmt`; `go vet ./...` should be clean. Error wrapping via
  `fmt.Errorf("...: %w", err)`.
- **Comments**: explain *why*, not *what*. If a comment restates what the
  code does line-for-line, delete it.
- **Tests**: prefer table-driven for pure functions with many cases. Prefer
  end-to-end tests through a `TestClient` for API surface.

Every existing file is a valid style reference; pick one similar to what
you're writing and match its density.

## Security

- The tool is **read-only against a target cluster**. No PR should
  introduce a write path — no `create`, `update`, `delete`, `patch`, no
  `apply`, no mutating admission webhook. If your change would enable
  writing to a target cluster, please open an issue to discuss first.
- Credentials **never** enter the DB. Only a k8s Secret name is stored
  (`credential_ref`); the engine resolves it at probe time via its own SA.
- The YAML export **actively edits only the PriorityClass**; QoS-class
  changes are commented guidance. This is a deliberate safety property; PRs
  that make QoS-class edits active in the export need a very strong
  justification and a `--yes-really` gate. Details in
  [`priority-ranking.md#safe-yaml-export`](priority-ranking.md#safe-yaml-export).

Security-relevant issues (RCE, credential leak, RBAC escalation): please
report privately first. We'll coordinate a fix and a disclosure timeline.

## Releases

- Tag: `vMAJOR.MINOR.PATCH`.
- Semantic versioning against the two contracts (schema + REST DTOs), not
  against internal Python/Go APIs. Adding a new feature that doesn't touch
  either is `MINOR`. Breaking a contract requires `MAJOR` and an
  API-path bump.
- Chart version and app version stay in lockstep; the app version is
  what the collector reports and what the engine's `/healthz` records.
- Images are tagged with the same version and pushed to the release-configured
  registry.

Happy hacking.
