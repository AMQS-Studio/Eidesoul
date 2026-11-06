# ADR-0009: Language-Agnostic Intent, Secret Gate, and Semantic Privacy Routing — Retiring Per-Language Keyword Lists (`monoagens/library/`)

- **Status:** accepted
- **Date:** 2026-11-06
- **Deciders:** Muqsith (owner)
- **Context:** Establishes an international, language-agnostic hybrid architecture for secret detection, privacy classification, turn-level intent/search routing, room vibe/style, and semantic memory maintenance, completely retiring and deleting the static per-language JSON keyword dictionaries under `monoagens/library/**`.
- **Supersedes:** —
- **Related:** ADR-0001 (sovereign core), ADR-0002 (unified memory and privacy), ADR-0003 (turn lifecycle), ADR-0006 (access control and guard), ADR-0008 (code hygiene)
- **Implementation:** implemented — structural token prefixes, Shannon-entropy secret gate, `assess_batch` privacy levels, `decide_turn` routing, and semantic `forget_by_intent` run on built-in English anchors and universal shapes; `monoagens/library/**` and its per-language JSON packs are gone.

---

## 1. Context

Relying on 15 static per-language JSON keyword dictionaries under `monoagens/library/**` (`lexicon/*.json`, `privacy/*.json`, `safety/*.json`, `style/*.json`, `affect/*.json`, `intent/*.json`, `guard/credentials.json`) created four architectural flaws:

1. **Hard Language Lock-In (`en` and `id` Only):**
   - `monoagens/engine/config/load_config.py` threw a fatal `ConfigError` (`"no safety pack for '<lang>'; add library/safety/<lang>.json"`) whenever an operator configured `soul.language` to any language other than `"en"` or `"id"` (such as `"ja"`, `"ar"`, `"es"`, `"zh"`, `"fr"`, `"de"`).
   - Humans communicate in hundreds of languages, regional dialects, bilingual code-switching, slang, and implicit phrasing that static JSON files can never enumerate.
2. **Context Blindness (False Positives & False Negatives):**
   - Static word lists in `library/privacy/*.json` and `library/lexicon/*.json` cannot evaluate semantics or negation:
     - Technical phrases like *"kill the background process"* or *"hit the gym"* match violence keywords.
     - Everyday phrases like *"work-life balance"* or *"river bank"* match financial keywords.
     - Apologies like *"Maaf, tadi aku yang salah"* match crime-admission keywords.
     - Local workspace commands like *"Buatkan file CSV pengeluaran minggu ini"* match temporal keyword `"minggu ini"`, triggering an unnecessary web search.
3. **Redundant Per-Language JSONs on Top of a Multilingual Embedder:**
   - MonoAgens already embeds text with `paraphrase-multilingual-MiniLM-L12-v2`, which maps 50+ languages into the **same 384-dimensional semantic vector space**. Maintaining separate `.en.json` and `.id.json` files for `affect` (`vibe.py`) and `intent` (`memory/intent.py`) is redundant when a single canonical semantic anchor bank in Python works across all 50+ languages in that shared vector space.
4. **Redundant Bypass of Existing Turn & Cognition LLM Calls:**
   - MonoAgens already executes a structured JSON turn-decision call (`decide_turn` in `monoagens/engine/turn/decision.py`) and a structured JSON memory assessment call (`assess_batch` in `monoagens/engine/memory/assess.py`).

## 2. Decision drivers

- **Zero Static Keyword JSONs (`monoagens/library/` Deleted):** Delete all 15 JSON files under `monoagens/library/**` and the calibration scripts `scripts/affect.py` and `scripts/intent.py`.
- **International Out-of-the-Box:** Any ISO language code in `soul.language` (`en`, `id`, `ja`, `ar`, `es`, `zh`, `fr`, `de`, etc.) must boot and operate with full safety, privacy, vibe, style, and intent capabilities without requiring per-language JSON packs.
- **Zero Extra LLM Round-Trips:** Piggyback on `decide_turn` (turn path), `assess_batch` (cognition path), and the shared multilingual MiniLM vector space.

## 3. Options considered

