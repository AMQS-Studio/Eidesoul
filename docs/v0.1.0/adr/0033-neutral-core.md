# ADR-0033: Neutral Core, Specialized Edges — No Culture, Transport, or Vendor in Mechanism

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0000 (surface shape and language data), ADR-0009 (the ADR home and its gate), ADR-0018 (gateway contract), ADR-0027 (clean by default), ADR-0028 (plugin unit), ADR-0031 (layout, growth budget, gates), ADR-0032 (one door)
- **Supersedes:** —
- **Related:** ADR-0006 (privacy ceiling), ADR-0007 (approval classes), ADR-0014 (automation), ADR-0019 (`/status`), ADR-0022 (role), ADR-0025 (stable prompt prefix)
- **Implementation:** draft — the contract and its gates are decided here; every violation listed in §1 is still in the tree.
- **Location note:** cross-cutting (it constrains every engine surface), so it sits at the ADR home root. The Indonesian copy is `docs/v0.1.0/adr/id/0033-neutral-core.md`.

---

## 1. Context

The engine already has the right *shape* for data-driven behavior: chat strings live in `eidesoul/lang/<code>.yaml` (two catalogs, 249 keys each), character data lives in `eidesoul/library/`, and an extension declares itself in a manifest. What it does **not** have is the rule that keeps mechanism and data apart. Culture, geography, transport, and vendor are written into the mechanism itself, so a deployment outside one country or one transport must edit code. Measured on 2026-11-04:

**Language and culture hot-wired into mechanism**

- Thirteen defaults force Indonesian: `language: str = "id"` on nine engine signatures (`character/character.py:53,61,68,116`, `guard/patterns.py:33,45,50`, `jobs/ambient.py:28`, `jobs/cognition.py:373`), plus `soul.py:60`, `i18n.py:50`, `i18n.ALIASES["auto"]`, and `llm.py:51`. Both catalogs carry the same 249 keys, so this is policy, not a capability limit.
- Four word tables are code: `access/privacy.py:27` (`YES`), `access/privacy.py:32-46` (`LEVEL_WORDS`), `character/character.py:188-211` (`SEVERITY`, seventeen Indonesian words), and `access/target.py:22-26` (a second profanity list).
- The safety net is culture-specific: `guard/patterns.py:15-19` hardcodes the crisis phrases (`bunuh diri`, `mau mati`, `gantung diri`, `overdosis`, …) and the sensitive-token regex includes `kredensial`, while its own module docstring claims those patterns are "language-independent". A French or Japanese deployment has no crisis detection at all.
- The shipped character library is one country: `eidesoul/library/patterns/jawatimur.json` (72 patterns) and `eidesoul/library/facts/indonesia.json` (306 facts) are the only packs, both `language: id`, so `packs_for("en")` is empty and a non-Indonesian soul resolves to no patterns and no facts. `lexicon/`, `affect/`, and `intent/` are already bilingual.

**Geography in mechanism**

- `jobs/schedule.py:16` sets `DEFAULT_TZ = "Asia/Jakarta"`; `zone()` and `_next_occurrence` use it whenever a manifest omits `tz`, so every schedule without an explicit zone runs on Jakarta time.
- `jobs/currents.py:18-31` carries one country's news topics (`pemerintah`, `ekonomi`, `rupiah`, `prabowo`, `sawit`, `surabaya`, `jawa timur`) and a query suffix `" Indonesia {YEAR} terbaru"`; `stores/currents.py` persists them under the `currents` state key, and `commands/konteks.py:1` documents itself as "the Indonesia (+62) context". `currents` also duplicates what the `web_search` tool and the facts packs already provide, and it binds the prompt to one country's news cycle.

**Vendor and plugin in mechanism**

