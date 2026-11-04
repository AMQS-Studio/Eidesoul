# Gateway and Plugin Contract

**Scope:** the extension surface of the Eidesoul engine: how a body (gateway) and an installable
capability bundle (plugin) plug in, what each owns, and what neither may do.
**Context:** written after ADR-0034 (prompt parts) and ADR-0035 (app-scoped skills). Clauses that
depend on those two are marked **[after 0034]** and **[after 0035]** and become binding when the
respective ADR leaves `draft`.
**Status:** normative contract. A gap between this file and the code is a defect, not an
alternative reading.
**Indonesian version:** `gateway-plugin.id.md`.

---

## 1. The three words

A **gateway** is a body. It moves events between an outside world and the engine and owns the
transport-specific actions of that body.

A **plugin** is an installable unit of capability code. It is fetched, verified, enabled, disabled,
and removed as one thing; it contributes tools, commands, and (after 0034) static prompt parts.

A **tool** is a capability the engine calls. A **command** is an operator-facing capability the
engine dispatches. Tools and commands are not extension units: they are the *contents* a core
module, a gateway, or a plugin may provide.

They are not interchangeable:

| Question | Answer |
| --- | --- |
| Does it own a transport, a run loop, and the body's own actions? | gateway |
| Is it installable/removable code with a manifest and a digest? | plugin |
| Is it a capability the model or an operator invokes? | tool or command |
| Can a gateway be "just a plugin"? | No. It may be *shipped* by a plugin, but it is registered and enabled as a gateway (ADR-0028 §4.1). |
| Can a plugin open its own transport? | Only through a gateway it ships and declares; never as an import-time side effect. |
| Can core contain a gateway's actions? | No. Gateway actions live in `eidesoul/gateways/<name>/tools|commands`; a plugin's actions live in the plugin tree. |

The one-line test: **a gateway is where bytes come from; a plugin is what can be installed; a tool
is what the model may call.**

---

## 2. Gateway contract

### 2.1 Location and discovery

- A core-shipped gateway is a package under `eidesoul/gateways/<name>/`.
- A gateway shipped by a plugin is code under `plugins/<name>/`, declared in
  `plugin.yaml: provides.gateways`, and registered through the gateway path. It is never described
  as a "plugin gateway" and its tools never enter the global plugin tool list.
- The engine never imports a gateway eagerly. `eidesoul/engine/**` must not import
  `eidesoul.gateways` or a plugin module at import time.
- A gateway is enabled by config (`gateways.<name>.enabled: true`), never by installing a plugin as
  a side effect. The shipped default body is `eidesoul/gateways/cli`.

### 2.2 Mandatory surface

A body implements `GatewayCore` from `eidesoul/engine/inbound.py`:

```python
name: str
def tools(self) -> list[Handler]: ...
def commands(self) -> list[Handler]: ...
async def run(self, agent) -> None: ...
async def stop(self) -> None: ...
async def dispatch(self, action: Action) -> str: ...
def self_handles(self) -> set[str]: ...
def chat_name(self, thread: str) -> str: ...      # default: the thread id
def prompt_section(self) -> str: ...               # default: ""
```

`CoreDefaults` supplies `chat_name` and `prompt_section`; the rest are required.

### 2.3 Lifecycle

1. The engine constructs the body from its `GatewaySettings` and the shared runtime.
2. `run(agent)` is the only loop. It produces `Percept` values and calls `agent.handle(percept,
   self)`; it never calls a store, the model, or a turn directly.
3. `stop()` ends the loop and releases transport resources. A restart constructs a new instance; no
   body keeps process-global state that survives `stop()` except what it persists deliberately.
4. Only the lease primary runs a body (ADR-0017). A standby body is not started; a body must not
   fight the lease.
5. `dispatch(action)` is the one outbound door for body verbs. An unknown capability raises
   `GatewayError`; it must not silently succeed or fall back to another verb.

### 2.4 Event, address, receipt

- **In.** One body event becomes one `Percept`: `source`, `thread`, `actor`, `kind`, `payload:
  Message`, `hints`, and optionally `raw` (transport-only; never stored as truth).
- **Hints are facts the body can prove.** Known keys: `dm`, `mention`, `reply`, `group`,
  `presence`, `self`, `roles`; after 0035, `app`. A body never invents a hint it cannot prove and
  never omits one a decision requires. The engine never requires a hint the body cannot supply.
- **Out.** The engine addresses an `Address(gateway, thread, proof)` and emits an `Action`
  (`capability`, `payload`, `approval_class`). The body routes by `capability` and answers with a
  `Receipt(id, state, reference, reason)`.