| Option | Summary | Verdict |
| --- | --- | --- |
| **A. Keep `monoagens/library/*.json` alongside LLM/vector routing** | Maintain 15 JSON files as fallbacks | **Rejected.** Keeps language lock-in in `load_config.py`, perpetuates false positives in fallback paths, and leaves 15 brittle JSON files in the repository. |
| **B. Pure Language-Agnostic Hybrid Architecture (Delete `monoagens/library/**`)** | Structural/entropy secret gate + `decide_turn` / `assess_batch` structured JSON + shared multilingual semantic anchor vectors in Python + statistical archive style learning | **Selected.** Zero JSON keyword files, works across 50+ languages out of the box, and eliminates keyword false positives. |

## 4. Decision

### 4.1 Physical Removal of `monoagens/library/**` and Language Pack Lock-In

1. **Delete `monoagens/library/**` (`15` JSON files) and `scripts/affect.py`, `scripts/intent.py`:**
   - Remove `monoagens/library/lexicon/*`, `monoagens/library/privacy/*`, `monoagens/library/safety/*`, `monoagens/library/style/*`, `monoagens/library/affect/*`, `monoagens/library/intent/*`, and `monoagens/library/guard/*`.
2. **Remove Safety Pack File Lock-In (`monoagens/engine/config/load_config.py`):**
   - Remove the `if not has_pack(soul.language)` `ConfigError` check in `load_config.py` so any language code is accepted without requiring a `<lang>.json` file on disk.

### 4.2 Language-Agnostic Structural & Shannon-Entropy Secret Gate (L5)

1. **Universal Vendor Token Shapes & URI Credentials (`monoagens/engine/guard/patterns.py` & `monoagens/engine/memory/evidence.py`):**
   - Detect vendor prefixes (`sk-[A-Za-z0-9_\-]{20,}`, `gh[pousr]_[A-Za-z0-9_]{20,}`, `github_pat_[A-Za-z0-9_]{20,}`, `AKIA[0-9A-Z]{16}`, `AIza[0-9A-Za-z_\-]{35}`, `xox[baprs]-[0-9A-Za-z\-]{10,}`, JWT `eyJ...`, PEM `-----BEGIN [A-Z ]*PRIVATE KEY-----`, and URI credentials `scheme://user:pass@host`).
2. **Shannon Entropy + Structural Key=Value Assignment:**
   - Flag unbroken candidate value tokens of length $\ge 24$ (excluding URLs, file paths, and UUIDs) mixing $\ge 3$ character classes with Shannon entropy $H \ge 4.2$ bits/char, as well as structural key/value assignments (`api_key`, `secret`, `token`, `password`, `passwd`, `pwd`, `bearer`, `authorization`).
3. **Unconditional Pre-Verdict Redaction in `CognitionCapture._label`:**
   - Redact L5 credentials in both `memories.text` and `memory_events.content` at the top of `_label()` before checking `verdict.worth`.

### 4.3 Context-Aware Privacy Level Classification in `memory_assess` & Structural PII (`evidence.py`)

1. **Primary Semantic Classification via `assess_batch` (`monoagens/engine/memory/assess.py`):**
   - `assess_batch` returns contextual `level` (`0..5`) and `category` in any language.
2. **Language-Agnostic Deterministic Floor in `evidence.py`:**
   - `monoagens/engine/memory/evidence.py` operates without `library/privacy/*.json`, using structural value shapes (`VALUE_SHAPES`: 16-digit ID/card numbers, vendor tokens, PEM keys, high-entropy secrets), Presidio/regex PII entities (`ENTITY`: email, phone, credit card, IBAN, IP), and universal semantic category classification so benign phrases (*"kill the background process"*, *"work-life balance"*, *"aku yang salah"*) never false-trigger L3/L4.

### 4.4 Turn-Level Intent, Crisis, & Search Routing (`decision.py`, `research.py`, `confirmations.py`, `patterns.py`)

1. **`decide_turn` Structured Routing (`monoagens/engine/turn/decision.py`):**
   - `Decision` carries `needs_search: bool | None = None`, `approval_intent: str = ""` (`"confirm"`, `"reject"`, `"modify"`, `""`), and `crisis: bool = False`.
2. **Language-Agnostic `lexicon.py` & `patterns.py` Without JSON Files:**
   - Replace file-backed `library/lexicon/*.json` and `library/safety/*.json` with compact, built-in universal multilingual anchor constants in Python (used only when `decide_turn` is bypassed or in offline unit tests), while `ResearchFlow._verify_research` exempts turns that executed non-search action tools.

