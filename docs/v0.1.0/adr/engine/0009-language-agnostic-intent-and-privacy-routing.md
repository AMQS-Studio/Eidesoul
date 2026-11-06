# ADR-0009: Language-Agnostic Intent, Secret Gate, and Semantic Privacy Routing — Retiring Brittle Per-Language Keyword Lists

- **Status:** accepted
- **Date:** 2026-11-06
- **Deciders:** Muqsith (owner)
- **Context:** Establishes an international, language-agnostic hybrid architecture for secret detection, privacy classification, turn-level intent/search routing, and semantic memory maintenance, replacing static per-language keyword lists (`monoagens/library/*.json`).
- **Supersedes:** —
- **Related:** ADR-0001 (sovereign core), ADR-0002 (unified memory and privacy), ADR-0003 (turn lifecycle), ADR-0006 (access control and guard)
- **Implementation:** partial — structural token prefixes and multilingual embeddings are active; Shannon entropy secret gate, LLM-integrated privacy level in `assess_batch`, `decide_turn` intent routing, and semantic vector `forget_by_intent` are tracked for completion.

---

## 1. Context

During real-LLM end-to-end stress testing (`deepseek-ai/DeepSeek-V4.1-Flash-1017` across multilingual human conversations), relying on static per-language JSON keyword dictionaries under `monoagens/library/**` (`lexicon/*.json`, `privacy/*.json`, `intent/*.json`, `guard/credentials.json`) exposed four structural failure modes:

1. **Inability to Scale Internationally:**
   - Humans communicate in hundreds of languages, regional dialects (Javanese, Sundanese), bilingual code-switching, slang, abbreviations (`"gass"`, `"gk ush"`), and implicit phrasing. Static JSON files for `"en"` and `"id"` fail whenever an operator or user speaks another language or mixes languages mid-sentence.
2. **Context Blindness (High False Positives & False Negatives):**
   - Substring and word-list matching in `library/privacy/*.json` and `library/lexicon/*.json` cannot evaluate semantics or negation:
     - Technical phrases like *"kill the background process"* or *"hit the gym"* match violence keywords.
     - Everyday phrases like *"work-life balance"* or *"river bank"* match financial keywords.
     - Apologies like *"Maaf, tadi aku yang salah"* match crime-admission keywords.
     - Local workspace commands like *"Buatkan file CSV pengeluaran minggu ini"* match temporal keyword `"minggu ini"` in `research_time`, triggering an unnecessary external web search whose empty result causes `ResearchFlow._verify_research` to discard the completed file-creation reply.
3. **Morphological & Synonym Fragility in Memory Maintenance:**
   - `forget_by_intent` in `monoagens/engine/memory/maintenance.py` relies on exact token intersection (`_tokens`). Agglutinative clitics (`"gajiku"` vs `"gaji"`), synonyms (*"hapus ingatan soal pekerjaanku"* vs *"Budi bekerja di PT X"*), or cross-language requests fail to match the target memory row even though every memory row already carries a 384-dimensional multilingual vector (`paraphrase-multilingual-MiniLM-L12-v2`).
4. **Redundant Bypass of Existing Turn & Cognition Intelligence:**
   - MonoAgens already executes a structured JSON turn-decision call (`decide_turn` in `monoagens/engine/turn/decision.py`), a structured JSON memory assessment call (`assess_batch` in `monoagens/engine/memory/assess.py`), and a 50+ language dense vector embedder (`monoagens/engine/embed.py`), yet bypasses them in favor of static keyword lists.

## 2. Decision drivers

- **International by Default:** Every safety gate, privacy classifier, confirmation detector, research router, and memory operation must work across any human language, dialect, or bilingual mix without requiring per-language keyword files.
- **Zero Extra LLM Round-Trips:** Piggyback on the existing `decide_turn` (turn path) and `assess_batch` (cognition path) structured JSON calls plus the existing local `paraphrase-multilingual-MiniLM-L12-v2` embeddings so latency and token budgets do not increase.
- **Deterministic Offline & Test Fallback:** When `llm` is `None` or offline in unit tests, deterministic structural gates and multilingual vector similarity continue to operate cleanly without network access.

## 3. Options considered

