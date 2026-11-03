# ADR-0022: Role `administrator`

- **Status:** accepted
- **Implementation:** implemented — evidence: `engine/roles.py` (`label`, `protected`, `require_administrator`, `assert_protected_roles`), `admin/roles.py` import migration with backup + audit, `roles.yaml`/templates/defaults renamed, `/whoami`/`/help`/guard agree, `tests/test_roles.py`; gate result: `pytest -q` 680 passed
- **Date:** 2026-11-03
- **Deciders:** Muqsith (owner), omp (reviewer)
- **Context:** the role name `administrator` collides with three other concepts that already use the same word (`owner_id` provenance, `administrator` in automation manifests, `lease_owner`); the owner decided to rename the role to `administrator`, to keep that role **protected** (it may not be deleted), and to let every role carry an optional **address term** (`label`) that tells the soul how to address its holder ("God", "Master", "Dev", ...)
- **Supersedes:** —
- **Related:** ADR-0001 (roles seed/mirror), ADR-0007 (approval class & tool matrix), ADR-0008 (`owner_id` provenance), ADR-0012 (`lease_owner`), ADR-0014 (`administrator` in automation), ADR-0016 (`config_changes`), ADR-0019 (log/role), ADR-0021 (scope administrator)
- **Location note:** ADR lives in `docs/v0.1.0/adr/engine/`; the Indonesian copy is `docs/v0.1.0/adr/engine/id/0022-administrator-role.md`.

---

## 1. Context

The word `administrator` is used for four different things:

| Meaning | Evidence | To change? |
|---|---|---|
| **Role** with the highest permission | `roles.yaml:31` `owner:`; `stores/recipes.py:29` `DEFAULT_ROLES = ("owner", "member")`; `admin/templates.py:143,158-159`; document `role:administrator` | **yes** → `administrator` |
| Owner of facts in memory | `owner_id` (ADR-0008), `owner/subject` (ADR-0006) | **no** |
| Automation owner | manifest `owner: person:...`, `owner=system`, `owner_roles_at_creation` (ADR-0014) | **no** |
| lease holder | `lease_owner` (ADR-0013), "owner generation" (ADR-0012) | **no** |

Moreover, the soul today only sees role + description (`prompts.py:141-147`), so it has no way to mention the role holder other than the person's name. The address the operator wants ("call him Master") has no place to live.

## 2. Decision drivers

- Unambiguous identifier: `administrator` means only the role.
- The highest role cannot be lost to a mis-edit: protection lives in the loader, not in operator discipline.
- The name is part persona/address for **any role** and is **optional**; it is neither permission nor identifier.
- Without alias/shim: one name, one meaning, a one-shot migration.
- Migration must not lower rights quietly.

## 3. Options considered

| Options | Contents | Cost | Risks | Verdict |
|---|---|---|---|---|
| **A. Hard rename + one-shot migration** | `administrator` → `administrator`, migration of the document on `config import`, without alias | moderate (one migration) | One migration error = no permission holder | **Selected** |
| **B. Alias (`administrator` stays valid)** | two names for one role | small | two names forever; breaks the no-shim rule; confuses the register terms | Rejected |
| **C. Status quo** | unchanged | zero | the four-way ambiguity remains | Rejected |

## 4. Decision

### 4.1 Identifier role

- The role name is **`administrator`**. Every place that names the role — `roles.yaml`, the document `role:<name>`, `DEFAULT_ROLES`, the admin template, the list `approval.roles`, and the address in the prompt — uses that name.
- The word `administrator` **ceases** to be the role name. It remains legal for the three other meanings in §1 and may not be used as a substitute for `administrator` in code or documents (register ADR-0009 rejects it). Prose in another ADR that still writes `administrator` for the role is read as `administrator`; the mechanical sweep happens at implementation time (a grep test for the role literal), not by changing the meaning of data.

### 4.2 Protected roles

