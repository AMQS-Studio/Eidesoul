# ADR-0024: Tool Input Contract — Argument Validation, Async Handlers, and Shell `env`

- **Status:** accepted
- **Implementation:** implemented — evidence: `registry` schema keyword subset + coroutine validation, `tools/__init__.py` `argument_errors`/order name→permission→args→safety, computed `shell.env` whitelist, `tests/test_tools_contract.py`
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** the single execution door checked names, permissions, and safety, but arguments were passed on as they were to the handler; synchronous handlers were not rejected at boot; and `shell` inherited the entire environment of the API process — including the API key and the DSN.
- **Supersedes:** —
- **Related:** ADR-0007 (permission class, sensitive patterns, §4.8 credentials), ADR-0015 (capability manifest), ADR-0016 (canonical schema), ADR-0019 (metric), ADR-0021 (tool output contract)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0024-tool-input-contract.md`.

---

## 1. Context

| Fact | Evidence |
|---|---|
| `execute` runs the order name → permission → `fn` empty → safety/approval → execution; arguments are never checked | `monoagens/engine/tools/__init__.py:182-239` |
| The handler is called with `await spec.fn(args, ctx)` | `monoagens/engine/tools/__init__.py:235` |
| Commands are also `await`ed | `monoagens/engine/agent.py:986` |
| The argument schema is only checked **for its shape** at discovery (`parameters` dict, `type: object`, `properties` object) | `monoagens/engine/registry.py:45-53`, `monoagens/engine/registry.py:104-108` |
| `validate()` checks name/alias/family/description, never the type of `fn` | `monoagens/engine/registry.py:70-108` |
| `shell` runs `create_subprocess_shell` without `env=` → the child inherits the process environment | `monoagens/engine/tools/shell.py:10-26` |
| `sandbox` already has a minimal env pattern (container + CLI client) | `monoagens/engine/tools/sandbox.py:18-20,83-85,155` |
| There is no `inspect.iscoroutinefunction` anywhere in `monoagens/` | repository grep |

The consequences today: an argument of the wrong type only fails **in the middle of the handler**, after side effects may have happened; a wrongly shaped schema is only discovered when the tool is used; and `shell` could read `OPENAI_API_KEY`/DSN only because it was in the parent environment.

## 2. Decision drivers

| Driver | Consequences |
|---|---|
| Determinism before side effects | An invalid argument is rejected before the LLM guard and before `fn` |
| Fail closed at boot | An invalid schema and a synchronous handler fail the start, not runtime |
| Zero new dependency | Spirit of `AGENTS.md` — Capability spine: a dependency only for what is truly necessary |
| One door | Validation only in `execute()`/dispatch, not inside the 13 handlers |
| Secrets do not travel to the child | `shell` receives a computed environment, not an inherited one |

## 3. Options considered

| Options | Summary | Value |
|---|---|---|
| **A. Leave it** | Validation handed to each handler | Rejected: scattered, easily forgotten, fails after side effects |
| **B. `jsonschema`** | Third-party validator | Rejected: a new dependency for a subset we use ourselves |
| **C. Stdlib subset validator + coroutine check + env whitelist** | One place, no dependency | **Selected** |
| **D. Validation in the handler** | Each tool checks itself | Rejected: duplication in 13 places, no consistent guarantee |

## 4. Decision

### 4.1 Order at one door

`name → permission → argument → safety/approval → execution`. An invalid argument may not create approval rows and may not call classifier guards: both are expensive and useless for input that will not be executed.

### 4.2 Supported JSON Schema subset

`type`, `properties`, `required`, `enum`, `items`, `minimum`/`maximum`, `minLength`/`maxLength`, `pattern`, `additionalProperties`. A keyword outside this list **fails the boot**, so that this subset really holds and no schema is silently ignored.

### 4.3 `additionalProperties` absent = `false`

A conscious deviation from JSON Schema: a foreign key is rejected by default; a handler that really accepts a free key writes `additionalProperties: true`. The reason is the real path — `shell` accepts `command` and `timeout`, and an unknown extra key is argument smuggling.

### 4.4 Shape of the validation result

`error: invalid arguments for <tool>: <field> (<reason>)` — deterministic, without an LLM, truncated to `MAX_ERROR` (500 characters), naming at most `tools.max_errors` fields. `ctx.used` is not written for a rejected call.

### 4.5 Handlers must be coroutines

`validate()` rejects a non-coroutine `fn` for tools **and** commands; the message names the file (`<source>.py: fn must be async`). The reason: both callers use `await` (`monoagens/engine/tools/__init__.py:235`, `monoagens/engine/agent.py:986`), so a synchronous handler is a bug that is cheaper to find at boot.

### 4.6 The shell `env` is computed, not inherited

`shell` uses the whitelist `engine.shell.env` (default `PATH, HOME, LANG, TZ, XDG_RUNTIME_DIR, TMPDIR, TERM`) following the `client_env()` pattern in `monoagens/engine/tools/sandbox.py:83-85`. The wildcard `*` is rejected at boot; the contents of `.env` are never passed to the child; a variable outside the whitelist does not participate even though it is in the process environment. `sandbox` does not change.

### 4.7 Boundary with credentials

This ADR closes the **env path**. Reading a secret from a file (`printenv`, `.env`, `/proc/*/environ`, SSH keys) is not this ADR's business, but a pattern class in ADR-0007 §4.8.

## 5. Configuration

```yaml
engine:
 tools:
 args: strict # strict: §4.2 validation + additionalProperties defaults to false
 max_errors: 5 # number of fields named in one error message
 shell:
 env: [PATH, HOME, LANG, TZ, XDG_RUNTIME_DIR, TMPDIR, TERM
```

There is no `tools.args: off`: disabling validation means reopening the hole this ADR closed.

## 6. Metrics and gates

| Metric | Start target |
|---|---|
| `tool_args_rejected_total{tool}` | present, per tool |
| `tool_schema_invalid_total` (boot) | 0 — boot fails when > 0 |
| `handler_sync_rejected_total` (boot) | 0 — boot fails when > 0 |
| `shell_env_names` | = the whitelist length; never loads `*` |
| `tool_input_error_ratio` | < 2% of tool calls |

## 7. Acceptance criteria

- [ ] Wrong type argument → `error:` before `fn` is called (test: a spy proves `fn` does not run).
- [ ] A foreign key is rejected; a handler with `additionalProperties: true` accepts it.
- [ ] A keyword outside §4.2 inside `parameters` → boot fails with a message naming the file.
- [ ] A synchronous handler on a tool **and** a command → boot fails.
- [ ] `shell` does not see `OPENAI_API_KEY` even though the variable is in the process environment (end-to-end test).
- [ ] `engine.shell.env: ["*"]` → boot fails.
- [ ] The error message is ≤ 500 characters and names the tool and the field.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| The subset deviates from JSON Schema | A foreign keyword is rejected at boot; §4.2 is the contract |
| Old schemas are too loose | `_schema_errors` already demands `type: object` + `properties`; every schema is audited at cutover |
| The whitelist breaks a tool that needs another variable | The operator adds the name; the wildcard stays rejected |
| Validation adds latency | Only a dict lookup; no dependency, no I/O |
| Argument rejection confuses the model | The message names the field and the reason; the model can fix the arguments in one iteration |

## 9. Interaction with other ADRs

- **ADR-0007**: the single-door permission does not change; §4.8 adds a credential pattern class that completes §4.6 here.
- **ADR-0021**: the **output** contract of tools; this ADR is the **input** contract. Both use the same `Handler` and must not have two schema sources.
- **ADR-0015**: the manifest shows `parameters` as they are; the validator uses the same field, so there is no second schema.
- **ADR-0016**: no change to the database schema.
- **ADR-0019**: a new metric via an in-process counter, without a new table.

## 10. Code references

- `monoagens/engine/tools/__init__.py:182-239` — `execute`, the one door (where argument validation is inserted); `:235` `await spec.fn`.
- `monoagens/engine/tools/__init__.py:17-19` — `MAX_OUTPUT`/`MAX_ERROR`/`TRUNCATED`.
- `monoagens/engine/registry.py:45-53` — `_schema_errors` (schema shape).
- `monoagens/engine/registry.py:70-108` — `validate()` (where the coroutine check is added).
- `monoagens/engine/registry.py:148-182` — `discover`/`discover_many`.
- `monoagens/engine/registry.py:31` — `Handler.parameters`.
- `monoagens/engine/agent.py:986` — command dispatch `await handler.fn(ctx)`.
- `monoagens/engine/tools/shell.py:10-26` — `create_subprocess_shell` without `env`.
- `monoagens/engine/tools/sandbox.py:18-20,83-85,155` — the reused env whitelist pattern.
- `monoagens/engine/config.py:98-108` — `SandboxConfig`, the shape reference for the `shell` config block.

---

*This ADR lives at `docs/v0.1.0/adr/engine/0024-tool-input-contract.md`.*
