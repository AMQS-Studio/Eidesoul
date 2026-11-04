# ADR-0038: Layered Privacy — Evidence Before Level, Deterministic Policy, Advisory Models

- **Status:** accepted
- **Date:** 2026-11-04
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** extends ADR-0006 (group privacy and pre-filter), ADR-0008 (memory integrity), ADR-0007 (approval classes), ADR-0010 (reflection and disputes); follows ADR-0033 (neutral core)
- **Supersedes:** —
- **Related:** ADR-0001 (single memory port), ADR-0011 (durability), ADR-0015 (capability), ADR-0020 (skills), ADR-0035 (app scope), ADR-0036 (roles)
- **Implementation:** draft — the layer contract, the evidence record, and the policy authority are decided here; the engine still lets the LLM propose a level without a stored evidence trail, has no semantic sensitivity classifier, and does not record a policy version per decision.
- **Location note:** an engine decision (it owns classification, disclosure policy, and audit), so it sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0038-privacy-layers.md`.

---

## 1. Context

The engine already has the right skeleton: a numeric level 0–4 on every memory (`memories.level`,
`schema.py:585`), deterministic level math, veto precedence, and disclosure verdicts in
`memory/privacy.py`, an agent-facing hook in `access/privacy.py`, and L3/L4 confirmation through
`approvals`. Credentials are never stored as memories. What is weak is **where the label comes
from** and **what evidence survives it**:

- The LLM proposes a provisional level; the deterministic layer decides disclosure. A reviewer
  cannot see which detector said what, with which score, under which policy version.
- Calibration of LLM scores is not universal: it depends on the task and the domain (ICML
  2026, `Confidence is Not Universal`). A single scalar is not an audit argument.
- Semantic classifiers can over-block. SafeGPT reports 40.5% precision and 78.6% false positives
  in its enterprise scenario for a knowledge-graph classifier, while deterministic pattern
  matching supplied most of the recall on structured PII (`arXiv:2601.06366`).
- Prompt injection and sensitive information disclosure are the top two data-exposure risks in the
  OWASP LLM Top 10 2026; the control is classification-based policy, not an instruction inside the
  prompt.
- Regulators ask for records, not intentions. EU AI Act Article 12 (automatic logging) and Article
  19 (record fields) apply to high-risk systems; the transparency phase applies from 2 August 2026,
  while the Annex III high-risk date moved to 2 December 2027. ISO/IEC 42001:2026 and Gartner's
  2026 AI TRiSM guidance both push governance from documents to **enforceable runtime controls**.
- Enterprises already run DLP and sensitivity labels (for example Microsoft Purview classifies
  prompts and responses, audits AI activity, and applies retention). A privacy layer that cannot
  feed those systems is a new silo, not a control.

The missing piece is not a bigger model. It is a **control plane**: layered detectors, an evidence
record, one deterministic policy, and a human path for the highest levels.

## 2. Decision drivers

1. **Deterministic authority.** A model may propose; only code decides the stored level and the
   disclosure action.
2. **Evidence over scores.** Every label carries its detector, version, span, and score.
   A bare `L4` is not reproducible.
3. **Local by default.** PII and secrets are classified before any external call; L4 and
   credentials never leave the host raw.
4. **Fail closed and abstain.** A low score becomes `needs_review`, never `allow`.
5. **Smallest model that closes a measured gap.** Regex and NER first; encoders or token
   classifiers next; a generative SLM only when evaluation proves the gap.
6. **One policy, one version.** Disclosure rules live in code/policy-as-code, not in a prompt.
7. **Replaceable layers.** Each detector is an interface with a digest; a layer that adds no
   measured recall is removed.

## 3. Options considered

**A. Keep the LLM as the classifier and authority.** Rejected: non-deterministic, uncalibrated
across tasks, injectable, and impossible to audit.

**B. One local generative SLM for everything.** Rejected as the default: heavy for every turn,
still uncalibrated, and no better than a token classifier for spans. Accepted only as an optional
residual detector behind the same evidence contract.

**C. Deterministic rules only.** Rejected: regex matches identifiers, not meaning; it cannot detect
a self-incriminating admission, a health disclosure, or an interpersonal conflict.

**D. External DLP only.** Rejected as the sole answer: it moves the data out and cannot see the
turn's provenance, veto, or subject. Accepted as an integration target.

**E. Layered detectors, evidence, deterministic policy, human review (chosen).**

## 4. Decision

### 4.1 The layers

| Layer | Examples | Authority |
| --- | --- | --- |
| **Deterministic** | regex, checksum, dictionary, credential patterns | sets a floor; can raise, never lower |
| **Entity/span** | Presidio/spaCy NER, GLiNER2, OpenAI Privacy Filter | evidence with spans; no verdict |
| **Semantic** | MiniLM + trained head, GLiNER2 classification, optional local SLM | evidence with labels; may abstain |
| **Policy** | `memory/privacy.py` and a versioned policy module | final level and action |
| **Human** | approvals for L4, confirmation for L3 in a group, review queue | required for the highest risk |
| **Residual LLM** (optional) | external or local LLM after redaction | evidence only; never final |

The order is a routing order, not a pipeline that every text must traverse. Cheap layers resolve
most cases; only unresolved text climbs. A layer may return `abstain`; it may not return `allow`
below a deterministic floor.

Semantic labels include at least: `credential`, `health`, `financial`, `crime_admission`,
`self_harm`, `violence`, `sexual`, `relationship_conflict`, `third_party_personal`, `legal`, and
`none`. The taxonomy is data, versioned with the policy; adding a label is a policy change, not a
code branch.

### 4.2 The evidence record

Each detector returns:

```text
Evidence:
  detector:   regex | checksum | ner | encoder | token_classifier | slm | llm
  version:    detector or model version
  digest:     sha256 of weights/config when a model is used
  label:      taxonomy label
  score: 0..1
  span:       [start, end] or null for whole-text labels