### 4.5 Shared Multilingual Vector Prototypes for `vibe.py`, `intent.py`, `maintenance.py`, and Statistical `style.py`

1. **`vibe.py` (`monoagens/engine/stores/vibe.py`) & `intent.py` (`monoagens/engine/memory/intent.py`):**
   - Store the canonical semantic prototype sentences directly as immutable Python constants in `vibe.py` and `intent.py` (scored via the shared multilingual embedder `embed.encode`). Because `paraphrase-multilingual-MiniLM-L12-v2` aligns 50+ languages in one vector space, a single canonical prototype bank serves all languages without `library/affect/*.json` or `library/intent/*.json`.
2. **`forget_by_intent` (`monoagens/engine/memory/maintenance.py`):**
   - Combine token/stem matching with multilingual vector cosine similarity (`embed.encode`) against candidate memories across `global`, `person`, and `thread` scopes.
3. **`style.py` (`monoagens/engine/stores/style.py`):**
   - Learn message length (`median`, `p90`), lowercase ratio, emoji ratio, and recurring short particles/tokens directly from the chat archive using a single universal baseline seed in Python, eliminating `library/style/*.json`.

## 5. Configuration

No breaking changes to `config.yaml`. `soul.language` now accepts any language code without requiring per-language files under `monoagens/library/`.

## 6. Metrics and gates

- `find monoagens/library -type f` returns zero files (directory removed).
- `python -P scripts/adr.py --check` passes across all ADRs.
- `ruff check monoagens scripts tests plugins migrations` and `python -P scripts/types.py --check` pass with zero errors.
- `pytest -q` passes 100% of the test suite, and live E2E verification with `TEST_DEEPINFRA` (`deepseek-ai/DeepSeek-V4.1-Flash-1017`, `service_tier="flex"`) passes across multilingual inputs.

## 7. Acceptance criteria

- [x] `monoagens/library/` and `scripts/affect.py`, `scripts/intent.py` are completely removed from the repository.
- [x] `load_config` accepts any `soul.language` (e.g. `"en"`, `"id"`, `"ja"`, `"es"`, `"ar"`) without requiring a safety JSON pack on disk.
- [x] L5 credentials (vendor prefixes + Shannon entropy $\ge 4.2$) are redacted in both `memories.text` and `memory_events.content` even when `verdict.worth` is `False`.
- [x] `assess_batch`, `decide_turn`, `vibe`, `style`, and `forget_by_intent` operate cleanly across multiple languages without any `monoagens/library/*.json` files.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Existing unit tests in `tests/test_language.py` or `tests/test_detectors.py` assert JSON file paths | Update those tests to verify the language-agnostic built-in contracts and multilingual behavior |
| High-entropy check could flag URLs or file paths | Exclude tokens containing `://`, `/`, UUID shapes, or fewer than 3 character classes |

## 9. Interaction with other ADRs

- **ADR-0001:** Aligns with the language invariant in `AGENTS.md` (chat catalogs live only in `monoagens/lang/<code>.yaml`).
- **ADR-0002:** Upgrades L0–L5 privacy classification and `forget_by_intent` to be language-agnostic.
- **ADR-0003:** Folds search and approval routing into `decide_turn`.
- **ADR-0006:** Replaces file-based safety/credential packs with structural/entropy and semantic detection.

## 10. Code references

- `monoagens/engine/guard/patterns.py`, `monoagens/engine/memory/evidence.py`, `monoagens/engine/jobs/cognition_capture.py`
- `monoagens/engine/memory/assess.py`, `monoagens/engine/memory/maintenance.py`, `monoagens/engine/memory/intent.py`
- `monoagens/engine/turn/decision.py`, `monoagens/engine/turn/research.py`, `monoagens/engine/turn/research_flow.py`, `monoagens/engine/turn/confirmations.py`
- `monoagens/engine/character/lexicon.py`, `monoagens/engine/stores/vibe.py`, `monoagens/engine/stores/style.py`, `monoagens/engine/config/load_config.py`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0009-language-agnostic-intent-and-privacy-routing.md`.*