- `admin/templates.py:70` writes `plugins: {"google": …}` into every fresh host config, `admin/templates.py:335-345` grants the plugin tool `google` to the `member` and `guest` roles, and `admin/templates.py:389-397` writes three Google environment names into `.env` even though the plugin manifest declares only one (`plugins/google/plugin.yaml`, `secrets: [GOOGLE_SERVICE_ACCOUNT_PATH]`). The template is a stale hand copy of a manifest, and a hundred plugins would mean a hundred edits there.
- `memory/episodes.py:149-161` maps tool names to target types in a constant, and two of those names belong to a plugin (`google`) and to a gateway (`send_to`, `chat_history`).

**Transport in mechanism**

- `access/relay.py` binds an approval to "the content owner's DM"; `stores/history.py:683` exposes `dm_thread`, called from `access/privacy.py:202`, `jobs/automation.py:1673`, and `jobs/cognition.py:1008`.
- `llm.py:33` carries `DIGEST_PROMPT` ("You summarize a chat group") and `llm.py:648` a `digest(chat_name, …)` method; `inbound.py` defines a `Chat` protocol. A robot embodiment must pretend to be a chat to use the engine.

**Names and legacy**

- Four commands carry Indonesian names (`commands/cari.py`, `kirim.py`, `konteks.py`, `memori.py`), six command aliases and two tool aliases do the same (`tenang`, `bantuan`, `bahasa`, `tautkan`, `ingat`, `lepas`; `tools/web_search.py:107`, `tools/memory.py:201`), and 76 catalog keys are named after them.
- `access/roles.py:20` keeps `LEGACY = "owner"` with a migration path in `admin/roles.py`, `admin/people.py:22` keeps `LEGACY_GATEWAY = "legacy"`, and `memory/ledger.py:104` and `stores/history.py:746` keep "shutdown compatibility hooks".

Two consequences follow. First, **safety is not portable**: the crisis net covers one language family. Second, **the core is not extensible**: every new country, transport, or plugin edits the mechanism, which is exactly the churn ADR-0031's growth budget was meant to prevent.

## 2. Decision drivers

1. **Neutrality must be executable, not remembered.** A review checklist already failed: every item above survived multiple review passes because it "worked".
2. **Safety cannot depend on a country.** Crisis detection is a spine behavior and must cover every language the engine claims to speak.
3. **Install is the first impression.** `eidesoul init` must produce a host that mentions no vendor, no country, and no transport.
4. **Extensions must not edit core.** A new plugin, gateway, or language adds data and manifests; it never touches `eidesoul/engine/**` or `eidesoul/admin/templates.py`.
5. **Refusal over silent fallback.** A missing pack, timezone, or plugin is an explicit error naming what is missing — never an Indonesian, English, or Jakarta default chosen on the operator's behalf.
6. **One gate, mechanical.** The rule lives in a script the suite runs, with a listed inventory, not in prose.

## 3. Options considered

**A. Status quo plus review discipline.** Rejected: §1 is the evidence that review alone does not hold the line.

**B. Configuration overrides.** Add `soul.language` and `engine.timezone` settings and document them. Rejected as insufficient: an override hides the default instead of removing it, and it does nothing for the word tables, the vendor names, or the crisis net.

**C. Packs, manifests, tool metadata, and one mechanical gate (chosen).** Every cultural, geographic, transport, and vendor fact becomes data with an owner (`lang/*.yaml`, `library/<kind>/<lang>.json`, `plugin.yaml`, `config.yaml`), the core declares nothing by name, and a gate proves it.

**D. Move i18n and safety out into a plugin.** Rejected: `i18n.py` is one of the fifteen top-level engine modules (ADR-0031), and a soul engine must speak and detect crisis before any plugin is installed. Safety is a spine behavior (ADR-0000, ADR-0007).

## 4. Decision

### 4.1 The neutral-core contract

The engine must boot, pass its suite, and serve `/status` under this configuration: `plugins: {}`, no gateway registered, no library pack installed, `language: en`, and an empty database. Any data it needs and cannot find is an explicit refusal that names the missing thing. This is the operational definition of "neutral"; anything weaker is a slogan.

### 4.2 Culture is data

Crisis and sensitive patterns, profanity severity, affirmation words, and level words live in per-language packs next to the existing ones:

