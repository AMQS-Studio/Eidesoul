# ADR-0046: Gateway Authorization in Roles, Gateway-Owned Onboarding, and the Post-Install Start Choice

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** follows ADR-0018 (gateway contract), ADR-0022 (the protected administrator), ADR-0027 (clean by default), ADR-0030 (install and init), ADR-0031 (engine structure), ADR-0033 (neutral core), ADR-0036 (role model), ADR-0039 (setup terminal), ADR-0040 (naming), and ADR-0042 (sync on start).
- **Supersedes:** the database-wins rule for `config.yaml`, `soul.yaml`, and `roles.yaml` in ADR-0042 §4.1; the rest of ADR-0042 stays.
- **Related:** ADR-0007 (authorization), ADR-0018 (gateway contract), ADR-0022 (administrator), ADR-0027 (distribution), ADR-0030 (installer), ADR-0033 (neutral core), ADR-0036 (role model), ADR-0039 (init), ADR-0042 (sync), ADR-0043 (research)
- **Implementation:** implemented — the role policy, file-first cache, `/reload`, gateway onboarding, and installer start choice landed with the same commit; the Indonesian copy is stamped to this text.
- **Location note:** the decision crosses the role core, the gateway seam, and the installer, so it sits at the root of the ADR home. The Indonesian copy is `docs/v0.1.0/adr/id/0046-gateway-access-and-onboarding.md`.

---

## 1. Context

A WhatsApp install can pair successfully and still look empty:

- pairing creates `.monoagens/whatsapp.db`, but the gateway process is not started;
- history is imported only by a running gateway, not by pairing;
- an unlinked sender resolves to `guest`;
- WhatsApp transport settings currently contain only `enabled`, `session_db`, and `backlog_seconds`;
- the capability catalog indexes gateway tools by name and replaces their real description with `gateway:<name>`;
- the installer owns the pairing loop, while the gateway owns the terminal QR and session.

The owner has now fixed the policy direction:

1. gateway authorization belongs in `roles.yaml`, not in a second per-gateway policy block in
   `config.yaml`;
2. the highest-priority role decides, not a union of every role;
3. `guest` defaults to deny all;
4. `roles.yaml` and `config.yaml` are the operator's source of truth, and commands that change those
   files write there;
5. after install, the operator is asked whether to start now or install an auto-restarting service.

The current contract says the database role documents are authoritative and YAML is a seed plus
mirror (`monoagens/engine/stores/documents.py:180-233`). That is exactly the source-of-truth conflict
this ADR resolves for these two operator files.

## 2. Decision drivers

| Driver | Consequence |
| --- | --- |
| Authorization is role data | gateway access is read from the resolved role, not from a parallel gateway policy map |
| One priority rule | the highest-priority role wins; a lower role cannot silently grant access |
| Deny is safe | a missing policy, an unknown sender, or a denied role is dropped before memory and model work |
| File-first operation | `roles.yaml` and `config.yaml` are editable, diffable, and written by the command that changes them |
| No hidden mutation | a running gateway never rewrites a role file; only `init`, `gateway add`, or an explicit doctor command may materialize defaults |
| Transport stays outside core | the core stores an opaque `gateways` mapping; only the gateway interprets its own policy |
| Gateway owns onboarding | QR, session files, and pairing waits are gateway concerns; the installer only invokes the hook |
| Explicit lifecycle | pairing never starts the agent; the finish step asks for foreground, service, or exit |
| Complete capability metadata | gateway tools expose their real description and parameters in the catalog |
| Clean by default | both files stay explicit; no implicit allow for a new gateway or a new role |

## 3. Options considered