- `administrator` carries `protected: true` (forced by the loader; the value `false` is rejected when loading).
- The loader **rejects boot** when: the role `administrator` does not exist; its `users` is empty; its `tools` does not include `"*"`; or its `commands` does not contain `approve`.
- This role **cannot be removed** and its identifier **cannot be renamed** via the role editor/admin manager; changes go only through `roles.yaml` + `eidesoul config import` (with a backup) or an action by the owner on the host.
- There is no path that creates the role `administrator` from a conversation; any tool/command that writes roles rejects this name except through import.

### 4.3 Address term (`label`) optional for every role

- **Every** role **may** carry a `label` (optional, empty by default), not just `administrator`. Its function is single: to tell the soul **who holds this role and what they want to be called**.
- The `label` goes into the `## Roles` block in the prompt (`roles_block`, `prompts.py:141-147`) as address text, e.g.:
`- administrator: role...; panggil dia "Majikan"`.
- When the `label` is empty: the soul uses the person's name (`people.name`) if there is one; otherwise it uses the role name. The label is never used to fill a name in logs, audit, or ids.
- The `label` **never** enters permission, is never compared in code, and never replaces the identifier; code always compares `role.name`.
- The `label` is the operator's text (T0), not the conversation's: it can only be changed through `roles.yaml` + `config import`, not through a tool/command. Its length is restricted (e.g. 40 characters) and it must not be whitespace-only.
- Changing the `label` requires no migration, changes the audit trail only through `config_changes`, and does not change who can approve.

### 4.4 Migration in one go

- `eidesoul config import` runs the migration: document `role:administrator` → `role:administrator` (the document name and the content of `name`), the key `owner:` in `roles.yaml` → `administrator:`, the role list in `approvals.roles` that still mentions `administrator` → `administrator`, and the field `administrator` on automation lines is left alone (it is the job owner, not the role).
- A backup is written before the changes; the migration is idempotent and recorded in `config_changes` as `role_rename`.
- Boot **fails with a message naming the migration command** if `role:administrator` is still present and `role:administrator` is not.
- If **both exist**, boot fails (no automatic merge): the operator chooses which one is correct, since merging could give permission to the wrong holder.

### 4.5 Code touch points

| File | Changes |
|---|---|
| `roles.yaml` | key `owner:` → `administrator:` + `label` (optional, every role), `protected: true` |
| `eidesoul/engine/stores/recipes.py:29` | `DEFAULT_ROLES = ("administrator", "member")` |
| `eidesoul/admin/templates.py:143,158-159` | default role in the template |
| role name check on engine | every literal `"owner"` that means the role → `"administrator"` |
| `eidesoul/engine/turn/prompts.py:141-147` | `roles_block` includes `label` when present (`; panggil dia "..."`) |
| `ADR-0015` `redact_infra: non_owner` | → value `non_administrator` |
| `ADR-0019` `status_owner_only` | → `status_administrator_only` |
| `ADR-0007` role approver | → `(administrator)` |

Data that does **not** change: the column `owner_id`, `thread_people`, `lease_owner`, and `administrator` in automation manifests.

## 5. Configuration

```yaml
roles:
 administrator:
 name: administrator
 label: administrator # optional; free-form: "Tuhan", "Majikan", "Dev"
 protected: true # enforced by the loader; false is rejected
 tools: ["*"
 memory_admin: true
 commands: ["*"] # must include approve
 member:
 name: member
 label: "" # optional; empty -> use the person name / role name
 tools: [read, grep, fetch, web_search, sandbox, chat_history, memory, google, send
 memory_admin: false
```

## 6. Metrics and gates

| Metric | Definition | Gate |
|---|---|---|
| `role_load_error_total` | boot rejected because the role administrator is invalid | 0 on healthy installation |
| `role_rename_migrations_total` | migration `administrator` → `administrator` | 0 or 1 per installation; > 1 = the migration is not idempotent |
| `label_changes_total` | any change to a role label | recorded in `config_changes` |
| `role_write_rejected_total` | attempt to remove/rename administrator | > 0 on test, 0 on normal operation |

