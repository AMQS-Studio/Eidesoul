# ADR-0041: Voice Layer Configuration — One Section, Off by Default, and a Fully Commented Config

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0027 (clean by default), ADR-0031 (engine structure), ADR-0033 (neutral core), ADR-0039 (setup terminal), and ADR-0040 (naming); replaces the four top-level voice sections with one `engine.voice` section and makes the generated `config.yaml` self-documenting.
- **Supersedes:** —
- **Related:** ADR-0001 (memory source of truth), ADR-0027 (distribution), ADR-0031 (module structure), ADR-0033 (neutral core), ADR-0039 (setup terminal), ADR-0040 (naming)
- **Implementation:** draft — the decision is recorded; the schema, the loader, and the commented template are not implemented in this ADR.
- **Location note:** the voice layer binds the engine runtime, so this ADR sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0041-voice-layer-off-by-default.md`.
- **Location update:** ADR-0045 moves the section from `engine.voice` to `soul.voice`, removes the old `soul.style` pack block, and removes pack selection from the character part. Read every `engine.voice` path below as `soul.voice`; the master/off-by-default semantics still apply. Option E is no longer rejected: voice tuning is per-soul data.

---

## 1. Context

ADR-0027 states the clean-by-default rule: a gateway, a tool, and a plugin are off until the
operator enables them; only the spine is always on. The voice layer is not the spine: emotion,
humanize, character, and rhythm are presentation features, not the memory store, the guard, or the
lease.

The shipped surface contradicts that rule and fragments one concept into four top-level switches:

| Surface | Current behaviour | Evidence |
| --- | --- | --- |
| `init` template | writes `affect.enabled: true`, `affect.vibe.enabled: true`, `humanize.enabled: true`, `style.enabled: true`, `character.enabled: false` | `admin/templates.py` |
| Loader defaults | `affect`, `affect.vibe`, and `style` default `True`; `humanize` and `character` default `False` | `engine/config.py` |
| `init` value | `build(voice="on")`, so the template's true values survive | `admin/init.py` |
| Wizard | asks *"keep emotion, humanize, and character on?"* with default `on` | `admin/wizard.py` |
| CLI | `--voice-layer` documents *on (default)* | `__main__.py` |
| README | *"The voice layer — emotion, humanize, character — is on by default"* | `README.md` |

Two defects follow. First, the operator sees four `enabled` flags for one layer, plus a third list in
`admin/init.py` (`VOICE_PARTS`), and the word `style` means two different things: `engine.style` is
the statistical rhythm store, while `soul.style` is the character pack selection. Second, the
generated `config.yaml` is a bare `yaml.safe_dump` of a dictionary (`templates.dump`): no comments, no
explanation, and no way for a fresh operator to know what a key does or which default it carries.

The owner direction is explicit: one section, one master switch, a per-subsection `enabled` as the
only selector, no separate parts list, every voice section off by default, and a generated
`config.yaml` that explains itself.

## 2. Decision drivers

| Driver | Consequence |
| --- | --- |
| One section | `engine.voice` owns every voice knob; the four old top-level sections disappear |
| One master | `engine.voice.enabled` is the only switch `init` asks about; default `false` |
| One per-part selector | Each sub-section keeps its own `enabled`; no `parts` list duplicates it |
| Clean by default | The effective default is off; sub-section defaults are inert until the master is on |
| Self-documenting config | The generated `config.yaml` carries a comment for every section and every `enabled` key |
| Mechanisms stay separate | The four implementations keep their own module and tests; only the namespace is unified |
| Clean cutover | Old top-level keys are removed; no alias, no shim, no dual read path |
| Spine unchanged | Memory, guard, lease, worker, and the gateway contract keep their defaults |

## 3. Options considered

| Option | Summary | Value |
| --- | --- | --- |
| **A. Keep four top-level sections** | `affect`, `humanize`, `character`, `style` each with `enabled` | Rejected: four switches for one layer, and `style` collides with `soul.style` |
| **B. `engine.voice` with a `parts` list** | one master plus a list of active parts | Rejected: `parts` duplicates the per-subsection `enabled`; two selectors drift |
| **C. `engine.voice` with master and per-subsection `enabled`** | one section, two levels | **Chosen** |
| **D. One flat `engine.voice` bag** | every key at one level | Rejected: roughly fifteen keys of different shapes; validation, tests, and docs lose the boundary |
| **E. Put the knobs in `soul.yaml`** | treat voice tuning as persona data | Rejected: soul is data, engine is mechanism; `soul.style` stays the pack selection |
| **F. Leave the generated config uncommented** | keep `yaml.safe_dump` | Rejected: opaque defaults; the operator cannot tell what a key does |
| **G. Commented `config.yaml`** | comments generated from one source | **Chosen** |

## 4. Decision

### 4.1 One section, two levels

```text
engine:
  voice:
    enabled: false
    emotion:
      enabled: true
    rhythm:
      enabled: true
    character:
      enabled: true
    humanize:
      enabled: true