| Option | Summary | Value |
| --- | --- | --- |
| **A. Per-gateway policy in `config.yaml`** | `gateways.whatsapp.dm/groups/...` | Rejected: duplicates role authorization and cannot express guest/member differences cleanly |
| **B. Role policy in `roles.yaml`** | each role carries `gateways.<name>` | **Chosen** |
| **C. Runtime auto-write by the gateway** | gateway fills missing role blocks while handling messages | Rejected: hidden authorization changes, races, and a transport writing the role store |
| **D. Database-first with YAML mirror** | keep ADR-0042 as-is | Rejected by the owner: invisible changes and a file that is not really the operator's source |
| **E. File-first for every document** | reverse precedence for roles, config, soul, and all mirrors | Rejected: broader migration than this decision needs; `soul.yaml` stays under ADR-0042 |
| **F. `init` starts the agent** | QR, database, and process start inside init | Rejected: init remains a deterministic file writer, usable headless and in tests |
| **G. Installer starts the agent automatically** | no prompt | Rejected: a foreground process blocks the installer and a service is an operator choice |
| **H. Gateway-owned onboarding hook** | `gateway add --connect` performs extra install, enable, policy write, and pairing | **Chosen** |
| **I. Catalog keeps placeholder descriptions** | `gateway:<name>` only | Rejected: the capability screen cannot explain what the tool does |
| **J. Auto-assign administrator** | any unknown WhatsApp sender becomes administrator | Rejected: privilege escalation; identity linking must be explicit |

## 4. Decision

### 4.1 File-first operator surfaces

For `config.yaml`, `soul.yaml`, and `roles.yaml`:

- the files are the operator's source of truth;
- database rows are caches or projections only;
- a command that changes these files writes the file first, then refreshes the cache;
- when a cache and a file disagree at startup, the file wins;
- a cache miss or corrupt cache is rebuilt from the files; it is never a fallback;
- role definitions and the role membership list both live in `roles.yaml`; the database keeps
  identity/link records and a derived cache, not the authority for who holds a role;
- secrets stay in `.env`, and changes that need a process restart are reported rather than silently
  applied;
- the sync lock remains the one serialization point for a rebuild.

This is a scoped amendment to ADR-0042, not a general "files win everywhere" rule. It makes gateway
authorization, transport settings, soul state, and role membership visible, diffable, and recoverable
from the repository or a backup. The database cache may be dropped at any time.

### 4.2 Role gateway policy

`Role` gains one opaque field:

```yaml
gateways:
  whatsapp:
    dm: true
    groups: mention
    reply_to_bot: true
```

The core stores the mapping but does not interpret gateway names or policy values. The gateway reads
`role.gateways[self.name]` and owns validation.

The policy vocabulary for a gateway is:

| Key | Values | Meaning |
| --- | --- | --- |
| `dm` | `true`, `false` | direct messages from this role may enter a turn |
| `groups` | `none`, `mention`, `reply`, `both`, `all` | which group messages may enter a turn |
| `reply_to_bot` | `true`, `false` | a reply to a bot message counts as addressed |

`send` and `chat_history` remain tool permissions; gateway access is not a substitute for tool
permission.

### 4.3 Role resolution

Resolution uses the existing order from `RoleRegistry.for_person`: priority descending, then role
name (`monoagens/engine/access/roles.py:61-74`).

1. Resolve every role for the sender.
2. Take the **first** role in that order.
3. Read `gateways.<gateway>` from that role only.
4. If the block is absent or malformed, deny; do not fall through to a lower role.
5. If the sender has no linked role, resolve `engine.default_role`.
6. Shipped `guest` policy is deny-all; a denied message is dropped before history, memory, and the
   model call, and one structured diagnostic is logged.

The same priority rule makes a role change observable: promoting a role can widen access, and
demoting it can close access without editing the gateway config.

### 4.4 Materializing missing policies

The files are source of truth, so missing policy is materialized in the file, never by the runtime:

- `init` writes explicit `gateways` blocks for every selected gateway and every seed role;
- `gateway add`/`gateway enable` writes missing blocks to `roles.yaml` and imports the projection;
- a runtime gateway treats a missing block as deny and logs `gateway_policy_missing`;
- `gateway doctor` reports missing blocks, and `gateway doctor --fix` is the explicit repair path.

The generated seed is:

| Role | `dm` | `groups` | `reply_to_bot` |
| --- | --- | --- | --- |
| `administrator` | true | all | true |
| `member` | true | mention | true |
| `guest` | false | none | false |
| any other/new role | false | none | false |

The owner can still make `member` deny-all, or make `guest` read-only, by editing `roles.yaml`. The
matrix is a seed, not a runtime exception.

