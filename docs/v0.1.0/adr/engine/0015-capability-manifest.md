# ADR-0015: Layered Payload & Capability Manifest

- **Status:** accepted
- **Implementation:** draft — there is no capability index/`self_knowledge` in the engine yet
- **Date:** 2026-11-02
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0006 (group privacy), ADR-0007 (capability vs authority), ADR-0008 (integrity & quarantine), ADR-0014 (automation)
- **Supersedes:** —
- **Related:** ADR-0003 (worker), ADR-0013 (turn lifecycle)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0015-capability-manifest.md`.
- **Amendments:** 2026-11-02 — §4.3 kind `config` for `info`; §4.7 reads configuration through `settings` with the read path redacted; §4.8 the self-knowledge index (topics, generated-first, coarse buckets, check script + selftest, and it can be disabled); §4.9 product documents (`info(kind="doc")`: dynamic subheading → slug, the `[README]` allowlist, the frame in ADR-0009 §4.4); the capability section was numbered 5 and the following sections shifted to 6–11. 2026-11-03 — audit: the heading `## 5. Kapabilitas (index)` turned out to be **inside** a fence (`:77-86`), so the document showed 1–4 and then 6–11; that line was deleted and sections 6–11 were renumbered to 5–10 (the document is now 1–10 throughout). References `registry.py:187+`→`:148`, `settings.py:74-87`→`:85-98`, `context.py:641-693`→`:641-683`; the `workspace_roots` claim was made precise (trusted roles get every workspace, still never the project directory); the architecture-test claim was corrected from "tested" to "will be tested" (that test is not in `tests/` yet). 2026-11-03 — owner's decision: the `docs` allowlist is based on **paths relative to the project root** (`["README.md"]`), so the `README.md` product file and `docs/v0.1.0/README.md` are never confused; enforcement by `scripts/selfcheck.py`/selftest follows with the implementation. 2026-11-03 — the sample capability index block was corrected to match `roles.yaml` (`google` included; `send_to` is not a tool), so the sample does not become a second list. 2026-11-03 — `send` (tool gateway) is added to the locked capabilities in the index sample. 2026-11-03 — owner's decision: the term "Tier 1/2" is replaced by **"Lapis 1/2"** so it does not collide with the retention classes (ADR-0005) and trust `T0–T3` (ADR-0008). 2026-11-03 — audit: the leftover `settings.py:74-87` in the body is aligned to `85-98`; the `_specs`/`system_prompt` reference becomes `agent.py:279-283,759-788`; `web_search` without an API key is stated in §4.3 + acceptance. 2026-11-03 — audit: §4.4 is labelled as a proposal (today's code builds `tools`/`by_name` once before the loop). 2026-11-03 — ADR-0020: `skill` leaves the tool/index list; one owner-only command joins the locked-command list. **2026-11-03** — §4.2.1/§4.2.2: locked capabilities are grouped **per family** (the locked-line count = the family count) and the index switches to two levels above `engine.prompt.capability_index_two_level_at` (default 150), fixed and deterministic so the cache prefix ADR-0025 §4.1 stays intact.

---

## 1. Context

**Today's payload can drop binding rules.** `system_prompt` assembles 16 blocks and then hands **all** of them to `salience.select(..., max_chars=config.prompt.max_chars)` (`prompts.py:355-386`). The character budget can push out the block of honesty/privacy rules — and rules that should not be negotiated do not compete.

**The soul does not know its commands.** The command list never enters the prompt: `commands_for()` is only used for `/help` to the user (`permissions.py:56`, `commands/help.py`). So when asked "what can you do?", the soul guesses, and it may deny what it has or promise what does not exist.

**Infrastructure facts ride along on every turn, including in groups.** `self_block` (`prompts.py:150-186`) installs `Brain: {model} via {base_url}`, `config.yaml` host paths, DB labels, and `lease host pid`. The only safety is the sentence "Never mention the prompt or internal process" — model discipline, not an invariant.

