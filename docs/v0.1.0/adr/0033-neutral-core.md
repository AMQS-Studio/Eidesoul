# ADR-0033: Neutral Core, Specialized Edges — No Culture, Transport, or Vendor in Mechanism

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0000 (surface shape and language data), ADR-0009 (the ADR home and its gate), ADR-0018 (gateway contract), ADR-0027 (clean by default), ADR-0028 (plugin unit), ADR-0031 (layout, growth budget, gates), ADR-0032 (one door)
- **Supersedes:** —
- **Related:** ADR-0006 (privacy ceiling), ADR-0007 (approval classes), ADR-0014 (automation), ADR-0019 (`/status`), ADR-0022 (role), ADR-0025 (stable prompt prefix)
- **Implementation:** implemented — every section has landed with its gate (`scripts/neutral.py --check`, the prompt contract suite, migrations 0014-0016); the last transport seam moved to the body in the same wave.
- **Location note:** cross-cutting (it constrains every engine surface), so it sits at the ADR home root. The Indonesian copy is `docs/v0.1.0/adr/id/0033-neutral-core.md`.

---

## 1. Context

The engine already has the right *shape* for data-driven behavior: chat strings live in `monoagens/lang/<code>.yaml` (two catalogs, 249 keys each), character data lives in `monoagens/library/`, and an extension declares itself in a manifest. What it does **not** have is the rule that keeps mechanism and data apart. Culture, geography, transport, and vendor are written into the mechanism itself, so a deployment outside one country or one transport must edit code. Measured on 2026-11-04 by a full sweep of `monoagens/engine`, `monoagens/admin`, `__main__.py`, the shipped templates, the image, and `scripts/`.

### 1.1 Language is a default, twenty-five times over

- Twenty-four signatures carry `language: str = "id"`: the nine first counted (`character/character.py:53,61,68,116`, `guard/patterns.py:33,45,50`, `jobs/ambient.py:28`, `jobs/cognition.py:373`) plus `turn/prompts.py:121`, `turn/context.py:317`, `turn/salience.py:49,62`, `memory/gate.py:63`, `memory/assess.py:133`, `memory/reflect.py:96,125,267`, `memory/store.py:681,1086`, `stores/affect.py:917`, `stores/history.py:280`.
- Eight call sites fall back with `or "id"`: `agent.py:897`, `access/privacy.py:93`, `access/relay.py:218`, `commands/__init__.py:31`, `jobs/automation.py:1944`, `jobs/cognition.py:387`, `jobs/supervisor.py:270`, `turn/prompts.py:239,247`.
- The module-level defaults match: `soul.py:60`, `i18n.py:50`, `i18n.ALIASES["auto"]`, `llm.py:51`, and `soul.py:82,251` which pass `"id"` into `normalize_language`.
- `soul.py:106-111` has no catalog at all: `if language == "en": "Reply in English."` and every other language is told **"Reply in Indonesian (Bahasa Indonesia)"**.
- Both catalogs carry the same 249 keys, so this is policy, not a capability limit.

### 1.2 Culture is a word table, eight of them

- `guard/patterns.py:15-19` hardcodes the crisis phrases (`bunuh diri`, `mau mati`, `gantung diri`, `overdosis`, …) and a sensitive-token regex that includes `kredensial`, while its own docstring claims those patterns are "language-independent".
- `character/character.py:188-211` — `SEVERITY`, seventeen Indonesian words; `:31` also names `pantun` as a preserved form.
- `access/target.py:22-26` — a second profanity list, with Indonesian markers at `:21-23,39-40`.
- `access/privacy.py:27,32-46` — `YES` (`ya`, `oke`, `boleh`, `sip`, `gas`, `lanjut`, `silakan`) and `LEVEL_WORDS`.
- `character/patterns.py:27` — `SERIOUS_REGISTERS = {"serious", "comfort", "soal"}`.
- `stores/style.py` is the largest: `:22` `PLACEHOLDERS = ("[Stiker]","[Gambar]","[Dokumen]","[Video]","[Audio]")`, `:27` `LAUGH = (wkwk|awok|hehe|haha)`, `:29-32` `PARTICLES` (twenty-three Indonesian/Javanese particles), `:33,59` `EMOJIS`, and `:34-60` `SEED` (median 34, p90 130, particles, emojis). It is the baseline for **every** soul: `:343-357` seeds an English chat with Indonesian rhythm.
- `self_knowledge.py:70-77` — `TOPIC_BEHAVIORS` keyed `ruangan`, `ingatan`, `izin`, `inisiatif`, `gagal`, `batas`; `:313-325` renders `## {key}` as a prompt heading and branches on `"izin"`, `"inisiatif"`, `"ingatan"`; `:462-465` falls back to `"id"` for a README name.
- `config.py:388` — the same six Indonesian ids as a config default, so `selfcheck` prints `topics=ruangan, ingatan, izin, inisiatif, gagal, batas`.
- `commands/memori.py:58,75,90,126` — the verb DSL is bilingual (`{daftar,list}`, `{cari,search}`, `{hapus,forget,lupakan}`, `{tambah,add,simpan}`), and `lang/en.yaml:121` leaks the Indonesian verbs into the **English** usage line.