### 4.5 Gateway-owned onboarding

A gateway descriptor declares its onboarding command, extra, schema, and capabilities. The command is
generic:

```bash
monoagens gateway add whatsapp --connect
```

It performs, in order:

1. install the declared extra;
2. enable the gateway in `config.yaml` (file-first);
3. materialize the seed role policies in `roles.yaml` (file-first);
4. call the gateway's own `pair`/onboarding function;
5. let the gateway print the QR and own `.monoagens/whatsapp.db`;
6. return without starting the agent.

`init` never performs steps 1, 4, 5, or 6. `install.sh` orchestrates them after the database exists,
but does not parse or render the QR. The current installer's `pair_gateways` call is the temporary
form of this hook; the final form is the gateway command.

Pairing is not runtime. A running agent is required for incoming messages and history sync.

### 4.6 Post-install start choice

After the database schema, administrator registration, gateway onboarding, and self-test:

```text
1) start now in this terminal
2) install and start a user service (auto restart)
3) exit
```

- The interactive default is start now; no process starts before the operator answers.
- Non-interactive installs print the equivalent command and exit.
- The service is a systemd user unit with `Restart=on-failure`, a restart delay, the install directory
  as `WorkingDirectory`, the project environment, and `python -m monoagens run`.
- The lease remains the only single-primary guard; a second process freezes instead of running two
  hearts.
- A foreground `run` is not a service and ends with the terminal.

### 4.7 Capability catalog descriptions

Gateway tools are discovered from their `TOOL = Handler(...)` declarations. The catalog must preserve
`description` and parameter metadata, not replace it with `gateway:<name>`. The multi-select's
`? details` view renders the full wrapped description; the line view remains compact.

The source of metadata is the gateway tool module; the catalog is an index, never a second description
table.

### 4.8 Administrator identity is never automatic

The gateway account that pairs is the agent's account, not the operator's personal account. An unlinked
sender remains `guest` and is denied by the seed policy. The installer may offer to link the operator's
number after pairing, but it must be an explicit answer or a first-contact claim code, never an
automatic administrator grant.

### 4.9 Reload is an owner command carried by any gateway

Reload is a core command, not a WhatsApp-specific command. A gateway only transports the message;
the same `/reload` works from the CLI, WhatsApp, or another body that exposes commands.

```text
/reload
```

The command is available only when the sender's highest-priority role lists `reload` in `commands`
(`administrator` by default). The normal gateway policy still applies: an administrator whose
`gateways.whatsapp` policy denies DM or mentions never reaches the command. The command is accepted
only in a private thread, so a group cannot trigger a configuration swap.

On invocation the primary:

1. takes the config lock;
2. reads `config.yaml`, `soul.yaml`, and `roles.yaml` from disk;
3. validates all files together;
4. rebuilds the cache and bumps one generation;
5. atomically swaps the runtime snapshot;
6. replies with a short receipt (changed sections and anything that still needs a restart).

If validation fails, nothing is swapped and the old runtime continues. The reply names the failing
file and the validation error, never a secret. A standby instance refuses; `/reload` does not take
the lease. File edits do not apply until this command, a restart, or the explicit startup reload.

Changes to `.env`, the database URL, workspace, sandbox runtime, and other host-level values are
reported as restart-required; the command does not pretend they are hot-reloadable.

## 5. Configuration

### 5.1 Transport-only gateway settings

```yaml
gateways:
  whatsapp:
    enabled: true
    session_db: .monoagens/whatsapp.db
    backlog_seconds: 600
```

No `dm`, `groups`, or `reply_to_bot` key belongs here. Those are authorization and live in
`roles.yaml`.

### 5.2 Role authorization

```yaml
roles:
  administrator:
    gateways:
      whatsapp:
        dm: true
        groups: all
        reply_to_bot: true

  member:
    gateways:
      whatsapp:
        dm: true
        groups: mention
        reply_to_bot: true

  guest:
    gateways:
      whatsapp:
        dm: false
        groups: none
        reply_to_bot: false
```

The examples omit the existing `priority` field; the role resolver uses it. The highest-priority role
of the sender is authoritative. If a person carries both `member` and `administrator`,
`administrator` wins by priority.