| Option | Summary | Verdict |
| --- | --- | --- |
| **A. Expand `monoagens/library/*.json` with more languages and regexes** | Keep adding words, stopwords, and regex exclusions to JSON files | **Rejected.** Unbounded maintenance burden; cannot solve polysemy, negation, slang, or arbitrary international languages. |
| **B. Dedicated extra LLM call per detector** | Run separate LLM calls for privacy, research trigger, confirmation intent, and forget intent | **Rejected.** Multiplies turn latency and token cost by 3x–4x. |
| **C. Hybrid Structural + Piggybacked Structured LLM + Multilingual Vector Architecture** | Use mathematical/structural secret detection (L5), fold privacy classification into `assess_batch`, fold search/approval routing into `decide_turn`, and use multilingual `pgvector` cosine similarity for `forget_by_intent` | **Selected.** International, context-aware, zero extra LLM calls, and deterministic at the security boundary. |

## 4. Decision

### 4.1 Language-Agnostic Structural & Shannon-Entropy Secret Gate (L5)

Secret credentials are structural and mathematical artifacts, not natural-language vocabulary:

1. **Universal Vendor Token Shapes (`monoagens/engine/guard/patterns.py` & `monoagens/engine/memory/evidence.py`):**
   - Match known token prefixes across all formats including hyphens and underscores:
     - `sk-[A-Za-z0-9_\-]{20,}` (OpenAI `sk-proj-...`, `sk-admin-...`, Anthropic `sk-ant-...`)
     - `gh[pousr]_[A-Za-z0-9_]{20,}` and `github_pat_[A-Za-z0-9_]{20,}` (GitHub PATs)
     - `AKIA[0-9A-Z]{16}` (AWS), `AIza[0-9A-Za-z_\-]{35}` (Google API), `xox[baprs]-[0-9A-Za-z\-]{10,}` (Slack)
     - `eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}` (JWT)
     - `-----BEGIN [A-Z ]*PRIVATE KEY-----` (PEM private keys)
     - `(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@/]+@` (DSN/URI credentials)
2. **High-Entropy Secret Assignment Filter (Gitleaks-style Shannon Entropy):**
   - Compute Shannon entropy $H(s) = -\sum p_i \log_2 p_i$ on candidate value tokens of length $\ge 24$ (excluding URLs, file paths, and UUIDs) that mix at least three character classes (uppercase, lowercase, digits, symbols) or follow an assignment operator (`=`, `:`) / secret key identifier. Tokens exceeding the entropy threshold ($H \ge 4.2$ bits/char) are classified as L5 credentials regardless of the surrounding human language.
3. **Unconditional Pre-Verdict Redaction in `CognitionCapture._label`:**
   - `CognitionCapture._label()` must run the L5 credential check and `_redact_credential(memory_id)` (redacting both `memories.text` and `memory_events.content`) **before** evaluating `if verdict is None` or `if not verdict.worth`, ensuring secrets pasted in turns that the LLM marks `worth: false` are never left in plaintext in PostgreSQL.

### 4.2 Context-Aware Privacy Level Classification in `memory_assess` (L0–L4)

1. **Structured Assessment Schema (`monoagens/engine/memory/assess.py`):**
   - Extend the `assess_batch` JSON schema and `Assessment` dataclass with:
     - `level: int` (`0` = public/general, `1` = ordinary preference/work/hobby, `2` = personal context/schedule, `3` = sensitive financial/legal/relationship/third-party, `4` = intimate health/medical/crisis/trauma, `5` = secret credential/key).
     - `category: str` (`"general"`, `"preference"`, `"personal"`, `"financial"`, `"relationship_conflict"`, `"third_party_personal"`, `"legal"`, `"health"`, `"credential"`).
2. **Hybrid Floor with Structural PII Entities:**
   - Structural PII detectors (16-digit national ID/credit card shapes, email/phone/NER entities in `evidence.py`) provide a deterministic minimum floor, while semantic/contextual sensitivity (distinguishing *"river bank"* from *"bank debt"* or *"kill the process"* from violence in any language) is determined by `verdict.level` and `verdict.category` from `assess_batch` (falling back to `evidence.classify` only when `llm` is `None`).

### 4.3 Turn-Level Intent & Search Routing in `decide_turn` (`monoagens/engine/turn/decision.py`)

