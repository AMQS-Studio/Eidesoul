# ADR-0025: Turn Cost — Stable Prefix, Gating `polish`, and Call Budgets

- **Status:** accepted
- **Implementation:** implemented — evidence: `llm.py` one `llm_calls` row per attempt (revision 8), `max_calls_per_turn` with wrap-up notice, two system messages + prefix hash with merge fallback, stable/volatile prompt sections, `tests/test_cost.py`
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** one turn calls the LLM several times (decision, reply loop, `polish`/`regulate`) and the whole prompt block — including the blocks that change every turn (`self` holds mode, uptime, lease; plus `memory`, `emotion`, `topic`, `currents`) — is reassembled into one system message; there is no limit on the number of calls per turn other than `run_budget_seconds` (240 s).
- **Supersedes:** —
- **Related:** ADR-0013 (turn cycle), ADR-0015 (layered payload), ADR-0017 (isolation & time limit), ADR-0019 (observability), ADR-0020 (episode & procedure), ADR-0021 (tool output contract)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0025-turn-cost.md`.

---

## 1. Context

| Fact | Evidence |
|---|---|
| All prompt blocks are joined into **one** system message, closed by the language line + `Date: ...` | `eidesoul/engine/prompts.py:187,355-389` |
| Block `self` holds `Mode`, `uptime`, `lease` — it changes without a soul/config change | `eidesoul/engine/prompts.py:150-186` (`:170`) |
| Blocks `topic`/`currents`/`style`/`person_style`/`skills` enter `COMPETING` and their order depends on salience | `eidesoul/engine/salience.py:20-24,48-71`, `eidesoul/engine/prompts.py:373-385` |
| LLM calls per turn: decision, reply loop with tools, then two polish calls | `eidesoul/engine/agent.py:503,758,810,829` |
| `polish` and `regulate` have no trigger: they are called every turn (unless empty) | `eidesoul/engine/character.py:122,293` |
| Deterministic tells already exist but are not yet used as a gate | `eidesoul/engine/character.py:62,105` |
| Guard against repeated calls: `repeat >= 3` for the same tool | `eidesoul/engine/llm.py:246-255` |
| Today's budget = time (`run_budget_seconds`), not the number of calls | `eidesoul/engine/llm.py:160-200`, `eidesoul/engine/config.py:79` |
| Affinity/session only via a provider header | `eidesoul/engine/llm.py:156,176,187,231`, `eidesoul/providers/openai.py:15` |

The consequences: the prompt prefix **changes every turn**, so provider prompt caching practically never hits; extra `polish`/`regulate` calls happen even when the draft is already clean or when `llm.run` ends in error; and there is no cost ceiling that can be explained to the operator.

## 2. Decision drivers

| Driver | Consequences |
|---|---|
| Cost and latency per turn | Extra calls must have a deterministic reason |
| Provider cache | A stable byte-identical prefix is a requirement, not an optional optimization |
| Honesty | The call ceiling stops with a notice, not a silent cut |
| No LLM to decide on LLM calls | Gating `polish`/`regulate` uses deterministic signals |
| One source | Token accounting stays with ADR-0019 |

## 3. Options considered

| Options | Summary | Value |
|---|---|---|
| **A. Status quo** | One system message, polish/regulate always | Rejected: the cache never hits, cost is invisible |
| **B. Leave caching to the provider** | Without changing the block order | Rejected: the prefix still changes every turn |
| **C. Two system messages + deterministic triggers + call ceiling** | The stable part separated, cost bounded | **Selected** |
| **D. Batch decision + reply** | One call for two tasks | Deferred: it changes decision quality; recorded as quality work, not cost |

## 4. Decision

### 4.1 Two system messages: stable then volatile

- **Stable** (`system[0]`): `base`, `character`, `roles`, `safety`, `person_style`, and the pinned Layer 1 rules (ADR-0015). It must not hold any value that changes without a soul/roles/config change; byte-identical across turns, with the hash recorded.
- **Volatile** (`system[1]`): `self` (mode/uptime/lease/surface/brain), `topic`, `currents`, `memory`, `emotion`, `vibe`, `style`, `skills`, `away`, `actions`, plus the language line and `Date`.
- The order is fixed and deterministic: `system[0] → system[1] → history → user message`. `Date` no longer attaches to the end of the chain; it lives in the volatile block.
- A provider that does not accept two system messages: both are merged with a fixed separator. The cache hit is lost, determinism remains.

### 4.2 Prefix hash

`prompt_prefix_hash` = hash of `system[0]` + the tool schema (ordered) + the names of the active tools. It changes only when the soul/roles/config/active tools change. It is used as a metric and a cache marker, **not** as permission or a truth condition.

### 4.3 `polish`/`regulate` gated by deterministic triggers

- `polish` runs only if the draft triggers the tell check (`ASSISTANT_TELL`/`MARKDOWN`, `eidesoul/engine/character.py:62,105`).
- `regulate` runs only when `polish` changes the text.
- Neither runs when the draft is empty or when `llm.run` ends the turn with `error:`.
- At most one polish call per turn.

### 4.4 Call ceiling per turn

`engine.llm.max_calls_per_turn` (default **6**) counts **every** LLM call in one turn: decision + reply loop + polish. When the ceiling is reached: the tool loop stops, the turn closes with the answer already produced + one `notice:budget` (the message pattern already in `eidesoul/engine/llm.py:170-183`), with no retry and no silent cut. The episode is still judged (ADR-0020).

### 4.5 Token accounting stays with ADR-0019

Every call is already recorded in `llm_calls`; this ADR only adds `turn_id` ↔ `prompt_prefix_hash` so that cost can be read per turn and per call type. There is no new table.

### 4.6 `run_budget_seconds` is not the turn budget

It remains the background work limit (ADR-0017 §5). The turn budget is time (`turn.timeout_seconds`, ADR-0017 §4.3) + calls (§4.4), not 240 seconds per loop.

## 5. Configuration

```yaml
engine:
 llm:
 prompt_cache: true # build a stable prefix + send the session header
 max_calls_per_turn: 6 # decision + reply loop + polish
 character:
 polish: auto # off | auto (auto = only when a deterministic trigger fires)
 regulate: auto # runs only when polish changed the text
 prompt:
 prefix_hash: true # compute and record the stable prefix hash