### 1.3 Geography and the clock

- `jobs/schedule.py:16` sets `DEFAULT_TZ = "Asia/Jakarta"`, used by `zone()` and `_next_occurrence`, and `jobs/automation.py:155,1133` re-inject it while validating a manifest and while scheduling, so a manifest without `tz` silently runs on Jakarta time.
- `Dockerfile:5` sets `TZ=Asia/Jakarta` for the whole process.
- `soul.py:30,65,256` defaults `locale: id-ID` three times, `turn/prompts.py:178-191` defaults `research_block(locale="id-ID")` and prescribes "local government, economy, currency, energy, food prices, and public figures" — the `currents` topic list rewritten as prose, so deleting the module does not finish §4.4.
- `access/target.py:126-133` builds a prompt that says "an Indonesian chat app" when the locale starts with `id`.
- `tools/web_search.py:16` names four search engines in `WEB_BACKENDS` and `:64` leaves the ddgs engine at its `region="us-en"` default, so search is silently American.
- Calendar work reads the container clock: `turn/prompts.py:498`, `jobs/automation.py:1539`, and `jobs/ambient.py:315` call `date.today()`; `memory/context.py:82`, `memory/ledger.py:25`, and `stores/affect.py:835` use `time.localtime`, while `memory/store.py:2216` uses `gmtime`, so two day boundaries coexist.
- `tools/sandbox.py:17` and `config.py:250-259` forward the host `TZ` and `LANG` into sandbox and shell children.
- `jobs/schedule.py:76-84` hardcodes Monday-first English weekday names and lowercases only the list branch, so `days: "Mon"` is refused while `days: ["mon"]` works.
- `jobs/currents.py:18-31` carries one country's topics (`pemerintah`, `ekonomi`, `rupiah`, `prabowo`, `sawit`, `surabaya`), a query suffix `" Indonesia {YEAR} terbaru"`, `stores/currents.py` persists them, and `commands/konteks.py:1,72` documents itself as "the Indonesia (+62) context".

### 1.4 Vendor and plugin

- `admin/templates.py:70` writes `plugins: {"google": …}` into every fresh host config, `:335-345` grants the plugin tool `google` to the `member` and `guest` roles, and `:389-397` writes three Google environment names although the manifest declares one (`plugins/google/plugin.yaml`, `secrets: [GOOGLE_SERVICE_ACCOUNT_PATH]`).
- `schema.py:927-946` defines `google_identity` and `google_device` in the **core** DDL, and core code reads them: `access/identity.py:22-23,345`, `admin/people.py:14,90`.
- `admin/people.py:57-59` falls back to `("whatsapp", value)` when it meets a legacy id.
- `memory/episodes.py:149-161` maps tool names to target types, including the plugin tool `google` and the gateway tools `send_to` and `chat_history`.
- `tools/settings.py:33-37` closes the document kinds to `("engine","host","gateway","soul","role")`.
- Spine dependencies are named in core by design (ADR-0001, ADR-0031): `embed.py:15` pins the MiniLM model, `memory/mem0.py:19,186-204,213,223` drives Mem0 through the `openai` and `huggingface` providers, and `llm.py:1,10,177` uses the OpenAI wire client. Those are the carve-out §4.12 has to write down, not violations.

### 1.5 Transport

- `turn/prompts.py` uses chat vocabulary on fifty-two lines. The load-bearing ones: `:309` "You live in chat as {name}", `:319` "real mentions, DMs, and replies to your messages must be answered", `:347-350` "This chat is a direct message (DM)", `:360-372` "one soul across rooms … chat_history reads another archived room … human typing from the same number", `:379-384` deleted / `view-once` / `undecryptable` semantics, `:408-412` group-scoped quoting, `:427-432` animated stickers and voice notes, `:441-449` `send_to` as the only way to reach another room.
- `turn/decision.py` uses twenty lines: `ACTS = {"reply","ignore","react"}` (`:51`), `bubbles` in the decision schema (`:40-45`), and a prompt block built on mentions, DMs, and sticker reactions.
- `stores/style.py:22` renders media as `[Stiker]`, `[Gambar]`, `[Dokumen]`; `turn/context.py:506-513` renders quotes as `↳ replying to …`; both are body-side renderings in core.
- The five first counted sit unchanged: `access/relay.py:1-10` binds an approval to "the content owner's DM", `stores/history.py:683` exposes `dm_thread` to `access/privacy.py:202`, `jobs/automation.py:1673`, and `jobs/cognition.py:1008`; `llm.py:33,648-655` summarizes a chat group; `inbound.py` defines a `Chat` protocol.

