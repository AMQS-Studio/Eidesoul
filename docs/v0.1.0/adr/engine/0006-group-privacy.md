# ADR-0006: Group Privacy & Pre-Filter — Ceiling Before Prompt, Self-Capture

- **Status:** accepted
- **Implementation:** partial — exists: guard cross-chat (`privacy.py`, buffer `quoted_refs`); rest: level+veto subject + `rooms_of`
- **Date:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows from ADR-0001 (canonical + Mem0 adapter), ADR-0002 (gate capture), ADR-0004 (labeling fallback), ADR-0005 (retention)
- **Supersedes:** —
- **Related:** ADR-0003 (worker), ADR-0007 (capability vs authority), ADR-0008 (integrity), ADR-0011 (crash safety)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0006-group-privacy.md`.
- **Amendments:** 2026-11-02 — skeleton normalization (ADR-0009 §4.4): Configuration promoted from subsection 4.5 to section 5; the sections after it shifted. 2026-11-03 — audit: the caller list of `PrivacyPolicy` was completed (the auditor review path, `engine/privacy.py:160,192`), not only tools and commands. 2026-11-03 — audit: the `capture_turn`/`_assess`/`render` ranges were aligned with the actual `def`s in the repo. 2026-11-03 — ADR-0000 §4.4 rename: `inbound.chat_id` → `inbound.thread`, memory scope `chat` → `thread`.

---

## 1. Context

**Ceiling privacy already exists, but it is not run on the prompt path.** `PrivacyPolicy.show()` and
`.disclose()` (`eidesoul/engine/memory/privacy.py:52-71`) are correct, but are only called on the
tool, command, and auditor review paths:

- `eidesoul/engine/tools/memory.py:118-119,145-146,175-177` — `policy.show(row, is_dm=ctx.is_dm)`.
- `eidesoul/engine/commands/memori.py:58,76,104` — likewise.
- `eidesoul/__main__.py:372-381` — policy self-test.

The path that builds the **memory block in the prompt** does not use the policy at all:

- `eidesoul/engine/memory/context.py:118-133` — `build()` filters only `vetoed`; it does not filter
`level` and does not receive `is_dm`.
- `eidesoul/engine/memory/mem0.py:536-557` — `context()` forwards to `build()` without a ceiling.
- `eidesoul/engine/agent.py:619-628` — the caller does not pass `is_dm`.

As a result, L3/L4 can enter a **group** prompt, and `MemoryContext.hits` (used by the auditor and the
disclosure path) contains rows that should never be visible in a group.

**Group capture is not restricted to "about oneself".** `capture_turn`
(`eidesoul/engine/privacy.py:225-277`) stores `[user text, soul reply]` with
`owner = person_id`, `subject = assess.subject or owner`, `audience = "group"` — with no rule that
in a group only facts the **speaker states about himself** may be stored.

**Confirmation questions leak in a group.** The `verdict.ask` path sends a question to
`inbound.thread` (`eidesoul/engine/privacy.py:267-277`), and the disclosure path does the
same (`:150-172`). In a group, that question reveals the existence of a fact being protected.

**What is already good and kept:**

- Hard exclusion of credentials before storing (`eidesoul/engine/privacy.py:225`,
`eidesoul/engine/memory/mem0.py:354-356`).
- Person memory with `audience="group"` is never carried to another chat
(`eidesoul/engine/memory/mem0.py:425-433`).
- `vetoed` always wins across every verdict (`memory/privacy.py:53-71`).
- Safe defaults: `group_max_level: 2`, `dm_max_level: 4`, `ask_level: 4`
(`eidesoul/engine/config.py:118-127`).

## 2. Decision drivers

1. **L3/L4 must never enter a non-DM prompt** — enforced at one funnel, not by caller discipline.
2. **Group capture is only the speaker's own facts** — a third-party fact needs the subject's consent (ADR-0007).
3. **No leaked questions** — the group channel is never used for level/content confirmation.
4. **Deterministic and testable** — no LLM on the enforcement path.
5. **DM is not sacrificed** — DM behavior (ceiling 4) stays.
6. **One policy source** — `PrivacyPolicy` is the only allow/ask/deny predicate.
7. **Data minimization** — better not to store than to store and then hide.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. Filter at each call site** | Every prompt/tool caller filters for itself | Does not change the API | Leaks as soon as there is a new caller; easily forgotten | Rejected |
| **B. Filter only in `render()`** | Clean prompt text | `MemoryContext.hits` still carries L4 → auditor/disclosure see the forbidden row | Not safe | Rejected |
| **C. Ceiling in the retrieval funnel** | `gather`/`build` receive `is_dm` and use `PrivacyPolicy.show`; self-capture; no questions in a group | One enforcement point; invariant tested; hits are already clean | Needs `is_dm` plumbed past `MemoryBackend.context` | **Selected** |
| **D. Separate table/index per audience** | A separate `memories_group` table | Physical isolation | Complex, needs a migration, two lifecycles; not needed yet | Rejected |

## 4. Decision

> **The privacy ceiling is enforced at retrieval, before anything is rendered or forwarded; in a group only the speaker's own facts are captured; and there are no privacy questions in a group.**

### 4.1 One predicate, one funnel

- `PrivacyPolicy.show(hit, is_dm=...)` becomes **the only** predicate for "may be seen in this chat"
(`vetoed=false` and `level <= ceiling`).
- `MemoryBackend.context()` **must** receive `is_dm`; `mem0.context()` forwards it to
`memory_context.build(..., is_dm=...)`.
- `build()` drops hits that fail `show()` **before** `render()` and **before**
`MemoryContext.hits` is formed. The auditor and the disclosure path therefore never see a row
above the ceiling.
- Valid for all three scopes: `global`, `person`, `chat`.
- `ceiling = dm_max_level` for a DM, `group_max_level` for a group.

### 4.2 Group capture = self-statement

In non-DM:

| Content | Treat |
|---|---|
| A fact the speaker states about himself (`subject` empty or == owner) | Stored: `scope=person(owner)`, `audience="group"`, `level <= group_max_level` |
| A third-party fact (the speaker states something about someone else) | **Dropped entirely** — no canonical row; needs the subject's consent (ADR-0007) |
| A reply from the soul itself | **Not stored** as person memory (it may carry context from a DM) |
| A fact shared with the group (group name, plan, agenda) | May be `scope=thread(thread_id)`, `level <= group_max_level`, `source='soul'` |
| Credentials/crisis/dangerous instruction | Hard exclusion, before `memory_events` (ADR-0002/0004) |

`audience="group"` and the cross-chat exception in `_search_scope` are kept. A drop is recorded
as a metric, not silenced.

### 4.3 No privacy questions in a group

- In a DM: today's behavior is kept — `verdict.ask` stores `confirmed=false` and then asks
in the DM.
- In a group: `verdict.ask` **does not** store an `ok` row. The row is created as `state='pending'` with
`level NULL` (the ADR-0004 sentinel), so it cannot be read and cannot be disclosed. No
question is sent to the group.
- Confirmation is offered through the owner's DM when a DM channel is available (`dm_offer: true`). Once the owner
confirms, the row transitions `pending → ok` with the confirmation level (still restricted by the
group ceiling when read in a group).
- If it is never confirmed: `pending` passes the 7-day TTL → purge + a content-free audit tombstone.
- `privacy`/`disclosure` approvals may only be sent to a DM chat. In a group, an `ask` verdict
is treated like a silent `deny`: the draft is still sent without the risky parts.

### 4.4 Retrieval scope in a group

- Group: `global` + `person(speaker)` + `chat(this chat)`, all through the group ceiling.
- Person memory from another chat with `audience="group"` is not used (already the case).
- Memory born from a DM is never part of a group's context.

## 5. Configuration

```yaml
memory:
 privacy:
 default_level: 2
 ask_level: 4
 group_max_level: 2
 dm_max_level: 4
 auditor: true
 group_capture:
 self_only: true
 third_party: drop # drop | deny
 capture_reply: false
 ask_in_group: false # verdict.ask di grup -> pending level NULL
 dm_offer: true
 retrieval:
 ceiling: true
