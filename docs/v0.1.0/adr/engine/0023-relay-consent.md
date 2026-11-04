# ADR-0023: Relay Consent — Forwarding Personal Answers to Others

- **Status:** accepted
- **Implementation:** implemented — evidence: `relay_grants` (revision 6), `RelayStore.ask`/`decide`/`revoke`/`check` with expiry + `none` refusal + content-free audit, `/approve` records the mode, `send_to` checks the grant, the ADR-0014 `relay` step asks with the run deadline, `tests/test_relay.py`, `tests/test_automation.py`
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** manager (A) asked the soul to remind B, C, D, then ask about obstacles/ETA and offer help; B's answer in the DM is B's personal space, so forwarding it to A is not a `cross_read` matter — it is a **relay** that requires the content owner's permission
- **Supersedes:** —
- **Related:** ADR-0006 (group & DM privacy), ADR-0007 (`cross_send`, approval class `consent`), ADR-0008 (information must not increase), ADR-0014 (task & automation), ADR-0016 (DDL), ADR-0019 (metric)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0023-relay-consent.md`.

---

## 1. Context

A real scenario: A (manager) says *"remind B, C, D about their job, ask about obstacles and roughly when it will be done, offer help."* The soul contacts all three (`cross_send`, attribution mandatory), then wants to bring their answers back to A.

Three different spaces are involved:

| Space | Contents | Old rules |
|---|---|---|
| Shared room (A+B+C group) | Answer B is already visible to A | No permit needed |
| B's DM with the soul | B's personal space | **must not** be read by A; `cross_read` is none |
| Task | manifest + run history + declared source | You may read the task itself |

What has no home yet: **relaying the contents of B's answer to A** with B's permission. It is not `cross_read` (there is no room read), nor an ordinary `cross_send` (its content is not a message to A). It is a **relay**.

## 2. Decision drivers

- Permission attaches to **the content owner**, not to the one requesting the report.
- Permission must be **specific** (task, recipient, mode, validity period), not "everything may be forwarded".
- It can be **revoked** at any time, and revocation immediately stops the next report.
- No forced path: neither A, the manager, nor administrators can give permission in B's name.
- Reports may not add facts (ADR-0008) and may not open other topics.
- All actions are auditable without storing personal content in the log.

## 3. Options considered

| Options | Contents | Cost | Risks | Verdict |
|---|---|---|---|---|
| **A. Free relay** | the soul forwards DM answers to A | zero | privacy violation; B does not know who reads the answer | Rejected |
| **B. Status only** | A only learns "has been contacted / has not replied" | zero | safe, but the manager's task is not completed | As a fallback |
| **C. Consent per task + mode** | B chooses: full / summary / anonymous / don't | small (one question) | tiresome when asked often | **Selected** |
| **D. Group only** | ask for all answers in the group | zero | forces B to talk in front of other people | Rejected as an obligation |

## 4. Decision

### 4.1 Relay grant

- One row per `(task_id, from_person, to_person)`:
`{ id, task_id, from_person, to_person, mode, scope_ref, created_at, expires_at, revoked_at, decided_via }`.
- `mode`: `full` (the content as it is), `summary` (brief, **without adding facts**), `anonymous` (the content is allowed, B's name is not told to A), `none` (not forwarded).
- `scope_ref` names the thread/task; the grant does not apply to other topics.
- `expires_at` default = the task deadline or 30 days, whichever comes first; it is not extended without asking again.

### 4.2 How to ask

- The soul asks about the relay **at the same time** as the reminder, or in one follow-up message; the options are shown explicitly: `lengkap / ringkasan / tanpa nama / jangan` (full / summary / anonymous / don't).
- The question names **who the recipient is** and **what will be forwarded** ("your answer about the obstacles & estimate will be sent to A").
- No answer → default **`none`**: A receives only action status, not content.
- One grant per task; no repeated questions per message.

### 4.3 While forwarding

- Only content covered by the grant is sent; `summary` must not add new facts, names, or conclusions.
- Attribution to the recipient remains unless `anonymous`; anonymous mode hides **names** from A, not from the ledger.
- The relay enters the ledger: `relay_id`, `grant_id`, `from`, `to`, `mode`, time — **without the content** of the message.
- The soul does not forward the contents of another room that the grant does not cover; the relay does not become a `cross_read` shortcut.

### 4.4 Revocation and expiry

- B says "do not forward again" → `revoked_at` is filled in, and the next report stops at once.
- A report already sent **cannot be withdrawn**; the soul says so plainly when revoking.
- Past `expires_at` or a completed task → the grant dies; further reporting requires a new grant.

### 4.5 Authority

- Only `from_person` can give/revoke the grant.
- A cannot give a grant in B's name; not even administrators can.
- An answer in a group where A is a member needs no grant (A can already read it), but the soul still **must not** quote it into another room.

### 4.6 Approval class

- A relay question is a **`consent`** (ADR-0007 §4.7): the decider is `from_person`; `scope_proof` = `{task_id, from, to, mode}`.
- Refusing does not lower any rights; refusing is not reported to A as "B is a problem", only the status.

## 5. Configuration

```yaml
relay:
 enabled: true
 require_explicit: true # no answer -> none (status only)
 default_expires_days: 30
 anonymous_min_recipients: 2 # anonymity is still guessable in a small circle; stated when choosing
 ask_cooldown_hours: 24 # avoid asking the same person for relay over and over
