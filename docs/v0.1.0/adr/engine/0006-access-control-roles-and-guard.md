# ADR-0006: Access Control, Role-Based Security, and Sandbox Guard — RBAC, Identity Linking, and Command Screening

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** Muqsith (owner)
- **Context:** Re-baselined for v0.1.0; consolidates capability authorization, the protected administrator role, relay consent, role models, identity linking, and sandbox command screening (pre-rebaseline 0007, 0022, 0023, 0036, 0046, 0049, 0052, 0053).
- **Supersedes:** pre-rebaseline 0007, 0022, 0023, 0036, 0046
- **Related:** ADR-0001 (sovereign core), ADR-0004 (core tools), ADR-0005 (gateway access enforcement)
- **Implementation:** implemented — `/link` rate limits and row locks, role-conflict merge refusal, unlink revocation, shell wrapper/IFS/SAFE_HEADS hardening, IPv6 proxy ACLs, and persona `mode` separated from RBAC roles.

---

## 1. Context

The access control and guard layers determine who may talk to the agent and what commands or tools may
run. Verification exposed several security vulnerabilities and conceptual collisions:
1. **Persona vs RBAC Collision & Dead `people.role` Column:**
   - `soul.yaml` defined `role: assistant` and `roles: [assistant, companion, advisor]` (persona modes),
     while `roles.yaml` defined RBAC roles (`administrator`, `member`, `guest`). Both were injected into
     system prompts as `Active role:` and `Sender roles:`, confusing the model.
   - The `people` table carried a dead `people.role` column (`schema.py:354`) that `people add` never
     populated, causing `monoagens people list` to print `-` or `member` for administrators.
2. **Multi-Role Gateway Rejection & Over-Privileged `guest` Seed:**
   - `RoleRegistry.policy_for` only inspected `roles[0]` (highest priority) and never fell back to lower
     roles. A user in both `administrator` (defining `cli`) and `member` (defining `whatsapp`) was
     rejected on WhatsApp with `policy_missing`. Roles with equal priority fell back to arbitrary
     alphabetical sorting.
   - The default `roles.yaml` seed granted `guest` (unverified strangers) access to `read`, `grep`,
     `fetch`, `web_search`, `chat_history`, `link`, and `unlink`—nearly identical to `member`.
3. **Identity Absorption Attack & Brute-Force on `/link` (`ST-10`):**
   - When an attacker (`guest`) created a `/link cli` code and tricked a single-link `administrator`
     into typing `/link <ATTACKER_CODE>`, `IdentityStore._attach` merged the admin into the attacker's
     `person_id` and executed `DELETE FROM people WHERE person_id = :admin_id`, permanently locking the
     admin out of the system!
   - Guessing a non-existent `/link` code (`row is None`) did not increment `attempts` or trigger any
     rate limiter, allowing unlimited brute-force enumeration.
   - `finish_code` ran across 3 separate transactions without `SELECT ... FOR UPDATE`.
   - `commands/link.py:62` matched the 6-character code regex (`[A-Za-z0-9]{6}`) *before* checking
     gateway names, making 6-letter gateways (`matrix`, `signal`, `wechat`) impossible to link via
     `/link matrix`.
   - `/unlink` deleted `person_links` without revoking active `relay_grants` or `approvals` and without
     writing an audit row to `actions`.
4. **Unused `HarnessGateway` Security Gaps (`BI-13`):**
   - `monoagens/gateways/harness/` omitted `access_refusal`, bypassed `SafetyPolicy` on `tools/run.py`,
     and crashed on `commands/inspect.py:23` (`action.target`). Because `harness` is unused in this
     repository (and future external harness tooling lives in its own dedicated project), keeping
     `monoagens/gateways/harness/` only increased attack surface.