```

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| L3/L4 hits that pass into a group prompt | 0 (tested) |
| Group captures dropped because `subject != owner` | Visible, not zero |
| A privacy/disclosure question sent to a non-DM chat | 0 (tested) |
| A `pending` result of `ask` in a group that is never confirmed | Visible + TTL working |
| Memory with `audience=group` that appears in another chat | 0 (tested) |
| Recall regression in DM | Does not drop |

## 7. Acceptance criteria

- [ ] `MemoryBackend.context()` refuses to be called without `is_dm`.
- [ ] `build()` drops a hit above the ceiling before `render()` and before `MemoryContext.hits` is formed.
- [ ] Test: `search` returns an L4 in a group → the prompt block does not contain it, and neither does `hits`.
- [ ] Test: a DM with the same row still shows it (DM ceiling = 4).
- [ ] In a group, capture rejects a third-party fact and the soul's own reply (no canonical row).
- [ ] No `privacy`/`disclosure` approval is sent to a non-DM chat.
- [ ] `verdict.ask` in a group produces `pending`/`level NULL` rows, not `ok` rows.
- [ ] A `pending` row from a group `ask` never appears in retrieval/prompt/disclosure.
- [ ] A DM confirmation changes `pending → ok` with a valid level.
- [ ] Tool and command keep using the same `PrivacyPolicy.show` (no second predicate).
- [ ] Hard exclusion still happens before `memory_events`.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| The soul feels "forgetful" in a group | Intentional; the speaker's own facts still get in; facts shared with the group go in as `scope=chat`; `dm_offer` to raise the level |
| A new read path crosses the funnel | `context()` without `is_dm` = error; the test fails when a second predicate is created |
| `pending` piles up from groups | 7-day TTL + backlog alert (ADR-0004) + metric |
| Subject resolution fails (ambiguous name) | Default **drop**, not owner; log + metric |
| The LLM auditor sees content above the ceiling | `hits` is already filtered before the auditor; in a group the maximum is L2 |
| The operator needs third-party capture | The `third_party: deny` lever is available as a conscious decision with a documented risk |

## 9. Interaction with other ADRs

- **ADR-0001** — canonical remains the source of truth; `audience` and `level` are the columns the funnel reads.
- **ADR-0002** — amended: in a group, the capture gate stops at self-statement; hard exclusion still happens before `memory_events`.
- **ADR-0003** — the worker must not raise `level` outside the `PrivacyPolicy` path.
- **ADR-0004** — `verdict.ask` in a group uses the `pending`/`level NULL` path; only `state='ok'` can be read, and the 7-day TTL applies.
- **ADR-0005** — retention is unchanged; the ceiling is a read filter, not a lifetime filter.
- **ADR-0007** — a third-party fact needs the subject's consent/veto; ADR-0006 closes its automatic capture path. The same ceiling applies on the exit path: text sent via `send` to another room must not contain a third-party fact above the destination room's ceiling.
- **ADR-0008** — provenance/trust does not raise the ceiling.
- **ADR-0011** — a retrieval failure must not fall back to "no ceiling"; failure = empty blocks.

## 10. Code references

- `eidesoul/engine/memory/privacy.py:52-71` — `show`, `disclose`.
- `eidesoul/engine/memory/context.py:40-86,88-133` — `gather`, `render`, `build`.
- `eidesoul/engine/memory/mem0.py:340-380,425-433,536-557` — `capture`, the cross-chat exception, `context()`.
- `eidesoul/engine/privacy.py:133-140,141-172,222-281,282-309` — `_risky`, `review`, `capture_turn`, `_assess`.
- `eidesoul/engine/agent.py:619-628,900-918` — the context caller and the capture spawn.
- `eidesoul/engine/tools/memory.py:104-119,145-146` — policy use in the tool.
- `eidesoul/engine/commands/memori.py:58,76,104` — policy use in the command.
- `eidesoul/engine/config.py:118-127` — `PrivacyConfig`.
- `eidesoul/__main__.py:372-381` — privacy self-test.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0006-group-privacy.md`.*