### 1.6 Names, legacy, and the extension surface

- Four commands carry Indonesian names (`commands/cari.py`, `kirim.py`, `konteks.py`, `memori.py`), six command aliases and two tool aliases do the same (`tenang`, `bantuan`, `bahasa`, `tautkan`, `ingat`, `lepas`; `tools/web_search.py:107`, `tools/memory.py:201`), 76 catalog keys are named after them, and `admin/templates.py:354-356,380` grants those names to roles.
- `access/roles.py:20` keeps `LEGACY = "owner"` with a migration path in `admin/roles.py`, `admin/people.py:22` keeps `LEGACY_GATEWAY = "legacy"`, and `memory/ledger.py:104` and `stores/history.py:746` keep "shutdown compatibility hooks".
- Extensibility is closed by name tables, not by metadata: `guard/policy.py:18` decides approval class from `AUTHORIZATION_TOOLS = {"shell","sandbox","settings","send","send_to"}`, `:108-111` from `DESTRUCTIVE_ACTIONS`, and `:289-356` from per-name branches; `tools/__init__.py:23` closes `FAMILIES` to six names and `plugins.py:508-511` validates a plugin's tool against that set, so a third-party tool cannot exist at all; `tools/__init__.py:349-367` reads `args["path"]` and special-cases `sandbox` by name.

Two consequences follow. First, **safety is not portable**: the crisis net covers one language family, the register gate rests on a guessed gender, and the approval class cannot describe a tool it has never heard of. Second, **the core is not extensible**: every new country, transport, plugin, or tool edits the mechanism, which is exactly the churn ADR-0031's growth budget was meant to prevent.

## 2. Decision drivers

1. **Neutrality must be executable, not remembered.** A review checklist already failed: every item above survived multiple review passes because it "worked".
2. **Safety cannot depend on a country.** Crisis detection is a spine behavior and must cover every language the engine claims to speak.
3. **Safety cannot depend on a name table.** An approval class decided by tool name means a plugin tool is either uncategorizable or silently misclassified.
4. **Install is the first impression.** `monoagens init` must produce a host that mentions no vendor, country, transport, or language.
5. **Extensions must not edit core.** A new plugin, gateway, tool, or language adds data and manifests; it never touches `monoagens/engine/**` or `monoagens/admin/templates.py`.
6. **Refusal over silent fallback.** A missing pack, timezone, locale, or plugin is an explicit error naming what is missing — never an Indonesian, English, or Jakarta default chosen on the operator's behalf.
7. **One gate, mechanical.** The rule lives in a script the suite runs, with a listed inventory, not in prose.

## 3. Options considered

**A. Status quo plus review discipline.** Rejected: §1 is the evidence that review alone does not hold the line.

**B. Configuration overrides.** Add `soul.language` and `engine.timezone` settings and document them. Rejected as insufficient: an override hides the default instead of removing it, and it does nothing for the word tables, the vendor names, the name tables, or the crisis net.

**C. Packs, manifests, tool metadata, and one mechanical gate (chosen).** Every cultural, geographic, transport, and vendor fact becomes data with an owner (`lang/*.yaml`, `library/<kind>/<lang>.json`, `plugin.yaml`, `config.yaml`), every handler declares its own metadata, and a gate proves it.

**D. Move i18n and safety out into a plugin.** Rejected: `i18n.py` is one of the fifteen top-level engine modules (ADR-0031), and a soul engine must speak and detect crisis before any plugin is installed. Safety is a spine behavior (ADR-0000, ADR-0007).

## 4. Decision

### 4.1 The neutral-core contract

The engine must boot, pass its suite, and serve `/status` under this configuration: `plugins: {}`, no **optional** gateway registered, no library pack installed, `language: en`, and an empty database. The body the engine ships for its own operator console (`monoagens/gateways/cli`) is not an optional gateway; it is the default surface and counts as part of the neutral boot. Any data it needs and cannot find is an explicit refusal that names the missing thing. This is the operational definition of "neutral"; anything weaker is a slogan.

### 4.2 Culture is data

Crisis and sensitive patterns, profanity severity, affirmation words, level words, laughter, particles, and the style seed live in per-language packs next to the existing ones:

- `monoagens/library/safety/<lang>.json` — `crisis`, `sensitive` phrase lists.
- `monoagens/library/severity/<lang>.json` — word → `{kind, level}`.
- `monoagens/library/lexicon/<lang>.json` — gains `affirmations`, `level_words`, `laughter`, `particles`.
- `monoagens/library/style/<lang>.json` — the style baseline (`seed_median`, `seed_p90`, `seed_lower`, `seed_emoji`, `particles`, `emojis`).

