# ADR-0044: Provider Wire Adapters — Native Gemini First, One Seam

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0027 (clean by default), ADR-0031 (module structure), ADR-0033 (neutral core), and ADR-0041 (voice configuration); replaces the assumption that every provider must speak the OpenAI wire with a protocol adapter boundary.
- **Supersedes:** —
- **Related:** ADR-0027 (distribution), ADR-0031 (module structure), ADR-0033 (neutral core), ADR-0041 (voice configuration), ADR-0045 (mode sampling)
- **Implementation:** draft — the adapter boundary, the native Gemini client, and the generic sampling fields are recorded; no runtime adapter is implemented in this ADR.
- **Location note:** the provider seam binds the engine LLM loop, so this ADR sits under `adr/engine/`. The Indonesian copy is `docs/v0.1.0/adr/engine/id/0044-provider-wire-adapters.md`.

---

## 1. Context

The engine currently has one client path: `LLM.__init__` builds `AsyncOpenAI`, and every request goes
through `client.chat.completions.create`. The provider package holds endpoint and auth facts only. That
works for the OpenAI wire, but it hides native controls:

| Surface | Current behaviour | Gap |
| --- | --- | --- |
| Gemini | Google's OpenAI-compatibility shim | no `safetySettings`, no `topK`, no native block reason |
| Anthropic | Anthropic's OpenAI-SDK compatibility layer | no prompt caching, no thinking summary, limited tool semantics |
| OpenAI-compatible providers | native OpenAI wire | correct as-is; no native client needed |
| Sampling | `temperature` only | `top_p`, `top_k`, and `min_p` are not configurable |
| Safety | no request-level switch | provider moderation cannot be addressed by the engine |
| Errors | OpenAI exception shape | native block and safety outcomes are not represented |

The owner direction is to make provider control explicit: native where the native API exposes a knob
the engine needs, and one wire protocol rather than one client per brand. The request is not to write a
separate loop for every provider; it is to stop hiding a native capability behind a shim.

The engine stays provider-neutral. A provider name is selected in `engine.llm.provider`; the engine
never branches on that name. The adapter is selected by the wire protocol recorded in the provider
record, and the adapter owns only request/response translation.

## 2. Decision drivers

| Driver | Consequence |
| --- | --- |
| Native controls where needed | Gemini can receive `safetySettings`, `topK`, and native block reasons |
| One turn loop | Every adapter normalizes into the same text, tool-call, usage, and finish shape |
| One wire family, not one brand | OpenAI-compatible providers keep the existing client |
| No provider-name branches in core | `llm.py` asks the adapter for capabilities; it does not compare names |
| Generic config | Sampling and safety are engine fields; unsupported values fail at load |
| No silent drop | An option the adapter cannot send is a configuration error, not ignored |
| No new loop | Retry, deadline, budget, checkpoints, and metrics stay in `llm.py` |
| Testable without network | Fixture requests and responses cover each adapter |
| Small dependency surface | Native adapters use the existing HTTP stack; no provider SDK is required |

## 3. Options considered

| Option | Summary | Value |
| --- | --- | --- |
| **A. Keep every provider on the OpenAI shim** | the current path | Rejected: Gemini safety and native finish reasons stay inaccessible |
| **B. Native client per brand** | Google SDK, Anthropic SDK, OpenAI SDK, separate loops | Rejected: duplicate tool, retry, deadline, and accounting behaviour |
| **C. Adapter per wire protocol** | OpenAI wire, Gemini `generateContent`, Anthropic Messages | **Chosen** |
| **D. Native for every provider** | one client per provider even when the wire is identical | Rejected: maintenance cost with no added control |
| **E. Proxy or sidecar** | a separate process translates native APIs | Rejected: a new runtime component for a request/response mapping |
| **F. Free-form provider options** | pass an arbitrary mapping to the provider | Rejected: no validation, no capability boundary, and a second config convention |

## 4. Decision

### 4.1 Three wire families

The provider record gains a `wire` value:

| Wire | Providers | Native adapter |
| --- | --- | --- |
| `openai` | OpenAI, OpenRouter, Groq, DeepSeek, Ollama, OpenCode, custom | no change; `AsyncOpenAI` |
| `gemini` | Google Gemini | **first native adapter** |
| `anthropic` | Anthropic Claude | native adapter when its features are needed |

The wire value is data in the provider record. It is not a branch in `llm.py` on a provider name.

### 4.2 Adapter boundary

An adapter owns only:

- request payload construction for one call;
- response normalization into text, tool calls, usage, and a finish reason;
- a capability report for the options it can send;
- native error classification that the LLM loop can map.

The adapter does not own retry, the turn deadline, the call budget, checkpoints, `llm_calls` writes, or
the tool registry. Those stay in the existing LLM loop.

The normalized response is an internal value with at least:

| Field | Meaning |
| --- | --- |
| `text` | assistant text, empty when the response is only a tool call |
| `tool_calls` | normalized calls with name, arguments, and id |
| `usage` | input and output token counts when the provider reports them |
| `finish_reason` | a normalized value and the native value |
| `blocked` | true when the provider refused or returned no candidate |

A blocked response is a first-class result, not an exception with a string. The turn can then report a
provider refusal without pretending a tool call or an answer happened.

### 4.3 Native Gemini

The Gemini adapter uses `generateContent`, not the OpenAI compatibility endpoint:

- endpoint shape: `v1beta/models/{model}:generateContent`;
- auth: the existing key variable in the `x-goog-api-key` header;
- messages: `contents` with role and parts;
- system prompt: `systemInstruction`;
- tools: `functionDeclarations` and `functionCall` results;
- sampling: `generationConfig.temperature`, `topP`, `topK`, and max output tokens;
- safety: `safetySettings` with the adjustable harm categories when the engine asks for `off`;
- response: candidates, finish reason, usage metadata, and block reason.

Safety `off` is not a promise that the model will comply. It removes the API threshold for the
categories the adapter sends; the model's own behaviour and the provider's terms still apply. A
provider rejection remains a visible error. Gemini documents that the adjustable filters default to
off for Gemini 2.5 and later, while core harms such as child safety are always blocked and cannot be
adjusted.

The other providers do not expose the same request-level control:

| Provider | Request-level safety | What a native adapter adds |
| --- | --- | --- |
| OpenAI | no threshold in Chat Completions; moderation is a separate classifier or inline score | Responses API and native tool semantics, not an off switch |
| Anthropic | no threshold; a refusal is `stop_reason: refusal` | caching, thinking, native tools, not an off switch |
| OpenRouter | guardrails can add block/redact rules and prompt-injection checks | routing and organization controls, not a way to disable an upstream provider's policy |
| Groq | moderation models are separate calls | no request-level safety threshold |
| DeepSeek | the public Chat Completions surface documents no safety threshold | no request-level safety threshold |
| Ollama | local inference; no API moderation layer | native sampling options and local control, not a provider filter |
| Gemini | `safetySettings` categories and thresholds | the only first-cut adapter that carries a safety threshold |

A native API is not automatically worth an adapter. It is worth one only when it exposes a control the
engine needs, or when the compatibility layer blocks a needed feature.

### 4.4 Native Anthropic, deferred

The Anthropic adapter is not part of the first cut. The compatibility layer stays until the owner needs
native prompt caching, thinking summaries, or native tool semantics. When it lands, it follows the same
adapter boundary: Messages API, top-level system, `input_schema` tools, `tool_use` and `tool_result`
blocks, and native usage accounting.

### 4.5 Compatible providers stay on the OpenAI wire

OpenRouter, Groq, DeepSeek, Ollama, OpenCode, and custom endpoints keep the OpenAI client. A native
client for these providers adds no control the engine needs, and the compatibility path already carries
tools and streaming-compatible message shapes.

### 4.6 Generic sampling and safety config

The engine exposes generic fields; the adapter validates what it can send. The boundary follows the
soul/engine split:

| Key | Default | Meaning |
| --- | --- | --- |
| `soul.voice.sampling.daily_temperature` | `0.9` | daily chat temperature |
| `soul.voice.sampling.professional_temperature` | `0.3` | task-mode temperature |
| `soul.voice.sampling.top_p` | empty | omitted means the provider default |
| `soul.voice.sampling.top_k` | empty | Gemini only in the first cut |
| `soul.voice.sampling.min_p` | empty | only when the adapter reports support |
| `engine.llm.utility_temperature` | `0.2` | decisions, summaries, and extraction |
| `soul.voice.safety` | `inherit` | `inherit` or `off`; `off` is adapter-gated |

The daily, professional, and sampling values are behavior choices, so they live with the soul. The
utility temperature is an operational call (decision, digest, extraction), so it stays in the engine.
`engine.llm` keeps the provider, model, timeout, retry, budget, and reasoning effort; it does not carry
a second copy of the behavior temperatures.

Empty numeric fields are omitted from the request. A non-empty value that the selected adapter cannot
send is a configuration error at load, not a silent no-op. A provider-side rejection after a valid
request is a runtime error with the native reason.

`soul.voice.safety: off` is accepted only when the adapter reports the capability. For Gemini, `off`
maps to the current `off` threshold (the API name that replaced `BLOCK_NONE`) for the adjustable harm
categories; core harms stay blocked. For an adapter without the capability, load fails with a message
naming the provider and the unsupported value. `inherit` means the provider default; there is no second
`engine.llm.safety` value.

The daily and professional values are owned by ADR-0045; this ADR defines the transport of the values,
not the character policy.

### 4.7 No provider-name vocabulary in the engine

`llm.py` receives a wire and an adapter; it does not import a provider module by brand and does not test
a provider string. The provider package may hold the adapter and the endpoint facts, but it imports no
engine module.