- Identity is the body's job: it normalizes its own actor ids (`person_id`) before they reach the
  engine. Core never parses a JID, a URL suffix, a phone number, or a transport address.

### 2.5 What a body owns

| Owned by the body | Never moved to core |
| --- | --- |
| transport connection, session, reconnect, backoff | transport address parsing |
| text rendering and quoting for its medium | chat formatting rules in the prompt |
| media download/upload and its limits | file handling policy for a specific transport |
| target resolution (`Directory`) and membership | contact/room tables in the skill or memory schema |
| private-thread mapping (`PrivateThread`) | per-person inference of a transport identity |
| body actions in `<body>/tools|commands` | those actions as core tools |
| one static `prompt_section()` | a second body prompt path |

### 2.6 Errors and prompt text

- `GatewayError` carries a message safe to show to the model or user. Transport stack traces,
  credentials, and raw payloads stay in logs, not in the turn.
- `SendRefused` and `StandbyError` are refusals: a caller never retries them.
- `prompt_section()` returns static English text, at most `PROMPT_SECTION_MAX = 1200` characters,
  with no user data, no secret, and no runtime interpolation. It is the single `body` prompt part
  (stable, pinned, immediately after `base`) after 0034.

---

## 3. Plugin contract

### 3.1 Unit of installation

```text
plugins/<name>/
  plugin.yaml          # the manifest: data, read before any import
  plugin.py            # entry module, one register() function
  deps/                # the plugin's own dependencies (never the image's locked set)
  ...                  # the plugin's code tree; the digest covers it
```

`plugin.yaml` requires: `name`, `version`, `engine`, `entry`, `digest`, `provides`, `requires`,
`secrets`, `credits`. `provides` maps names to lists: `tools`, `commands`, `gateways`, and (after
0034) `prompt`. `secrets` are environment *names*, never values. `credits` carries the rows the
README credits table owes.

### 3.2 Verification before import

The loader performs, in order:

1. read and validate the manifest (unknown keys, name, semver, entry shape, provides lists);
2. compute the SHA-256 of the code tree (sorted relative paths and bytes; `plugin.yaml`, `deps/`,
   and bytecode excluded) and compare it to the manifest digest;
3. compare it again to the pinned `plugins.<name>.digest`;
4. check the `engine` range against the running engine version;
5. check that every declared secret name exists in the environment;
6. only then import `entry` and call `register()`.

Any failure leaves the plugin `unavailable` with a message that carries no secret, and changes no
config. There is no partial unpack, no fallback copy, and no "installed but untrusted" state.

### 3.3 Registration

`register()` takes no arguments and returns a mapping:

```python
{
    "tools": [Handler, ...],      # declared 1:1 in provides.tools
    "commands": [Handler, ...],   # declared 1:1 in provides.commands
    "prompts": [PromptPart, ...], # after 0034; declared 1:1 in provides.prompt
}
```

Rules:

- Every returned handler must match its declared name exactly; missing or undeclared names are a
  `PluginError`.
- A name collision with a core or already-loaded capability is a `PluginError`; a plugin cannot
  shadow a core tool or another plugin.
- A handler is validated with the same `Handler` rules as a core module (`family`, `target`,
  `approval_class`, `destructive`, `path_arg`, `scan`, parameters). Plugins get no permission
  exceptions.
- A gateway a plugin ships follows §2 and is handed to the gateway registry; it is not a tool, not
  a command, and not part of `provides.tools`.
- A failing `register()` purges the plugin's modules and leaves it `unavailable`; the engine keeps
  running.

### 3.4 Lifecycle

`absent → installed → enabled ⇄ disabled → removed` (ADR-0028 §4.3).

- **installing**: fetch by digest, verify, unpack, fetch `requires` into `deps/`.
- **installed**: code present, nothing imported, nothing granted.
- **enabled**: verify, import once, register, write `plugins.<name>.enabled: true` only after
  verification; the role must still grant each capability.
- **disabled**: capability off; files and state stay.
- **removed**: code and plugin state deleted; the ledger keeps the history.

Every step is an operator action under `authorization` and lands in `config_changes` and the action
ledger. There is no auto-update and no background fetch.

### 3.5 What a plugin owns

| Owned by the plugin | Not the plugin's |
| --- | --- |
| its manifest, code tree, and digest | the engine's schema |
| its dependencies in `deps/` | the image's locked dependency set |
| its tools/commands' logic | permission checks (`execute` owns them) |
| its static prompt parts (after 0034) | the prompt assembly order or budget (loader and core own them) |
| its own data files under its root | core tables; writes go through the engine's stores |