`monoagens/engine/**` carries no non-English literal and no language-specific word list. The only exception is the English safety net, which the gate records explicitly. A language without a style pack gets no style baseline, not an Indonesian one.

### 4.3 Language is a required value

No `language: str = "id"` default, no `or "id"` fallback, and no `normalize_language(..., "id")` argument survives in `monoagens/engine/**`; `soul.language` is written by `monoagens init`, every call site passes it explicitly, and catalog lookup falls back to `en` only. `"auto"` resolves to the operator's locale, never to a fixed country. `soul.language_line` stops branching in code: the instruction is a catalog key, so a French soul is told to reply in French or refused, never in Indonesian.

### 4.4 Geography is a required value

An automation manifest must state `event.tz`; a missing `tz` is a `ManifestError` at validation time, and the scheduler reads the stored value instead of re-defaulting it. The image carries no `TZ`. `currents` is deleted in full — `jobs/currents.py`, `stores/currents.py`, `/konteks` with its flag description, its prompt block, its state key — **and** the topic list must leave `turn/prompts.py:178-191`, which currently repeats it as prose. Fresh facts come from the `web_search` tool; durable knowledge comes from facts packs grown by `scripts/grow.py`; a live-news capability is a plugin. Search is asked for a region explicitly or none at all; a vendor default such as `us-en` is not a default.

### 4.5 Transport lives in the seam

`dm_thread`, the `Chat` protocol, `digest`, group summaries, sticker and reaction semantics, quote rendering, media placeholders, message ids, delivery state, and "the model lives in chat" leave the engine's own vocabulary. Where the engine needs "the private thread with this person", the body answers through the inbound seam (ADR-0018, ADR-0032).

Transport facts reach the prompt from the body, never from a core branch. `CoreDefaults` gains `prompt_section() -> str`, returning the body's own text: how it addresses people, what a mention is here, quoting, view-once or undecryptable markers, stickers, and which targets its tools can reach. The engine assembles `neutral base + body section`, so the CLI and WhatsApp describe themselves, and a body with no rooms — a robot with one thread per sensor, an email body — returns nothing and gets a neutral prompt. The section is static per boot, English, capped at 1200 characters, may not interpolate user data, secrets, or runtime values, and joins the system prompt, so `prompt_prefix_hash` (ADR-0025) covers it and the prefix cache stays correct. Quote rendering is core formatting from body-supplied quote data (sender, time, text); the body decides whether it supplies a quote at all, and its dialect lives in its section. The private thread a person holds is a body fact too: `PrivateThread` answers it, the engine never reads a thread kind or a transport address.

### 4.6 Vendor names are absent from core, spine excepted

A plugin, gateway, or vendor identifier may appear only inside its own tree and its manifest, except for the spine named in §4.12. In particular:

- `admin/templates.py` writes `plugins: {}` and grants role templates core tools only.
- `.env` is generated from the core names, the chosen provider's key, and the union of `secrets` declared by the plugin manifests actually installed.
- Plugin tables live with the plugin; `schema.py` does not define `google_identity` or `google_device`, and no core module reads them.
- `memory/episodes.py` derives a target from the tool's own declaration, not from a name table.

### 4.7 Discovery over enumeration

Metadata lives with the thing it describes: a tool declares `target` (`file`, `room`, `document`, `process`, or `""`) and keeps its `family`; a plugin declares `provides` and `secrets`; a pack declares `language`; a manifest declares `tz`; a handler declares its approval class (§4.11); a body declares its `prompt_section()` (§4.5), empty by default. No central name → kind table exists in core.

### 4.8 Delete specialization; do not deprecate

Because nothing is released, specialization is removed rather than shimmed: `currents` (module pair, command, prompt prose, state key), `LEGACY`/`LEGACY_GATEWAY`, the shutdown compatibility hooks, and the `memori` verb DSL.

Gender splits three ways. The soul's own `gender` (`soul.py:62`, read by the decision prompt) stays: it is identity data written by `soul.yaml` and `monoagens init`. Everything that guesses or gates a **person's** gender goes: `access/target.py` in full, `identity.display_target` and its call site, the `/gender` command and its catalog keys, the `gender_gate`/`gender_confidence`/`gender_avatar` keys, the `people.gender*` columns with `affect.gender`/`set_gender`, and the gateway `avatar` signal — `gateways/whatsapp/profile.py` exists only for that guess.

