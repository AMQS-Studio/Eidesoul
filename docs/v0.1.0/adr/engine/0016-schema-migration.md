# ADR-0016: Canonical Schema & Migration Scheme, One DDL, One Alembic Baseline

- **Status:** accepted
- **Implementation:** implemented — evidence: one Alembic chain (revisions 1-13: baseline, lease fencing, turn lifecycle, memory spine, cognition/episodes/skills, relay, approval class, `llm_calls`, search, automation, P0-3 FK/CHECK/index set, `message_media`, fences/events/efficiency columns), primary-only `upgrade` under the advisory lock, the `threads` parent upsert on ingest, the write-side `visibility` clamp, media bytes read/written only through `message_media`; gate: `pytest -q` 831 passed, `--check` 31/31, `--translate-check` 0 masalah
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** the 2026-11-03 audit found the `memories` DDL in ADR-0001 stale (no `state` column, `level not null default 2`, `confirmed default true`), the ADR-0004 constraint clashing with `state='superseded'` from ADR-0008, and no migration owner for the columns born in ADR-0005/0008/0010/0012/0013/0014.
- **Supersedes:** —
- **Related:** ADR-0001 (canonical), ADR-0004 (state/level), ADR-0005 (retention), ADR-0008 (provenance/trust), ADR-0010 (dispute), ADR-0011 (reaper/ledger), ADR-0012 (`generation`), ADR-0013 (`turns`), ADR-0014 (automation), ADR-0009 (ADR home)
- **Amendments:** 2026-11-03 — §4.4 now mirrors the landed chain (revisions 1-12); the P0-3 set landed in 11 and the media split in 12. 2026-11-03 — owner's decision: ADR-0019 (observability) joins the set; the `llm_calls` column shape is added in §4.6 as the single DDL owner, with retention `engine.metrics.retention_days` (ADR-0005). 2026-11-03 — ADR-0000 §4.4 rename: `thread_id` → `thread_id`, `chats`/`thread_eras`/`thread_people` → `threads`/`thread_eras`/`thread_people`, scope `thread` → `thread`; the baseline revision carries the new names. 2026-11-03 — revision 13 adds the columns the remaining ADRs needed: `cognition_jobs.generation` (ADR-0012 §4.4), `memories.dispute_asked_at` with `ix_memories_subject_asked` (ADR-0010 §4.7), `threads.gateway` (ADR-0014 §4.6), `turns.category` (ADR-0013 §11), `skills.priority` (ADR-0020 §4.6), `automation_runs.payload` and the partial `ix_automations_message` index (ADR-0014 §4.3); `memories_readable` is recreated so the appended column stays visible on an upgraded database.
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0016-schema-migration.md`.

---

## 1. Context

- The repo has **23 tables** in one `MetaData` (`eidesoul/engine/schema.py`), `SCHEMA_VERSION = 1` (`db.py:20`), and `create_all` (`db.py:57-58`), which writes the version into `schema_meta` (`db.py:63-65`). There is no Alembic and no schema-change path other than creating a new table of zeros.
- **The `memories` DDL in ADR-0001 §4 no longer matches the decisions derived from it.** It carries no `state` column at all, it forces `level smallint not null default 2` and `confirmed boolean not null default true` — whereas ADR-0004 makes `level` nullable and lets `confirmed` mean something only when `state='ok'`.
- **The ADR-0004 constraint clashes with a decision in ADR-0008.** `state='superseded'` is born from an `ok` row that has a level; the CHECK `(state IN ('ok','staged')) = (level IS NOT NULL)` forces it to `level NULL`, so a superseded row loses its level and its inventory.
- **New columns are scattered with no migration home:** retention (ADR-0005), provenance/trust/quota (ADR-0008), dispute metadata (ADR-0010), `generation` (ADR-0012), `turns`/`outbound_messages` (ADR-0013), `automations`/`automation_runs` (ADR-0014). ADR-0012 even points to "ADR-0005 (migration scheme)", but ADR-0005 is retention, not migration.
- **The P0-3 review items of 2026-11-01 still have no owner:** FK for `thread_people`/`person_links`/`skill_files`/`google_identity`/`thread_eras`, CHECK `engine_lease.id=1` and `visibility`/`privacy` 0-4, indexes `messages(ts)`, `approvals(thread_id,sender,status,expires_at)`, `approvals(status,expires_at)`, `thread_people(person_id)`, as well as separating media blobs into `message_media`.

## 2. Decision drivers

1. **One definition per concept** (repo rules): one canonical DDL per table, not four versions in four ADRs.
2. **Parts the engine can check**, not human memory, while implementing.
3. **The owning ADR stays sovereign over the semantics of its columns**; this ADR holds the shape, constraints, and order.
4. **Safe for existing data**: add a nullable column → backfill → constraint, not the other way around.
5. **Visible**: the schema version and pending migrations must be readable (`db check`), not hidden.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. One DDL owner ADR + Alembic baseline** | the canonical `memories` DDL here; other tables stay with their ADR owners; migrations here | one place for the shape, semantics stay with the owner | one additional document | ** selected** |
| B. Big amendment to ADR-0001 | all DDL moves to 0001 | no new document | ADR-0001 would then mix decisions, schemas, and history; every derivative ADR edits the same file | rejected |
| C. `ALTER` scattered per ADR | each ADR writes its own `ALTER TABLE` | no coordination needed | the application order is not guaranteed; this is the situation that created the problem | rejected |

| Migration tools | Decision |
|---|---|
| **Alembic** (baseline + append-only revisions) | **chosen** — the only path that preserves order and can be `check`ed |
| A manual SQL sequence | rejected — the order lives in people's heads |
| `create_all` + ad-hoc `ALTER` | rejected — correct only for an empty DB |

## 4. Decision

### 4.1 Rule of ownership

| Area | DDL owner | Note |
|---|---|---|
| `memories`, `memory_events`, `memory_uses`, `memory_tombstones`, `memory_index` | **here, §4.2** | columns born in ADR-0001/0004/0005/0008/0010; form & constraints here |
| `memories_readable` (view) | ADR-0004 §5.3 | the view shape is not duplicated here |
| `cognition_jobs` | ADR-0003 §5.4 | **the canonical name**; `memory_outbox` in ADR-0001/0002 is the old name, not a second table |
| `turns`, `turn_steps`, `outbound_messages` | ADR-0013 §6 | `outbound_messages` carries `scope_key` (§4.3 in ADR-0013) |
| `automations`, `automation_runs` | ADR-0014 §4.1/§4.10 | — |
| `engine_lease`, `engine_control` | ADR-0012 | cross-table CHECK/FK set here |
| the 17 other old tables | `schema.py` (baseline) | touched only through §4.5 |

Rule: **ADR owners define the meaning of a column; this ADR sets the final form, constraint, index, and migration order.** A new column = an amendment of the owning ADR **plus** one revision in §4.4. A table or column that no owner lists = a defect, not freedom.

### 4.2 Canonical DDL `memories`

```sql
memories(
 id uuid primary key,
 scope text not null, -- global | person | thread (ADR-0001)
 person_id text not null default '',
 thread_id text not null default '',
 kind text not null default 'fact',-- fact|preference|event|note|commitment|rule (ADR-0008)
 text text not null,
 state text not null default 'pending', -- see 4.3 (ADR-0004)
 level smallint null, -- NULL = must not be used (ADR-0004)
 confirmed boolean null, -- meaningful only when state='ok' (ADR-0004)
 owner_id text not null default '', -- stated_by (ADR-0008)
 subject_id text not null default '', -- about (ADR-0008)
 via text not null default 'turn',-- enum 4.3 (ADR-0008)
 refs jsonb not null default '[]', -- message/event references (ADR-0008)
 trust smallint not null default 1, -- T0..T3 (ADR-0008)
 audience text not null default 'private',
 source text not null default 'turn',-- legacy column, mapped to via (ADR-0008)
 vetoed boolean not null default false, -- (ADR-0004)
 vetoed_by text not null default '',
 vetoed_at double precision not null default 0,
 pinned boolean not null default false, -- (ADR-0005)
 durability text not null default 'normal', -- (ADR-0005)
 category text not null default 'general', -- (ADR-0005)
 use_count int not null default 0, -- (ADR-0005)
 distinct_use_days int not null default 0, -- (ADR-0005)
 last_used_at double precision not null default 0, -- (ADR-0005)
 max_lifetime_at double precision null, -- (ADR-0005)
 expires_at double precision null, -- NULL = no limit (pin/Tier 1) (ADR-0005)
 supersedes uuid null, -- the row it replaces (ADR-0008)
 superseded_by uuid null, -- replacement (reciprocal) (ADR-0008)
 due_at double precision null, -- commitment: deadline (ADR-0008 B3)
 task_status text null, -- open|done|cancelled|postponed|overdue (ADR-0008 B2)
 dispute_state text not null default 'none',-- none|open|resolved|stale (ADR-0010)
 disputed_with uuid null, -- (ADR-0010)
 dispute_reason text not null default '', -- (ADR-0010)
 disputed_at double precision not null default 0, -- (ADR-0010)
 asserted_at double precision not null default 0, -- (ADR-0008)
 created_at double precision not null,
 updated_at double precision not null)