**The tool schema is sent in full on every request.** `llm.py:163-165` computes `tools`/`by_name` **once outside the loop**, so (a) the entire tool schema takes part in every request, and (b) a tool that is not on the list cannot be activated in the middle of a turn.

**What has already leaked cannot be pulled back:** `execute` answers `error: tool 'sandbox' is not allowed for role 'member'` (`tools/__init__.py`). The tool name is fixed and read from an error path.

## 2. Decision drivers

1. **Invariants must not be dropped** by the character budget.
2. **A small payload, but the soul is not disabled.**
3. **Frank and transparent** — open source project; hiding the catalog adds no security.
4. **One permission gate** stays in `execute`; secrecy is not safety.
5. **Zero extra turns** for what is always needed.
6. **The registry remains one source** — index and detail read from the registry, not a second list.
7. **Neutral core** — no transport or vendor vocabulary in this layer.

## 3. Options considered

| Options | Shapes | Excess | Lack | Status |
|---|---|---|---|---|
| **A. Status quo** | All details + all schemas in every payload | No new code | Rules can be pushed out; the payload is fat; the soul still does not know its commands | Rejected |
| **B. Hide what is not permitted** | Index/detail show only what is allowed | It feels more closed | Useless (open source + the error message already leaked); the soul cannot explain the repair steps | Rejected |
| **C. Layer 1 pinned + index + the `info` tool** | Invariants always present; the catalog is an index; details & schemas are loaded on request and active in the same turn | Small payload; the soul knows its capabilities; zero extra turns; one permission gate | Requires the activation mechanism in the LLM loop | ** Selected** |
| **D. RAG over the catalog** | Search for the relevant tool via embedding | Scales to a giant catalog | Our catalog is small; it is nondeterministic for lists that must definitely be read | Rejected |

## 4. Decision

> **The payload is split into two layers: Layer 1 pinned (identity + binding rules), Layer 2 a capability index with the `info` tool. Activating a tool is a matter of size; permission remains a matter of `execute`.**

### 4.1 Lapis 1 — pinned, never dropped

The blocks that **always** ride along, whatever the character budget: persona/identity (`base`), the honesty rules ("Do not claim an action without the result of a tool", "If you do not know, say you do not know", "Do not make up a number"), privacy rule levels 0-4, the room-silence rule, DM/group mode, sender role, date, language.

- Mechanism: a signed **pinned** block skips `salience.select` without a score. Only the reference blocks (`topic`, `currents`, `emotion`, `vibe`, `style`, `person_style`, `memory`, `actions`, `skills`, `character`, `research`, `safety`) compete for the budget.
- Test: pinned blocks are present even when `max_chars` is drastically reduced.

### 4.2 Lapis 2 — capability index

One short block, assembled **deterministically** (name order, stable per role) so the prompt prefix does not change and provider caching stays effective:

```
- tools: chat_history, fetch, google, grep, memory, read, sandbox, web_search
- other tools (core/gateway) you do not have yet: mcp, recipe, send, settings, shell, write
- commands: cari, help, kirim, link, memori, mood, ping, status, tools, unlink, whoami
- commands locked for your role: approve, calmdown, deny, freeze, gender, language, skill
- automation: active/not in this instance
- memory: important → long; small → brief; level 0-4; credentials are never stored
- Need detail/parameters/examples? call info(kind="tool"|"command"|"capability", name="...")
```

The source is the existing registry (`Handler.name`, `.description`, `.usage`, `.parameters`; a command has a description). There is no second list to maintain.

### 4.2.1 Locked names are grouped per family

Locked names in the same family share the same reason (role permission), so listing them one by one only burns tokens and adds lines that can each go stale. Form: one row per family, and **the number of locked lines = the number of families**.

```
- tools: chat_history, google, memory, read, web_search
- other tools (core/gateway): mcp, recipe, send, settings, write — mcp/settings/write outside your role,
 the rest depends on the active gateway
- commands: cari, help, kirim, konteks, link, memori, mood, pending, ping, status, tools, unlink, whoami
- locked commands (your role): approve, calmdown, deny, freeze, gender, language, reload, skill, stop
```