```

The master is `engine.voice.enabled`, default `false`. Every sub-section keeps its own `enabled`,
default `true`, but the value is inert while the master is `false`. The old top-level sections
(`engine.affect`, `engine.humanize`, `engine.character`, `engine.style`) are removed.

`engine.style` is renamed `engine.voice.rhythm` so the word `style` no longer names two different
things. `soul.style` is unchanged: it remains the pack selection (`patterns`, `facts`, `registers`,
`max_patterns`, `max_facts`) that `eidesoul library resolve` derives from the soul text.

### 4.2 Effective state

| `engine.voice.enabled` | Sub-section `enabled` | Effective |
| --- | --- | --- |
| `false` | any | off; the sub-section value is ignored |
| `true` | `true` | on |
| `true` | `false` | off |

An explicit value in an existing `config.yaml` is never overridden by `init`. The master and the
sub-sections are the only selectors; there is no `parts` key.

### 4.3 What each part gates

| Part | Gates | When off |
| --- | --- | --- |
| `emotion` | `AffectStore` (PAD, drives, relationships, `vibe`) and the emotion prompt block | the store is not built; `/mood` and `/calmdown` answer unavailable; new memories carry no valence metadata |
| `rhythm` | `StyleStore` (statistical chat rhythm) and the style/person-style prompt blocks | the store is not built; the prompt receives no rhythm block |
| `character` | the `polish`/`regulate` pass **and** the `select_patterns`/`select_facts` selection from `soul.style` | the draft is not rewritten; no pattern or fact pack is selected |
| `humanize` | bubble planning and delivery pacing (`max_bubbles`, `cpms`, delays, stickers) | one message, no typing or between-bubble delay; `max_bubbles` is ignored |

### 4.4 `init`, the wizard, and the flag

`init` asks one question: turn the voice layer on or off. The default is `off`. `--voice-layer`
accepts `off` (default) and `on`; it toggles the master only. The wizard does not ask for a list of
parts; a single part is disabled in `config.yaml` by setting its `enabled` to `false`. The
`VOICE_PARTS`/`voice_off()` list in `admin/init.py` is removed.

## 5. Generated config

The generated `config.yaml` is self-documenting at every depth. The file is the host block
(`database`, `workspace`, `roles_file`, `soul_file`, `sync_documents`, `sandbox`, `web_search`, `mcp`,
`plugin`, `plugins`), the `engine` block, and the `gateways` block; all three are covered.

- Every top-level section has a comment that states its purpose.
- Every key at every depth has a comment: its purpose, its default, and the effect of `true` and
  `false` where the value is a switch.
- No key is bare; no section is rendered as an empty mapping without a comment that explains the
  shape.
- Comments are English, like the rest of the operator surface, and are generated from one source
  (`admin/templates.py`), so a default cannot drift between the comment and the dataclass tree.
- Comments are ignored by the loader and by `eidesoul config import`; the file stays valid YAML.

The comment map is data keyed by the dotted path (`database.pool_size`,
`engine.llm.retry.base_seconds`, `sandbox.image`, `gateways.cli.enabled`). A test walks the generated
YAML and asserts every leaf key has a comment.

Example comment shape:

| Comment line | Meaning |
| --- | --- |
| `# database.pool_size — Postgres connection pool size. Default: 5.` | a host key with its default |
| `# sandbox.image — container image for the sandbox. Default: ...` | a host key with its default |
| `# engine.llm.timeout_seconds — per-call model timeout. Default: 120.` | an engine key with its default |
| `# voice.enabled — master switch for the voice layer. Default: false.` | a switch and its default |
| `#   false: emotion, rhythm, character, and humanize are all off.` | the off effect |
| `#   true:  each sub-section decides; a sub-section defaults to true.` | the on effect |

## 6. Configuration

| Key | Default | Meaning |
| --- | --- | --- |
| `engine.voice.enabled` | `false` | master switch; the only value `init` writes |
| `engine.voice.emotion.enabled` | `true` | PAD, drives, relationships, vibe; inert while the master is off |
| `engine.voice.rhythm.enabled` | `true` | statistical chat rhythm; inert while the master is off |
| `engine.voice.character.enabled` | `true` | polish/regulate and pack selection; inert while the master is off |
| `engine.voice.humanize.enabled` | `true` | bubble planning and pacing; inert while the master is off |

Flag and wizard semantics:

| Input | Result |
| --- | --- |
| `--voice-layer off` or absent | `engine.voice.enabled: false` |
| `--voice-layer on` | `engine.voice.enabled: true`; sub-sections keep their values |
| wizard blank | default `off` |
| wizard `on` | master `true` |