A plugin writes memory, skills, or settings only through the engine's public stores/ports. It never
opens the database, bypasses `execute`, or writes a core table directly.

---

## 4. Composition rules

These are the rules that keep "modular" from meaning "overlapping".

1. **One transport, one gateway.** A body that opens a socket/serial/HTTP endpoint and emits
   `Percept` is a gateway. Core has none of it.
2. **One installable unit, one plugin.** Code that adds capabilities but owns no transport is a
   plugin. A plugin may contain several tools/commands; it does not become a gateway.
3. **A plugin may ship a gateway; the gateway stays a gateway.** The gateway is declared in
   `provides.gateways`, registered through the gateway path, and enabled by gateway config. It does
   not enter the plugin's tool list and does not gain plugin lifecycle semantics.
4. **Body actions stay in the body.** A gateway's tools/commands live in
   `eidesoul/gateways/<name>/tools|commands` (or the plugin tree that ships it). Core never holds a
   WhatsApp reply command, a robot alarm tool, or a calendar API.
5. **A tool does not choose its transport.** A capability receives `ToolContext`; it does not read
   a transport name to decide behavior. If it needs a body fact, the fact is in `ctx`.
6. **No silent optional path.** A missing gateway, plugin, or tool is `unavailable`, never a
   fabricated success and never a traceback in the turn (ADR-0027 §4.3).
7. **Removal is clean.** Disabling a plugin never leaves a registered handler; stopping a gateway
   never leaves a running loop; a removed plugin's code is deleted, not shadowed.

### 4.1 After ADR-0034: the prompt surface

- A gateway contributes exactly one static body section through `prompt_section()`; it cannot
  register prompt parts and cannot add a second section.
- A plugin contributes static `PromptPart`s declared in `provides.prompt`, returned by
  `register()["prompts"]`, namespaced `plugin:<plugin>:<slug>`, `stable=True`, `produce=None`,
  non-empty `text`, `len(text) <= 1200`, and with `order` assigned by the loader after core parts.
- A prompt part is not a tool: no permission check, no `ToolContext`, no async call, no user data.
  A part that fails validation is a `PluginError` before the plugin joins the turn path.
- The body section is the only gateway prompt contribution; a plugin cannot pretend to be a body.

### 4.2 After ADR-0035: the app hint

- A gateway may report `Inbound.hints["app"]` (the application or corpus the event belongs to). It
  is optional; a body that omits it reports "no app".
- The engine reads the hint only to select an app-scoped corpus. It never derives an app from a
  body name, a thread id, or a URL.
- A plugin tool receives `ctx.app` (from the same hint) and may filter its own pack with it. It must
  not read `ctx.gateway.name` to infer an app.
- A gateway that ingests skills writes through `ctx.skills`/`SkillStore` with the same app it
  reports in the hint; it never writes the `skills` table directly.
- Missing hint fails closed: app-scoped skills are hidden, shared skills remain visible.

---

## 5. Permissions and trust

- **Roles are the only source of permission.** A gateway's or plugin's handler has no default
  grant; a missing `tools`/`commands` entry denies everything. The one check is
  `eidesoul/engine/tools/__init__.py:execute`.
- **Approval class is declared by the handler**, not by its name. A plugin that wants to be safe
  declares `approval_class="none"` only when the action is fail-safe; every unknown handler
  defaults to `authorization`.
- **The guard sees model-initiated calls.** It is not a sandbox for operator-installed plugin code.
  Installing a plugin is an operator trust decision, bounded by the digest, the engine range, and
  the credits/license record.
- **Secrets are env names.** No manifest, prompt part, tool result, or log line carries a secret
  value.
- **The body normalizes identity; the core never guesses it.** A gateway that cannot prove a fact
  leaves the hint out; the engine treats absence as the safe branch.

---

## 6. Failure semantics

| Failure | Result |
| --- | --- |
| Gateway cannot connect | logs, stays unavailable, the engine and other bodies continue |
| Gateway `dispatch` gets an unknown capability | `GatewayError`; no fallback verb |
| Gateway returns a malformed receipt | the action is `unknown`; the caller does not retry blindly |
| Plugin manifest invalid | `unavailable`, no import, no config change |
| Plugin digest mismatch | `unavailable`, no import, no partial unpack |
| Plugin `register()` raises | `unavailable`, modules purged, engine continues |
| Plugin declares a capability it does not return | `PluginError`; the plugin does not load |
| Plugin returns an undeclared capability | `PluginError`; the plugin does not load |
| Role lacks the grant | denied at `execute`; no plugin-specific bypass |
| Worker not primary | standby refuses intake and sends nothing (lease) |