`gender_gate` is an unlock, not a net: `effective_limits` gives a target it believes male the configured `toxicity`/`adult_memes` while everyone else is clamped to `(1, 0)`, and `character/patterns.py:28,167-171` gates pattern selection on the same `TARGETS` axis. Its removal therefore ships its replacement in the same commit, never after: the register comes from the room's configuration, the "male target" precondition on the highest `adult_memes` level is deleted, `TARGETS`/`_eligible` lose the axis, and the prompt rule becomes target-independent ("no sexual or degrading material about a real person; an unclear target or room stays gentle"). The hard floor in `character.py` (minors, doxxing, credible threats, hate) is unchanged and remains the actual safety line.

### 4.9 Command names are English, with no localized aliases

`cari`, `kirim`, and `memori` become `search`, `send`, and `memory`; `konteks` disappears with `currents`; the six Indonesian command aliases, two tool aliases, and the eight localized verb branches in `commands/memori.py` are removed, and the catalog keys are renamed with them, including the leaked verbs in `lang/en.yaml:121`. A gateway or plugin may expose its own namespaced commands; core does not keep a localization layer for its own surface.

### 4.10 The gate is mechanical

Section 6 lists the checks. They run in the suite, not in review.

### 4.11 A handler declares its own metadata

The registry carries facts, not names:

```text
TOOL = Handler(
    name="read",
    family="read",          # declared, validated by shape, never checked against a closed set
    target="file",          # file | room | document | process | ""
    approval_class="none",  # none | consent | authorization
    destructive=(),         # the actions that destroy state, e.g. ("forget",)
    path_arg="path",        # the argument that names a filesystem target
    scan=(),                # the arguments a credential and injection scan must read
)
```

`FAMILIES`, `AUTHORIZATION_TOOLS`, `DESTRUCTIVE_ACTIONS`, and the per-name branches in `guard/policy.py` and `tools/__init__.py` are deleted. A plugin's tool passes validation by declaring the same fields; core never learns its name.

### 4.12 The spine is the only vendor allowlist

The spine named by ADR-0001 and ADR-0031 is fixed and may be named in core: PostgreSQL with pgvector, Mem0, the shared MiniLM embedder, and the OpenAI-compatible wire client. Everything else is optional and stays out of `monoagens/engine/**`: search engines and their regions, language-model vendors, cloud APIs, and plugin tables. The allowlist is a constant with one entry per spine component and a comment naming its ADR, so adding one is a reviewed change, never an accidental import.

### 4.13 One clock, one locale

`locale` and `timezone` are required soul values written by `monoagens init`, not code defaults. Core reads the clock through one helper, `now(tz)`, and stores day buckets in that zone; `date.today()`, `time.localtime()`, and a mixed `gmtime`/`localtime` pair are forbidden in `monoagens/engine/**`. The host `TZ` and `LANG` are not forwarded into sandbox or shell children. Weekday parsing accepts ISO numbers or case-insensitive names, and week start is stated, not assumed.

## 5. Configuration

```yaml
soul:
  language: en            # written by `monoagens init`; required, no default
  locale: en-US           # required; drives formatting and search region
  timezone: Europe/Berlin # required; the host's home zone for prompts and day buckets
engine:
  safety:
    allow_degraded: false # true runs the English net when a language pack is absent
  web_search:
    region: ""            # empty means no region bias; never a vendor default
automation:
  event:
    tz: Europe/Berlin     # required per manifest; no engine default
plugins: {}               # no plugin key in a shipped template
```

Pack layout:

```text
monoagens/library/safety/<lang>.json     {"version":1,"language":"en","crisis":[…],"sensitive":[…]}
monoagens/library/severity/<lang>.json   {"version":1,"language":"en","words":{"word":["kind",2]}}
monoagens/library/style/<lang>.json      {"version":1,"language":"en","seed_median":34,"particles":[…],"emojis":[…]}
monoagens/library/lexicon/<lang>.json    gains "affirmations", "level_words", "laughter", "particles"
```

## 6. Metrics and gates

Counts to drive to zero, measured 2026-11-04:

| Specialization | Count | Where |
| --- | --- | --- |
| language defaults | 25 + 8 fallbacks | 24 signatures, `soul`/`i18n`/`llm` module defaults, 8 `or "id"` sites |
| hardcoded word tables | 8 | `privacy` ×2, `character.SEVERITY`, `target`, `style` ×4, `patterns.SERIOUS_REGISTERS`, `self_knowledge` topics |
| locale and country defaults | 9 | `Dockerfile` `TZ`, `locale` ×3, `research_block`, ddgs region, `Asia/Jakarta` ×3 |
| vendor references | 13 + 2 tables | templates ×3, episodes, `schema.google_*`, `people.whatsapp`, `WEB_BACKENDS`, ddgs |
| transport references | 72 lines | 52 in `turn/prompts.py`, 20 in `turn/decision.py`, plus `relay`, `dm_thread` ×3, `Chat`, `digest`, placeholders, quote rendering |
| name tables that block extensions | 6 | `FAMILIES`, `AUTHORIZATION_TOOLS`, `DESTRUCTIVE_ACTIONS`, policy branches, `PROVIDES_KEYS`, `settings` document kinds |
| clock sites | 6 | `date.today()` ×3, `localtime` ×3 against one `gmtime` |
| non-English surface names | 20 | 4 commands, 6 command aliases, 2 tool aliases, 8 verb branches |
| legacy markers | 6 | `LEGACY`, `LEGACY_GATEWAY`, `people.whatsapp`, compatibility hooks ×2 |

