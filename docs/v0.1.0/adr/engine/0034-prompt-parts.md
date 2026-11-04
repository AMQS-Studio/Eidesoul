# ADR-0034: Prompt Parts — One Registry for Block Metadata, Not a Second Tool Contract

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0015 (prompt budgets and the capability index), ADR-0025 (stable prefix and prompt cache), ADR-0027/0028 (plugin unit and clean-by-default capability), ADR-0033 (neutral core and `prompt_section`)
- **Supersedes:** —
- **Related:** ADR-0031 (module layout and the growth budget), ADR-0024 (tool input contract), ADR-0018 (gateway seam)
- **Implementation:** draft — the registry, the metadata, and the plugin extension point are decided here; the tree still assembles blocks from two hardcoded dictionaries and three name lists.
- **Location note:** an engine decision (it owns prompt assembly and the extension surface for blocks), so it sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0034-prompt-parts.md`.

---

## 1. Context

`system_prompt()` already receives rendered strings and only assembles them (`turn/prompts.py:244`). The producer side is equally honest: stores hand back text (`AffectStore.prompt`, `StyleStore.profile`, the memory context), and the agent passes it in. What the assembly lacks is a single description of a block. Measured on 2026-11-04:

- Sixteen blocks exist. Five stable blocks are built into `stable_blocks` (`prompts.py:400`): `base`, `character`, `roles`, `safety`, `person_style`. Eleven volatile blocks are built into `volatile_blocks` (`prompts.py:407`): `self`, `topic`, `away`, `emotion`, `vibe`, `style`, `research`, `memory`, `actions`, `skills`, `capabilities`.
- The same sixteen names appear a second time in `turn/salience.py`: `PINNED` (`:11`), `MANDATORY` (a historical alias, `:12`), `COMPETING` (`:13`), and `ORDER` (`:26`). `select()` indexes `ORDER` (`:87`) and filters by membership in `PINNED`/`COMPETING` (`:75`, `:90`).
- The agent carries a third copy implicitly: twelve named keyword arguments at the call site (`agent.py:1445-1464`) and the producer calls that fill them (`agent.py:1368-1456`).
- Adding one block therefore touches three lists in `salience.py`, the signature and both dictionaries in `prompts.py`, the call site in `agent.py`, and `tests/test_salience.py` (which imports `COMPETING`, `MANDATORY`, and `ORDER` directly). Deleting one block is the same edit in reverse.
- A gateway already contributes text through `prompt_section()` (ADR-0033 §4.5), capped at 1200 characters (`inbound.py:220`). A plugin has no prompt surface at all: its `register()` returns tools and commands only (`plugins.py:505-520`), so a capability that needs a model-facing instruction must edit core.
- The split exists for a reason: `Prompt.stable` and `Prompt.volatile` are sent as two system messages (`llm.py:232-237`, ADR-0025), so the stable prefix is cached and the volatile half changes per turn. Which block belongs where is a caching decision, not a formatting decision.
- The structure works and is tested (`tests/test_prompt_contract.py` pins per-body properties and the stable-prefix hash). The problem is the number of places that must agree on a name, and the absence of an extension point.

## 2. Decision drivers

1. **One owner per fact.** A block's name, stability, order, and salience weight live in one entry, not in three files.
2. **The cache split stays explicit.** `stable` is a declared field; the assembler never guesses it from the name or from where the text came.
3. **Deterministic order.** The prefix hash must not depend on import order or dictionary insertion order.
4. **Extensions add data, not core edits** (ADR-0027, ADR-0028, ADR-0033). A plugin prompt block is declared and verified; it never edits `prompts.py`.
5. **A prompt block is not a tool.** No permission check, no user-facing catalog entry, no async call, no `ToolContext`.
6. **Fail closed.** Duplicate names, duplicate orders, unknown plugin declarations, and out-of-cap text are boot failures with the offending name.
7. **`prompts.py` stays pure.** It may not import stores, the agent, or a gateway; the context is data handed in.

## 3. Options considered

**A. Keep the string dictionaries and the name lists.** Rejected: the evidence in §1 is the cost, and every new plugin or body block either edits core or goes without.

**B. Make `prompts.py` a `Handler` registry.** Rejected: `Handler` is the tool/command contract — async `fn(args, ctx)`, JSON parameters, role permissions, `execute()`, and a user-visible catalog (`registry.py`). A prompt block has none of those; forcing it through `execute` creates meaningless permissions and a second discovery folder. It also puts block text behind the approval path, which is wrong by construction.

**C. Stores push their text into `prompts.py`.** Rejected: it inverts the dependency (`turn/` would import `stores/`), makes the block type a process-global mutable registry, and moves the stable/volatile decision into the store — the one place that cannot know the cache contract.

**D. A `PromptPart` metadata registry with an explicit context (chosen).** The registry owns metadata and producers; the agent builds one context per turn; salience reads fields, not names.

**E. Plugin prompt via `prompt_section` only.** Rejected as the sole answer: `prompt_section` is the body's transport facts and is tied to a gateway instance. A plugin block is a capability instruction with no transport; it needs its own declared, static surface.

## 4. Decision

### 4.1 One block is one `PromptPart`

```python
@dataclass(frozen=True)
class PromptPart:
    name: str                 # ^[a-z][a-z0-9_]{0,63}$, unique in the assembled list
    stable: bool              # joins the cached prefix (ADR-0025)
    order: int                # unique; stable parts sort before volatile parts
    bonus: float = 0.0        # salience weight, ADR-0015
    pinned: bool = False      # never dropped by the budget
    produce: Callable[[PromptContext], str] | None = None
    text: str = ""            # extension parts only: static text, no callable
