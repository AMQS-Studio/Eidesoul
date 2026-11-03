# ADR-0018: Gateway Contract — Core, Rich, Directory

- **Status:** accepted
- **Implementation:** implemented — evidence: `GatewayCore`/`Rich`/`Directory`, `send_sticker`, `participants`, boot check + `gateway_capabilities`, degradation tests, harness body, and the participants refusal wired into `chat_history`/`send_to` (replacing their old cross-chat check)
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** review (`Eidesoul-Review-Arsitektur-2026-11-01.md`) §15 P1-6 — one Protocol holding 16 methods that mix transport, directory, policy, and data; two methods never used by the engine; every gateway is forced to stub all of them; `send_reaction` actually sends stickers; ADR-0007 needs `participants(thread_id)`, which is not in the contract
- **Supersedes:** —
- **Related:** ADR-0007 (`rooms_of`, `participants`, fail closed), ADR-0015 (capability index, `self_handles`, `render`, `prompt_hints`), ADR-0013 (send path), ADR-0011 (send ledger)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0018-gateway-contract.md`.

---

## 1. Context

`inbound.py:31-72` defines one `Protocol` with 16 methods. Its contents are three different concerns:

| Group | Method | Used by the engine? |
|---|---|---|
| Transport | `send_text`, `send_file`, `send_reaction`, `typing`, `prepare_media`, `render` | yes, except `send_reaction` (used at `agent.py:354`) |
| Directory/identity | `chat_name`, `self_handles`, `resolve_target` | yes (`chat_name` 8×, `self_handles` 3×) |
| Dead directory | `list_targets`, `data_paths` | **never** |
| Policy/topology | `prompt_hints` | yes (2×) |
| Lifecycle | `run`, `stop`, `tools`, `commands` | yes |

Three consequences:

1. **Mandatory stubs.** `cli/gateway.py:88-140` must provide all sixteen methods; half of them are `pass` or `return []`. A new gateway pays the same cost for capabilities it does not have.
2. **A dead surface.** `list_targets` and `data_paths` are called by no one in `eidesoul/` — a contract that is unused but binding.
3. **Names that lie.** `send_reaction` sends a **sticker** (`whatsapp/gateway.py:348-356` reads `reaction_stickers`), not a reaction to a message.
4. **A seam that does not exist yet.** ADR-0007 §4.5 requires `participants(thread_id)` as the source of truth for membership; that method is not in the contract, so its implementation will be born as a wild attribute per gateway.

## 2. Decision drivers

- The gateway is an **appendage**: transport only, it must not force capabilities it does not have.
- Capabilities that do not exist must **fail visibly** (explicit degradation), not `pass` silently.
- Cross-room is mandatory fail closed: without membership evidence, the read is rejected (ADR-0007).
- One testable contract: a gateway that does not meet the Core fails at boot, not on the first message.

## 3. Options considered

| Options | Contents | Cost | Risks | Verdict |
|---|---|---|---|---|
| **A. Core + Rich + Directory** | three protocols; the engine checks them with `isinstance` (`runtime_checkable`) | medium (two gateways touched) | a gateway author must read three names | **Selected** |
| **B. One Protocol + `capabilities: frozenset[str]`** | stringly-typed flags | small | stubs remain mandatory; a flag typo escapes until runtime | Rejected |
| **C. Minimal** | drop two dead methods, rename one | small | mandatory stubs and `participants` still have no home | Rejected |

## 4. Decision

### 4.1 Three protocols

| Protocol | Contents | Mandatory? |
|---|---|---|
| `GatewayCore` | `name`, `tools`, `commands`, `run`, `stop`, `send_text`, `render`, `prompt_hints`, `self_handles`, `chat_name` (default: `thread_id`) | **mandatory** |
| `Rich` | `send_file`, `send_sticker`, `typing`, `prepare_media` | optional |
| `Directory` | `resolve_target(query)`, `participants(thread_id) -> set[str] \| None` | optional |

- `list_targets` and `data_paths` are **deleted** from the contract (no caller).
- `send_reaction` is **renamed** to `send_sticker` — one name for one behavior.
- `tools`/`commands` stay in Core because the engine assembles the catalog from the gateway (ADR-0015 §4.2).

### 4.2 Explicit degradation

| State | Behavior |
|---|---|
| Gateway without `Rich` | media replies fall back to text (file name + contents, if text); `typing` is skipped; the sticker is not sent |
| Gateway without `Directory` | the target name cannot be resolved → the soul asks for an id; cross-room reads are **rejected** (fail closed) |
| `participants()` returns `None` | treated as "unknown" → reject cross-room, do not guess (ADR-0007) |
| `participants()` returns a set | used as is as evidence of the requester's membership |

### 4.3 Check on boot

- The engine checks that every registered gateway meets `GatewayCore`; failure → a boot error naming the missing method.
- `Rich`/`Directory` are **not** checked; their absence is legitimate and triggers the degradation above.
- The result of the check is recorded as `gateway_capabilities` in the boot log (ADR-0019) — the operator knows what is missing before users find it.

### 4.4 A single send path

- Every send goes through `send` (ADR-0007 register) or `send_text` directly from the agent; `Rich` adds no permission path.
- The primary gate (ADR-0017 §4.4) applies before `send_text`/`send_file`; `Rich` may not bypass it.
- The ledger `outbound_messages` (ADR-0013) records every send, including those that fall back to text because `Rich` is absent.

## 5. Configuration

There is no new configuration key in this ADR. Gateway capabilities are properties of the code, not operator settings.

## 6. Metrics and gates

| Metric | Definition | Gate |
|---|---|---|
| `gateway_core_ok` | gateway passed the Core check at boot | 1 for every registered gateway |
| `rich_missing_total` | media replies that fell back to text | visible, not silently zero |
| `directory_missing_total` | cross-room reads rejected for lack of `Directory` | 0 on WhatsApp; > 0 is allowed on the CLI |
| `participants_unknown_total` | `participants()` returned `None` on cross-room | rejected, never guessed |

## 7. Acceptance criteria

- [ ] A gateway that meets only `GatewayCore` can run a full text turn (test with a fake gateway).
- [ ] Gateway without `Rich`: media replies become text, `typing` is skipped, no exception.
- [ ] Gateway without `Directory`: cross-room is rejected, not guessed.
- [ ] `participants()` returns `None` → reject; returns a set → permission follows the set.
- [ ] A gateway that does not meet the Core fails at boot, with the missing method name in the error message.
- [ ] No reference to `send_reaction`, `list_targets`, or `data_paths` remains in `eidesoul/engine/` or `eidesoul/gateways/`.
- [ ] WhatsApp implements `Rich` + `Directory`; the CLI implements Core (and `Rich` as far as possible), with no empty stubs for what is unused.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| `isinstance` on `runtime_checkable` only checks method names, not signatures | the contract test calls each method with real arguments; signatures are checked through the boot error message |
| Gateway authors confused by three protocols | table §4.1 in this ADR + one minimal example in the `inbound.py` docstring |
| Degradation hides features that could actually be used | counters `rich_missing_total` and `directory_missing_total` in `/status` |
| The contract change breaks a third-party gateway | the contract is not released yet (pre-0.1.0); no shim (ADR-0014/AGENTS) |

## 9. Interaction with other ADRs

- **ADR-0007** — `participants(thread_id)` becomes an official method, and its absence automatically fails closed.
- **ADR-0015** — `self_handles`, `render`, `prompt_hints` stay in Core because the capability index and the reply format depend on them.
- **ADR-0013** — the send path and the ledger are unchanged; only the contract surface is narrowed.
- **ADR-0017** — primary gates apply on every send path, including `Rich`.
- **ADR-0011** — a failed send still goes to the ledger/dead-letter as before.

## 10. Code references

- `eidesoul/engine/inbound.py:31-72` — the 16-method Protocol (before it is split).
- `eidesoul/engine/agent.py:311,335,354` — calls to `send_text`, `send_file`, `send_reaction`.
- `eidesoul/engine/agent.py:763,977` — `prompt_hints` in the payload.
- `eidesoul/gateways/cli/gateway.py:88-140` — the sixteen-method stub (evidence of the fat contract's cost).
- `eidesoul/gateways/whatsapp/gateway.py:348-356` — `send_reaction` that sends the sticker.
- `eidesoul/engine/access/permissions.py:48` — `command_allowed` (command gate, unchanged).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0018-gateway-contract.md`.*