1. **Unified Turn Decision Schema:**
   - Extend `Decision` in `monoagens/engine/turn/decision.py` with two optional fields populated in the same single `decide_turn` JSON call:
     - `needs_search: bool = False` — `True` only when answering requires external current/live/verifiable web facts that are not local workspace actions (`write`, `read`, `send_file`, `task`, `skill`) or stable common knowledge.
     - `approval_intent: str = ""` — `"confirm"`, `"reject"`, `"modify"`, or `""`, evaluated when the turn has pending staged approvals in the active thread.
2. **Action-Tool Exemption in `ResearchFlow._verify_research` (`monoagens/engine/turn/research_flow.py`):**
   - When a turn executes non-search action/workspace tools (`any(t not in {"web_search", "info"} for t in tool_ctx.used)`) and `research_card.status != "ok"`, `_verify_research` must never overwrite the tool execution reply with `agent.research_abstain`.

### 4.4 Multilingual Semantic Vector Matching for `forget_by_intent` (`monoagens/engine/memory/maintenance.py`)

1. **Semantic + Lexical Candidate Matching:**
   - In `forget_by_intent()`, in addition to exact token overlap (kept as a fast path for single-match cases), rank the speaker's readable memories (`global`, `person`, and `thread`) using the multilingual embedding encoder (`embed.encode(text)`) against each candidate memory's stored embedding (`memories.c.embedding`) and `search_tsv`.
   - This enables natural forgetting across morphology (`"gajiku"` $\leftrightarrow$ `"gaji"`), paraphrases (*"forget where I work"* $\leftrightarrow$ *"Works at Acme Corp"*), and any language supported by the multilingual encoder.

## 5. Configuration

No breaking changes to `config.yaml`. `engine.research.mode` (`auto`, `always`, `off`) and `engine.memory.privacy` continue to govern runtime policy.

## 6. Metrics and gates

- `python -P scripts/adr.py --check` passes across all ADRs (including `ADR-0009`).
- `ruff check monoagens scripts tests plugins migrations` and `python -P scripts/types.py --check` pass with zero errors.
- `pytest -q` passes 100% of the test suite with zero failures.

## 7. Acceptance criteria

- [ ] `CognitionCapture._label()` redacts L5 credentials in both `memories.text` and `memory_events.content` even when `verdict.worth` is `False` or `verdict` is invalid.
- [ ] `_TOKEN_SHAPE` and `VALUE_SHAPES` detect hyphenated/underscored vendor tokens (`sk-proj-...`, `sk-ant-...`, `github_pat_...`) and high-entropy secret assignments across any language.
- [ ] `memory_assess.assess_batch` returns contextual `level` (`0..5`) and `category` in any language without false-triggering on benign idioms (*"kill the background process"*, *"work-life balance"*, *"aku yang salah"*).
- [ ] `ResearchFlow._verify_research` never overwrites a reply with `agent.research_abstain` when workspace/action tools (`write`, `send_file`, etc.) executed during the turn.
- [ ] `forget_by_intent` resolves morphological variants (`"gajiku"` $\rightarrow$ `"gaji"`) and multilingual semantic paraphrases via vector similarity when exact token overlap does not yield a single match.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Offline unit tests run with `FakeLLM` or `llm=None` | Keep deterministic regex/vector fallbacks active when the LLM response omits optional fields or `llm is None` |
| High-entropy check could flag long URLs or file paths | Exclude tokens containing `://`, `/`, UUID shapes, or fewer than 3 character classes from Shannon entropy flagging |

## 9. Interaction with other ADRs

- **ADR-0002:** Strengthens L0–L5 layered privacy and `forget_by_intent` for international multilingual deployments.
- **ADR-0003:** Integrates turn-level routing into `decide_turn` and `CognitionCapture` without adding extra turn checkpoints.
- **ADR-0006:** Enhances `guard/patterns.py` credential scanning and bilingual/multilingual natural approval confirmations.

## 10. Code references

- `monoagens/engine/guard/patterns.py`, `monoagens/engine/memory/evidence.py`, `monoagens/engine/jobs/cognition_capture.py`
- `monoagens/engine/memory/assess.py`, `monoagens/engine/memory/maintenance.py`
- `monoagens/engine/turn/decision.py`, `monoagens/engine/turn/research.py`, `monoagens/engine/turn/research_flow.py`, `monoagens/engine/turn/confirmations.py`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0009-language-agnostic-intent-and-privacy-routing.md`.*