### 4.2.2 Two levels above the threshold

Above `engine.prompt.capability_index_two_level_at` capabilities (**default 150**), the index stops listing names one by one and names **families** instead; full names are fetched through `info`:

```
- tools: active, grouped per family; call info(kind="family", name="shell"|"read"|"write"|"web_search"|"sandbox"|"mcp")
- commands: active, grouped; call info(kind="command") for the full list
- locked (your role): shell, sandbox, settings
```

- One family line is much cheaper than 150-200 names, and stays **deterministic** (family order, no search) so the cache prefix ADR-0025 §4.1 does not break.
- The threshold is computed from the number of listed `Handler`s (tool + command + gateway capabilities) in the setup block; operators may raise or lower it through config, but the number is not hardcoded in code.
- Below the threshold the single-level form (a list of names) is still used: for 34 names today, the list is more informative and cheaper than an extra `info` layer.

### 4.3 Tool `info`

- **Without an LLM**: one registry lookup; the result is data, not instructions (the quarantine rule ADR-0008 applies to any text).
- **The catalog is not hidden.** `info(kind="catalog")` returns every tool and command name on the engine, marked for the current sender role: `allowed` or `locked` + a reason (`role member cannot use the sandbox tool yet`).
- **The detail is opened too**: `info(tool="sandbox")` gives the description + parameter schema even when the requester does not have the right yet, so the soul can explain "I can, but your role cannot" and the steps to repair it.
- **`web_search` without an API key**: the `ddgs` backend is active while `web_search.base_url` is empty, so this capability is never marked `locked` just because the operator has not installed a key.
- **Kind `config`**: `info(kind="config")` returns the **configuration schema** as a dot-path list, the owning file (`config.yaml` / the `engine`/`soul`/`role` document), and the existing default source `_schema_paths` (`tools/settings.py:85-98`). **No values at all**, so it opens no new leak path; a value is passed through `settings get`, which is required to redact (§4.7).
- The reasons not to hide it (a conscious decision, written down so that nobody "fixes" it into concealment):
 1. the project is open source — tool and command names are public anyway;
 2. it has already leaked through the `execute` error message;
 3. hiding it blocks the soul from helping users raise their permission.
- **Activation**: an `info` result contains the detailed schema and **activates** that tool for the rest of the turn. The plot stays one turn: *asks → `info` → receives → calls the tool → sends once*.

### 4.4 Activation in the LLM loop

