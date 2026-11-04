# ADR-0045: Character Without Local Packs — Soul, Exemplars, No Gender Gate, No Level Table

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0008 (rules), ADR-0025 (turn cost), ADR-0033 (neutral core), ADR-0034 (prompt parts), ADR-0038 (privacy), ADR-0041 (voice configuration), ADR-0043 (research contract), and ADR-0044 (provider wire adapters); removes the runtime character catalogue, the `soul.style` selector, the gender distinction, and the character level table, and moves voice tuning to `soul.voice`.
- **Supersedes:** ADR-0041 (configuration location and the `soul.style` premise only; its master/off-by-default semantics remain)
- **Related:** ADR-0008 (memory integrity), ADR-0025 (turn cost), ADR-0033 (neutral core), ADR-0034 (prompt parts), ADR-0038 (privacy), ADR-0041 (voice configuration), ADR-0043 (research contract), ADR-0044 (provider wire adapters)
- **Implementation:** draft — the decisions are recorded; the pack removal, the prompt change, the mode sampling, and the tests are not implemented in this ADR.
- **Location note:** the change binds the engine turn, so this ADR sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0045-character-without-local-packs.md`.

---

## 1. Context

The character layer currently mixes four different jobs:

| Job | Current surface | Problem |
| --- | --- | --- |
| Persona | `soul.yaml` `background` and `persona` | correct source, but only part of the character prompt |
| Canonical moves | `library/patterns/*.json` selected per turn | a local catalogue that nobody maintains at the current scale |
| Public facts | `library/facts/*.json` selected per turn | ADR-0043 removes it from the runtime |
| Register limits | `toxicity` 0-4 and `adult_memes` 0-3 | a numeric level table that turns a voice into policy |
| Gender targeting | pattern `target` and `soul.gender` | a distinction the owner does not want |
| Polish | one `LLM.complete` call on a robotic draft | useful, but coupled to the same level table |
| Level rewrite | `regulate()` after the draft | a second model call that can flatten the voice |

The owner direction is explicit:

- character comes from `soul.yaml` and from the model, not from a local pack;
- a few real examples in the soul outperform a large row catalogue;
- no gender distinction in the character path;
- no toxicity/adult level table and no level rewrite;
- daily chat may be more expressive; professional mode stays genuinely useful;
- `affect`, `humanize`, privacy levels, and the tool guard keep their own contracts.

This ADR is about the **character** level table only. It does not remove the privacy levels L0-L4 from
ADR-0038, and it does not remove the tool sandbox or approval policy.

## 2. Decision drivers

| Driver | Consequence |
| --- | --- |
| One character source | `soul.yaml` persona and examples are the only character data |
| No local catalogue | `library/patterns` and `library/facts` leave the runtime |
| No resolver | `soul.style`, `library resolve`, and the pack vectors are removed, not kept as dead config |
| No gender distinction | no gender field, no gender prompt line, no gender target |
| No level table | no toxicity/adult values, no `over_level()`, no `regulate()` |
| Daily is expressive | daily sampling is higher and persona-led |
| Professional is useful | professional mode is task-led, lower temperature, no polish |
| Configurable sampling | daily, professional, utility, and polish temperatures are operator values |
| Detector lexicon only | `library/lexicon` keeps deterministic word lists, not prompt examples |
| No new character dependency | no external character library is introduced |

## 3. Options considered

| Option | Summary | Value |
| --- | --- | --- |
| **A. Keep the local packs and levels** | continue selecting pattern and fact rows by register and level | Rejected: the owner will not maintain the catalogue; it constrains the voice and duplicates the model |
| **B. Replace packs with an external character library** | ingest a roleplay/persona dataset at runtime | Rejected: no Indonesian drop-in exists; dataset licensing, language, and format do not fit a runtime prompt |
| **C. Soul plus exemplars plus model** | persona, a few examples, memory, and the selected model | **Chosen** |
| **D. Keep gender targeting** | classify the target before choosing a move | Rejected: the owner does not want the distinction |
| **E. Keep levels but make them configurable** | expose the same 0-4 table | Rejected: the table is the problem, not only its values |
| **F. Remove levels and keep the rewrite** | keep `regulate()` without a table | Rejected: a rewrite with no rule still flattens the voice |
| **G. One global temperature** | keep `engine.llm.temperature` for every mode | Rejected: daily and professional need different sampling |
| **H. Prompt-only character** | no character call at all | Rejected: polish still removes assistant tells from a robotic draft |

## 4. Decision

### 4.1 One character source

`soul.yaml` is the only character source:

| Field | Role |
| --- | --- |
| `description` | one-line identity |
| `background` | stable setting and history |
| `persona` | voice, register, cultural anchors, reaction rules |
| `examples` | three to five short `user`/`soul` exchanges |
| `rules` | behavior rules from ADR-0008, not style |

`render_persona()` already emits `## Dialogue examples`. The examples are the few-shot anchor; they are
not a database, a pack, or a second memory. They must not contain a real third party's name, phone
number, URL, or raw transcript. A synthetic exchange that captures the rhythm is preferred.

The model carries stable cultural prior. Current, specific, or numeric claims still follow ADR-0043:
search when evidence is required, abstain when search fails.

### 4.2 Remove the local character catalogue

The runtime no longer loads `library/patterns` or `library/facts`.

Removed:

- `select_patterns()` and `select_facts()` from the turn loop;
- `character/patterns.py` and `character/knowledge.py`;
- the `patterns` and `facts` fields on `PromptContext`;
- the `## Room patterns` and `## Known facts` blocks;
- the package data entries for both trees.

The files are not moved to another runtime location. A test fixture may carry a small synthetic
exchange, but no shipped pack is loaded per turn. The data files are deleted after the tests that
depend on them are replaced.

### 4.3 Remove `soul.style`

`SoulStyle` contains only the pack selectors and their limits, so it is removed with the packs:

- `patterns`, `facts`, `registers`, `max_patterns`, and `max_facts` leave `soul.yaml`;
- `SoulStyle`, `style_block()`, and `_style()` leave `soul.py`;
- `eidesoul library resolve` and `admin/library.py` are retired;
- `init` stops writing a `style:` block and stops running the resolver;
- the `style:` block is not kept as an empty compatibility surface.

The separate `engine.style` section
from ADR-0041 is a different field: it is the voice-layer reply profile. It moves to
`soul.voice.style` under §4.9; the engine section is removed rather than kept in parallel.

### 4.4 Remove the gender distinction

The character path does not classify or target a gender:

- `soul.gender` and the `GENDERS` validation are removed;
- the decision prompt no longer prints a gender line;
- pattern `target` values disappear with the pattern catalogue;
- the stale `gender_gate`, `gender_confidence`, and `gender_avatar` keys are removed from the generated
  config;
- no replacement classifier is added.

`soul.pronouns` remains an identity field: it controls self-reference, not a target class. A profile
without pronouns uses the language catalog default.

### 4.5 Remove the character level table

The following are removed from the character path:

| Removed | Reason |
| --- | --- |
| `CharacterConfig.toxicity` | 0-4 register table |
| `CharacterConfig.adult_memes` | 0-3 adult-meme table |
| `CharacterConfig.roast_jabs` | a level-shaped cap with no other owner |
| `CharacterConfig.max_questions` | a level-shaped rewrite trigger |
| `effective_limits()` | the table accessor |
| `over_level()` and `severity()` | the marker scanner |
| `regulate()` and `too_many_questions()` | the second rewrite call |
| `library/severity` | the profanity severity pack |
| `library/lexicon` `level_words` | the level map used by the character scan |
| `character_block()` | the level/register prompt block |

`character.py` keeps only the polish path: detect a robotic or overlong draft, make one
`LLM.complete` call, preserve facts and tool results, and fall back to the original draft when the
result is suspicious. `CharacterConfig` keeps `enabled`, `max_chars`, `max_tokens`, and
`temperature`.

The base prompt still states the honest limits that already live there: no invented facts, no claimed
tool action, no doxxing, no threats. This ADR does not add a new moderation layer.

Two non-graded knobs remain in `soul.yaml`:

```yaml
voice:
  profanity: free      # free | avoid
  safety: inherit      # inherit | off
```

`voice.profanity` is a prompt instruction, not a scanner and not a table. `free` tells the soul it may
swear when the thread already does; `avoid` tells it not to. There is no 0-4 scale, no per-word
severity pack, and no rewrite.

`voice.safety` is a per-soul policy selector. `inherit` uses the provider default; `off` asks the
adapter for its native unfiltered setting. There is no `engine.llm.safety` duplicate: the soul is the
only place where this behavior is chosen. `off` still requires an adapter that reports the capability;
otherwise the config fails at load, per ADR-0044. A provider may still refuse; the override removes the
request threshold, not the model's behaviour or the provider's terms.

### 4.6 Daily and professional sampling

The decision already separates `daily` and `professional` (`decision.py`). This ADR makes the sampling
difference explicit and configurable. Behavior sampling lives with the soul; the operational utility
call stays with the engine:

| Key | Default | Used by |
| --- | ---: | --- |
| `soul.voice.sampling.daily_temperature` | `0.9` | daily draft |
| `soul.voice.sampling.professional_temperature` | `0.3` | professional draft |
| `soul.voice.sampling.top_p` | empty | all wires that support it |
| `soul.voice.sampling.top_k` | empty | native Gemini first |
| `soul.voice.sampling.min_p` | empty | only when the adapter supports it |
| `soul.voice.character.temperature` | `0.7` | daily polish |
| `engine.llm.utility_temperature` | `0.2` | decision, summary, extraction |

The split is deliberate: a value that changes how the soul speaks belongs in `soul.yaml`; a value that
describes an operational call belongs in `engine.llm`. There is no second copy of the daily or
professional value in `engine.llm`.

The behavior values are defaults, not fixed policy. An empty value means the provider default. A
non-empty value unsupported by the selected wire fails at load, per ADR-0044.

Daily mode is the default for ordinary conversation. Professional mode is selected for real tasks and
is not a global default. Professional has helpful defaults: no polish, tool-complete work, a lower
temperature, and the same honest reporting rules. If the owner later wants professional as the default
for every chat, that is a separate decision.

### 4.7 Detector lexicon only

`library/lexicon` stays only for deterministic detection that other engine paths need:

| Kept | Used by |
| --- | --- |
| `assistant_tells` | polish trigger |
| `negations` | memory contradiction |
| `stopwords` | salience and drift |
| `injection` | guard and tool output |
| `affirmations` | privacy answer detection |
| `laughter`, `particles`, `serious_registers` | style profile and detector context |

Removed from the lexicon contract:

- `prompt_examples` and every `lexicon.example()` call in the character and prompt paths;
- `level_words` when it is only a character-level map. The privacy path may keep its own numeric
  mapping under a clearly separate key; it is not a character level.

The prompt no longer borrows formal words, curse openers, self-reference lists, or polish examples from
the lexicon. Those are persona material and belong in `soul.yaml` or nowhere.

### 4.8 Unchanged layers

- `affect` and its eight VAD prototypes stay.
- `humanize` stays; it changes delivery, not wording.
- Privacy levels L0-L4 and the disclosure policy stay.
- The tool guard, sandbox, and approval policy stay in the engine.
- The voice layer's **implementation** stays out of scope; its configuration moves in §4.9.

### 4.9 Voice tuning lives in `soul.yaml`

All character and voice tuning moves to one `soul.voice` block. This reverses ADR-0041 option E: voice
tuning is per-soul data, not an engine-wide mechanism. The engine still owns the implementations; the
soul owns their settings.

```yaml
soul:
  voice:
    enabled: false
    sampling:
      daily_temperature: 0.9
      professional_temperature: 0.3
      top_p: 0.95
      top_k: 40
      min_p: null
    affect:
      enabled: true
      volatility: expressive
      grudge_half_life_hours: 1.5
      vibe:
        enabled: true
        attack: 0.35
        baseline_influence: 0.05
        hold_seconds: 1.2
        tick_seconds: 1.0
    style:
      enabled: true
      max_chars: 800
    character:
      enabled: false
      max_chars: 400
      max_tokens: 500
      temperature: 0.7
    humanize:
      enabled: true
      cpms: 25.0
      min_delay: 0.5
      max_delay: 5.0
      between_delay: 0.7
      max_bubbles: 3
      reaction_stickers: []
    profanity: free
    safety: inherit
```

The old top-level `engine.affect`, `engine.style`, `engine.character`, and `engine.humanize` sections are
removed. `engine.voice` is not introduced; ADR-0041's one-section/master semantics apply under
`soul.voice`. `engine.guard` does **not** move: it is the security boundary, not character tuning.

If `soul.voice` is absent, the defaults above apply. A sub-section is inert while `soul.voice.enabled`
is `false`, except `sampling`, `profanity`, and `safety`, which are request-level and not gated by the
master.
`soul.voice.style` is the statistical reply profile formerly at `engine.style`; it is not the removed
`soul.style` pack selector.

## 5. Configuration

```yaml
engine:
  llm:
    utility_temperature: 0.2

soul:
  voice:
    enabled: false
    sampling:
      daily_temperature: 0.9
      professional_temperature: 0.3
      top_p: 0.95
      top_k: 40
      min_p: null
    affect: {enabled: true}
    style: {enabled: true}
    character: {enabled: false}
    humanize: {enabled: true}
    profanity: free
    safety: inherit
```

Removed engine keys: `affect`, `style`, `character`, and `humanize` sections. Removed character keys:
`toxicity`, `adult_memes`, `roast_jabs`, `max_questions`. Removed soul keys: `gender` and the old
`soul.style` pack block. Removed host config keys: `gender_gate`, `gender_confidence`, `gender_avatar`.
`voice.profanity` and `voice.safety` are retained, not level fields.

`soul.yaml` keeps:

```yaml
name: ...
language: ...
locale: ...
timezone: ...
pronouns: ...
background: |
  ...
persona: |
  ...
voice:
  enabled: false
  sampling:
    daily_temperature: 0.9
    professional_temperature: 0.3
    top_p: 0.95
    top_k: 40
    min_p: null
  affect:
    enabled: true
  style:
    enabled: true
  character:
    enabled: false
  humanize:
    enabled: true
  profanity: free
  safety: inherit
examples:
  - user: ...
    soul: ...
rules: []
```

A missing `voice` block means the defaults in §4.9: `enabled: false`, each sub-section `enabled: true`,
`profanity: avoid`, and `safety: inherit`. The soul value is resolved before the LLM request; `off` is
still gated by the adapter capability from ADR-0044.

## 6. Metrics and gates

| Metric | Target |
| --- | ---: |
| Runtime character packs loaded | 0 |
| `select_patterns()` / `select_facts()` calls | 0 |
| `soul.style` fields | 0 |
| Gender fields in the character path | 0 |
| Character level fields | 0 |
| `soul.voice` sections | 5 (sampling, affect, style, character, humanize) |
| `soul.voice` master knobs | 3 (enabled, profanity, safety) |
| Top-level engine voice sections | 0 |
| Level rewrite calls per turn | 0 |
| Daily and professional temperature keys | 2 |
| Polish calls with a configured temperature | 1 |
| Prompt examples read from lexicon | 0 |
| `pytest -q` | pass |
| `ruff check` | pass |
| `python -P scripts/types.py --check` | pass |
| `python -P scripts/adr.py --check` | pass |
| `python -P scripts/adr.py --translate-check` | pass |
| `python scripts/selfcheck.py --check` | pass |

## 7. Acceptance criteria

- [ ] No runtime path reads `library/patterns` or `library/facts`.
- [ ] `select_patterns()` and `select_facts()` no longer exist.
- [ ] `soul.style` and `eidesoul library resolve` no longer exist.
- [ ] The character prompt has no room-pattern or known-facts block.
- [ ] The character path has no gender field, gender prompt line, or gender target.
- [ ] The character path has no toxicity/adult table, severity pack, level scan, or level rewrite.
- [ ] `soul.voice.profanity` and `soul.voice.safety` are configurable per soul.
- [ ] `engine.affect`, `engine.style`, `engine.character`, and `engine.humanize` no longer exist.
- [ ] `soul.voice` has sampling, affect, style, character, and humanize sub-sections.
- [ ] No daily or professional temperature remains in `engine.llm`.
- [ ] `voice.safety` is the only safety selector and `off` fails when unsupported.
- [ ] A missing `voice` block resolves to `profanity: avoid` and `safety: inherit`.
- [ ] `character.py` still polishes a robotic draft and preserves facts and tool results.
- [ ] Daily and professional temperatures are operator-configurable.
- [ ] Professional mode remains task-selected, not the default for ordinary chat.
- [ ] `affect`, `humanize`, privacy levels, and the tool guard still work.
- [ ] No external character library or new runtime dependency is added.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Persona drift without packs | `soul.yaml` persona plus examples is the single source; tests assert the examples reach the prompt |
| The model becomes too uniform | daily temperature is configurable and the persona carries the rhythm |
| Professional becomes too flat | professional defaults are explicit; the task prompt already reports outcome and evidence |
| Tool calls become less reliable | professional uses a lower temperature; tool-call tests stay at the existing budget |
| Removing levels changes behavior | this is intentional; no compatibility table is kept |
| Gender returns through a new field | the neutral test asserts the character path has no gender field or prompt line |
| Pack tests break | tests that depend on packs are replaced with fixture-based soul and mode tests |
| The lexicon becomes a hidden catalogue | only detector lists remain; prompt-example and character-level keys are removed |
| Privacy levels are confused with character levels | ADR-0038 keeps L0-L4; this ADR removes only the character table |
| `soul.yaml` grows too large | voice is one nested block; persona and examples stay the only long prose |
| Guard is moved into persona data | `engine.guard` stays in the engine config; the schema test asserts it |
| Per-soul safety bypasses the engine setting | precedence is explicit; `off` still requires native adapter support and is logged |

## 9. Interaction with other ADRs

- **ADR-0008**: `rules` stay behavior rules; they are not a style catalogue.
- **ADR-0025**: polish remains a gated call; removing `regulate()` lowers the worst-case call count.
- **ADR-0033**: no culture, gender, or vendor vocabulary enters the mechanism; persona content stays in
  `soul.yaml`.
- **ADR-0034**: the `character` prompt part is removed with its block; the registry keeps the parts that
  still produce text.
- **ADR-0038**: privacy levels and disclosure stay; only the character table is removed.
- **ADR-0041**: its one-section/master and off-by-default semantics remain; ADR-0045 moves the location
  from `engine.voice` to `soul.voice`, removes the old `soul.style` pack block, and removes character
  pack selection from the character part.
- **ADR-0043**: public facts move to model prior and websearch; facts no longer enter the character
  block.
- **ADR-0044**: the mode temperatures are carried through the provider adapter; daily and professional
  values are validated against the selected wire. `soul.voice.safety` is resolved before the request; the adapter capability gate still applies.

## 10. Code references

- `eidesoul/engine/character/patterns.py:1-27` — the local pattern catalogue and its selection helpers.
- `eidesoul/engine/character/knowledge.py:1-30` — the local fact catalogue.
- `eidesoul/engine/character/character.py:190-268` — `severity()`, `effective_limits()`, and `over_level()`.
- `eidesoul/engine/character/character.py:310-360` — `regulate()` and its rewrite path.
- `eidesoul/engine/character/lexicon.py:23-47` — lexicon keys, including `level_words` and prompt examples.
- `eidesoul/engine/soul.py:49-55` — `SoulStyle`.
- `eidesoul/engine/soul.py:95-109` — `style_block()`.
- `eidesoul/engine/soul.py:255-285` — soul loading and `style` assignment.
- `eidesoul/engine/turn/loop.py:16-17` — imports of `select_facts` and `select_patterns`.
- `eidesoul/engine/turn/loop.py:393-414` — per-turn pattern and fact selection.
- `eidesoul/engine/turn/loop.py:534-579` — daily polish and level regulation.
- `eidesoul/engine/turn/prompts.py:171-214` — `character_block()` and the level text.
- `eidesoul/engine/turn/prompts.py:442-449` — the character prompt part.
- `eidesoul/engine/turn/decision.py:25-26` — the daily/professional decision contract.
- `eidesoul/engine/turn/decision.py:392-397` — the identity prompt with the gender line.
- `eidesoul/engine/config.py:582-614` — `AffectConfig`, `StyleConfig`, `CharacterConfig`, and
  `HumanizeConfig`.
- `eidesoul/engine/config.py:1724-1732` — character config parsing.
- `eidesoul/engine/soul.py:58-72` — `Soul` fields that will carry `voice`.
- `eidesoul/engine/soul.py:255-285` — soul loading and the future `voice` assignment.
- `eidesoul/admin/library.py:1-27` — the pack resolver.
- `eidesoul/admin/templates.py:389` — the generated soul style block.
- `eidesoul/__main__.py:102-111` — `AffectStore`, `StyleStore`, and `VibeRunner` construction.
- `eidesoul/__main__.py:600` — the `library resolve` call.
- `tests/test_library.py` — pack-selection coverage.
- `tests/test_resolver.py` — resolver coverage.
- `tests/test_character.py` — polish and regulate coverage.
- `tests/test_prompt_contract.py` — prompt registry and register coverage.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0045-character-without-local-packs.md`.*