5. **Sandbox Guard Bypass Wrappers, False Positives, Rigid Approval UX, & IPv6 Egress Leak (`FD-11, ST-14`):**
   - `SHELL_WRAPPERS` omitted `timeout`, `flock`, `setsid`, `script`, `watch`, `doas`, and `pkexec`,
     and `_sensitive_segment` stripped arguments after `env -S`, allowing `timeout 5 git push --force`,
     `env -S "git push --force"`, and `rm${IFS}-rf${IFS}/` to bypass command scanning (`scan_command=False`).
   - `SAFE_HEADS` allowed `cat /etc/shadow`, `cat ~/.ssh/id_rsa`, `sort -o hacked.txt`, and
     `find . -fprint hacked.txt` as *"simple safe"*, while `BLOCKED_RE` falsely denied
     `cat docker-compose.yml` and `grep sudo notes.txt` because filename arguments matched `docker`/`sudo`.
   - `ApprovalConfig` included a coarse `sensitive_tools` list (forcing every benign read/inspection via
     `shell` or `sandbox` to pause for `/approve`), allowed only a single pending approval per turn
     (`if ctx.approval_id:`), forced users to type rigid `/approve <id>` commands rather than confirming
     naturally in conversation (e.g. *"Oke, kirim"*, *"Ya"*), and stopped dead after executing `/approve`
     instead of resuming the LLM turn loop to complete multi-step workflows.
   - `deploy/sandbox-proxy/squid.conf` blocked IPv4 private ranges but omitted IPv6 loopback/private
     ranges (`::1/128`, `fc00::/7`, `fe80::/10`, `::ffff:0:0/96`) and `0.0.0.0/8`.

## 2. Decision drivers

1. **Fail-Closed RBAC:** Permissions come strictly from `roles.yaml`. A missing entry denies access;
   `administrator` is the protected top role (`protected: true`) and must always exist.
2. **Unambiguous Persona vs RBAC Vocabulary:** Persona modes (`mode` / `modes` in `soul.yaml`) never
   collide with access control roles (`roles.yaml`).
3. **Consistent Multi-Role Evaluation:** A person in multiple roles receives the union of tools/commands
   and the first matching gateway policy in priority order.
4. **Tamper-Proof Identity Linking:** `/link` resists brute-force guessing, executes atomically, and
   never deletes or absorbs an identity registered in `roles.yaml`.
5. **Defense-in-Depth Command & Network Guard:** Shell wrappers, `${IFS}`, out-of-workspace file reads,
   write flags, and IPv6 local egress are deterministically blocked.

## 3. Options considered

**A. Keep `role` in Both `soul.yaml` and `roles.yaml`.** Rejected: causes persistent prompt and operator
confusion.

**B. Rely Exclusively on LLM Classifier for Command Safety.** Rejected: vulnerable to prompt injection
and latency; deterministic token/AST screening must enforce hard boundaries first.

**C. Hardened RBAC, Atomic `/link`, Wrapper-Aware Sandbox Guard, and IPv6 Proxy Rules.** Accepted.

## 4. Decision

### 4.1 RBAC Architecture, Multi-Role Fallback, and Tightened `guest` Seed

- Permissions derive exclusively from `roles.yaml`:
  - `administrator`: Protected top role (`priority: 100`, `protected: true`, `tools: "*"`). Cannot be
    deleted or renamed, and at least one administrator must exist before boot (`require_administrator`).
  - `member`: Trusted user role (`priority: 10`).
  - `guest`: Untrusted/unknown sender role (`priority: 0`). In the default seed template, `guest` is
    restricted to `help`, `ping`, and `whoami` (losing `read`, `grep`, `fetch`, `chat_history`, `link`,
    and `unlink`; `link` is enabled for `guest` only when explicitly configured by the operator).
- `RoleRegistry.policy_for(person_id, gateway)` iterates assigned roles in priority order and returns
  the first role that defines a policy block for `gateway` (with an explicit documented tiebreak when
  priorities are equal).
- The dead `people.role` column is dropped via migration, and `monoagens people list` displays actual
  assigned roles from `RoleRegistry`.

### 4.2 Separation of Persona Mode (`soul.yaml`) and RBAC (`roles.yaml`)

- `soul.yaml` renames `role` / `roles` to `mode` / `modes` (e.g. `mode: assistant`,
  `modes: [assistant, companion, advisor]`).
- Prompts render `Active mode:` instead of `Active role:`. Changing `soul.mode` (or invoking `/mode`
  when enabled) adjusts persona tone and sampling only, never RBAC permissions.

### 4.3 Hardened Identity Linking (`/link` and `/unlink`)

