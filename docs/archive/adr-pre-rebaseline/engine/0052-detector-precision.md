# ADR-0052: Detector Precision — Word Boundaries, Value-Shaped Secrets, No Substring Triggers

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** runtime probes reproduced false detections live on everyday Indonesian and English sentences ("Halo sobat", "lampu mau mati", "token listrik", "ketemu pukul 3"); the owner wants natural conversation without hijacked words.
- **Supersedes:** —
- **Related:** ADR-0033 (neutral core), ADR-0038 (privacy layers), ADR-0043 (research contract), ADR-0045 (character without local packs), ADR-0049 (audit remediation)
- **Implementation:** draft — detectors are unchanged.
- **Location note:** the change binds detectors in the engine and the data under `monoagens/library/**`, so this ADR sits under `adr/engine/`.

---

## 1. Context

Detectors match substrings or context-free regex, and they fire on normal chat:

| Detector | Input | Result |
| --- | --- | --- |
| research trigger | "Halo sobat" ("obat"), "duduk di kursi" ("kurs"), "berhasil" ("hasil"), "I know" ("now") | forced web search, then abstain |
| research `EXACT` | "jam 10:00", "0812-3456", "65 kg", "bab 1-3" | public-claim trigger |
| crisis | "lampu kamar mau mati", "aku mau mati ketawa", "I don't want to die" | crisis mode, memory blocked |
| secret filter | "token listrik", "hotpot" ("otp"), "secretary", "max_tokens" | text erased to `""` |
| injection | "how do I write a good system prompt" | blocks memory, skill, fetch |
| privacy evidence | "pukul 3 sore", "aku yang salah", "sakit hati", ISO dates | L3/L4 or drop |
| privacy answer | "oke nanti jam 3", "biasa aja sih" | parsed as a level answer |
| forget intent | "hapus pesan yang tadi" | hard-deletes a memory sharing "yang" |
| sandbox screen | `cat docker-compose.yml` denied; `cat /etc/shadow` allowed | wrong in both directions |

Also: `gate.structural` checks only the English `SENSITIVE` pattern, so "kata sandi saya hunter2"
reaches `memories.text`; the research trigger reads only the `soul.language` pack, so an English
question to an Indonesian soul triggers nothing.

## 2. Decision drivers

1. A detector decides on a word, a shape, or a model signal, never on a letter run inside a word.
2. Secrets are values, not mentions of the word "token".
3. Everyday words stay out of deterministic triggers; the model calls `web_search` itself where needed.
4. Both language packs are consulted for safety-class detection (ADR-0033: data in
   `monoagens/library/<kind>/<lang>.json`).
5. A detector that has no effect on the decision is removed or made to count.

## 3. Options considered

**A. Add exceptions per false case.** Rejected: an endless list.

**B. Replace all detectors with a model call.** Rejected: cost, latency, and a model on the crisis path.

**C. Token-boundary matching, value-shaped secrets, narrower triggers, evidence that counts.** Accepted.

## 4. Decision

### 4.1 Matching

All word-list detectors match whole tokens (`\b` or a token set). Substring tests such as
`word in body` are removed from `turn/research.py`, `guard/patterns.py`, and `memory/evidence.py`.

### 4.2 Secrets

`SENSITIVE` matches a credential name followed by `:` or `=` and a value of six or more characters, or a
known prefix (`sk-`, `ghp_`, `AIza`, `xox[baprs]-`). A bare "token" or "password" is not a secret. The
gate also reads `safety(language).sensitive` so "kata sandi" is covered. A stored text that is later
labelled `not_worth` for a credential has its `memories.text` cleared.

### 4.3 Research trigger

Polysemous words ("sekarang", "hari ini", "hasil", "jadwal", "kontak", "alamat", "obat", "harga",
"kurs") leave the forced trigger. `EXACT` keeps currencies, percentages, and full dates, and drops clock
times and numeric ranges. The trigger consults both language packs.

### 4.4 Crisis, injection, privacy

- Crisis phrases check a negation window and an idiom list per language before firing.
- The injection regex gets a right word boundary and drops terms that are common technical vocabulary
  ("system prompt", "developer mode") unless paired with an override verb.
- Privacy patterns in `library/privacy/*.json` are rewritten so `pukul`, `aku yang salah`, `sakit`,
  `putus`, `jatuh`, `kill`, `hit`, `sick`, and ISO dates need a context word. `or risky` is removed from
  the auditor's mention selection. Policy reads the entity and centroid layers, or they are not run.
- `_regex_answer` accepts only an explicit answer: a lone `0`–`4`, a level word as the whole message, or
  `/privacy`.
- `forget` removal filters stopwords from the token overlap and needs two content tokens.

### 4.5 Sandbox screen

`BLOCKED_RE` ignores arguments that are plain file names; read-only heads are checked against the
workspace and write flags (ADR-0049 §4.1.6).

## 5. Configuration

No new key. Lists live in `monoagens/library/{lexicon,privacy,safety}/<lang>.json`.

## 6. Metrics and gates

- A false-detection fixture of the sentences in §1, run per detector, expects no hit.
- A true-positive fixture keeps every real case (real keys, real crisis phrases, real injections).
- `scripts/neutral.py --check` stays green.

## 7. Acceptance criteria

- [ ] The sentences in §1 pass through as plain chat in both languages.
- [ ] "kata sandi saya hunter2" never reaches `memories.text`.
- [ ] No `word in body` substring test remains in the three detector modules.
- [ ] Each privacy layer either changes a level or is not computed.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| A real crisis phrase is missed | The negation window narrows only listed negations; the true-positive fixture gates it |
| A secret slips through the narrower filter | Value-shaped patterns plus the language pack; the LLM guard stays the second layer |
| Context words reduce privacy recall | The eval of ADR-0038 reports recall and precision per label |

## 9. Interaction with other ADRs

ADR-0038 keeps evidence before level and a deterministic policy; this ADR fixes the evidence data.
ADR-0043 keeps the contract; only the trigger narrows. ADR-0033 stays binding on where word lists live.

## 10. Code references

- `monoagens/engine/turn/research.py`
- `monoagens/engine/guard/patterns.py`, `sandbox.py`
- `monoagens/engine/memory/evidence.py`, `gate.py`, `intent.py`
- `monoagens/engine/access/privacy.py`
- `monoagens/library/privacy/`, `monoagens/library/lexicon/`, `monoagens/library/safety/`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0052-detector-precision.md`.*