- `eidesoul/library/safety/<lang>.json` — `crisis`, `sensitive` phrase lists.
- `eidesoul/library/severity/<lang>.json` — word → `{kind, level}`.
- `eidesoul/library/lexicon/<lang>.json` — gains `affirmations` and `level_words`, so the negative and positive sides are symmetric.

`eidesoul/engine/**` carries no non-English literal and no language-specific word list. The only exception is the English safety net, which the gate records explicitly.

### 4.3 Language is a required value

No `language: str = "id"` default survives in `eidesoul/engine/**`. `soul.language` is written by `eidesoul init`, every call site passes it explicitly, and catalog lookup falls back to `en` only. `"auto"` resolves to the operator's locale, never to a fixed country.

### 4.4 Geography is a required value

An automation manifest must state `event.tz`; a missing `tz` is a `ManifestError`. No country's zone is a default, and no module names a country's news topics. `currents` is deleted in full — `jobs/currents.py`, `stores/currents.py`, the `/konteks` command, its prompt block, and its state key. Fresh facts come from the `web_search` tool; durable knowledge comes from facts packs grown by `scripts/grow.py`; a live-news capability is a plugin.

### 4.5 Transport lives in the seam

`dm_thread`, the `Chat` protocol, `digest`, and group summaries leave the engine's own vocabulary: a body resolves its own threads and summarizes its own rooms. The engine speaks of people, threads, and actions. Where the engine needs "the private thread with this person", the body answers through the inbound seam (ADR-0018, ADR-0032).

### 4.6 Vendor names are absent from core

A plugin, gateway, or vendor identifier may appear only inside its own tree and its manifest. In particular:

- `admin/templates.py` writes `plugins: {}` and grants role templates core tools only.
- `.env` is generated from the core names, the chosen provider's key, and the union of `secrets` declared by the plugin manifests actually installed.
- `memory/episodes.py` derives a target from the tool's own declaration, not from a name table.

### 4.7 Discovery over enumeration

Metadata lives with the thing it describes: a tool declares `target` (`file`, `room`, `document`, `process`, or `""`) and keeps its `family`; a plugin declares `provides` and `secrets`; a pack declares `language`; a manifest declares `tz`. No central name → kind table exists in core.

### 4.8 Delete specialization; do not deprecate

Because nothing is released, specialization is removed rather than shimmed: `currents` (module pair, command, prompt block, state key), the gender inference in `access/target.py` together with its `gender_gate`, `LEGACY`/`LEGACY_GATEWAY`, and the shutdown compatibility hooks. The explicit identity data stays; only the heuristic guess and its gate go.

### 4.9 Command names are English, with no localized aliases

`cari`, `kirim`, and `memori` become `search`, `send`, and `memory`; `konteks` disappears with `currents`; the six Indonesian command aliases and two tool aliases are removed, and the catalog keys are renamed with them. A gateway or plugin may expose its own namespaced commands; core does not keep a localization layer for its own surface.

### 4.10 The gate is mechanical

Section 6 lists the checks. They run in the suite, not in review.

## 5. Configuration

```yaml
soul:
  language: en            # written by `eidesoul init`; required, no default
engine:
  safety:
    allow_degraded: false # true runs the English net when a language pack is absent
automation:
  event:
    tz: Europe/Berlin     # required per manifest; no engine default
plugins: {}               # no plugin key in a shipped template
```

Pack layout:

```text
eidesoul/library/safety/<lang>.json     {"version":1,"language":"en","crisis":[…],"sensitive":[…]}
eidesoul/library/severity/<lang>.json   {"version":1,"language":"en","words":{"word":["kind",2]}}
eidesoul/library/lexicon/<lang>.json    gains "affirmations" and "level_words"
```

Handler metadata (proposal):

```text
TOOL = Handler(name="read", family="read", target="file", …)
```

## 6. Metrics and gates

Counts to drive to zero, measured 2026-11-04:

| Specialization | Count | Where |
| --- | --- | --- |
| language defaults | 13 | 9 signatures + 4 module/config defaults |
| hardcoded word tables | 4 | `privacy` ×2, `character`, `target` |
| country defaults | 2 | `Asia/Jakarta`, `currents` topic list |
| vendor references | 4 | template `plugins`, role grant, `ENV_TAIL`, `TARGET_TYPES` |
| transport references | 5 | `relay`, `dm_thread` ×3, `Chat`, `digest` |
| non-English surface names | 12 | 4 commands, 6 command aliases, 2 tool aliases |
| legacy markers | 4 | `LEGACY`, `LEGACY_GATEWAY`, compatibility hooks ×2 |

The gate, `scripts/neutral.py --check` (new module), fails when:

1. `eidesoul/engine/**` contains a non-ASCII character outside the explicit English safety allowlist.
2. `eidesoul/engine/**` contains a token from the banned vocabulary list (Indonesian function words plus the words moved to packs).
3. A vendor, gateway, or plugin identifier appears under `eidesoul/engine/**` (allowed only in `eidesoul/providers/`, `eidesoul/gateways/`, `plugins/`).
4. `eidesoul/engine/**` contains a `language="id"` default, a `default="id"`, or a `"Asia/` literal.
5. A catalog language has neither a safety pack nor a refusal record.
6. A `Handler` lacks `target`, or a shipped template names a plugin.
7. The neutral smoke fails: `pytest -q` and `eidesoul selftest` run with `plugins: {}`, `language: en`, and no gateway.

It runs beside the existing gates: `pytest -q`, `ruff check eidesoul scripts tests plugins migrations`, `python -P scripts/types.py --check`, `python scripts/selfcheck.py --check`, `python scripts/adr.py --check`.

## 7. Acceptance criteria

- `grep -Pn '[^\x00-\x7F]' eidesoul/engine` is empty apart from the allowlisted English safety net.
- The banned-vocabulary and vendor scans under `eidesoul/engine/**` report zero hits.
- `language` has no default anywhere in `eidesoul/engine/**`; every call site passes it.
- A manifest without `tz` fails with `ManifestError`; no `Asia/` literal exists in the engine.
- `currents` is gone: no `jobs/currents.py`, no `stores/currents.py`, no `/konteks`, no reader of the `currents` state key, no prompt block.
- `packs_for(lang)` is non-empty for every language that ships a catalog, or the soul is refused with a message naming the gap.
- `eidesoul init` on a clean host writes `plugins: {}` and a `.env` with no plugin's secrets; installing a plugin adds its declared `secrets` to the generated `.env` without editing `templates.py`.
- Deleting every plugin and gateway still boots the engine and `eidesoul selftest` reports `safety: ok` for a language with a pack and `safety: degraded` only when `allow_degraded: true`.
- Every command and tool name is English; no localized alias resolves through core.

## 8. Risks and mitigations

- **Safety regression while the crisis net moves to packs.** Mitigation: the English net stays compiled in as the last line, a language without a pack is refused unless `allow_degraded: true`, and `selftest` asserts one crisis phrase per shipped pack.
- **Pack authoring cost.** Mitigation: `scripts/grow.py` already grows packs from a corpus, `--check` validates a pack's shape, and the first release ships `en` and `id` only.
- **Rename churn.** Mitigation: one commit per phase; tests, catalogs, role templates, and docstrings move in the same commit; nothing is released, so no shim is written.
- **Banned-word gate false positives.** Mitigation: the gate prints the matched token and file; the non-ASCII scan is primary, the word list is curated and reviewed per phase.
- **`Handler.target` touches every tool.** Mitigation: the field defaults to `""`, which means "no target", so a tool that does not declare one behaves as today and only loses episode matching.
- **Losing live context with `currents`.** Mitigation: `web_search` covers ad hoc lookups, facts packs cover durable knowledge, and a plugin can reintroduce a news feed without core changes.

## 9. Interaction with other ADRs