The existing rule that a different wire protocol needs an adapter, not a data entry, is kept. What
changes is that the adapter may now carry request/response code, not only endpoint metadata.

### 4.8 Non-streaming first

The first native adapter is non-streaming. The current loop already consumes a complete response and
records it; streaming is a separate decision with its own delivery and cancellation contract.

## 5. Configuration

The existing provider selector stays:

```yaml
engine:
  llm:
    provider: google
    model: <operator choice>
    utility_temperature: 0.2

soul:
  voice:
    sampling:
      daily_temperature: 0.9
      professional_temperature: 0.3
      top_p: 0.95
      top_k: 40
      min_p: null
    safety: inherit
```

`provider` and `model` are required and have no code default. The behavior sampling values are defaults,
not fixed policy: the operator may change them. A value the selected wire cannot carry is rejected when
the config is loaded.

## 6. Metrics and gates

| Metric | Target |
| --- | ---: |
| Wire families | 3 |
| Native adapters in the first cut | 1 |
| Provider-name comparisons in engine code | 0 |
| Unsupported sampling values silently dropped | 0 |
| Native responses normalized to one shape | yes |
| Live network calls in adapter tests | 0 |
| Existing OpenAI-wire tests changed for behaviour | 0 |
| `pytest -q` | pass |
| `ruff check` | pass |
| `python -P scripts/types.py --check` | pass |
| `python -P scripts/adr.py --check` | pass |
| `python -P scripts/adr.py --translate-check` | pass |
| `python scripts/selfcheck.py --check` | pass |

## 7. Acceptance criteria

- [ ] `ProviderDefaults` carries a wire and `llm.py` selects an adapter without a provider-name branch.
- [ ] One normalized response type carries text, tool calls, usage, finish reason, and blocked state.
- [ ] Gemini can send `safetySettings` when `soul.voice.safety: off` and `topK` when configured.
- [ ] Gemini block reasons and empty candidates are visible to the turn, not reported as an answer.
- [ ] OpenAI-compatible providers keep the existing request and tool behaviour.
- [ ] Anthropic stays on the compatibility layer until a native adapter is accepted.
- [ ] `top_p`, `top_k`, and `min_p` are sent only when supported; unsupported values fail at load.
- [ ] Adapter tests use fixture requests and responses and never call a live endpoint.
- [ ] A native provider error still records one `llm_calls` row with the correct purpose.
- [ ] Daily, professional, sampling, and utility temperatures are operator-configurable.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Native API drift | Each adapter has fixture tests and a versioned request shape; a drift fails a test, not production |
| Two tool-call dialects | Normalize both dialects at the adapter boundary and test tool name, arguments, and result ids |
| Native safety rejection | Keep the provider error visible; `off` is not a guarantee of compliance |
| Unsupported sampling | Validate at load and name the provider and field; never silently drop |
| A new HTTP dependency | Use the existing HTTP stack where possible; if a direct dependency is added, record its license row |
| Token accounting differs | Use the native usage object when present; keep the existing empty-value fallback |
| Duplicate retry logic | Adapters never retry; `llm.py` owns retry and deadline |
| Provider-name branches return | The provider record and adapter capabilities are the only dispatch surface |

## 9. Interaction with other ADRs

- **ADR-0027**: native Gemini is not installed by default; the provider remains an operator choice.
- **ADR-0031**: the adapter boundary stays beside the provider package and the LLM seam; the engine module
  budget does not change.
- **ADR-0033**: no language, culture, transport, or vendor vocabulary enters the mechanism; Gemini
  category constants live in the Gemini adapter.
- **ADR-0041**: voice configuration is unaffected; the voice layer still consumes the engine.
- **ADR-0043**: research and citations are unchanged; a native provider does not become a source of truth.
- **ADR-0045**: daily and professional temperature defaults are defined there; this ADR carries them to
  the adapter.

## 10. Code references

- `eidesoul/providers/base.py:12-18` — `ProviderDefaults` and the future `wire` field.
- `eidesoul/providers/__init__.py:19-54` — provider registry and `resolve`.
- `eidesoul/providers/gemini.py:1-13` — the current OpenAI compatibility entry.
- `eidesoul/providers/anthropic.py:1-15` — the current compatibility layer and its caveats.
- `eidesoul/engine/llm.py:173-183` — `AsyncOpenAI` construction.
- `eidesoul/engine/llm.py:287-323` — the single provider call and its recording.
- `eidesoul/engine/llm.py:368-379` — OpenAI request kwargs and `temperature`.
- `eidesoul/engine/llm.py:590-625` — the tool-free `complete` call and its temperature.
- `eidesoul/engine/config.py:147-160` — `LLMConfig`.
- `eidesoul/admin/templates.py:100` — the generated engine temperature.
- `pyproject.toml:9-28` — the dependency set.
- `tests/test_providers.py` — provider registry coverage.
- `tests/test_llm.py` — the existing LLM loop coverage.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0044-provider-wire-adapters.md`.*