- **Rate Limiting:** Failed `/link` attempts (including `row is None` missing codes) increment per-sender
  and per-gateway rate limit counters.
- **Atomic Consumption & Role Protection (`ST-10`):** `finish_code` executes in a single transaction
  using `SELECT ... FOR UPDATE` on `link_codes`, and refuses to merge/delete any identity that holds an
  assigned role in `roles.yaml` (or reverses merge direction so the privileged identity is preserved).
- **Gateway Name Precedence:** `commands/link.py` checks whether the argument matches an enabled or
  known gateway name (e.g. `matrix`, `signal`, `wechat`) *before* testing the 6-character code regex.
- **Revocation on `/unlink`:** Unlinking an address revokes that person's active `relay_grants` and
  pending `approvals` and records an audit entry in `actions`.

### 4.4 Central Gateway Screening, Sandbox Guard, and Egress Proxy

- **Removal of `harness` & Central `access_refusal` (`BI-13`):**
  - `monoagens/gateways/harness/` is deleted completely (ADR-0005 §4.2), and `access_refusal` is enforced
    centrally in core `Agent.handle` / `TurnIntake.receive` for all external gateways.
- **Command & Wrapper Screening (`FD-11, ST-14`):**
  - `SHELL_WRAPPERS` in `guard/policy.py` and `guard/sandbox.py` recursively unwraps `timeout`, `flock`,
    `setsid`, `script`, `watch`, `doas`, `pkexec`, and preserves arguments passed to `env -S`.
  - Commands containing `${IFS}` variable expansion or brace expansion `{a,b}` are rejected.
  - `SAFE_HEADS` (`cat`, `sort`, `find`, `uniq`) verify that all path arguments resolve inside the
    allowed workspace and reject output/write flags (`-o`, `-fprint`, `-exec`, `-delete`).
  - `BLOCKED_RE` inspects command/subcommand tokens rather than plain file name arguments, allowing
    benign reads like `cat docker-compose.yml` and `grep sudo notes.txt`.
- **IPv6 Egress Proxy Hardening:** `deploy/sandbox-proxy/squid.conf` denies `::1/128`, `fc00::/7`,
  `fe80::/10`, `::ffff:0:0/96`, and `0.0.0.0/8` alongside RFC1918 IPv4 ranges.

### 4.5 Action-Based Approval Modes (`default`, `yolo`, `strict`) and Conversational Checkpoints

- **Elimination of `sensitive_tools` and Three-Tier `approval.mode`:**
  - `approval.sensitive_tools` is removed; gating is determined by what an action or command *does*
    rather than blanket-blocking `shell` or `sandbox` on read-only checks.
  - `engine.approval.mode` defaults to `default` and supports three tiers:
    1. **`default` (default):** Read-only tools (`read`, `grep`, `web_search`, `fetch`, `thread_history`,
       `memory` recall, `info`) and read-only shell/sandbox inspections (`SAFE_HEADS` within workspace)
       run immediately; all write, mutating, process-modifying, `settings`, task-scheduling, and
       cross-room/cross-gateway (`AUTHORIZATION`) actions pause for confirmation.
    2. **`yolo`:** Read-only and normal write/sandbox/shell actions run automatically; confirmation is
       required *only* when a command matches `sensitive_commands` (or `credential_read`), modifies
       `settings`, schedules a task (when `confirm_tasks: true`), or crosses room/gateway boundaries
       (`confirm_cross_gateway: true`).
    3. **`strict`:** Every tool call—including read-only tools and write/mutating actions—requires
       confirmation before execution, while normal conversational replies to the origin room (`CONSENT`)
       remain unblocked.
