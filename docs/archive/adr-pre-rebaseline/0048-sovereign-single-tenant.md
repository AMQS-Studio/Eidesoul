# ADR-0048: Sovereign Single-Tenant Deployment — One Deployment, One Soul, One Database, One Primary

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** records the product owner's decision in `docs/note.md` and closes the multi-tenant recommendations of `docs/audit.md` and `docs/audit2.md`; builds on ADR-0012 (one lease row) and ADR-0027 (distribution).
- **Supersedes:** —
- **Related:** ADR-0012 (lease safety), ADR-0027 (distribution), ADR-0031 (engine structure), ADR-0049 (audit remediation)
- **Implementation:** implemented — the schema, lease, and deployment model already follow it; this ADR makes the choice binding and removes the competing roadmap items.

---

## 1. Context

`docs/audit.md` §3.2 (P3) and §3.1.2 (pillar 5) recommend a shared multi-tenant layer: a `tenant_id`
column on 25+ tables, composite primary keys, Postgres Row-Level Security, and a shared inference
daemon. `docs/audit2.md` §5.2 repeats the migration caveats. The audit read the missing `tenant_id` as
a defect.

It is not. The owner's note states the deployment shape deliberately:

```
1 deployment = 1 soul = 1 PostgreSQL database = 1 primary process
```

Two reasons carry the decision:

1. **Business moat.** A codebase with native shared multi-tenancy lets any third party add a login
   front end and launch a competing SaaS with no orchestration work. A sovereign single-tenant agent
   forces anyone who hosts many users to build container-per-tenant orchestration themselves.
2. **Data sovereignty.** An agent with long-term memory and layered privacy (L0–L4) is the worst place
   for a shared table: noisy neighbours, accidental cross-tenant reads, and RLS bypasses are all
   avoidable by giving each organisation, family, or person its own database.

## 2. Decision drivers

1. Privacy is a structural property of the deployment, not a filter applied at query time.
2. The schema stays small, fast, and free of a tenant key on every row.
3. Engineering effort goes into the agent's quality, not into a shared-hosting layer.
4. `engine_lease.id = 1` stays the proof that exactly one heart beats per database (ADR-0012).
5. Isolation between users of a hosted service is the infrastructure layer's job (one container and one
   database per tenant), outside this repository.

## 3. Options considered

**A. Shared multi-tenant core (`tenant_id` + RLS).** Rejected: it removes the moat, widens the blast
radius of every query bug, and costs a migration across every table and primary key.

**B. Single-tenant core with an optional tenant column.** Rejected: an optional key is a half-built
multi-tenant layer and still tempts a competitor to finish it.

**C. Sovereign single-tenant deployment.** Accepted.

## 4. Decision

### 4.1 Invariants

- One deployment holds one soul, one PostgreSQL database, and one primary process. The `engine_lease`
  row keeps `CheckConstraint("id = 1")` (`monoagens/engine/schema.py`).
- No `tenant_id`, `organization_id`, or `soul_id` column is added to any table. Composite primary keys
  that exist only to carry a tenant key are not introduced.
- RLS and a shared inference sidecar are not built. Each deployment loads its own embedder.

### 4.2 Closed audit items

| Audit item | Outcome |
| --- | --- |
| `tenant_id` on every table (P3-1) | cancelled |
| composite `engine_lease` key | cancelled; `id = 1` is the invariant |
| authenticated multi-app isolation (P3-2) | cancelled as a tenant feature; `app` scoping stays per ADR-0035 |
| shared embedding and NER daemon (P3-3) | cancelled |
| phase P3 as a whole | removed from the roadmap |

### 4.3 What the effort buys instead

Idle memory on a cheap VPS (target 350–500 MB, ADR-0051), cross-gateway commands for one owner
(ADR-0050), fewer false detections in everyday Indonesian chat (ADR-0052), crash recovery without
phantom identities or repeated sends (ADR-0049), and a one-file path to add a gateway or plugin.

### 4.4 Guidance for auditors and contributors

A missing tenant key or RLS policy is not a finding. Treat the repository as a dedicated autonomous
agent harness; user isolation across deployments belongs to the container layer.

## 5. Configuration

None. This is a structural invariant, not a setting.

## 6. Metrics and gates

- `tests/test_architecture.py` and the schema tests keep `engine_lease` single-row.
- No migration under `migrations/` adds a tenant-keyed column.

## 7. Acceptance criteria

- [x] `docs/note.md` states the intent and `docs/audit*.md` point to it.
- [x] The lease row is constrained to `id = 1`.
- [ ] A schema test fails if a column named `tenant_id` appears in `monoagens/engine/schema.py`.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A hosted offering needs many deployments | Container-per-tenant orchestration lives outside this repository |
| Per-deployment memory cost | ADR-0051 removes the second vector store and its model load |
| A future contributor re-proposes multi-tenancy | This ADR is the standing answer; reopening needs a new ADR |

## 9. Interaction with other ADRs

ADR-0012 stays the lease contract; this ADR only fixes why the row is single. ADR-0027 gains the
rule that scaling out means more deployments, not a shared one. ADR-0035 keeps `app` as a corpus
namespace inside one deployment.

## 10. Code references

- `monoagens/engine/schema.py` — `engine_lease` and its `CheckConstraint("id = 1")`.
- `monoagens/engine/jobs/lease.py` — the single-primary lease.
- `docs/note.md` — the owner's note this ADR records.

---

*This ADR lives at `docs/v0.1.0/adr/0048-sovereign-single-tenant.md`.*
