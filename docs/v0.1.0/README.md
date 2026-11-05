# ADR — v0.1.0

The authoritative decision records for MonoAgens v0.1.0. Each ADR states one architectural decision,
the options rejected, the configuration it introduces, metrics, and code references.

**English is the sole language** for all ADRs (`adr/NNNN-slug.md`, ADR-0008 §4.1).

> [!NOTE]
> The historical pre-rebaseline decisions (ADR 0000–0055) are preserved in `docs/archive/adr-pre-rebaseline/`.
> This directory holds the unified clean-slate baseline for v0.1.0.

## How to work with it

```bash
python -P scripts/adr.py --check            # structure gate (also run by tests/test_adr.py)
python -P scripts/adr.py --new "ADR Title"  # scaffold the next number
```

`--check` verifies the contract of ADR-0008 §4.1: canonical sections, monotonic numbering, fence-aware
headings, resolvable `§` references, register synonyms scoped to definition lines, a footer naming the
file's own path, and an `Implementation` line (`implemented`, `partial`, or `draft`).

## Index

| ADR | Title | Folder | Implementation |
|---|---|---|---|
| [0001](adr/0001-sovereign-agent-and-invariants.md) | Sovereign Agent and Core Invariants — Single Tenant, One Primary, and Hexagonal Seam | root | implemented |
| [0002](adr/engine/0002-unified-memory-and-privacy.md) | Unified Memory and Layered Privacy — Native PostgreSQL, Hybrid Retrieval, and Mem0 Retired | engine | partial |
| [0003](adr/engine/0003-turn-lifecycle-lease-and-durability.md) | Turn Lifecycle, Primary Lease, and Crash Safety — Atomic Fencing and Idempotent Resume | engine | partial |
| [0004](adr/engine/0004-core-tools-and-cross-gateway-messaging.md) | Core Tools and Cross-Gateway Messaging — Universal Delivery, Origin Verification, and Tool Contracts | engine | partial |
| [0005](adr/engine/0005-unified-job-engine-and-extensions.md) | Unified Job Engine and Extension Architecture — Workflows, Tasks, Plugins, and Provider Adapters | engine | partial |
| [0006](adr/engine/0006-access-control-roles-and-guard.md) | Access Control, Role-Based Security, and Sandbox Guard — RBAC, Identity Linking, and Command Screening | engine | partial |
| [0007](adr/0007-distribution-install-and-initialization.md) | Distribution, Installation, and Initialization — Terminal Wizard, Thin Bootstrap, and Container Packaging | root | partial |
| [0008](adr/0008-modular-architecture-and-code-hygiene.md) | Modular Architecture, Code Hygiene, and Honest Budgets — ADR Home, Typed Pipeline, and Vendor Trimming | root | partial |
