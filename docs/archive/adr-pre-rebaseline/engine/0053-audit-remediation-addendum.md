# ADR-0053: Audit Remediation Addendum — Stores, Jobs, Identity, Access, and Install Gaps

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** completes ADR-0049 with every store, job, identity, access, and install defect (`BI-01..BI-20`, `M-01..M-08`) that ADR-0049..0052 do not already own.
- **Supersedes:** —
- **Related:** ADR-0011 (crash safety), ADR-0012 (lease safety), ADR-0016 (schema migration), ADR-0022 (administrator role), ADR-0036 (role model), ADR-0046 (gateway access), ADR-0048 (single-tenant), ADR-0049 (audit remediation), ADR-0050 (cross-gateway), ADR-0052 (detector precision)
- **Implementation:** draft — none of the items below is applied.
- **Location note:** the items bind engine stores, jobs, and access, so this ADR sits under `adr/engine/`.

---

## 1. Context

ADR-0049 holds the highest-impact defects. Several intra-deployment key collision items
stay real in a single deployment because they concern two gateways or two apps inside one
database. Items already owned elsewhere are not repeated: identity on resume,
`propose`, `promote_staged`, pinned prompt parts, the exit door, send idempotency, guard wrappers,
plugin `deps/`, the install path (ADR-0049); cross-gateway tools (ADR-0050); Mem0 and recall (ADR-0051);
detectors (ADR-0052).

## 2. Decision drivers

1. A defect that loses a message, runs a job twice, or lets one person act as another comes first.
2. Intra-deployment isolation (gateway, app, thread) is still required; tenant isolation is not.
3. Every fix keeps the existing store contracts and gets a test on the real seam (ADR-0049 §4.4).
4. Operator-facing commands do what the documentation says or are removed.

## 3. Options considered

**A. Fold everything into ADR-0049.** Rejected: that ADR would pass the module cap for docs and hide
the owner of each fix.

**B. Leave the low-severity items undocumented.** Rejected: the audit's test-suite lesson is that
untracked defects reappear.

**C. One addendum with grouped, individually testable items.** Accepted.

## 4. Decision

### 4.1 Message and identifier collisions

1. `turns.id` is the raw transport message id. Two gateways that both emit `msg-1` collide, and
   `history.intake` drops the second message silently. The turn key becomes
   `gateway:thread:message_id` (or an internal id with a unique index on those three), and
   `outbound_messages.scope_key` follows it.
2. `threads.thread_id` is global with one `gateway`. `RouteConflict` must reject loudly with a log
   line and a notice, not drop the message.
3. `safe_chat(thread_id)` maps `app:user_1` and `app_user:1` to one scratch directory. It appends a
   short hash of the original id so two threads never share files.
4. `SkillStore.save` conflicts on `name` alone and overwrites another app's skill. The key is
   `(app, name)`. `app` stays a corpus namespace (ADR-0035); it is not a security boundary, and the
   `app` hint is accepted only from the gateway that declares it in its config.
5. `approvals.find_action` scans the latest 50 rows in Python. Add a unique index on the action id and
   look it up in SQL.

### 4.2 Jobs and concurrency

1. `AutomationStore.reap` adds `AND status IN ('claimed','running')` to its `UPDATE`; a job that
   finished between `SELECT` and `UPDATE` stays finished.
2. `AutomationStore.interrupt` filters on `claimed_by` and the lease generation, not every claimed row.
3. Cognition claims renew their heartbeat during the call, and the claim lease exceeds
   `llm.timeout_seconds` by a margin. Claim expiry for `turns`, `cognition_jobs`, and `automation_runs`
   uses the database clock (`clock_timestamp()`), like `engine_lease`.
4. `MemoryStore.record_uses` becomes one set-based statement per turn instead of up to 48 queries.
5. `AffectStore` stops locking one `state.key='emotion'` row for every turn; per-turn reads are
   lock-free and writes are a single compare-and-set. `_soothe` no longer scans all people.
6. `engine.db` sets `max_overflow` explicitly (default 0 with the pool sized for `io.workers`), and
   admin CLI commands share a small pool.
7. `VibeStore` retries after a transient failure with a backoff instead of staying failed until restart.
8. `engine.llm.run_budget_seconds` is enforced: `LLM.run` takes the smaller of the budget and the turn
   deadline instead of testing `deadline is None`.
9. `LLM.run` image-retry replaces the last user message only; after a tool round it drops the image
   parts from the original user message and leaves the `assistant -> tool` order intact. Tool output
   added to the running messages is capped per round so a second round cannot exceed the context window.
10. The outbox writes `intent` and the receipt in separate transactions around `body.dispatch`; the
    window is closed by the reaper rule in ADR-0049 §4.1.4 plus a startup reconcile that marks every
    `intent` older than the turn deadline as `unknown`.

### 4.3 Fencing and standby

`_sync_soul_rules()` moves out of `Agent.__init__` into the primary start path, so a standby writes
nothing at boot. `AffectStore`, `Ambient`, and tool execution check the lease generation before a write
with side effects (the atomic predicate of ADR-0049 §4.2.3).

### 4.4 Identity and access

1. `/link` counts a wrong code against the sender and the gateway (rate limit), consumes a code in one
   transaction with `SELECT ... FOR UPDATE`, and refuses to merge a person who holds a role in
   `roles.yaml`.
2. `/link` tests the argument against the active gateway names before the six-character code regex, so
   `matrix`, `signal`, and `wechat` can be linked.
