# ADR-0029: Test Store Is the Real Store — Postgres+pgvector, One Test Database, Truncate per Test

- **Status:** accepted
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0016 (canonical schema and migration), ADR-0001 (memory source of truth), ADR-0011 (crash safety), ADR-0012 (lease safety), ADR-0026 (search), ADR-0027 (distribution and default-off capability)
- **Supersedes:** —
- **Related:** ADR-0009 (the gate these tests run beside), ADR-0019 (observability), ADR-0021 / ADR-0024 (tool contracts)
- **Implementation:** implemented — evidence: `tests/conftest.py`, `tests/support.py`, `tests/test_pgvector.py`, `eidesoul/engine/db.py`, `eidesoul/engine/schema.py`, `compose.yaml`; gate result: `pytest -q` 561 passed in 31 s on Postgres+pgvector, unset variable failure verified
- **Location note:** this ADR binds every surface (it decides what "verified" means), so it sits at the root of the ADR home. The Indonesian copy is `docs/v0.1.0/adr/id/0029-test-store.md`.
- **Amendments:** 2026-11-03 — first version.

---

## 1. Context

The suite runs on SQLite today, which means several shipped mechanisms are **simulated** rather than tested:

| Mechanism | Why SQLite cannot represent it | Evidence |
|---|---|---|
pgvector and Mem0 | semantic memory is skipped entirely on a SQLite URL | `eidesoul/engine/memory/__init__.py:178` |
Upsert semantics | the code chooses a dialect-specific insert, so only one branch is exercised | `eidesoul/engine/db.py:12-13,123-124` |
Real DDL and migration | tests build tables through `ensure()`/`ensure_schemas()`, never through the production `db upgrade` path | `tests/support.py:88,539` |
`JSONB` behaviour | it degrades to plain JSON; containment and path operators are never exercised | `eidesoul/engine/schema.py:22` |
Vector maintenance | `ensure_vector()` is best-effort and a no-op without the extension | `eidesoul/engine/db.py:76-90` |
`timestamptz`, row locks, claim races | Postgres semantics with no SQLite equivalent (ADR-0011/0012) | `eidesoul/engine/db.py:46-49` |

The cost of the switch is bounded because the dependence is centralised: 522 tests in 56 files, only five
files mention SQLite, and the harness builds the store in one place (`tests/support.py:71,88,515,539`).
Fixing the harness moves the whole suite; keeping SQLite keeps a second, shipped-but-untested code path.

## 2. Decision drivers

| Driver | Consequence |
|---|---|
| The tested store is the shipped store | One engine path, no dialect branches, no "works on my test DB" |
| Honest coverage | pgvector, `ON CONFLICT`, JSONB operators, row locks, and real migrations are exercised |
| Determinism | no model download and no network inside a test run |
| Fast, isolated | one database, DDL once per session, truncation per test |
| Explicit failure | a missing test database is a loud error, never a silent downgrade |
| Small surface | the harness is one module; the suite keeps running without a rewritten test |
| CI-ready | a service container is enough; no fixture framework is introduced |

## 3. Options considered

| Option | Summary | Value |
|---|---|---|
| **A. Keep SQLite** | the suite keeps its memory database | Rejected: the simulated mechanisms above stay untested and the dialect branches stay alive |
| **B. Postgres for everything** | one store, the shipped one, with a throwaway database per test run | **Chosen** |
| **C. Two lanes (SQLite fast, Postgres thorough)** | a fast subset plus a Postgres lane | Rejected for now: two paths mean the quick one is not proof, and the code keeps its SQLite branch |
| **D. Ephemeral containers per run (testcontainers-style)** | the suite starts and stops its own server | Deferred: it adds a dependency and start-up cost for something an operator-provided DSN already gives |

## 4. Decision

### 4.1 One store

The suite runs against **Postgres with `pgvector`**, the same store the engine ships with.

- The DSN comes from `EIDESOL_TEST_DATABASE_URL`.
- When it is unset the suite **fails with that variable's name and the exact command to start a test
  database** — it never falls back to SQLite and never skips the store-backed tests.
- The test database is a throwaway: it holds nothing an operator cares about, and it is never the dev or
  production database (different name, different port, separate compose project).

### 4.2 Isolation

| Level | Rule |
|---|---|
| Session | connect once, `CREATE EXTENSION IF NOT EXISTS vector`, then run the **production migration path** (`db upgrade`) once |
| Test | `TRUNCATE <every table> RESTART IDENTITY CASCADE` before the test body, so no test sees another's rows |
| Migration tests | a dedicated fixture may create and drop its own database; it never truncates the shared one |
| Gateway schemas | created by the session fixture through the same `ensure_schemas` the engine calls |

### 4.3 Determinism

- The embedder is replaced by a **deterministic stub at 384 dimensions** inside tests, so no model is
  downloaded and no test depends on timing or network. Production keeps the single shared embedder
  (ADR-0001); the stub lives in the test harness only.
- pgvector is exercised with those stub vectors: at least one test asserts that a nearest-neighbour query
  returns the row it should, and that a deleted or irrelevant row does not.
- No test reaches the network; a test that needs an external service is marked as such and is not part of
  the default run.

### 4.4 SQLite is removed, not kept as a fallback

- `tests/support.py` stops offering a SQLite URL and stops building a temporary config around it.
- The dialect branches go with it: the insert-dialect choice in `eidesoul/engine/db.py`, the SQLite skip in
  `eidesoul/engine/memory/__init__.py`, the integer-variant primary key in `eidesoul/engine/schema.py`,
  and the `"sqlite"` mention in the storage rule in `AGENTS.md`.