```

`produce` and `text` are exclusive: a core part has a producer; an extension part has literal text. A part returning `""` is dropped before selection, exactly as an empty string is dropped today.

### 4.2 `PromptContext` is the explicit data dependency

`PromptContext` is a frozen dataclass whose fields are the union of the parts' inputs: `config`, `soul`, `roles`, `runtime`, `query`, `thread_id`, `person_id`, `direct`, `body_section`, `language`, `limits`, `patterns`, `facts`, `affect`, `style`, `vibe`, `topic`, `away`, `memory`, `actions`, `skills`, `tools`, `commands`, `bodies`. It is built once per turn by the agent and never mutates.

A new part that needs a new input adds one field to `PromptContext` and one line at the agent's construction site. The registry does not hide data dependencies; it only removes name duplication.

Producers are synchronous and side-effect free. The engine never awaits a part.

### 4.3 The registry lives in `turn/prompts.py`

`CORE_PARTS: tuple[PromptPart, ...]` sits beside `system_prompt()` in the same module (444 lines today; the registry and helpers stay well under the 800-line cap, so no new module is introduced and ADR-0031's tree does not change). The assembled list is per agent: `CORE_PARTS + plugin_parts`, with the body contribution as the core part named `body`.

`system_prompt(config, roles, *, context, parts=...)` returns the same `Prompt` type:

- `stable` = join of the parts with `stable=True`, in `order`;
- `volatile` = join of the selected parts with `stable=False`, in `order`, followed by the fixed tail (`language_line`, `Date` from `soul.timezone`) that must always be last;
- a stable part is never selected away and never moved after a volatile part.

### 4.4 Salience reads metadata, not names

`salience.select(parts, query, ...)` takes the list of `(PromptPart, text)` and drops the weakest non-pinned part until the budget fits. `PINNED`, `MANDATORY`, `COMPETING`, and `ORDER` are deleted. `MANDATORY` does not survive as an alias; the contract is the field `pinned`.

### 4.5 Extension: plugins declare, they do not edit core

A plugin contributes static prompt parts through its existing registration:

- `plugin.yaml` declares `provides.prompt: ["<slug>", ...]`;
- `register()` returns `{"tools": [...], "commands": [...], "prompts": [PromptPart, ...]}`;
- an extension part must set `stable=True`, `produce=None`, non-empty `text`, `len(text) <= 1200`, and a namespaced `name = "plugin:<plugin>:<slug>"`; `order` is assigned by the loader after core parts, so a plugin cannot reorder the core blocks;
- text is literal: no formatting placeholders, no user data, no secrets, no runtime interpolation. There is no callable, so there is no context to read;
- a collision, an empty text, an over-cap text, a callable on an extension part, or a name outside `provides.prompt` is a `PluginError` before the plugin joins the turn path.

A gateway does not register additional parts. Its one contribution stays `prompt_section()` (ADR-0033 §4.5), modelled as the `body` part: `stable=True`, `pinned=True`, `order` directly after `base`, capped at 1200 characters. One body contribution, one pathway.

### 4.6 Boot checks

The registry rejects, naming the part: a duplicate `name`, a duplicate `order`, an invalid name, a core part with both `produce` and `text` or with neither, an extension part that is not static, and an extension part whose name is absent from `provides.prompt`. The errors aggregate; the first message is not the only one.

### 4.7 What this deliberately does not change

The producers stay where they are: `AffectStore.prompt`, `StyleStore.profile`, `self_block`, `roles_block`, `_capability_index`, and the memory renderer keep their signatures. The agent still fetches the per-turn inputs, because only the agent holds the turn. The registry removes the three name lists and the sixteen-argument signature; it does not move store logic into `turn/`.

## 5. Configuration

No new engine key. The only new data is the plugin manifest key `provides.prompt` (ADR-0028's `provides` mapping already accepts named lists after ADR-0033 §4.6; this ADR fixes its meaning). The extension cap is one constant `PROMPT_PART_MAX = 1200`, the same value as `PROMPT_SECTION_MAX`.

## 6. Metrics and gates

Counts to drive to zero, measured 2026-11-04:

| Specialization | Count | Where |
| --- | --- | --- |
| block name lists | 3 | `PINNED`/`MANDATORY`/`COMPETING`/`ORDER` in `turn/salience.py` |
| blocks without a `PromptPart` | 16 | `stable_blocks` + `volatile_blocks` in `turn/prompts.py` |
| block-name literals in `system_prompt`'s signature | 12 | `agent.py:1445-1464` |
| plugin prompt surfaces | 0 | `register()` returns tools/commands only |

The gate:

1. `turn/salience.py` contains no block name and no `PINNED|MANDATORY|COMPETING|ORDER` symbol; `tests/test_salience.py` builds its cases from `PromptPart` fixtures.
2. Every core block has exactly one `PromptPart`; `CORE_PARTS` names equal the set the tests expect, with no duplicate name or order.
3. A stable part produces byte-identical text for two different per-turn contexts (different date, message, memory) — the check runs in `tests/test_prompt_parts.py`; a stable part that reads per-turn data fails the suite.
4. `bodies` has exactly one part (`body`), it is the only `pinned` part whose text comes from `prompt_section`, and it is capped at 1200.
5. An extension part is static, namespaced, under the cap, and absent from the core registry; a duplicate or unknown name is refused.
6. `tests/test_prompt_contract.py` keeps pinning per-body properties and the normalized stable-prefix hash; the hash changes only when a stable part's text or order deliberately changes.
7. `scripts/neutral.py --check` also refuses a plugin prompt tree that carries a non-ASCII letter or a banned culture word, using the same scope it already applies to core (the plugin tree is scanned through its manifest).

## 7. Acceptance criteria

- Adding a block is one `PromptPart` entry plus its producer; no other file changes. Deleting a block is the reverse.
- `turn/salience.py` holds only the selection algorithm; no block names.
- `system_prompt` takes a context and a part list; the sixteen keyword arguments are gone.
- The stable prefix is assembled only from `stable=True` parts, in `order`, and `prompt_prefix_hash` changes only with those parts.
- A plugin can add one static instruction block through `provides.prompt` and `register()["prompts"]`, with no core edit, and the block appears in the prompt after the core blocks.
- A plugin that declares a callable, a duplicate name, or 1201 characters fails to register with one named error.
- The gateway path is unchanged: `prompt_section()` remains the single body contribution, capped and static.
- `pytest -q`, the neutral gate, and the prompt contract suite stay green.

## 8. Risks and mitigations

- **A stable part reads per-turn data and breaks the cache.** Mitigation: the two-context equality check in the suite; `stable` is declared, never inferred.
- **A global registry leaks between agents in one process.** Mitigation: the assembled list is per agent; `CORE_PARTS` is immutable module data and extension parts are copied at boot.
- **A plugin uses the prompt as an injection surface.** Mitigation: extension parts are literal text with no callable and no context, capped, English-only by the neutral gate, namespaced, and declared in the manifest before registration.
- **Order churn breaks every cache.** Mitigation: `order` is data in one file; changing a stable part or its order is a deliberate diff, recorded by the prompt contract hash.
- **The registry becomes a god object.** Mitigation: the context is a frozen dataclass with named fields; a part that needs a new input makes that need visible as a new field and a new agent line, not a hidden lookup.
- **Two extension pathways (body section and plugin part) confuse authors.** Mitigation: the split is by nature — transport facts belong to the body; capability instructions belong to the plugin; neither can take the other's path.

## 9. Interaction with other ADRs

- ADR-0025: the stable/volatile split and the prefix hash are the reason `stable` and `order` are fields.
- ADR-0015: the budget and the capability index remain; salience now reads `pinned`/`bonus` from metadata.
- ADR-0027/0028: a plugin still owns its code and manifest; `provides.prompt` is data, verified like the rest of the manifest, and the digest covers the text's producer (there is none — the text is data in the code tree).
- ADR-0031: no new module and no new top-level file; `prompts.py` keeps one reason to change (assembly) and the registry stays under the growth cap.
- ADR-0033 §4.5: `prompt_section` survives untouched and is modelled as the `body` part; a gateway gains no second surface.
- ADR-0024: prompt parts are not handlers and never enter `execute` or the argument validator.

## 10. Code references

- `eidesoul/engine/turn/prompts.py:16,45,244,400,407,428` — `Prompt`, `_capability_index`, `system_prompt`, the two block dictionaries, and the fixed volatile tail.
- `eidesoul/engine/turn/salience.py:11,12,13,26,53,68,75,87,90` — the name lists and every membership/index use.
- `eidesoul/engine/agent.py:1101-1107,1184,1368-1372,1445-1464` — the emotion/vibe producers, the decision call, `character_block`, and the sixteen-argument assembly call.
- `eidesoul/engine/llm.py:232-237` — the two system messages that make the split a cache decision.
- `eidesoul/engine/inbound.py:220` — `prompt_section`, the cap, and the body contribution.
- `eidesoul/engine/plugins.py:261-268,505-520` — `provides` parsing and `register()`; the extension point this ADR defines.
- `tests/test_salience.py:5,18,44,83-84` — the tests that import the name lists and will be rebuilt from fixtures.
- `tests/test_prompt_contract.py`, `tests/data/prompt_cases.yaml` — the per-body property and stable-hash contract that must keep passing.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0034-prompt-parts.md`.*