- **Smart Conversational Checkpoints & Multi-Step Continuation (`natural_confirm: true`):**
  - Multiple staged approvals may be created within a workflow (removing the single `ctx.approval_id`
    turn bottleneck).
  - When an action is parked as `pending`, the engine returns the structured pending state to `LLM.run`
    so the **LLM phrases the confirmation question naturally in conversation** (never a hardcoded template).
  - When `natural_confirm: true` and the sender has pending approval(s) in the active thread, natural
    conversational replies (e.g. *"Oke, kirim"*, *"Ya"*, *"Setuju"*, *"Batal"*, or *"Ganti ke grup B"*)
    are classified against the pending approval summary:
    - **`confirm`:** The engine deterministically verifies `scope_proof` and RBAC in Python, executes the
      approved action(s), and **feeds the execution receipt back into the active LLM turn loop** so the
      agent can immediately continue the next stage of a multi-step request (e.g. confirming file delivery
      to WhatsApp and then proposing the 11:00 reminder checkpoint).
    - **`modify` / `reject`:** Marks the pending row `superseded` or `denied` and lets the LLM adjust or
      cancel the plan naturally in conversation.
  - The explicit `/approve <id>` command remains supported as a direct shortcut and also resumes the
    follow-up turn step when applicable.

## 5. Configuration

Configured in `roles.yaml` and `config.yaml`:
```yaml
engine:
  approval:
    mode: default  # default | yolo | strict
    natural_confirm: true
    confirm_cross_gateway: true
    confirm_tasks: true
    settings: true
    ttl_seconds: 900
    sensitive_commands:
      - rm *
      - git push *--force*
```

## 6. Metrics and gates

- Shell evasion test suite (`ST-14` & `FD-11`): Verifies `timeout 5 git push --force`,
  `env -S "git push --force"`, `rm${IFS}-rf${IFS}/`, `cat ~/.ssh/id_rsa`, and `sort -o` are blocked,
  while `cat docker-compose.yml` inside the workspace is allowed.
- Identity absorption test (`ST-10`): Verifies an administrator typing a guest's `/link` code never
  loses their `per_…` identity or `administrator` role.
- Multi-role test: Verifies a user assigned `[administrator, member]` accesses WhatsApp via `member`
  fallback policy.

## 7. Acceptance criteria

- [ ] `RoleRegistry.policy_for` falls back across assigned roles in priority order.
- [ ] Default `guest` seed in `roles.yaml` excludes `read`, `grep`, `fetch`, `chat_history`, `link`, and `unlink`.
- [ ] `soul.yaml` uses `mode`/`modes` and `people.role` column is removed.
- [ ] `/link` rate-limits missing codes, locks rows with `FOR UPDATE`, protects role holders, and accepts 6-letter gateway names.
- [ ] `/unlink` revokes active relay grants and pending approvals and logs an `actions` row.
- [ ] `monoagens/gateways/harness/` is deleted, `access_refusal` runs centrally in core, and `guard` blocks wrapper/IFS/workspace escapes.
- [ ] `approval.sensitive_tools` is removed; `approval.mode` supports `default` (read-only allowed, writes/mutations ask), `yolo` (only `sensitive_commands`, `settings`, and cross-gateway/task checkpoints ask), and `strict` (all tools ask).
- [ ] Conversational confirmation (`natural_confirm: true`) resolves pending approvals with deterministic `scope_proof` checks and resumes the LLM turn loop for multi-step workflows.
- [ ] `deploy/sandbox-proxy/squid.conf` blocks IPv6 loopback and private address ranges.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Tighter default `guest` role restricts public bots | Operators can explicitly add tools/commands to `guest` in `roles.yaml` |
| `soul.yaml` `role -> mode` rename affects existing files | Config loader raises a clear migration error naming `mode` / `modes` |

## 9. Interaction with other ADRs

- **ADR-0001:** Enforces single outbound door (`Agent.dispatch`) for all guarded actions.
- **ADR-0002:** Shares word-boundary and pattern detector primitives in `guard/patterns.py`.
- **ADR-0004:** Evaluates `CONSENT` vs `AUTHORIZATION` classes for cross-gateway messaging.

## 10. Code references

- `monoagens/engine/access/roles.py`, `permissions.py`, `identity.py`, `relay.py`, `approvals.py`
- `monoagens/engine/commands/link.py`, `unlink.py`
- `monoagens/engine/guard/policy.py`, `sandbox.py`, `patterns.py`
- `monoagens/gateways/harness/gateway.py`, `tools/run.py`, `commands/inspect.py`
- `monoagens/engine/soul.py`, `deploy/sandbox-proxy/squid.conf`

---

*This ADR lives at `docs/v0.1.0/adr/engine/0006-access-control-roles-and-guard.md`.*