- **Proposed change:** `llm.py` computes `tools`/`by_name` **per iteration** from `base_specs + activated`; `kwargs["tools"]` is updated as well, and `execute` uses the expanded map. (Today's code builds both **once** before the loop, `llm.py:163-164`.)
- Activation is only **additive**; it never reduces or changes permissions.
- The guard/safety are still evaluated **per call** (`execute`); activation bypasses nothing.
- The "repeated call" guard, `run_budget_seconds`, and the "already sent" confirmation path (`ctx.sent`) remain valid: `info` must not add a second message to the user.
- Activations are recorded in the ledger for auditing.

### 4.5 Boundary of disclosure

| Open | Closed |
|---|---|
| A tool/command name and its permission status for the requester | provider `base_url`, config host, detailed DB label, `lease host pid` |
| Description, `usage`, parameter schema | The contents of another room, another person's memory |
| Capability status (e.g. `automation`: not yet active) | The roles of others |

The infrastructure facts appear only in the DM/owner context; in other rooms the label is coarse (`Brain: <model>`, `Surface: whatsapp`, `Mode: primary`).

### 4.6 Honesty about capabilities

- The soul does not claim a capability that is **not there**; when the requester is not authorised, it explains the step that repairs that, instead of pretending.
- The soul **does not** promise what does not exist yet: if automation is not active in that instance, it says so — it is not on yet, so it cannot remind them.
- When asked how it works, it answers with **behavior** (what it remembers, when it asks for permission, why it cannot discuss another room), not with infrastructure detail.

### 4.7 Reading configuration: through `settings`, not `shell`/`read`

Closed defects: today the soul tends to read configuration with `shell`/`read` and only uses `settings` when changing something. Whereas:

- Reading through `settings` **needs no approval** (`get`/`schema`/`reload`, `tools/settings.py:294-297`), so there is no reason to use the shell.
- Writing sensitive keys is already blocked (`SECRET` → `ValueError`, `:346-347`), but **exposing** an unredacted sensitive value is not: `action=get` returns `str(value)` as is, and a raw file when `path` is empty (`:371-385`), while `redact_dsn` is only used on the write path (`:395-396`, `:436`).

Rules:

| Rules | Contents |
|---|---|
| Routing | Configuration is read through `settings` (`schema` for the structure, `get` for values), **not** through `shell`/`read` |
| Redaction read path | A matching `SECRET` key → `***`; `database.url` → `redact_dsn`; URLs with userinfo are stripped; `get` without `path` never returns a raw file |
| `.env` | Guarded on the configuration read path: `settings` does not return its contents and `read` rejects it. `shell` can still read it **only** through approvals; to close the secret path the guard adds the patterns `.env`/`printenv`/`/proc/*/environ` as sensitive commands. `info` only mentions the **names** of env variables, never their values. |
| `read` | Rejects host configuration files and points to `settings` (deterministic, closing the `cat config.yaml` path) |
| Index | Marks it explicitly: `settings`: free to read (`schema`/`get`), writing needs `/approve` |
| Audit | Reading does not need approval, but changes are recorded through audits as they are now |

The principle equals 4.5: **structure and capability open; secret values and infrastructure kept closed.**

### 4.8 Self-knowledge: topic index, generated-first, on by default

The soul must be able to answer questions about itself correctly **without** putting long prose into every payload, and it must not go stale when the architecture changes.

**Index (kept short, on Layer 2):**

```
- capabilities & permissions (tool/command + role status) -> info(kind="catalog")
- how I work (ruangan, ingatan, izin, inisiatif, gagal, batas) -> info(kind="self", name=...)
- effective thresholds (retention, daily limits, permission mode) -> info(kind="config")
- product documentation (README: what this is, architecture, roles, credits) -> info(kind="doc", name="README")
```

**Topic `self` and its sources:**

| Topic | Contents | Source |
|---|---|---|
| `ruangan` | a soul lives in many rooms; it only has its requester's room; each instance is born in one fixed room | `behavior:rooms` |
| `ingatan` | what is remembered; what fades vs survives; a completed task is stored; credentials are not stored; a correction changes the value but stores the old one; contradictions are marked | `behavior:memory` + the config bucket |
| `izin` | how the role is determined; reading settings is free; changing requires `/approve`; approval does not add permissions | `behavior:permissions` + `approval.mode` |
| `inisiatif` | it does not greet first; the reminder of the requester, only in the room | `behavior:initiative` + the automation flag |
| `gagal` | no claim without evidence; no promise of silence; a missed job is named + apologized for + offered | `behavior:failure` |
| `batas` | what may and may not be told (media/path/DB/replacing a room/other people's business/internal matters) | `behavior:limits` |

**Four generators (all without an LLM):**

| Generator | Single source | Note |
|---|---|---|
| `catalog` | `registry.discover` + `Handler.parameters` | JSON-shaped schema. |
| `config_schema` | `_schema_paths` over the dataclass (`tools/settings.py:85-98`) | dot-path + default, without values |
| `behavior` | one module `engine/self_knowledge.py` | the only prose; each entry annotates `behavior:<id>` |
| `topology` | `ast.parse` over `eidesoul/engine/**.py` | dependency map; the same data feeds the AGENTS architecture test (`engine` does not import `gateways`) |

**Rules against staleness:**

1. **Generated-first**: structured facts (schema, registry, config flags, import graph) are never retyped by a human.
2. **No hardcoded numbers**: retention and limits are expressed through **coarse buckets** computed from config (`a matter of weeks` / `months` / `> a year`). The exact number may only come from the operator.
3. **A docstring is not the source of the truth**; only structured facts are trusted.
4. **Cached at boot**: generator results are computed once and cached; `info` only looks up strings. A `max_chars` limit per topic.

**Guard (not discipline)** *follows implementation 0015; until `scripts/selfcheck.py` exists, the items below are not machine-enforced*: `scripts/selfcheck.py --check` + `eidesoul selftest`:

- every `behavior:<id>` is registered; removing or renaming a behavior without updating the list → **failure**;
- every `config:`, `role:`, and `registry` reference resolves to a real key;
- the **forbidden-token list** never appears in any topic output: host path, `base_url`, DSN, env name, model id, internal threshold, table name;
- every topic is non-empty and below `max_chars`;
- `topology` is consistent with the AGENTS rules.

**Pinned stays separate.** The Layer 1 block (silence in every room + no oracle, no claim without tool evidence, no memory creation, credentials never stored, the meaning of approval, AI honesty) is **always present**, regardless of this feature, including when it is off: when in doubt, do not guess.

**On by default, can be disabled.** `engine.self_knowledge.enabled: true` by default. When disabled:

- the capability index, the `self` topics, the `config` kind, and `topology` are not injected/generated;
- `info` returns a clear signal that self-explanation is **disabled** (data, not an error);
- the soul **does not guess**: it states that self-explanation is off and points to the operator;
- the Layer 1 block still applies, so the honesty/privacy rules do not die.

### 4.9 Product documents: subheading index, section read

The README explains the product (what this is, the architecture, roles, credits). It is read like this:

```
info(kind="doc", name="README") → subheading list (slug) + one-line hint
info(kind="doc", name="README", section="credits") → that section's contents only
```

**Why not `read`:** the README is in the project directory, not in the workspace, so `read` cannot reach it. `workspace_roots` points `workspace` at just `public/` + `scratch/<chat>` for regular roles, and at all workspaces for trusted roles; none of them points to the project directory. Reading it must go through an explicit permission, not an accident.

**Dynamic rules (no slug list is stored):**

| Rules | Contents |
|---|---|
| Normalization | **GitHub-compatible**: lowercase; strip inline formatting (`code`, **bold**, links); **drop other punctuation** (including dots: `soul.yaml` → `soulyaml`, not `soul-yaml`); spaces → `-`; anything outside letters/digits is dropped |
| Duplicate | twin headings get `-2`, `-3` |
| Ambiguous | resolution order: exact slug → small fuzzy match; if still ambiguous, return the **heading list** so the soul can re-select in the same turn |
| Section fill | from that heading to the next heading at the same level or higher; subsections included |
| Code in fence | a heading inside a fenced code block is ignored; our own documents contain sample blocks with `##...` |
| Cache | the index is computed from `(path, mtime, size)`; a changed file → a new index; section contents are read on call |
| Read failure | a clear data signal ("document not available"), not an internal `error:` |
| Language | `README.md` vs `README.id.md` selected from `soul.language`; slugs are computed per file, so an Indonesian title works without a special mapping |
| Name | `name` is a **path relative to the root** (e.g. `README.md`, `docs/v0.1.0/README.md`); there is no bare-name resolution, so two files named README never get confused |
| Limit | index: headings only; section content is bounded by `max_chars`; pasting raw files is forbidden |
| Qualities | a data result, not instructions (quarantine ADR-0008); the forbidden-token check is still run |

**Default allowlist:** `docs: ["README.md"]` — entries are **paths relative to the project root**, not bare names, so the `README.md` product file and `docs/v0.1.0/README.md` (the ADR index) can be told apart. `LOG.md`, `TASK.md`, and `AGENTS.md` are **not** in the default: LOG may contain incident records, TASK an internal worklist, AGENTS the code-change rules. The operator may add them consciously.

**Verified against the real files (2026-11-02):** `README.md` has 13 headings (1×H1 + 12×H2), no duplicates, and **all 13/13 anchors used by the document itself match** a GitHub-compatible slug, `1` included. Size: 421 lines / 21.5 KB; the largest sections are `soul.yaml` 3.4 KB, `Credits` 2.9 KB, and `Safety` 2.5 KB, so `docs_max_chars: 4000` covers everything. A section that exceeds the cap is cut at a line boundary, not truncated silently. `H1` is treated as the **document title**, not as a section.

Document records (not index problems): `README.id.md` still links English anchors (`#architecture`, `#quickstart`, `#credits`, ...) while its headings are Indonesian, so seven links are off; our index is not affected because slugs are computed from the heading.

**Decisive rule** (completes 4.5): the operator's runtime stays closed (model address/name, host path, env name, DSN, lease host/pid, the exact model id, internal thresholds). Explaining the product does not expose the runtime prompt/configuration.

**Mandatory test** (small fixture): twin heading · emoji heading · heading containing a link · heading inside a fence · nested headings, 1—3 levels · missing file — assert the slug, dedupe, rejection of a heading in a fence, resolution to the level limit, and the signal when the file does not exist.

## 5. Configuration

```yaml
engine:
 prompt:
 pinned_blocks: [base, identity, honesty, privacy, cross_room
 capability_index: true
 capability_index_max_chars: 1200
 capability_index_two_level_at: 150 # above this, the index names families (not names); §4.2.2
 salience: true # only reference blocks join the budget
 thread_chars: 4000 # thread quote
 tail_max_messages: 20 # conversation replay window (previously hardcoded in assembly.py)
 tail_min_messages: 6 # minimum replay even when the character budget is tight
 tail_chars: 1200 # replay character budget before the minimum applies
 tools:
 hot: [read, chat_history, memory, info # the schema is always sent
 activate_on_demand: true # the rest goes through info
 self_block:
 redact_infra: non_administrator # non_administrator | never | always
 self_knowledge:
 enabled: true # on by default; turning it off does not turn off the Layer 1 block
 capability_index: true
 topics: [ruangan, ingatan, izin, inisiatif, gagal, batas
 coarse_buckets: true # retention/limits are stated coarsely, not as hardcoded numbers
 topology: true # dependency map (also used by the AGENTS architecture test)
 max_chars_per_topic: 1200
 forbidden_token_check: true
 docs: ["README.md"] # allowlist of paths relative to the project root; README.id follows the soul language
 docs_max_chars: 4000 # limit per section read
```

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| P50/p95 prompt characters per turn | Falling compared with the status quo; monitored by role |
| `capability_index_chars` p95 | ≤ `capability_index_max_chars` (1200) in both modes |
| `capability_index_locked_lines` | = the number of families (setting §4.2.1) |
| `capability_index_level` | 1 (by name) or 2 (families); it only shifts when the number of capabilities changes |
| `info` calls per turn after two-level mode | A modest rise; a large surge means the family index is less informative |
| `info` calls per turn | Measured; a spike means the index is less informative |
| Tool activation in the middle of a turn | Works; zero extra turns for indexed capabilities |
| Missing pinned blocks | 0 (tested with a small budget) |
| False capability claims (eval probe) | 0 |
| Credential leak through the configuration read path | 0 (tested: DSN, `SECRET` key, proxy userinfo) |
| `settings get` without `path` returning a raw file | 0 (tested) |
| A `behavior:<id>` not listed by the check script | 0 (check failure) |
| Forbidden token in topic output | 0 (tested) |
| Self-knowledge probe (correct answer without leaking) | 100% |
| Document slug resolution (first match) | ≥ 95%; the rest self-correct from the heading list |
| Heading inside the indexed fence | 0 (tested) |
| Forbidden token in document section output | 0 (tested) |
| An infrastructure fact leaking in a non-administrator room | 0 (tested) |

## 7. Acceptance criteria

- [ ] Pinned blocks are always present; only reference blocks compete in `salience`.
- [ ] The capability index contains **all** tool and command names the engine has, with the marker `allowed`/`locked` + the reason for the sender's role.
- [ ] The catalog is not hidden; `info` gives details and schemas even when the requester is not authorised.
- [ ] `web_search` is fixed to `allowed` without an API key (the `ddgs` backend); the index never marks it `locked` because of the key.
- [ ] `info` does not call the LLM, and its result is treated as data, not instructions.
- [ ] `info` enables the tool for the rest of the turn; `llm.py` computes `tools`/`by_name` per iteration.
- [ ] Activation only adds; the guard/safety and the permissions are still evaluated per call in `execute`.
- [ ] `info` does not add a second message to the user (the `ctx.sent` path is fixed).
- [ ] Infrastructure facts are hidden in non-administrator rooms; the DM/owner still sees the details.
- [ ] The index is compiled deterministically (name order, stable per role) so the prompt prefix is stable.
- [ ] `info(kind="config")` returns the configuration schema (dot-path, file, default) without values.
- [ ] `settings get` redacts the `SECRET` key, `database.url`, and URLs with userinfo; without `path` it does not return the raw file.
- [ ] `read` rejects host configuration files and points to `settings`; `.env` is unreadable through `read`/`settings`, and the guards mark `.env`/`printenv`/`/proc/*/environ` as sensitive commands (approval).
- [ ] The capability index calls out `settings`: free to read, writing needs `/approve`.
- [ ] `info` self-knowledge without an LLM; the result is built from the generators (`catalog`, `config_schema`, `behavior`, `topology`) and cached at boot.
- [ ] Retention and limits are expressed through coarse buckets from config; no hardcoded numbers in the prose.
- [ ] Every `behavior:<id>` is listed; `scripts/selfcheck.py --check` + selftest fail when one is missing or a forbidden token appears.
- [ ] `topology` is consistent with the AGENTS rules (the engine does not import the gateways).
- [ ] `engine.self_knowledge.enabled: true` by default; when disabled, the index/topics/`config`/`topology` are not produced, `info` signals it as disabled, and the soul does not guess.
- [ ] Disabling the feature **does not** disable the Layer 1 block (the honesty/privacy/silence rules remain).
- [ ] `info(kind="doc", name="README")` returns the list of subheadings computed from the current file (not a stored list); a changed heading → a changed slug.
- [ ] GitHub-compatible slugs: punctuation is removed (not replaced with `-`), so `section="soulyaml"` and the anchor the README itself uses both match.
- [ ] Sections that exceed `docs_max_chars` are truncated at a line boundary, not silently; `H1` is treated as the document title.
- [ ] Section content stops at the next heading of the same level; headings inside a fence are ignored.
- [ ] Files per language are selected from `soul.language`; a missing file yields a clear data signal.
- [ ] The default allowlist is `[README]`; `LOG.md`/`TASK.md`/`AGENTS.md` are not included without an operator choice.
- [ ] Document output is bounded by `docs_max_chars` and never attaches raw files; it returns data, not instructions.
- [ ] The forbidden-token check is also run on the document output.
- [ ] The soul locks capabilities that need a repair step and does not promise capabilities that do not exist yet.
- [ ] Two locked names in the same family produce **one** line (the locked-line count = the family count).
- [ ] Fixture 151 → the capability index switches to two-level mode (families, not 151 names); the order is deterministic and the block hash is the same across two consecutive compositions.
- [ ] `capability_index_max_chars` is still enforced in both modes; negative values for `capability_index_two_level_at` are rejected at boot.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| The model does not know it must expand a tool | The index is required to name every name + the rules require detail → call `info` |
| An extra turn for what is always needed | The invariant is on Layer 1, not behind a pointer |
| `salience` drops a critical block | Pinned blocks skip the selection + a small-budget test |
| The provider prompt cache breaks | A deterministic, per-role-stable index |
| Prompt bloat from the index | The `capability_index_max_chars` limit |
| `info` used for enumeration | Allowed (open source) and recorded; the permission gate stays in `execute` |
| Activation used to raise permissions | Activation never changes permission; it only adds schema |
| A data leak through `info` | `info` exposes only capabilities and schemas; configuration values never enter the log |
| Credentials leaking through the configuration read path | Redaction on the read path (`SECRET`/`redact_dsn` + `get` without `path`) is rejected + `read` rejects configuration files |
| The behavior prose goes stale when the architecture changes | One module `self_knowledge.py` + the `behavior:<id>` annotation + a failed check when the list is out of sync |
| The numbers in the prose differ from config | No hardcoded numbers; coarse buckets computed from config |
| The model answers without calling the index | A Layer 1 line: "when asked how you work → call the index first, do not guess" + an eval probe |
| The feature is off and the soul guesses | When disabled, `info` signals it clearly, and Layer 1 requires it not to guess |
| The generators leak internals | The forbidden-token list tested by the script check |
| Document headings change shape (emoji/link/nested) | Slugs are computed at the time, not stored; the fixtures include emojis, links, twins, nesting, and fences |
| Reading work documents (LOG/TASK/AGENTS) | The default allowlist is only `[README]`; anything else is a conscious operator choice |
| The README contains sensitive examples | The forbidden-token check is also run on document output |

## 9. Interaction with other ADRs

- **ADR-0006** — write and disclosure limits follow the room ceilings; `info` never touches memory data.
- **ADR-0007** — the index shows permissions per requester; the gate is fixed in `execute`, and activation is not a permission.
- **ADR-0008** — an `info` result is data and subject to the quarantine rules; capabilities raise no trust.
- **ADR-0013** — activation happens within the same turn; there is no second turn.
- **ADR-0014** — the automation status (active/inactive) appears in the index, so the soul does not promise it wrongly.
- **`AGENTS.md`** — the repo shape rules (e.g. `engine` does not import `gateways`) **will** be tested from the `topology` data, not from rewritten prose. That test is not in `tests/` yet; creating it is part of the acceptance criteria in §7.

## 10. Code references

- `eidesoul/engine/self_knowledge.py` — **new module**: four generators (`catalog`, `config_schema`, `behavior`, `topology`) and the `behavior:<id>` prose.
- `scripts/selfcheck.py` — **new script**: verifies `behavior:<id>`, the `config:`/`role:`/`registry` resolutions, the forbidden-token list, and `topology` consistency.
- `eidesoul/engine/registry.py:148` `discover` (the single source for the tool/command catalog).
- `eidesoul/engine/tools/settings.py:85-98` `_schema_paths` (the single config schema source); `:294-297` free reads without approval.
- `scripts/affect.py`, `scripts/patterns.py` — precedent for the "data + guard script" pattern (`--check`).
- `README.md`, `README.id.md` — the source of the product documents (the subheading index is computed from this file).
- `eidesoul/engine/tools/read.py:28-30` + `tools/__init__.py:120-165` — the `workspace_roots` limit (why `read` cannot reach the project directory).
- `eidesoul/engine/prompts.py:150-186` `self_block` (the place of the infrastructure facts).
- `eidesoul/engine/prompts.py:190-386` `system_prompt`, the block list, `salience.select`, `language_line`, `Date`.
- `eidesoul/engine/salience.py:38-60` `JOIN`, `block_score`, `select` (where pinned blocks are passed through).
- `eidesoul/engine/llm.py:150-260` the tool loop `execute`: `tools`/`by_name`, `kwargs["tools"]`, budget, repeated-call guard.
- `eidesoul/engine/tools/__init__.py:180-235` `execute` (the one permission gate) and the error message that already names the tool.
- `eidesoul/engine/permissions.py:56` + `eidesoul/engine/commands/help.py` `commands_for` (the command-list source).
- `eidesoul/engine/registry.py` `Handler` (`name`, `description`, `usage`, `parameters`) as the `info` detail source.
- `eidesoul/engine/context.py:641-683` + `FOOTER` (`:55-58`) — the immutable user-content format.
- `eidesoul/engine/agent.py:279-283,759-788` `_specs(roles)` and the `system_prompt` assembly.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0015-capability-manifest.md`.*