The gate, `scripts/neutral.py --check` (new module), fails when:

1. `monoagens/engine/**`, `monoagens/admin/**`, or the shipped templates contain a **non-ASCII letter** (Unicode category `L*`) outside the explicit English safety allowlist. Typographic punctuation and arrows (`§ — … → • ≥ ↳`) are structural, not culture; emoji and country or state symbols are refused in any user-visible string.
2. The banned vocabulary list (Indonesian function words plus the words moved to packs) appears in that same scope.
3. A vendor, gateway, or plugin identifier appears there outside the spine allowlist (§4.12), `monoagens/providers/`, `monoagens/gateways/`, and `plugins/`.
4. A `language`/`locale`/`tz` default or fallback appears there: no `language="id"`, no `default="id"`, no `"id-ID"`, no `"Asia/`, no `TZ=` in the `Dockerfile`.
5. A catalog language has neither a safety pack nor a refusal record.
6. A `Handler` lacks `target` or `approval_class`, or a closed name table (`FAMILIES`, `AUTHORIZATION_TOOLS`, `DESTRUCTIVE_ACTIONS`, `PROVIDES_KEYS`) is found, or a name branch decides an approval class.
7. A shipped template has a non-empty `plugins:` map, names a plugin tool in a role seed, or writes plugin credentials into `.env`.
8. `date.today()`, `time.localtime()`, or `time.gmtime()` appears in `monoagens/engine/**` instead of the `now(tz)` helper; or a host `TZ`/`LANG` is forwarded to a child process.
9. The neutral smoke fails: `pytest -q` and `monoagens selftest` run with `plugins: {}`, `language: en`, no optional gateway, and an empty database.
10. A body's `prompt_section()` is longer than 1200 characters, changes between two calls in one boot, or a body that returns an empty section still yields transport vocabulary in the assembled prompt.
11. The prompt contract suite fails: `tests/data/prompt_cases.yaml` declares, per case, the required fragments, the forbidden tokens, and the expected `prompt_prefix_hash`, and `pytest -q` runs it against the real prompt builders. It pins properties, never wording, so a reworded sentence does not force a fixture edit but a leaked transport, culture, or language token does.

It runs beside the existing gates: `pytest -q`, `ruff check monoagens scripts tests plugins migrations`, `python -P scripts/types.py --check`, `python scripts/selfcheck.py --check`, `python scripts/adr.py --check`.

## 7. Acceptance criteria

- The gate in §6 passes with zero hits, in the scope it names (`engine`, `admin`, `__main__.py`, templates, `Dockerfile`).
- `language`, `locale`, and `timezone` have no default anywhere in `monoagens/engine/**`; `monoagens init` writes all three, and the seed `soul.yaml` carries none of them until it does.
- A French soul is told to reply in French through the catalog, or refused; `language_line` carries no language branch in code.
- A manifest without `tz` fails with `ManifestError`; no `Asia/` literal and no `TZ=` exist outside a deployment manifest.
- `currents` is gone: no module, no command, no state key, no prose topic list in `turn/prompts.py`.
- A body with an empty `prompt_section()` (the dispatch-only robot) produces a prompt with no transport vocabulary; the CLI and WhatsApp sections are their own text, each under 1200 characters, identical across two calls in one boot.
- The prefix hash is recorded per body, so a change to a body's section is a deliberate diff, not a cache surprise.
- The prompt contract suite carries one case per body (`cli`, a group thread, a dispatch-only robot) and per behavior (crisis, approval class, register, language refusal) and runs inside `pytest -q`.
- `stores/style.py` carries no particle, laughter, emoji, or seed constant; a language without a style pack gets no style block.
- One day boundary: the same `now(tz)` helper feeds prompts, automation budgets, ambient stamps, and memory buckets.
- `days: "Mon"` and `days: ["mon"]` both parse; the week start is documented.
- A test plugin that declares a new family and a new `target` boots and its tool is classified without editing core; no `FAMILIES` or `AUTHORIZATION_TOOLS` set exists.
- `schema.py` defines no plugin table; a plugin creates its own.
- `plugins: {}` and a `.env` without plugin secrets; installing a plugin adds its declared `secrets` without editing `templates.py`.
- Deleting every plugin and optional gateway still boots the engine; `monoagens selftest` reports `safety: ok` for a language with a pack and `safety: degraded` only when `allow_degraded: true`.
- Every command, tool, and verb name is English; no localized alias or verb branch resolves through core.

