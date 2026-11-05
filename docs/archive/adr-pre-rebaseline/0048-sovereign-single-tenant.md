# ADR-0048: Sovereign Single-Tenant Deployment — One Deployment, One Soul, One Database, One Primary

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** records the architectural invariant for a lightweight dedicated deployment model; builds on ADR-0012 (one lease row) and ADR-0027 (distribution).
- **Supersedes:** —
- **Related:** ADR-0012 (lease safety), ADR-0027 (distribution), ADR-0031 (engine structure), ADR-0049 (audit remediation)
- **Implementation:** implemented — the schema, lease, and deployment model already follow it; this ADR makes the choice binding.

---

## 1. Context

The deployment shape of MonoAgens is deliberately scoped to a single dedicated instance:

```
1 deployment = 1 soul = 1 PostgreSQL database = 1 primary process
```

Keeping each deployment dedicated ensures the schema remains compact, fast, and lightweight while
preserving strict structural data sovereignty for long-term memory and layered privacy (L0–L4).

## 2. Decision drivers

1. Privacy is a structural property of the deployment, not a filter applied at query time.
2. The schema stays small, fast, and free of extra partition keys on every row.
3. Engineering effort goes into the agent's quality and low memory footprint.
4. `engine_lease.id = 1` stays the proof that exactly one heart beats per database (ADR-0012).
5. Isolation between separate deployments belongs to the container/infrastructure layer outside this repository.

## 3. Options considered

**A. Shared multi-database or partitioned table core.** Rejected: widens the blast radius of query bugs
and adds migration and index overhead across every table and primary key.

**B. Single-deployment core with optional partition columns.** Rejected: unnecessary schema complexity.

**C. Dedicated sovereign deployment.** Accepted.

## 4. Decision

### 4.1 Invariants

- One deployment holds one soul, one PostgreSQL database, and one primary process. The `engine_lease`
  row keeps `CheckConstraint("id = 1")` (`monoagens/engine/schema.py`).
- No cross-deployment partition column is added to core tables.
- Each deployment manages its own local embedder and database connection pool.

### 4.2 Scope boundaries

| Item | Outcome |
| --- | --- |
| Cross-deployment partition columns on core tables | out of scope |
| Composite `engine_lease` key | out of scope; `id = 1` is the invariant |
| `app` corpus namespace inside one deployment | kept per ADR-0035 |
| External shared inference sidecar | out of scope |

### 4.3 Engineering focus

Idle memory on a small server (target 350–500 MB, ADR-0051), cross-gateway commands for one owner
(ADR-0050), fewer false detections in everyday Indonesian chat (ADR-0052), crash recovery without
phantom identities or repeated sends (ADR-0049), and a one-file path to add a gateway or plugin.

## 5. Configuration

None. This is a structural invariant, not a setting.

## 6. Metrics and gates

- `tests/test_architecture.py` and the schema tests keep `engine_lease` single-row.

## 7. Acceptance criteria

- [x] The lease row is constrained to `id = 1`.
- [x] Schema tables remain dedicated to a single soul per database.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Per-deployment memory cost | ADR-0051 removes the second vector store and its model load |

## 9. Interaction with other ADRs

ADR-0012 stays the lease contract; this ADR fixes why the row is single. ADR-0027 gains the
rule that scaling out means separate deployments. ADR-0035 keeps `app` as a corpus
namespace inside one deployment.

## 10. Code references

- `monoagens/engine/schema.py` — `engine_lease` and its `CheckConstraint("id = 1")`.
- `monoagens/engine/jobs/lease.py` — the single-primary lease.

---

*This ADR lives at `docs/v0.1.0/adr/0048-sovereign-single-tenant.md`.*