### 5.3 File-first reconciliation

```text
startup:
  read config.yaml + soul.yaml + roles.yaml
  validate all three
  build a cache keyed by content hash
  load runtime from the files
  keep the old runtime if validation fails

/reload:
  lock
  read and validate the same files
  rebuild cache, bump generation
  swap runtime atomically
  report restart-required host changes
```

A command that changes a file writes the file first, then refreshes the cache. A cache miss or a
corrupt cache is rebuilt; it is never a fallback. No gateway writes a file at runtime.

### 5.4 `/reload` command configuration

`reload` is just another core command for permission purposes:

```yaml
roles:
  administrator:
    commands:
      - "*"
    gateways:
      whatsapp:
        dm: true
        groups: all
        reply_to_bot: true
```

A role that should not reload simply omits `reload` from `commands`. The command is private-thread
only and still subject to the gateway access policy. The reply uses the normal language catalog; no
chat-visible string is hard-coded in the command.

## 6. Metrics and gates

| Gate | Check |
| --- | --- |
| Priority | a person with `member` and `administrator` uses the administrator policy |
| Deny default | an unlinked sender resolves to `guest` and produces no model call or memory write |
| Missing policy | a role without `gateways.<name>` is denied and emits one diagnostic |
| File-first | editing `roles.yaml` then restarting or `/reload` changes access without a separate import command |
| No runtime mutation | a denied or allowed message leaves `roles.yaml` byte-identical |
| Reload | `/reload` from the administrator validates all files, swaps the runtime, and reports restart-required changes; an invalid file keeps the old runtime |
| Cache | deleting the cache and restarting rebuilds it from the files; a corrupt cache is never used as authority |
| Seed completeness | `init` writes a policy block for every seed role when a gateway is selected |
| Onboarding | `gateway add --connect` installs the extra, enables the gateway, writes policy, and calls the gateway's onboarding function exactly once |
| Start choice | installer prints the three choices; service path starts one process under the lease; non-interactive prints commands |
| Catalog | every indexed gateway tool has a non-empty description; `? details` shows it |
| Neutrality | no gateway name or transport check appears in `monoagens/engine/` |

## 7. Acceptance criteria

- [x] `Role` accepts a `gateways` mapping without knowing any gateway name.
- [x] The first role returned by `for_person` is the only policy source; a lower role cannot grant access.
- [x] `guest` seeded policy denies DM, groups, and reply-to-bot.
- [x] A denied message is dropped before history, memory, LLM, and tool dispatch; the diagnostic names the role and gateway.
- [x] `init --capabilities` writes the seed gateway policies to `roles.yaml`.
- [x] `gateway add --connect` writes missing policies to `roles.yaml` and calls the gateway's onboarding hook.
- [x] A missing policy at runtime denies and is reported by `gateway doctor`; it is not auto-written by the running gateway.
- [x] The installer does not render QR; the gateway prints it.
- [x] After a successful install, the operator sees start-now, user-service, and exit choices.
- [x] The user service restarts on failure and never runs a second primary.
- [x] The capability catalog shows the real description of `chat_history`, `send`, and `send_to`.
- [x] `config.yaml`, `soul.yaml`, and `roles.yaml` are the write targets for operator changes; the DB cache can be deleted and rebuilt.
- [x] `roles.yaml` contains both the role definitions and the role membership list; the DB only caches them.
- [x] `/reload` is a core command granted by `commands: ["reload"]`, private-thread only, and subject to the gateway policy.
- [x] `/reload` reports success and restart-required fields; invalid YAML leaves the old runtime running.
- [x] `AGENTS.md`'s file/DB authority paragraph is amended in the implementation commit, pointing to this ADR.
- [x] Existing ADR gates and the full test suite pass.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Reversing DB precedence creates two writers | one file-first path with the existing advisory lock; commands write file first; cache is derived |
| A reload is half-applied | validate all files, rebuild the cache, then swap one runtime snapshot; invalid input leaves the old snapshot |
| Cache becomes a hidden authority | cache entries carry content hashes; a cache miss/corruption is rebuilt; no DB fallback when the file is invalid |
| `guest` deny-all makes a fresh install look broken | installer explains access, offers operator-number linking, and `gateway doctor` reports the reason |
| Missing policy silently locks people out | startup diagnostic, `gateway doctor`, and explicit seed materialization at init/enable |
| Auto-materialization surprises an operator | only `init` and explicit `gateway add`/`doctor --fix` write files; runtime never does |
| Auto-admin from pairing | never auto-link; explicit number/claim code only |
| QR exposure in a group | onboarding stays terminal-bound; never a group message |
| Duplicate primary after service install | lease remains authoritative; service uses `python -m monoagens run`; no `--takeover` |
| Gateway names leak into core | core treats `gateways` as opaque data; gateway validates its own block |
| Tool descriptions diverge from handlers | metadata is extracted from the handler declaration; no second description table |

