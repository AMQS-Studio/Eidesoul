# ADR-0001: Sovereign Agent and Core Invariants — Single Tenant, One Primary, and Hexagonal Seam

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates initial architectural invariants (pre-rebaseline 0000, 0040) with the authoritative single-tenant architectural intent in `docs/note.md` and pre-rebaseline 0048.
- **Supersedes:** pre-rebaseline 0000, 0040, 0048
- **Related:** ADR-0002 (memory), ADR-0003 (turn lifecycle), ADR-0004 (tools)
- **Implementation:** implemented — single lease constraint, gateway isolation, and engine seam are active in code and schema.

---

## 1. Context

MonoAgens (Eidesoul) is a persistent autonomous agent harness maintaining one continuous identity
(soul), long-term memory, and role-based access control across multiple physical appendages (gateways).
Earlier audits explored a shared multi-tenant SaaS model (adding `tenant_id` across 25+ database
tables and PostgreSQL Row-Level Security).

The product owner explicitly decided in `docs/note.md` that native shared multi-tenancy is not a
defect or oversight, but a deliberate business and technical moat. Multi-tenancy in an AI agent with
deep personal memory creates catastrophic privacy blast radiuses (noisy neighbors, RLS bypasses,
accidental cross-user memory leakage). Isolation between clients belongs strictly to the
infrastructure and container orchestration layer outside this repository.

## 2. Decision drivers

1. **Sovereignty and Privacy:** 1 Deployment = 1 Soul = 1 PostgreSQL Database = 1 Primary Process.
2. **Anti-Competition Moat:** Third parties cannot simply attach a login UI to run mass-hosted SaaS
   without engineering container-per-tenant isolation.
3. **Hexagonal Architecture:** Core engine (`monoagens/engine/`) must remain appendage-agnostic and
   never import gateway protocol SDKs.
4. **Single Outbound Door:** Every outgoing delivery and tool side effect must pass through
   `Agent.dispatch()` for centralized authorization, auditing, and idempotency.
5. **Authoritative Configuration:** Host YAML files (`config.yaml`, `roles.yaml`, `soul.yaml`, `.env`)
   are the operator's single source of truth; database document rows are derived caches.

## 3. Options considered

**A. Native Shared Multi-Tenancy (`tenant_id` + RLS).** Rejected: destroys data sovereignty, expands
the blast radius of query bugs, and removes our competitive barrier.

**B. Multi-Tenant with Shared Process & Multiple DBs.** Rejected: unnecessary complexity inside the
Python asyncio loop; container isolation outside the repo is cleaner and safer.

**C. Sovereign Single-Tenant Harness.** Accepted.

## 4. Decision

### 4.1 Sovereign Invariants

- Exactly one primary process per database enforced via `engine_lease` with `CheckConstraint("id = 1")`.
- No `tenant_id`, `organization_id`, or `soul_id` columns exist in any core schema table.
- Host configuration files are authoritative: edits to YAML files override database caches on boot.
- Secrets reside exclusively in `.env` and are never committed to version control.

### 4.2 Appendage and Gateway Isolation

- Gateways connect via contracts in `monoagens/engine/inbound.py`: `Percept`, `Address`, `Action`,
  `Receipt`, and `GatewayCore`.
- Core holds no transport-specific vocabulary (no JIDs, phone prefixes, or protocol tokens).
- Transport normalization and dispatch are owned by gateway adapters.

### 4.3 Outbound Door and Idempotency

- All side effects and messages go through `Agent.dispatch()`.
- Outbound actions require an explicit `origin: Address` to guarantee cross-room authorization.

## 5. Configuration

Core host configuration resides in `config.yaml`:
```yaml
engine:
  lease:
    ttl_seconds: 30
    renew_seconds: 10
```

## 6. Metrics and gates

- Schema tests verify `engine_lease` enforces `id = 1`.
- `tests/test_architecture.py` verifies `monoagens/engine/` contains zero gateway imports.

## 7. Acceptance criteria

- [x] Schema contains no `tenant_id` columns.
- [x] `engine_lease` CheckConstraint locks `id = 1`.
- [x] Standby processes remain frozen until the primary lease expires.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| High hosting cost per tenant | Memory footprint optimized to 350–500 MB (ADR-0002) for $5/mo VPS |
| Accidental tenant column additions | CI schema linter rejects any column named `tenant_id` |

## 9. Interaction with other ADRs

- **ADR-0002:** Scopes all memories to `global` + `person:<sender>` + `thread:<active>`.
- **ADR-0003:** Uses the single lease row to fence turn execution and job claims.

## 10. Code references

- `monoagens/engine/schema.py` — `engine_lease` table and constraints.
- `monoagens/engine/inbound.py` — `Percept`, `Address`, `Action`, `GatewayCore`.
- `monoagens/engine/agent.py` — `Agent.dispatch()` single outbound door.

---

*This ADR lives at `docs/v0.1.0/adr/0001-sovereign-agent-and-invariants.md`.*