---

## 7. Conformance checks

A gateway is conformant when:

1. it implements `GatewayCore` and imports only `eidesoul.engine.inbound`, `registry`, and the turn
   types it needs;
2. `run()` produces `Percept` and never touches stores or the model directly;
3. `dispatch()` refuses unknown capabilities with `GatewayError`;
4. `prompt_section()` is static, English, and within 1200 characters;
5. its tools/commands live in its own tree and never in `eidesoul/engine/tools|commands`;
6. it normalizes its own identities and never parses transport ids in core;
7. with the body stopped, no loop, task, or connection survives.

A plugin is conformant when:

1. `plugin.yaml` validates, the digest matches the tree and the pin, and the engine range is
   satisfied;
2. `register()` returns exactly the declared tools/commands/prompts and nothing else;
3. all secrets are env names and all are present before enable;
4. its dependencies live in `deps/` and the image lock is unchanged;
5. disabling/removing it leaves no handler, no prompt part, and no imported module behind;
6. its tools pass the same `Handler` validation and permission check as core tools;
7. if it ships a gateway, the gateway meets the gateway checks above.

The executable form of these checks lives in `tests/test_gateway.py`, `tests/test_plugins.py`, and
the prompt contract suite. A new body or plugin adds its case; it does not add a new mechanism.

---

## 8. Worked example: a robot guard

- **Gateway `robot`.** Owns the serial/sensor loop, presence detection, identity input (RFID,
  keypad, voice), TTS, and the alarm relay. It emits `Percept(hints={"app": "robot", "presence":
  True, ...})` and dispatches `send_text` (speech) and `guard_alarm`/`guard_control` (its own
  verbs).
- **Body tools.** `guard_alarm` (fail-safe, `approval_class="none"`), `guard_control`
  (`authorization`, reset/unlock). The timeout and latch live in the body; the model is not in the
  safety path.
- **Prompt.** One static `prompt_section()`: "you are a guard; never open without confirmation; if
  confirmation is missing, the body raises the alarm." The invariant is repeated in `soul.rules`,
  not stored as a retrievable skill.
- **Plugin (optional).** A `guard-playbooks` plugin could add static prompt guidance or a
  `guard_lookup` tool for visitor types. It does not own the alarm, the transport, or the latch.
- **App.** `app="robot"` isolates robot playbooks/skills from other bodies' corpora. It is not a
  permission: the role still decides which tools the robot may call.

---

## 9. Known gaps (as of 2026-11-04)

These are defects against this contract, not options:

- `plugins.py:_register` imports and registers only `tools` and `commands`; a manifest with
  `provides.gateways` is accepted but the gateway is not registered. Until the gateway path exists,
  a declared gateway must fail closed as `unavailable` rather than silently disappear.
- `HINT_KEYS` does not include `app`; ADR-0035 is still draft.
- Plugin prompt parts do not exist until ADR-0034 lands; today `register()` returns only
  tools/commands.
- `Inbound` appears in older prose (including `AGENTS.md`); the live seam is `Percept`/`Action` in
  `eidesoul/engine/inbound.py`. This contract uses the live names.

---

## 10. References

- `eidesoul/engine/inbound.py` — `Percept`, `Action`, `Address`, `Receipt`, `GatewayCore`, `Rich`,
  `Directory`, `PrivateThread`, `HINT_KEYS`, `PROMPT_SECTION_MAX`.
- `eidesoul/engine/plugins.py` — manifest schema, `tree_digest`, `_verify`, `_register`,
  lifecycle states, `PluginError`.
- `eidesoul/engine/registry.py` and `eidesoul/engine/tools/__init__.py` — `Handler`, `ToolContext`,
  `execute`, the one permission check.
- `eidesoul/engine/agent.py` — `handle(percept, gateway)` intake, `dispatch(...)` outbound door,
  the gateway tool/command merge.
- ADR-0027 §4.1 — a gateway, a tool, and a plugin are three different things.
- ADR-0028 §4.1–4.4 — plugin unit, manifest, lifecycle, loading.
- ADR-0032 — address and command message; body routing, no engine-side quoting.
- ADR-0033 §4.5 — one body `prompt_section`.
- ADR-0034 — prompt parts: static plugin parts, one body contribution.
- ADR-0035 — app namespace: gateway supplies `hints["app"]`, plugin reads `ctx.app`, core filters.