## 7. Acceptance criteria

- [ ] `roles.yaml` with `owner:` and documents `role:administrator` trigger boot failure + messages containing the migration command.
- [ ] `config import` migrates both, writes a backup, is idempotent, and is recorded as `role_rename`.
- [ ] If `role:administrator` and `role:administrator` both exist, boot fails without merging.
- [ ] Deleting the role `administrator` via the editor/command is rejected; changing its identifier is rejected; `protected: false` is rejected at load.
- [ ] `users` empty, `tools` without `"*"`, or `commands` without `approve` → boot fails.
- [ ] The `label` can be set on **any role** (test: `member` + `administrator`), and `roles_block` includes it (`; panggil dia "..."`).
- [ ] The `label` is empty on any role → the prompt uses the person or role name; no blank label is printed.
- [ ] Changing the `label` does not change the outcome of any permission check (test: "God" vs. empty, identical permission matrix).
- [ ] The `label` cannot be changed from a conversation (tool/command write object), and never appears in log/audit as an identifier.
- [ ] Grep test: no literal `"owner"` that means the role in `eidesoul/` and `roles.yaml`.
- [ ] `owner_id`, `lease_owner`, and `administrator` in automation keep working after the migration (round-trip test).
- [ ] `approvals.roles` entries containing `administrator` are migrated; an unmapped old approval is marked and rejected at `/approve`, not executed.

## 8. Risks and mitigations

| Risks | Mitigation |
|---|---|
| Migration goes wrong → no permission holder | backup before writing, fail fast, boot blocked without `administrator` |
| Two role documents (owner + administrator) | boot fails; the operator selects one, no automatic merge |
| Label used as identifier or permission | code only compares `role.name`; test labels vs. permission |
| Label used to slip instructions through the conversation | changeable only through `roles.yaml` + import (T0), not through a tool/command |
| Protected role locked completely while misconfigured | the boot message names the wrong field; fix by passing `roles.yaml` + import |
| The old approval points at the missing role | marked during migration; `/approve` rejects it |
| Admin editor rewriting role | role endpoints reject removing/renaming `administrator`; only `label` may change |

## 9. Interaction with other ADRs

- **ADR-0007** the approval class names the `authorization` decider as `administrator`, not `administrator`.
- **ADR-0001** `roles.yaml` stays seed+mirror; `config import` is the only legal writing path.
- **ADR-0008** `owner_id` does not change its meaning or its name; it is provenance, not a role.
- **ADR-0014** `administrator` in a fixed automation manifest means job owner; not renamed.
- **ADR-0016** `config_changes` notes `role_rename`; no change in DDL.
- **ADR-0019** log/`/status` use `role.name`; the label is never entered as an identifier.
- **ADR-0021** scope `administrator` (all workspace, SSRF fixed) uses the new name.
- **ADR-0009** the register adds `administrator` and `label`, and rejects `administrator`/`admin`/`root` as the role name.

## 10. Code references

- `roles.yaml:31` — the role key `administrator` (rename target).
- `eidesoul/engine/stores/recipes.py:29` `DEFAULT_ROLES`.
- `eidesoul/admin/templates.py:143,158-159`
- `eidesoul/engine/turn/prompts.py:141-147` `roles_block` (where `label` is used).
- `eidesoul/engine/access/permissions.py` `normalize`/`allows` (the role name is normalized here).
- `eidesoul/engine/config.py` the `roles` section (seed/import).
- `eidesoul/engine/schema.py` `people.name` (fallback address if the label is empty); no change to the role table.
- `AGENTS.md` — role rule in the safety section (called `administrator`).

---

*This ADR lives at `docs/v0.1.0/adr/engine/0022-administrator-role.md`.*