No `parts` key is introduced. The old `engine.affect`, `engine.humanize`, `engine.character`, and
`engine.style` keys are no longer read. `soul.style` is unchanged.

## 7. Metrics and gates

| Metric | Target |
| --- | ---: |
| Effective voice parts on by default | 0 |
| Top-level voice sections | 1 (`engine.voice`); old four: 0 |
| `parts` keys | 0 |
| Keys at every depth in the generated `config.yaml` without a comment | 0 |
| Bare keys in the generated `config.yaml` | 0 |
| Sections rendered as an empty mapping without an explanatory comment | 0 |
| `config import` and loader parse the commented file | pass |
| `pytest -q` | pass |
| `ruff check` | pass |
| `python -P scripts/types.py --check` | pass |
| `python -P scripts/adr.py --check` | pass |
| `python -P scripts/adr.py --translate-check` | pass |
| `python scripts/selfcheck.py --check` | pass |

## 8. Acceptance criteria

- [ ] `engine.voice` is the only voice section; the four old top-level sections are gone.
- [ ] `engine.voice.enabled` defaults to `false`; a sub-section is inert while the master is off.
- [ ] No `parts` key exists in the config, the wizard, or the loader.
- [ ] `engine.style` is renamed `engine.voice.rhythm`; `soul.style` is unchanged.
- [ ] `--voice-layer` accepts only `off` and `on`; the wizard asks the master only.
- [ ] `voice.character.enabled: false` disables both the polish pass and the pattern/fact selection.
- [ ] `voice.humanize.enabled: false` produces one message with no typing or between-bubble delay.
- [ ] The generated `config.yaml` has a comment on every key at every depth — host, engine, and gateways.
- [ ] No key is bare; no section is an empty mapping without an explanatory comment.
- [ ] `eidesoul config import` and the loader parse the commented file.

## 9. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A comment drifts from its default | Comments are data; a coverage test asserts presence, and values stay derived from the dataclasses |
| Old `config.yaml` files stop working | Pre-release; `config import` maps the old keys once; no runtime fallback |
| Master and sub-section confuse the operator | The effective-state table is the contract; tests cover master-off, master-on, and sub-off |
| `style` to `rhythm` leaves stale references | Clean cutover; the gate `grep` and the rename checklist cover the tree |
| `character` off still selects packs | `select_patterns`/`select_facts` are gated by `voice.character.enabled`; a test asserts no pack when off |
| `humanize` off still splits bubbles | The off path sets one message and ignores `max_bubbles`; a test asserts one send |
| Affect off loses valence metadata on new memories | Accepted; the memory path treats valence as absent. A separate decision would be needed to keep it |
| The comment map grows large | One map keyed by dotted path; a coverage test fails on any bare key |

## 10. Interaction with other ADRs

- **ADR-0027**: this ADR applies the clean-by-default rule to the voice layer; the spine exception is unchanged.
- **ADR-0001**: memory, Mem0, and the action ledger are unaffected; only the presentation stores change defaults.
- **ADR-0031**: the module boundaries and the 800-line budget do not move; the comment map stays in `admin/templates.py`.
- **ADR-0033**: no language, culture, or vendor literal enters the engine; the comments are operator surface, not chat text.
- **ADR-0039**: the voice step keeps its place in the rail; its default and wording change, and the location, credential, and review behaviour do not.
- **ADR-0040**: the `init` command name follows the naming cutover; this ADR uses the generic `init`.

## 11. Code references

- `eidesoul/admin/templates.py` — the commented `config.yaml` template and the comment map.
- `eidesoul/engine/config.py` — `VoiceConfig`, the four sub-configs, and the loader fallbacks.
- `eidesoul/engine/turn/loop.py` — the emotion, rhythm, character, and humanize call sites.
- `eidesoul/engine/character/character.py` — the `polish`/`regulate` gate and the pattern/fact selection.
- `eidesoul/engine/stores/affect.py` — the affect store, built only when `voice.emotion` is on.
- `eidesoul/engine/stores/style.py` — the rhythm store, built only when `voice.rhythm` is on.
- `eidesoul/engine/turn/reply.py` — `plan_chunks` and the humanize-off path.
- `eidesoul/engine/turn/delivery.py` — `typing_delay` and the between-bubble delay.
- `eidesoul/admin/init.py` — the master switch, `VOICE_PARTS`/`voice_off()` removal, and `build()`.
- `eidesoul/admin/wizard.py` — the master-only voice prompt and its default.
- `eidesoul/__main__.py` — the `--voice-layer` argument and its help text.
- `eidesoul/engine/agent.py` — the `/status` configuration report for the voice parts.
- `README.md`, `README.id.md` — the voice-layer paragraph.
- `tests/` — default-off, master-on, sub-off, commented-template, and one-message coverage.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0041-voice-layer-off-by-default.md`.*