- **ADR-0000** already declares language data as catalogs and the seam as embodiment-neutral; this ADR makes both claims executable.
- **ADR-0006** keeps the privacy ceiling; only the affirmative vocabulary moves into a pack.
- **ADR-0007** is untouched: deleting the gender gate removes a heuristic, not an approval class.
- **ADR-0009** owns the register and the ADR gate; the new gate follows its pattern.
- **ADR-0014** gains a required `event.tz`; the automation state machine is unchanged.
- **ADR-0018** keeps the contract, while `Chat` and `dm_thread` move out of core into bodies.
- **ADR-0019** reports safety mode in `/status` and in `selftest`.
- **ADR-0022** keeps the protected role; only the legacy name path goes.
- **ADR-0025** is unaffected: a required language is already part of the stable prefix.
- **ADR-0027** is enforced here: clean by default means a template with no vendor block.
- **ADR-0028** gains the rule that template `.env` is derived from a manifest's `secrets`.
- **ADR-0031** provides the gates this ADR extends and the module budget it must respect.
- **ADR-0032** provides the door; this ADR removes the transport vocabulary that reached around it.

## 10. Code references

- `eidesoul/engine/guard/patterns.py:15-19` — the hardcoded crisis and sensitive patterns.
- `eidesoul/engine/character/character.py:53,61,68,116` — the language defaults that shelter the hot-wired vocabulary.
- `eidesoul/engine/character/character.py:188-211` — `SEVERITY`, seventeen Indonesian words.
- `eidesoul/engine/access/target.py:22-26,111-155` — the second profanity list and the gender inference.
- `eidesoul/engine/access/privacy.py:27,32-46` — `YES` and `LEVEL_WORDS`.
- `eidesoul/engine/soul.py:60` — `language: str = "id"`.
- `eidesoul/engine/i18n.py:50,59` — the `normalize_language` default and the `auto` alias.
- `eidesoul/engine/jobs/schedule.py:16,30-36,136` — `DEFAULT_TZ` and its two uses.
- `eidesoul/engine/jobs/currents.py:1,18-31` — the topics and the country query suffix.
- `eidesoul/engine/stores/currents.py:1` — the `currents` state persistence.
- `eidesoul/engine/commands/konteks.py:1,11,71-75` — the country context command and its refresh words.
- `eidesoul/engine/commands/cari.py:48`, `eidesoul/engine/commands/kirim.py:30`, `eidesoul/engine/commands/memori.py:161` — Indonesian names and aliases.
- `eidesoul/engine/tools/web_search.py:107`, `eidesoul/engine/tools/memory.py:201` — Indonesian tool aliases.
- `eidesoul/admin/templates.py:70,335-345,389-397` — the plugin default, the role grant, and the `.env` tail.
- `plugins/google/plugin.yaml` — the manifest whose `secrets` the template already contradicts.
- `eidesoul/engine/memory/episodes.py:149-161` — `TARGET_TYPES` naming a plugin and gateway tools.
- `eidesoul/engine/access/relay.py:1-10` — the DM-bound relay approval.
- `eidesoul/engine/stores/history.py:683` — `dm_thread`, called by `access/privacy.py:202`, `jobs/automation.py:1673`, `jobs/cognition.py:1008`.
- `eidesoul/engine/llm.py:33,648-655` — the group digest prompt and method.
- `eidesoul/engine/access/roles.py:20` — `LEGACY = "owner"`; `eidesoul/admin/people.py:22` — `LEGACY_GATEWAY`.
- `eidesoul/engine/memory/ledger.py:104`, `eidesoul/engine/stores/history.py:746` — the compatibility hooks.
- `eidesoul/library/patterns/jawatimur.json`, `eidesoul/library/facts/indonesia.json` — the only shipped packs, both `language: id`.
- Proposed, not code yet: `eidesoul/library/safety/<lang>.json`, `eidesoul/library/severity/<lang>.json`, the `affirmations`/`level_words` lexicon keys, `Handler.target`, and `scripts/neutral.py`.

---

*This ADR lives at `docs/v0.1.0/adr/0033-neutral-core.md`.*