- `JSONB` stays a Postgres type; nothing pretends to be portable any more.

### 4.5 Speed budget

Session DDL happens once and per-test isolation is a truncate, so the target is **≤ 180 s for the full
suite**. A test that is genuinely slow is marked and reported, never skipped silently; a test that needs a
fresh database owns that cost explicitly.

### 4.6 What still gates a change

`pytest -q` (now store-backed), `python -m eidesoul selftest`, and `python scripts/adr.py --check` remain
the three gates. A change is not verified because SQLite said so, and not verified by a green subset.

## 5. Configuration

| Key | Default | Meaning |
|---|---|---|
| `EIDESOL_TEST_DATABASE_URL` | unset → the suite fails with instructions | DSN of the throwaway test database |
| `POSTGRES_DB` (compose) | `eidesoul` | parameterised so a test stack uses `eidesoul_test` |
| `POSTGRES_PORT` (compose) | `5432` | parameterised so a test stack can run beside dev on `5433` |
| compose project | `eidesoul-test` for the test stack | separate volumes, separate network, separate database |

The dev stack keeps its own values; nothing here changes how an operator runs the engine.

## 6. Metrics and gates

| Metric | Initial target |
|---|---|
| Dialect branches for a second store in `eidesoul/engine` | 0 |
| Store-backed tests that can silently fall back to SQLite | 0 |
| Mechanisms from §1 with no test on the real store | 0 (pgvector, upsert, `db upgrade`, JSONB, locks) |
| Full-suite runtime | ≤ 180 s |
| Model downloads during a test run | 0 (stub embedder) |
| Network calls during a test run | 0 |
| Tests that build tables with `metadata.create_all` instead of `db upgrade` | 0 outside migration-specific fixtures |

## 7. Acceptance criteria

- [ ] `pytest -q` with `EIDESOL_TEST_DATABASE_URL` set runs the whole suite on Postgres+pgvector, and green means green on the shipped store.
- [ ] `pytest -q` without it fails with one message naming the variable and the compose command to start a test database; no test silently skips.
- [ ] The session runs `db upgrade` (the production path) and `CREATE EXTENSION vector`; the schema it produces matches `SCHEMA_VERSION`.
- [ ] At least one test asserts a pgvector nearest-neighbour result through the memory path (not through a hand-written SQL string in the test).
- [ ] At least one test asserts `memory/__init__.py` no longer skips anything: the semantic path runs on the test store.
- [ ] No test downloads a model: the embedder is the 384-dimension stub and the suite passes offline.
- [ ] Two consecutive runs of the same test file produce identical results (truncation, no order dependence).
- [ ] Grep finds no `sqlite` in `eidesoul/engine/**` and none in `tests/**`; `AGENTS.md` no longer calls SQLite a valid store for tests.
- [ ] Full-suite runtime is at or below the §6 budget on this workstation, measured once with the report pasted into the change.
- [ ] `python -m eidesoul selftest` and `python scripts/adr.py --check` still pass.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| First Postgres run turns tests red and the temptation is to weaken them | Red means a bug that SQLite hid: the fix goes into the engine, and the test stays; blocking a store-backed test requires an ADR amendment, not a skip |
| The suite becomes slow enough that nobody runs it | One DDL per session, truncate per test, no model load; §6 budget is measured and reported |
| A developer without a database is blocked | The failure message carries the one-line compose command; the test stack is a throwaway, not a service to operate |
| Truncation deadlocks or leaves rows behind | A single connection per test, `RESTART IDENTITY CASCADE`, and a test that asserts the store is empty after teardown |
| Removing the dialect branches breaks a hidden dependency | The branches are deleted in a separate, greppable step; the suite must still pass before the deletions land |
| pgvector missing on the operator database is confused with a test failure | `ensure_vector()` stays best-effort in production (ADR-0001); the test database requires the extension and says so when it is absent |
| CI has no service container yet | The lane is defined here; wiring CI is a follow-up change, and until then the local command is the same one the message prints |

## 9. Interaction with other ADRs

- **ADR-0001**: the pgvector and Mem0 path stops being invisible to tests; the single write port is what the tests exercise.
- **ADR-0011 / ADR-0012**: queue claim and lease CAS are tested against real row locks, not a single-writer file database.
- **ADR-0016**: `db upgrade` becomes the only way tests obtain a schema, so the migration path is exercised on every run.
- **ADR-0026**: `tsvector` and `pg_trgm` are tested on the store that can actually run them.
- **ADR-0027**: the test stack is a second compose project with its own database and port; the dev stack is untouched.
- **ADR-0009**: the ADR gate stays independent of the store; documentation checks never need a database.

## 10. Code references

- `tests/support.py:71,88,515-539` — the SQLite DSN, the temporary config, and the helpers to replace.
- `eidesoul/engine/db.py:12-13,46-49,76-90,123-124` — dialect branches, `ensure_vector()`, insert-dialect choice.
- `eidesoul/engine/memory/__init__.py:178` — the SQLite skip that disappears with this ADR.
- `eidesoul/engine/schema.py:22-24` — `JSONB` and the integer-variant primary key.
- `compose.yaml` — `POSTGRES_DB` / `POSTGRES_PORT` parameterisation and the `eidesoul-test` project.
- `AGENTS.md` §Storage, §Verify — the rule text that this ADR amends at implementation time.
- `pyproject.toml` — no new dependency: `pytest` is already the runner and no container library is introduced.

---

*This ADR lives at `docs/v0.1.0/adr/0029-test-store.md`.*