## 9. Interaction with other ADRs

- **ADR-0007**: access denial complements approval classes; a denied gateway message never reaches an action.
- **ADR-0018**: gateway contract stays; onboarding and authorization are gateway-owned data, not core branches.
- **ADR-0022**: `administrator` keeps `tools: "*"`, but its gateway policy is still explicit; auto-admin remains forbidden.
- **ADR-0027**: gateways remain clean-by-default and removable; no gateway import in the engine.
- **ADR-0030**: the installer gains the start choice and delegates onboarding; `init` remains file-only.
- **ADR-0031**: role access code stays in `access/`; gateway policy parsing stays in the gateway; no new core module without need.
- **ADR-0033**: no gateway or transport names in core; role data is opaque.
- **ADR-0036**: role bundles and priority remain the single role model.
- **ADR-0039**: init remains deterministic; capability selection writes files.
- **ADR-0042**: file wins for `config.yaml`, `soul.yaml`, and `roles.yaml`; the rest of the document sync remains.
- **ADR-0045**: `soul.yaml` content stays governed by the soul contract; this ADR changes only its source-of-truth precedence.

## 10. Code references

- `monoagens/engine/access/roles.py:28-39` — the current `Role` dataclass; `gateways` is **usulan**.
- `monoagens/engine/access/roles.py:61-74` — priority ordering for a person.
- `monoagens/engine/access/permissions.py:51-56` — tool/command authorization stays separate from gateway access.
- `monoagens/engine/config.py:1163-1178` — current `GatewaySettings` transport options.
- `monoagens/engine/stores/documents.py:180-233` — current DB-wins synchronization; ADR-0042 is amended for two files.
- `monoagens/gateways/whatsapp/config.py:9-24` — transport-only WhatsApp settings.
- `monoagens/gateways/whatsapp/ingress.py:51-60,194-198` — mention/reply hints; the gateway policy gates them before the turn.
- `monoagens/engine/turn/decision.py:269-284` — current direct/mention/reply forcing; access must happen before this point.
- `monoagens/gateways/__init__.py:102-166` — gateway index, CLI dispatch, and loader; onboarding metadata is **usulan**.
- `monoagens/admin/gateway.py:30-79` — current enable/add/disable/pair CLI; file-first policy materialization is **usulan**.
- `monoagens/engine/turn/commands.py` — command dispatch; `/reload` is **usulan**.
- `monoagens/engine/commands/` — new `reload.py` with the core command is **usulan**.
- `monoagens/engine/stores/documents.py:235-300` — current sync; cache rebuild and file-first loading are **usulan**.
- `install.sh:951-986` — current `pair_gateways`; moving QR ownership into `gateway add --connect` is **usulan**.
- `install.sh:1626-1646` — current finish output; the start/service prompt is **usulan**.
- `monoagens/admin/wizard.py:295-379` — capability selection; seed role policy generation is **usulan**.
- `monoagens/admin/catalog.py:23-50` — the current `gateway:<name>` placeholder; extracting the real description is **usulan**.
- `monoagens/__main__.py:209-230` — current gateway command dispatch; descriptor-driven onboarding is **usulan**.
- `tests/test_install.py`, `tests/test_catalog.py` — current tests to extend for the gates above.

---

*This ADR lives at `docs/v0.1.0/adr/0046-gateway-access-and-onboarding.md`.*