3. `/unlink` revokes the person's relay grants and pending approvals and writes an `actions` audit row.
4. `monoagens people add` runs the address through `GatewayCore.normalize_address` (ADR-0050 §4.2), so
   `+62…` and `…@s.whatsapp.net` reach the same link as the inbound digits. `monoagens people adopt`
   is implemented or its parser entry is removed.
5. `RoleRegistry.policy_for` returns the first role that defines the gateway; roles of equal priority
   have an explicit, documented tiebreak.
6. The `guest` role loses `read`, `grep`, `fetch`, `chat_history`, `link`, and `unlink` in the seed;
   an unknown sender gets `help`, `ping`, and `whoami`, plus `link` only when the operator enables it.
7. `monoagens run` in the CLI prints one refusal line when `access_refusal` rejects the console user
   instead of discarding input silently.
8. The harness gateway implements `access_refusal`; its `/inspect` command reads `action.address`.
9. A relay grant can be created from a conversation turn (a `relay` tool, role-gated), and the relay
   check accepts a group target through the gateway's own membership rule.

### 4.5 Send, approval, and attachment details

1. The cross-gateway tools of ADR-0050 can send to a number that has no archive row; the read-first
   rule applies to rooms that exist, and a new direct target is allowed with a `reason` and the
   `authorization` class.
2. A text-then-file send records two steps; a failed file never marks a delivered text `failed`.
3. `/approve` from the origin room works for a request created at a foreign target (ADR-0049 §4.1.3
   stores `origin_thread`), and the executed action reports back to the origin room.
4. The member role can forward its own attachment through `attachment_id` (ADR-0050 §4.4) without
   read access to `attachments/`.

### 4.6 Install, CLI, plugins

1. `compose.yaml` takes `EXTRAS` from the chosen gateway, mounts the configuration directory rather than
   single files, and the installer runs `db upgrade` and the admin bootstrap before `up -d`.
2. `plugins install` accepts a local source directory and writes the digest into the config; there is
   a command that refreshes the digest after a local edit (ADR-0054 §4.3).
3. Migrations `0013` and `0018` drop the dependent `memories_readable` view before dropping a column in
   `downgrade()`; the missing `ix_thread_people_person` index is added to `schema.py`.
4. `monoagens/engine/turn/observability.py` receives `extra={"event": …}` from its callers, or the
   module stops claiming an event field.
5. `deploy/sandbox-proxy/squid.conf` also denies `::1/128`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96`, and
   `0.0.0.0/8`.
6. `StyleStore.learn` is wired to the end of the turn or the store is deleted (ADR-0049 §4.3.2).

### 4.7 Documentation and configuration truth

1. `README.md` and `TASK.md` agree on the configuration source: the files are authoritative and the
   `documents` rows are a cache (AGENTS.md, ADR-0046); `TASK.md` lines that say the DB wins are
   corrected.
2. `README.md` states that `soul.role` changes the persona line only; `/mode` is not claimed until
   ADR-0049 §4.3.3 lands.
3. `config.yaml`, `roles.yaml`, and `soul.yaml` are untracked templates (AGENTS.md); the repository
   carries `config.example.yaml` style seeds instead, written by `monoagens init`.
4. The `thread_chars` budget counts quote text, media lines, and inline attachment text, and
   `character.polish` rejects a rewrite that changes numbers or dates of the draft.
5. `scripts/ablate.py` calls the renamed `max_chars`; `scripts/xlsx.py` is deleted.

## 5. Configuration

New or changed keys: `engine.db.max_overflow`, a cognition claim heartbeat, and the `guest` seed in
`roles.yaml`. Each is written with its comment by `monoagens init`.

## 6. Metrics and gates

- A two-gateway intake test with equal message ids; a `/link` abuse test; a reaper race test.
- A downgrade round trip on the Alembic chain (`0018 -> 0010 -> 0018`).
- `pytest -q -W error::DeprecationWarning` for the touched modules.

## 7. Acceptance criteria

- [ ] Two gateways with the same message id both get a reply.
- [ ] A finished automation is never re-queued by the reaper.
- [ ] An administrator who types an attacker's `/link` code keeps their identity.
- [ ] `monoagens people add whatsapp +628… --role member` works after the first administrator.
- [ ] Migration downgrade runs on a database with data.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Turn key change breaks resume of existing rows | Migration rewrites ids; a one-time backfill |
| Tighter `guest` breaks a public bot | The operator can restore it in `roles.yaml`; `init` documents it |
| Set-based `record_uses` changes counts | A unit test compares counts with the old loop |

## 9. Interaction with other ADRs

ADR-0049 owns the headline defects and the phases; this ADR is its checklist companion. ADR-0036 keeps
the role model; §4.4.5–6 change evaluation and seeds only. ADR-0046 still owns gateway policy.

## 10. Code references

- `monoagens/engine/stores/turns.py`, `history.py`, `skills.py`, `affect.py`, `vibe.py`
- `monoagens/engine/jobs/automation_store.py`, `cognition.py`, `monoagens/engine/db.py`
- `monoagens/engine/access/identity.py`, `roles.py`, `approvals.py`
- `monoagens/engine/commands/link.py`, `unlink.py`, `approve.py`
- `monoagens/engine/llm.py`, `monoagens/engine/tools/__init__.py`
- `Dockerfile`, `compose.yaml`, `install.sh`, `migrations/versions/`, `deploy/sandbox-proxy/squid.conf`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0053-audit-remediation-addendum.md`.*