## 8. Risks and mitigations

- **Safety regression while the crisis net moves to packs.** Mitigation: the English net stays compiled in as the last line, a language without a pack is refused unless `allow_degraded: true`, and `selftest` asserts one crisis phrase per shipped pack.
- **Approval routing breaks when the name tables go.** Mitigation: `approval_class` becomes a declared field with a default of `authorization` for unknown handlers, so a forgotten declaration fails closed rather than open; the suite asserts every shipped handler declares one.
- **Pack authoring cost.** Mitigation: `scripts/grow.py` already grows packs from a corpus, `--check` validates a pack's shape, and the first release ships `en` and `id` only.
- **Rename churn.** Mitigation: one commit per phase; tests, catalogs, role templates, and docstrings move in the same commit; nothing is released, so no shim is written.
- **The gate becomes the enemy of prose.** Mitigation: it checks non-ASCII **letters**, not punctuation, so `§`, `—`, `→`, and `•` in docstrings stay legal while emoji and flags in user-visible strings do not.
- **Spine allowlist drift.** Mitigation: the allowlist is one constant with an ADR citation per entry; the gate names the file and line of any addition.
- **Clock migration.** Mitigation: one helper, one test that sets the host `TZ` to two different zones and expects identical day buckets.
- **A body's prompt section becomes an injection surface.** Mitigation: it is operator-shipped code at plugin trust level, static per boot, capped at 1200 characters, and forbidden from interpolating user data, secrets, or runtime values; the suite asserts the cap and the static property, and the section never carries content from a message.
- **A dynamic section breaks the prompt cache.** Mitigation: `prompt_section()` takes no argument, the test calls it twice in one boot and expects identical text, and per-turn facts stay in the volatile half of the prompt.
- **Losing live context with `currents`.** Mitigation: `web_search` covers ad hoc lookups, facts packs cover durable knowledge, and a plugin can reintroduce a news feed without core changes.

## 9. Interaction with other ADRs

- **ADR-0000** already declares language data as catalogs and the seam as embodiment-neutral; this ADR makes both claims executable.
- **ADR-0001** fixes the spine named in §4.12, including Mem0 and the shared embedder.
- **ADR-0006** keeps the privacy ceiling; only the affirmative vocabulary moves into a pack.
- **ADR-0007** owns the approval classes; §4.11 changes how a class is found, not what the classes mean.
- **ADR-0009** owns the register and the ADR gate; the new gate follows its pattern.
- **ADR-0014** gains a required `event.tz`; the automation state machine is unchanged.
- **ADR-0018** keeps the contract, while `Chat`, `dm_thread`, and the prompt's chat vocabulary move out of core into bodies.
- **ADR-0019** reports safety mode in `/status` and in `selftest`.
- **ADR-0022** keeps the protected role; only the legacy name path goes.
- **ADR-0025** is unaffected: a required language is already part of the stable prefix.
- **ADR-0027** is enforced here: clean by default means a template with no vendor block.
- **ADR-0028** gains the rule that template `.env` is derived from a manifest's `secrets`, and that a plugin owns its schema.
- **ADR-0031** provides the gates this ADR extends and the module budget it must respect.
- **ADR-0032** provides the door; this ADR removes the transport vocabulary that reached around it.

## 10. Code references