```

## 6. Metrics and gates

| Metric | Definition | Gate |
|---|---|---|
| `relay_grants_asked_total` | relay questions sent | limited by `ask_cooldown_hours` |
| `relay_grants_granted_total` `_declined_total` | B's answers per mode | visible |
| `relay_grants_revoked_total` | revocations | reports after it equal 0 |
| `relay_forwarded_total` | relays actually sent | only with an active grant |
| `relay_blocked_total` | relay attempts without a grant | > 0 on test, 0 on normal operation |
| `relay_expired_total` | grants dead from time/task | visible |

## 7. Acceptance criteria

- [ ] B answers "no" → A receives only action status ("has been contacted / has replied"), without content (test).
- [ ] With no relay answer → default `none`, not `full` (test).
- [ ] `summary` does not add facts that are absent from B's answer (test with a comparison fact).
- [ ] `anonymous` hides the name from A, but `relay_grant` still records `from_person` (test).
- [ ] Neither A nor administrators can create a grant in B's name (test `/approve` and tool).
- [ ] Revocation stops the next report; the old report is not withdrawn and is not faked as "never sent".
- [ ] An expired grant (time/task finished) → the next relay is rejected, not forwarded silently.
- [ ] The relay never reads room B: no `cross_read` on this path (access test).
- [ ] The relay ledger contains id + mode, without the message content (test dump line).
- [ ] An answer in a group A belongs to needs no grant, but must not be quoted into another room.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| B feels pressured to give permission | neutral question, the `jangan` option treated as equal, without consequences; `ask_cooldown_hours` |
| Anonymity leaks in a small circle | `anonymous_min_recipients`; a warning when choosing |
| Content already sent cannot be withdrawn | stated honestly when revoking; the next report stops |
| A grant becomes a general permit | scope per task + expiry; no "all topics" grant |
| A asks under disguise in B's name | the grant can only come from `from_person`; an admin cannot override |
| A summary adds a fact | the "information must not increase" rule + a comparison test |
| Question fatigue | one grant per task + cooldown |

## 9. Interaction with other ADRs

- **ADR-0006** — the relay only concerns content that really does belong to B; group privacy questions stay fixed on the subject's DM.
- **ADR-0007** — the relay is a `consent` class; `cross_send` (the reminder) still requires `author`+`reason` attribution.
- **ADR-0008** — `summary` is bound by the ban on adding facts; the relay creates no new trust.
- **ADR-0014** — the relay runs as a `send` step inside the task; the task status remains the single source of truth.
- **ADR-0016** — DDL `relay_grants` (§4.9).
- **ADR-0019** — the §6 metrics enter the counters and `/status`.

## 10. Code references

- `monoagens/engine/access/approvals.py` — the `consent` path used by relay questions (the class from ADR-0007 §4.7).
- `monoagens/engine/agent.py:311` — `send_text` (the send path that records to `outbound_messages`).
- `monoagens/gateways/whatsapp/tools/send.py` — tool `send` (the reminder and the relay report use the same path).
- `monoagens/engine/schema.py` — the `approvals` table (TTL/binding pattern) as a shape reference for `relay_grants`.
- `monoagens/engine/turn/prompts.py:141-147` — role/label block (the relay mentions the `label` of the administrator/manager when present).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0023-relay-consent.md`.*