```

Every column cites the ADR that owns its semantics; ADR-0001 §4 no longer carries DDL, only the table list plus the role.

### 4.3 Constraint & index

```sql
-- state: six values, one source
CHECK ( state IN ('pending','ok','staged','failed','not_worth','superseded'))
-- level exists only on labelled rows; superseded is born from a labelled row (ADR-0008 B)
CHECK ( (state IN ('ok','staged','superseded')) = (level IS NOT NULL))
CHECK ( level IS NULL OR level BETWEEN 0 AND 4)
CHECK ( state <> 'ok' OR confirmed IS NOT NULL)
CHECK ( scope IN ('global','person','thread'))
CHECK ( via IN ('turn','tool','command','reflection','ambient','automation','soul_doc'))
CHECK ( dispute_state IN ('none','open','resolved','stale'))

CREATE INDEX memories_live ON memories (scope, person_id, state);
CREATE INDEX memories_thread ON memories (thread_id, state);
CREATE INDEX memories_expiry ON memories (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX memories_refs_gin ON memories USING gin (refs);
CREATE UNIQUE INDEX memories_supersede_pair ON memories (id, supersedes) WHERE superseded_by IS NOT NULL;
```

- `state='superseded'` **keeps** `level` and `refs`: only the status changes, together with `superseded_by`. The old constraint that forced `level NULL` is revoked here.
- `expires_at NULL` = indefinitely (Tier 1/pin). ADR-0005 remains the owner of the rules for when `NULL` is valid.
- Tables that did not exist in the old schema (`memory_uses`, `memory_tombstones`, `memory_index`, `memory_events`) use the same shape as called out in ADR-0001/0005, with FK to `memories(id)`.

### 4.4 Alembic baseline & migration sequence

`SCHEMA_VERSION` rises by one per revision. The baseline = the state of `schema.py` today (23 tables, version 1).

| Version | Contents | Decision owner |
|---|---|---|
| 1 | baseline: the 23 existing tables | `schema.py` |
| 2 | fencing: `engine_lease.generation`, the single-row CHECK | ADR-0012 |
| 3 | `turns`, `turn_steps`, `outbound_messages`, `engine_control.shutting_down` | ADR-0013, ADR-0011 |
| 4 | memory spine: `memories` (state/level/confirmed/retention/provenance/dispute), journal, uses, tombstones, index, view | ADR-0001, 0004, 0005, 0008, 0010 |
| 5 | `cognition_jobs`, `episodes`, `skills` procedure columns, `skill_revisions` | ADR-0003, ADR-0020 |
| 6 | `relay_grants` | ADR-0023 |
| 7 | `approvals.approval_class`, `scope_proof` | ADR-0007 |
| 8 | `llm_calls` | ADR-0019, ADR-0025 |
| 9 | `messages.text_tsv` (generated), GIN + trigram indexes | ADR-0026 |
| 10 | `automations`, `automation_runs`, `automation_state_log` | ADR-0014 |
| 11 | P0-3 FK/CHECK/index set | ADR-0016 |
| 12 | `message_media` split; `messages` keeps metadata only | ADR-0016 |
| 13 | fences/events/efficiency columns: `cognition_jobs.generation`, `memories.dispute_asked_at` `(+ ix_memories_subject_asked)`, `threads.gateway`, `turns.category`, `skills.priority`, `automation_runs.payload`, partial `ix_automations_message`; `memories_readable` recreated | ADR-0010, 0012, 0013, 0014, 0020 |

Rules per revision:

1. **Add nullable first**, backfill in batches (`backfill_batch`), then the new constraint/`NOT NULL`.
2. A constraint on a large table is added `NOT VALID`, then `VALIDATE CONSTRAINT` after backfill.
3. No `DROP COLUMN`/`DROP TABLE` without a release pause and a note in `LOG.md`.
4. `down` is dev-only (`allow_down: false` by default) — downgrading in production means losing data.
5. Migration runs only on the **primary**, under `pg_advisory_lock`; a standby never migrates.
6. `create_all` is for empty DBs and tests only; production uses migrations.

**Initial backfill (revision 3):** old rows have no `state`, and every stored row is considered readable, so `state='ok'` for rows with `level` 0-4 (the old default 2) and `confirmed` set to `true` (the older behavior); the rest get `state='pending'`. One-off, idempotent, recorded in `schema_meta`.

### 4.5 FK, CHECK, and index of review P0-3

- FK: `thread_people.person_id`, `person_links.*`, `skill_files.skill_id`, `google_identity.*`, `thread_eras.thread_id` to their parent tables (`ON DELETE CASCADE` for owned children, `SET NULL` for references).
- CHECK: `engine_lease.id = 1`; `visibility BETWEEN 0 AND 4` (messages/memory associated); `level BETWEEN 0 AND 4` (already at 4.3).
- Index: `messages(ts)`, `approvals(thread_id, sender, status, expires_at)`, `approvals(status, expires_at)`, `thread_people(person_id)`.
- Media blobs are separated into `message_media(message_id, kind, mime, bytes_ref)`; `messages` keeps pointers.

### 4.6 Table `llm_calls` (metrics charge, ADR-0019)

```sql
CREATE TABLE llm_calls (
 id bigserial PRIMARY KEY,
 ts timestamptz NOT NULL DEFAULT clock_timestamp(),
 turn_id text, -- null for background work (job_id/run_id set)
 job_id bigint,
 run_id text,
 purpose text NOT NULL, -- decision | reply | polish | digest | extract | reflect | research | label
 model text NOT NULL,
 tokens_in integer, -- null when the provider does not return usage
 tokens_out integer,
 latency_ms integer NOT NULL,
 ok boolean NOT NULL);
CREATE INDEX ix_llm_calls_ts ON llm_calls (ts);
CREATE INDEX ix_llm_calls_turn ON llm_calls (turn_id);
```

- No FK to `turns`/`cognition_jobs`/`automation_runs`: the metrics table must not hold up or block entity deletion, and a row without an id is still useful (e.g. a failure before a turn).
- It stores ids and numbers only; prompt/response text never enters it (ADR-0019 §4.1).
- Retention: `engine.metrics.retention_days` (default 90), removed by the ADR-0005 retention job.
- **Amendment 2026-11-03:** owner's decision — ADR-0020 (derived procedures) joins the set; `skills` is extended and `skill_revisions` is added in §4.7. 2026-11-03 — §4.8: the `approval_class` + `scope_proof` columns on `approvals` (ADR-0007 §4.7). §4.9: `relay_grants` + `automation_state_log` (ADR-0023, ADR-0014 §4.13).

### 4.7 Table `skills` and `skill_revisions` (derived procedure, ADR-0020)

```sql
ALTER TABLE skills
 ADD COLUMN scope text NOT NULL DEFAULT 'thread', -- thread | person | global
 ADD COLUMN via text NOT NULL DEFAULT 'reflection',
 ADD COLUMN stated_by text,
 ADD COLUMN source_episode text,
 ADD COLUMN uses integer NOT NULL DEFAULT 0,
 ADD COLUMN last_used timestamptz,
 ADD COLUMN revision integer NOT NULL DEFAULT 1,
 ADD COLUMN trust text NOT NULL DEFAULT 'T2'; -- T0-T3 (ADR-0008)

ALTER TABLE skills
 ADD CONSTRAINT ck_skills_scope CHECK (scope IN ('thread','person','global')),
 ADD CONSTRAINT ck_skills_trust CHECK (trust IN ('T0','T1','T2','T3'));

CREATE TABLE skill_revisions (
 skill text NOT NULL,
 revision integer NOT NULL,
 description text NOT NULL,
 body text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 source_episode text,
 PRIMARY KEY (skill, revision));

CREATE INDEX ix_skills_scope ON skills (scope);
CREATE INDEX ix_skill_revisions_skill ON skill_revisions (skill, revision DESC);
```

- `skill_files` remains a supporting file; its scope follows the parent `skills` row.
- The column semantics (default scope, `via=reflection`, `uses`/`last_used` for reuse, append-only revisions) belong to ADR-0020 §4.5; ADR-0016 only holds the shape.
- `global` may only be born from a T0 source or an approval (ADR-0020 §4.4); the CHECK cannot enforce that on its own, so it is guarded in the write path plus a test.

### 4.8 Approval class on `approvals` (ADR-0007 §4.7)

```sql
ALTER TABLE approvals
 ADD COLUMN approval_class text NOT NULL DEFAULT 'authorization', -- consent | authorization
 ADD COLUMN scope_proof json NOT NULL DEFAULT '{}';

ALTER TABLE approvals
 ADD CONSTRAINT ck_approvals_class CHECK (approval_class IN ('consent','authorization'));
```

- `scope_proof` is the deterministic result computed when the approval is requested: the list of action **targets** (path, room, column) + a snapshot of the requester's role roots + the lowered class. Execution compares it with the scope at that time; a scope difference cancels the approval or raises the class.
- No new index: the fixed queries `(thread_id, sender, status)` and `(status, expires_at)` suffice.

### 4.9 `relay_grants` and `automation_state_log` (ADR-0023, ADR-0014 §4.13)

```sql
CREATE TABLE relay_grants (
 id bigserial PRIMARY KEY,
 task_id text NOT NULL,
 from_person text NOT NULL,
 to_person text NOT NULL,
 mode text NOT NULL, -- full | summary | anonymous | none
 scope_ref text NOT NULL, -- thread/task
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz,
 revoked_at timestamptz,
 decided_via text NOT NULL DEFAULT '' -- consent | owner_note);
CREATE UNIQUE INDEX ux_relay_grant ON relay_grants (task_id, from_person, to_person);
CREATE INDEX ix_relay_grants_expiry ON relay_grants (expires_at);

CREATE TABLE automation_state_log (
 id bigserial PRIMARY KEY,
 automation_id bigint NOT NULL,
 from_state text NOT NULL,
 to_state text NOT NULL,
 reason text NOT NULL DEFAULT '',
 actor text NOT NULL DEFAULT '',
 at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE INDEX ix_automation_state_log ON automation_state_log (automation_id, at DESC);
```

- `mode='none'` still stores the row (it is not deleted): A accepts the action status only, and B's decision remains auditable.
- `relay_grants` does not hold message content; the content stays in its own room.
- `automation_state_log` only records state transitions (ADR-0014 §4.13), not every run.

## 5. Configuration

```yaml
database:
 migrations:
 mode: on_boot # on_boot | manual
 allow_down: false # `down` only in dev
 backfill_batch: 500
 advisory_lock_timeout_seconds: 30
```

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| Interference between the code version and `schema_meta` | 0 (otherwise boot fails) |
| `NOT VALID` constraint left hanging | 0 after backfill |
| `memories` rows with `state` NULL | 0 |
| Two names for one table (`memory_outbox` + `cognition_jobs`) | 0 |
| P0-3 index missing after migration | 0 |
| Migration running on a standby | 0 |

## 7. Acceptance criteria

- [ ] There is only one DDL for `memories` across the ADRs, in §4.2; ADR-0001 §4 contains the table list + role list only.
- [ ] `state='superseded'` keeps `level`; the CHECK in 4.3 replaces the ADR-0004 §5.2 CHECK.
- [ ] `level` and `confirmed` are nullable in the schema; `level NULL` = not to be used.
- [ ] The Alembic baseline (version 1) + revisions 2-9 can run from both an empty DB and version 1.
- [ ] Upgrading a version-1 DB succeeds; `db check` reports the version and the pending migrations.
- [ ] Migration runs only on the primary, under the advisory lock; a standby refuses.
- [ ] `memory_outbox` is never created as a table; `cognition_jobs` is the only name.
- [ ] FK/CHECK/index P0-3 present after revision 7.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Migration blocks boot | `mode: manual` for the operator; revision identity; `db check` first |
| Old backfill on a large table | batches + the `memories(expires_at)` index; run while empty |
| Two migrations at once | `pg_advisory_lock` + primary only |
| `down` destroys data | `allow_down: false` by default |
| DDL scattered back into another ADR | migration check: a table/column without an owner in §4.1 = failure |
| `create_all` used in production | fail fast if `mode=on_boot` and the versions differ |
| A new column added without an ADR | the same check rejects columns with no row in the §4.1 table |

## 9. Interaction with other ADRs

- **ADR-0001** — the canonical DDL remains there; it moves here, with the table list + roles fixed at ADR-0001 §4.
- **ADR-0003** — `cognition_jobs` is the canonical name; `memory_outbox` in 0001/0002 reads as the old name.
- **ADR-0004** — the semantics of `state`/`level`/`confirmed` and the `memories_readable` view remain its own; the CHECK is fixed in 4.3.
- **ADR-0005** — retention columns; the rule for when `expires_at` is valid remains its own.
- **ADR-0008** — provenance/trust and `superseded`; the `level` constraint is synced here.
- **ADR-0010** — dispute columns; it adds no `state` value.
- **ADR-0011** — reaper/ledger; the migration sequence closes `outbound_messages` at revision 8.
- **ADR-0012** — `generation` + the `engine_lease.id=1` CHECK at revision 7.
- **ADR-0013** — `turns`/`turn_steps`/`outbound_messages`; the column nuances stay in ADR-0013 §6.
- **ADR-0014** — `automations`/`automation_runs` at revision 9.
- **ADR-0009** — this file is part of the migration to `docs/v0.1.0/adr/`; that migration moves documents only.
- **`AGENTS.md`** — amended 2026-11-03: `MemoryStore` becomes the single write port (ADR-0001), and what is forbidden is a second semantic path (`Never revive MemoryStore` is replaced by that prohibition).

## 10. Code references

- `eidesoul/engine/db.py:20` `SCHEMA_VERSION`.
- `eidesoul/engine/db.py:57-58` `create_all` (empty DB/tests only).
- `eidesoul/engine/db.py:63-65` — writes the version to `schema_meta`.
- `eidesoul/engine/schema.py` — 23 tables; `:270-290` `engine_lease`, `engine_control`.
- `eidesoul/engine/memory/mem0.py:264-281` — JSON metadata that today stores provenance outside the schema.
- `eidesoul/engine/tools/settings.py:85-98` — config schema (not DB schema), precedent for a "single schema source".
- No `alembic.ini`/`migrations/` in this repo — a prerequisite, not the current state.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0016-schema-migration.md`.*