- `monoagens/engine/guard/patterns.py:15-19` — the hardcoded crisis and sensitive patterns.
- `monoagens/engine/character/character.py:31,188-211,227-235` — `pantun`, `SEVERITY`, and `effective_limits`.
- `monoagens/engine/access/target.py:21-26,39-40,50,111-155` — the second profanity list, the markers, the `TARGETS` axis, and the inference.
- `monoagens/engine/access/privacy.py:27,32-46,93` — `YES`, `LEVEL_WORDS`, and the `or "id"` fallback.
- `monoagens/engine/character/patterns.py:27,28,167-171` — `SERIOUS_REGISTERS`, `TARGETS`, `_eligible`.
- `monoagens/engine/stores/style.py:22,27,29-33,34-60,343-357` — placeholders, laughter, particles, emojis, seed, and the prompt baseline.
- `monoagens/engine/config.py:215-218,250-259,388` — search config, forwarded `TZ`/`LANG`, Indonesian topic ids.
- `monoagens/engine/self_knowledge.py:70-77,313-325,462-465` — topic keys, prompt headings, README fallback.
- `monoagens/engine/commands/memori.py:58,75,90,126` — the localized verb DSL; `monoagens/lang/en.yaml:121` — the same verbs in the English catalog.
- `monoagens/engine/soul.py:22-44,60,65,82,106-111,251,256` — the seed document, `language`, `locale`, `language_line`, and both fallbacks.
- `monoagens/engine/i18n.py:50,59` — `normalize_language` default and the `auto` alias.
- `monoagens/engine/jobs/schedule.py:16,29-36,76-84,136` — `DEFAULT_TZ`, `zone`, weekday parsing.
- `monoagens/engine/jobs/automation.py:155,1133,1539` — the two `tz` re-defaults and the day key.
- `monoagens/engine/jobs/currents.py:1,18-31`, `monoagens/engine/stores/currents.py:1`, `monoagens/engine/commands/konteks.py:1,11,72` — the country context and its flag.
- `monoagens/engine/commands/cari.py:46-48`, `kirim.py:28-30`, `memori.py:159-161` — Indonesian names, aliases, emoji descriptions.
- `monoagens/engine/tools/web_search.py:16,64,107` — `WEB_BACKENDS`, the ddgs region default, the tool alias.
- `monoagens/engine/tools/sandbox.py:17,69-71` — forwarded `TZ`/`LANG`; `monoagens/engine/tools/settings.py:33-37` — closed document kinds.
- `monoagens/engine/guard/policy.py:18,108-111,289-356` — the approval name tables and branches.
- `monoagens/engine/tools/__init__.py:23,349-367` — `FAMILIES` and the per-name door logic.
- `monoagens/engine/plugins.py:105-110,508-511` — `PROVIDES_KEYS` and plugin tools validated against core families.
- `monoagens/engine/schema.py:927-946`, `monoagens/engine/access/identity.py:22-23,345`, `monoagens/admin/people.py:14,22,57-59,90` — the plugin tables, their core readers, and the WhatsApp/legacy fallbacks.
- `monoagens/admin/templates.py:70,335-345,354-356,380,389-397` — plugin default, role grants, Indonesian command names, `.env` tail.
- `monoagens/engine/memory/episodes.py:149-161` — `TARGET_TYPES` naming a plugin and gateway tools.
- `monoagens/engine/turn/prompts.py:61-84,121,138-159,178-191,239,247,309,319,329-332,347-350,360-372,379-384,408-412,427-432,441-449,498` — the chat vocabulary and the prose topic list.
- `monoagens/engine/turn/decision.py:13-51,343-345` — mentions, DMs, `ACTS`, `bubbles`, prompt labels.
- `monoagens/engine/inbound.py:168-176` — `CoreDefaults`, where `prompt_section()` lands with an empty default.
- `monoagens/engine/llm.py:138-145` — `prompt_prefix_hash`, which will cover the body section automatically.
- `monoagens/gateways/cli/gateway.py:91-106`, `monoagens/gateways/whatsapp/gateway.py` — bodies that will carry their own section instead of teaching core their vocabulary.
- `monoagens/engine/turn/context.py:317,506-513` — language default and quote rendering.
- `monoagens/engine/access/relay.py:1-10,218`, `monoagens/engine/stores/history.py:683,746` — the DM-bound relay, `dm_thread`, the compatibility hook.
- `monoagens/engine/llm.py:1,10,33,51,177,648-655` — the wire client, the digest prompt and method, the language default.
- `monoagens/engine/memory/mem0.py:19,186-204,213,223`, `monoagens/engine/embed.py:15` — the spine components §4.12 allowlists.
- `monoagens/engine/memory/context.py:82`, `monoagens/engine/memory/ledger.py:25,104`, `monoagens/engine/stores/affect.py:835,917`, `monoagens/engine/memory/store.py:2216` — the mixed day boundaries.
- `monoagens/engine/access/roles.py:20`, `monoagens/engine/memory/ledger.py:104` — the legacy markers.
- `Dockerfile:5` — `TZ=Asia/Jakarta`.
- `monoagens/library/patterns/jawatimur.json`, `monoagens/library/facts/indonesia.json` — the only shipped packs, both `language: id`.
- `tests/support.py:66-120` — `default_soul`, `role`, and `build_config`, the helpers the prompt contract suite reuses instead of rebuilding a world.
- Proposed, not code yet: `monoagens/library/{safety,severity,style}/<lang>.json`, the `affirmations`/`level_words`/`laughter`/`particles` lexicon keys, `Handler.target`/`approval_class`/`destructive`/`path_arg`/`scan`, `now(tz)`, `soul.timezone`, `scripts/neutral.py`, and `tests/data/prompt_cases.yaml`.

---

*This ADR lives at `docs/v0.1.0/adr/0033-neutral-core.md`.*