```

A memory stores the evidence array and the final policy result:

```text
PrivacyDecision:
  level:          0..4
  action:         allow | redact | block | ask | drop
  policy_version: string
  approver:       person id or empty
  expires_at:     epoch or null
```

`memories.privacy_evidence` (JSONB, default `[]`) is the persisted evidence array; `memories.level`
stays the final level. The disclosure decision is appended to the action ledger with the same
policy version. A migration adds the JSONB column; no second store is introduced.

### 4.3 Deterministic composition

- The deterministic floor is `max()` of all deterministic evidence; no later layer lowers it.
- A model may raise the level, or abstain; it may not set `level` directly.
- `needs_review` is a state, not a level. A low score routes to it; a reviewer may downgrade
  with a recorded reason and approver.
- Derived memories (summaries, reflections, skills) inherit the **maximum** level of their sources;
  a downgrade requires redaction, not a new model opinion.
- Veto by the subject wins over every detector and every policy.
- Credentials (`credential`/`secret`) are never stored; the action is `drop`, not `L5`.

### 4.4 Semantic sensitivity, not just PII

Entity detection answers “who/what is named”. It does not answer “what did this person admit or
feel”. The semantic layer exists for that gap:

| Example | Label | Default policy |
| --- | --- | --- |
| “aku tadi nyuri” | `crime_admission` | L4, review, retention override; legal review |
| “aku gak suka banget dia” | `relationship_conflict` about a third party | L3, subject veto, no third-party disclosure |
| “aku sakit…” | `health` | L4, restricted |
| “passwordku …” | `credential` | drop, never store |

These are policy defaults, not classifier facts. Sarcasm, quotation, hypotheticals, and reported
speech require an `ambiguous`/`hypothetical` label or abstention; the conservative branch wins.
Coreference to a third person (“dia”) defaults to `third_party_personal`, not to a public fact.

### 4.5 Data movement

- Deterministic, entity, and semantic layers run locally by default.
- L4 and credentials are never sent raw to an external model. Redaction or tokenization happens
  before the call; rehydration happens only in the trusted process.
- An external residual LLM is off by default. When enabled, it receives redacted text, no tools, a
  schema-constrained output, and a provider with a DPA, region pinning, no-training terms, and an
  auditable retention setting.
- A fully local deployment (regex + NER + encoder + optional local SLM) must remain possible; no
  feature may require an external model.
- Model weights, thresholds, and policy versions are data with digests; loading an unverified
  model is a boot failure.

### 4.6 Audit

Every disclosure and every level change records:

- decision id, timestamp, actor, thread, source message id;
- content hash, labels, spans, scores, detector versions and digests;
- policy version and hash;
- action, approver, expiry, and retention class;
- model/provider identity when an external model was used.

The record is append-only (action ledger or an equivalent tamper-evident series), exportable to
SIEM, and mapped to EU AI Act Article 12/19, ISO/IEC 42001, and the NIST AI RMF. The record must be
sufficient to reconstruct the decision without the model.

### 4.7 Evaluation

A privacy golden set covers, per language and per label:

- structured PII, credentials, and checksum-valid identifiers;
- health, financial, criminal, sexual, self-harm, violence, and relationship disclosures;
- Indonesian colloquial and code-mixed text;
- sarcasm, quotation, hypotheticals, and reported speech;
- prompt injection that tries to lower a level or reveal the policy;
- cross-thread and aggregation leaks;
- false positives that would block ordinary conversation.

Metrics: precision, recall, FPR per label, calibration error, abstention rate, and review-queue
volume. A 30-day detection-only mode is allowed before enforcement. A model update requires a new
evaluation; a policy update requires a new policy version and a re-run of the gate.

### 4.8 What does not change

- The four stored levels and their disclosure ceilings (ADR-0006).
- Veto precedence and subject identity.
- `MemoryStore` as the single write port and `memories_readable` as the read path (ADR-0001).
- The approval continuation for L3/L4 (ADR-0007).
- No credential is stored.
- The LLM may still help phrase a confirmation question; it does not decide the level.

## 5. Configuration

Detector enablement and thresholds are configuration data under `privacy`, with these defaults:

- deterministic + local entity + local semantic: on;
- external residual LLM: off;
- generative SLM: not in the spine; an optional extra/plugin;
- thresholds are per label and per language, never one global number;
- model paths, digests, and policy versions are checked at boot; a missing digest is a failure.

No secret enters a prompt, evidence record, or model. Credentials in text are dropped before
storage.

## 6. Metrics and gates

Counts to drive to zero, measured 2026-11-04:

| Gap | Count | Where |
| --- | --- | --- |
| LLM writes `level` without evidence | 1 path | `memory/store.py` labeling path |
| decision records without policy version | all disclosures | `access/privacy.py`, `approvals.py` |
| semantic labels for sensitivity | 0 | no taxonomy today |
| external-model calls without redaction | 0 allowed | new gate |
| derived-level downgrades | 0 allowed | memory reflection/merge |

The gate:

1. A detector result cannot set `level`; only the policy does.
2. The deterministic floor cannot be lowered by a model; a human downgrade requires an approver and
   a reason in the record.
3. L4/credential text never reaches an external provider; a fake provider records zero raw calls.
4. A low score yields `needs_review`; no path defaults to `allow`.
5. Derived memories never have a lower level than their highest source.
6. A prompt injection fixture cannot change the policy, the level, or the disclosure action.
7. Every disclosure has the required audit fields and a policy version/hash.
8. The golden set reports per-label precision/recall/FPR and calibration; a regression is a gate.
9. A fresh install runs with no external LLM and no SLM.
10. `pytest -q`, `ruff`, `pyright`, `selfcheck`, and the ADR gates stay green.

## 7. Acceptance criteria

- `memories.privacy_evidence` exists, is populated by the detectors, and is queryable for an audit.
- `level` is written only by the policy; every model output is evidence.
- A credential is dropped; an L4 decision requires approval; an L3 group disclosure requires
  confirmation; veto wins.
- The semantic taxonomy covers the labels in §4.4 and is versioned with the policy.
- An external residual model is off by default and cannot receive raw L4/credential text.
- The audit record can reconstruct a decision without calling the model again.
- The golden set and its per-label metrics are part of the repository.
- The fully local deployment passes the smoke test without any network model.

## 8. Risks and mitigations

- **Over-classification blocks ordinary talk.** Mitigation: per-label thresholds, review queue,
  and a measured FPR target; L2 stays fluid.
- **Semantic false negatives on colloquial Indonesian.** Mitigation: local golden set, few-shot
  encoder/SetFit head, abstention, conservative default for third-party and criminal labels.
- **A model update silently changes behavior.** Mitigation: digest/version pin, evaluation gate,
  policy version, and a rollback path.
- **External residual model leaks data.** Mitigation: off by default, redaction before call, no raw
  L4, DPA/region/no-training, and a fake-provider test.
- **Audit volume becomes unmanageable.** Mitigation: hash content, store spans and versions, not
  raw text; retention policy for the record itself.
- **Legal overreach (criminal/health data).** Mitigation: policy defaults, legal review, retention
  override, and a `drop` action for data the operator must not keep.

## 9. Interaction with other ADRs

- ADR-0006: the levels, ceilings, and pre-filter stay; this ADR gives the label an evidence trail.
- ADR-0008: provenance, instruction quarantine, and anti-poisoning stay; untrusted content is
  evidence, never policy.
- ADR-0007: approvals remain the continuation for L3/L4; the policy names the class.
- ADR-0010: disputes and veto keep precedence over every detector.
- ADR-0001: the memory port stays single; evidence is a column, not a second store.
- ADR-0033: detectors and labels are data; no language, vendor, or transport default enters the
  core.
- ADR-0035/0036: app scope and roles are orthogonal; a role may read a level, but the level is not
  a role.
- ADR-0034: prompt text never carries the policy; the policy is code.

## 10. Code references

- `eidesoul/engine/memory/privacy.py` — `clamp_level`, `PrivacyVerdict`, `AuditResult`,
  `PrivacyPolicy`.
- `eidesoul/engine/access/privacy.py` — the agent-facing review, audit, ask, and disclosure path.
- `eidesoul/engine/memory/store.py` — the labeling/capture path and the level write.
- `eidesoul/engine/schema.py:575-620` — `memories.level` and the row that will carry
  `privacy_evidence`.
- `eidesoul/engine/access/approvals.py` — the L3/L4 continuation.
- `eidesoul/engine/access/ledger.py` — the action ledger that carries the decision record.
- External: ICML 2026 `Confidence is Not Universal`
  (https://proceedings.mlr.press/v306/jang26a.html); SafeGPT (https://arxiv.org/html/2601.06366v3);
  OWASP LLM Top 10 2026 (https://www.forcepoint.com/blog/insights/owasp-llm-top-10); EU AI Act
  Articles 12/19 (https://artificialintelligenceact.eu/article/12/,
  https://artificialintelligenceact.eu/article/19/); ISO/IEC 42001:2026
  (https://www.iso.org/en/home/insights-news/resources/iso-42001-explained-what-it-is.html);
  Gartner AI TRiSM (https://www.gartner.com/en/articles/ai-governance-trism); Microsoft Purview AI
  controls (https://learn.microsoft.com/en-us/purview/ai-m365-copilot); OpenAI Privacy Filter
  (https://github.com/openai/privacy-filter); GLiNER2 (https://arxiv.org/html/2507.18546).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0038-privacy-layers.md`.*