```

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| `prompt_prefix_changes_total` | 0 while soul/roles/config/active tools are unchanged |
| `calls_per_turn` p50/p95 | p50 ≤ 3, p95 ≤ 6 |
| `tokens_per_turn` p95 | < 20 000 |
| `polish_skipped_total{alasan}` | visible, not silently zero |
| `turn_budget_hit_total` | < 1% of turns |
| `prompt_cache_hit_ratio` | available when the provider reports it; no claim otherwise |
| `time_to_first_send_ms` p95 | < 6 s |

## 7. Acceptance criteria

- [ ] Two consecutive turns produce a byte-identical `system[0]` even though uptime, mode, and memory change (hash test).
- [ ] `Date`, uptime, and lease do not appear in `system[0]` (test checking the contents of the stable block).
- [ ] `polish` is not called when the draft is empty or when `llm.run` returns `error:` (test counting LLM calls).
- [ ] `regulate` is called only when `polish` changes the text.
- [ ] `max_calls_per_turn` reached → the turn closes cleanly + one `notice:budget`; no retry and no silent cuts.
- [ ] Every turn call is recorded in `llm_calls` with `turn_id` and `prompt_prefix_hash` (ADR-0019).

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Some providers reject two system messages | Merge with a fixed separator; only the cache hit is lost |
| Pinned rules go stale because the prefix is cached | The hash changes when the rules change → automatic invalidation; the cache is not the source of truth |
| `polish: auto` skips a needed repair | Deterministic trigger + the `polish_skipped_total` metric reviewed via `/status` |
| The call ceiling cuts a long task | Notice + the episode is still judged (ADR-0020); a task that needs more calls moves up to automation (ADR-0014) |
| Determinism claimed without evidence | Acceptance demands a byte-identical test, not a manual assessment |

## 9. Interaction with other ADRs

- **ADR-0015**: pinned Layer 1 is the content of `system[0]`; Layer 2 (index + `info`) stays in the volatile block/tools, not in the stable prefix.
- **ADR-0017**: `turn.timeout_seconds` is the time budget; `llm.max_retries` follows the retry table there (amendment §4.6).
- **ADR-0019**: `llm_calls` remains the only place tokens are recorded; `prompt_prefix_hash` is tied to `turn_id`.
- **ADR-0020**: a turn that hits the ceiling still closes the episode and may be judged; a cost failure is not a learning failure.
- **ADR-0021**: concise mode reduces the tool output tokens, which also determines `tokens_per_turn`.

## 10. Code references

- `eidesoul/engine/prompts.py:187` — `system_prompt` (where the stable/volatile split goes).
- `eidesoul/engine/prompts.py:355-389` — block assembly, `salience.select`, join, language line + `Date`.
- `eidesoul/engine/prompts.py:150-186` — `self_block` (mode/uptime/lease; the first candidate moves to the volatile block).
- `eidesoul/engine/prompts.py:141-147` — `roles_block`.
- `eidesoul/engine/salience.py:20-24,48-71` — `COMPETING`/`ORDER`/`select`.
- `eidesoul/engine/agent.py:503,758,810,829` — decision, reply loop, `polish`, `regulate`.
- `eidesoul/engine/character.py:62,105,122,293` — deterministic tells and the two polish functions.
- `eidesoul/engine/llm.py:160-200` — the loop with `run_budget_seconds` and the budget message.
- `eidesoul/engine/llm.py:246-255` — the guard for repeated tool calls (`repeat >= 3`).
- `eidesoul/engine/llm.py:156,176,187,231` — `session_headers` per request.
- `eidesoul/providers/openai.py:15` — `session_header="x-opencode-session"` (provider affinity).
- `eidesoul/engine/config.py:69-81,161-163,202-204` — `LLMConfig`, `PromptConfig`, `CharacterConfig`.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0025-turn-cost.md`.*
