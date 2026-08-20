## What & why

<!--
One paragraph on what this PR changes and *why*. Reviewers should be able to
form an opinion from this section alone. Include a link to the issue if one
exists.
-->

## How

<!--
Anything about the approach that isn't obvious from the diff:
- shape of the change,
- alternatives you considered,
- follow-ups you're deliberately deferring.
-->

## Testing

<!--
- What tests were added / updated?
- Anything that had to be verified manually (kind cluster, real Prometheus,
  UI walkthrough), and how a reviewer would reproduce it.
-->

## Contract check

- [ ] No schema changes, **or** a new numbered migration file was added under
      `collector/internal/store/migrations/{sqlite,postgres}/`.
- [ ] No `/api/v1` DTO changes, **or** the change is additive and documented in
      [`docs/api.md`](../docs/api.md).
- [ ] Safe-YAML export invariant preserved: **PriorityClass is the only active
      edit**; QoS-class changes remain commented guidance. See
      [`docs/priority-ranking.md#safe-yaml-export`](../docs/priority-ranking.md#safe-yaml-export).
- [ ] Docs updated for any user-observable change (README / `docs/`).
- [ ] All existing tests still pass locally.

---

<sub>Kube Pyramid is read-only advisory tooling — please don't introduce
anything that mutates the target Kubernetes cluster.</sub>
