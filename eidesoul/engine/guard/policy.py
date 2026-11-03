"""SafetyPolicy: the deterministic gate before sensitive tools (seam for a physical robot)."""


import fnmatch
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import ApprovalConfig
from ..registry import allows

READ_ONLY = frozenset({"read", "memory", "skill", "chat_history", "web_search"})

# ADR-0007 §4.7: the target, not the tool, decides who approves.
AUTHORIZATION_TOOLS = frozenset({"shell", "sandbox", "settings", "send", "send_to"})
CONSENT = "consent"
AUTHORIZATION = "authorization"
CLASSES = frozenset({CONSENT, AUTHORIZATION})
# ADR-0007 §4.8: reading a host secret is authorization, whatever the requester's role.
CREDENTIAL_PATTERNS = (
    "env",
    "printenv",
    "export -p",
    "set",
    "*.env",
    ".env",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    ".aws/credentials",
    ".netrc",
    "/proc/*/environ",
    "/proc/*/cmdline",
)
SECRET_FILE = re.compile(
    r"(^|/)(\.env(\.[^/]*)?|[^/]*\.env|[^/]*\.pem|[^/]*\.key|id_rsa|id_ed25519|"
    r"credentials|\.netrc)$"
)

SHELL_TOOL = "shell"
SHELL_WRAPPERS = frozenset(
    {
        "sudo",
        "env",
        "nohup",
        "nice",
        "ionice",
        "time",
        "command",
        "builtin",
        "exec",
        "stdbuf",
        "xargs",
    }
)
WRAPPER_VALUES = {
    "sudo": frozenset(
        {
            "-u",
            "-g",
            "-p",
            "-r",
            "-t",
            "-U",
            "-C",
            "-h",
            "--user",
            "--group",
            "--prompt",
            "--role",
            "--type",
            "--chdir",
            "--host",
        }
    ),
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-t", "-p", "--class", "--classdata", "--pid"}),
    "xargs": frozenset(
        {
            "-I",
            "-n",
            "-P",
            "-L",
            "-s",
            "-d",
            "-E",
            "-a",
            "--replace",
            "--max-args",
            "--max-procs",
            "--max-lines",
            "--max-chars",
            "--delimiter",
            "--eof",
            "--arg-file",
        }
    ),
    "exec": frozenset({"-a"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
}
SHELL_INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
SHELL_OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")"})
DESTRUCTIVE_ACTIONS = {
    "memory": frozenset({"forget"}),
    "skill": frozenset({"archive"}),
}
MAX_DEPTH = 3
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


@dataclass(frozen=True)
class ActionRequest:
    tool: str
    args: dict
    thread_id: str
    sender: str
    gateway: str
    roles: tuple[str, ...]
    workspace: str = ""
    roots: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()  # already resolved by the caller, never re-derived here


@dataclass(frozen=True)
class SafetyDecision:
    verdict: str  # "allow" | "pending" | "deny"
    reason: str = ""


class SafetyPolicy(Protocol):
    def check(self, request: ActionRequest) -> SafetyDecision: ...


class AllowAllSafety:
    """approval.mode=off: every tool runs immediately (document the risk)."""

    def check(self, request: ActionRequest) -> SafetyDecision:
        return SafetyDecision("allow")


def _tokens(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _segments(tokens: list[str]) -> list[list[str]]:
    found: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in SHELL_OPERATORS:
            if current:
                found.append(current)
                current = []
        else:
            current.append(token)
    if current:
        found.append(current)
    return found


def _sensitive_segment(tokens: list[str], patterns: tuple[str, ...], depth: int) -> bool:
    tokens = list(tokens)
    while True:
        while tokens and _ASSIGNMENT.fullmatch(tokens[0]):
            tokens.pop(0)
        if not tokens:
            return False
        head = os.path.basename(tokens[0])
        if head not in SHELL_WRAPPERS:
            break
        values = WRAPPER_VALUES.get(head, frozenset())
        tokens.pop(0)
        while tokens and tokens[0].startswith("-"):
            option = tokens.pop(0)
            if option in values and tokens:
                tokens.pop(0)
    head = os.path.basename(tokens[0])
    if head in SHELL_INTERPRETERS:
        for index, token in enumerate(tokens[1:], 1):
            if token.startswith("-") and not token.startswith("--") and "c" in token:
                if index + 1 >= len(tokens):
                    return True
                return _scan(tokens[index + 1], patterns, depth + 1)
    text = " ".join([head, *tokens[1:]]) + " "
    return any(fnmatch.fnmatchcase(text, pattern) for pattern in patterns)


def _scan(text: str, patterns: tuple[str, ...], depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return True
    for line in text.splitlines() or [text]:
        remainder = line
        for match in _SUBSTITUTION.finditer(line):
            inner = match.group(1) if match.group(1) is not None else (match.group(2) or "")
            if _scan(inner, patterns, depth + 1):
                return True
            remainder = remainder.replace(match.group(0), " ", 1)
        if "$(" in remainder or "`" in remainder:
            return True
        try:
            tokens = _tokens(remainder)
        except ValueError:
            return True
        for segment in _segments(tokens):
            if _sensitive_segment(segment, patterns, depth):
                return True
    return False


def scan_command(text: str, patterns: tuple[str, ...]) -> bool:
    """Public wrapper over the deterministic shell scanner used by the approval policy."""
    return _scan(str(text or ""), tuple(patterns))


def credential_read(text: str, extra: tuple[str, ...] = ()) -> bool:
    """Does this raw command/path read a host secret? (ADR-0007 §4.8)

    Deterministic and conservative: a bare `env`/`printenv`, `export -p`, a secret file, or a
    `/proc/*/environ` read is a credential read. `env VAR=x cmd` is a wrapper, not a dump.
    """
    body = str(text or "")
    additions = tuple(str(item) for item in extra if str(item).strip())
    for line in body.splitlines() or [body]:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            tokens = shlex.split(stripped, posix=True)
        except ValueError:
            tokens = stripped.split()
        if not tokens:
            continue
        head = os.path.basename(tokens[0])
        rest = [token for token in tokens[1:] if not _ASSIGNMENT.fullmatch(token)]
        if _dump_command(head, tokens, rest):
            return True
        if any(_secret_token(token, additions) for token in tokens):
            return True
    return False


def _dump_command(head: str, tokens: list[str], rest: list[str]) -> bool:
    """One of the commands whose whole purpose is printing the environment."""
    if head == "printenv":
        return True
    if head == "env" and not rest:
        return True
    if head == "export" and "-p" in tokens:
        return True
    if head == "set" and len(tokens) == 1:
        return True
    return head == "docker" and (
        tokens[1:2] == ["inspect"] or tokens[1:3] == ["compose", "config"]
    )


def _secret_token(token: str, additions: tuple[str, ...]) -> bool:
    """A path that names a secret file, a proc env dump, or an operator-listed path."""
    if token.startswith("/proc/") and token.endswith(("environ", "cmdline")):
        return True
    if SECRET_FILE.search(token):
        return True
    return any(fnmatch.fnmatchcase(token, pattern) for pattern in additions)


def _absolute(request: ActionRequest, raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path(request.workspace or ".").expanduser() / path
    return str(path)


def action_targets(request: ActionRequest) -> list[dict]:
    """The deterministic target list stored in `scope_proof` (path, room, column, host)."""
    args = request.args if isinstance(request.args, dict) else {}
    targets: list[dict] = []
    if request.tool != "settings":
        if request.paths:
            targets.extend({"kind": "path", "value": str(item)} for item in request.paths)
        else:
            path = _absolute(request, str(args.get("path") or ""))
            if path:
                targets.append({"kind": "path", "value": path})
    if request.tool in {"send", "send_to"}:
        room = str(args.get("chat") or "").strip()
        targets.append({"kind": "room", "value": room or request.thread_id})
    if request.tool == "settings":
        value = f"{args.get('file') or ''}:{args.get('path') or ''}"
        targets.append({"kind": "config", "value": value})
    if request.tool == "memory":
        targets.append(
            {
                "kind": "column",
                "value": f"memory:{args.get('action') or ''}:{args.get('scope') or ''}",
            }
        )
    if request.tool in {"shell", "sandbox"}:
        targets.append({"kind": "host", "value": request.tool})
    url = str(args.get("url") or "").strip()
    if url:
        targets.append({"kind": "url", "value": url})
    return targets


def _inside(value: str, roots: tuple[str, ...]) -> bool:
    if not value or not roots:
        return False
    path = Path(value)
    for root in roots:
        try:
            if path.is_relative_to(Path(root)):
                return True
        except (OSError, ValueError):
            continue
    return False


def approval_class(request: ActionRequest, *, extra: tuple[str, ...] = ()) -> str:
    """`consent` when every target is inside the requester's scope, else `authorization`."""
    args = request.args if isinstance(request.args, dict) else {}
    if request.tool in AUTHORIZATION_TOOLS:
        return AUTHORIZATION
    command = " ".join(
        str(args.get(key) or "") for key in ("command", "text", "query")
    )
    if credential_read(command, extra):
        return AUTHORIZATION
    if request.tool == "memory":
        action = str(args.get("action") or "").strip().lower()
        scope = str(args.get("scope") or "").strip().lower()
        if scope == "global" and action in {"add", "forget", "veto"}:
            return AUTHORIZATION
    for target in action_targets(request):
        kind = target["kind"]
        value = str(target["value"])
        if kind in {"config", "host"}:
            return AUTHORIZATION
        if kind == "path" and not _inside(value, request.roots):
            return AUTHORIZATION
        if kind == "path" and request.tool == "write" and _inside(
            value, (str(Path(request.workspace) / "public"),)
        ):
            return AUTHORIZATION
        if kind == "room" and value not in {"", request.thread_id}:
            return AUTHORIZATION
    return CONSENT


def scope_proof(request: ActionRequest, *, extra: tuple[str, ...] = ()) -> dict:
    """The proof computed at request time and re-checked at execution (ADR-0007 §4.7)."""
    return {
        "targets": action_targets(request),
        "roots": list(request.roots),
        "class": approval_class(request, extra=extra),
    }


def approval_summary(tool: str, request: ActionRequest, proof: dict) -> str:
    """The request summary: resolved targets for consent, requester + artifacts for authority."""
    targets = proof.get("targets") if isinstance(proof, dict) else None
    where = ", ".join(
        f"{item.get('kind')}={item.get('value')}"
        for item in (targets or [])
        if isinstance(item, dict)
    )
    where = where or "no explicit target"
    if proof.get("class") == CONSENT:
        text = f"{tool} inside your scope: {where}"
    else:
        who = " ".join(str(request.sender or "").split()) or "the sender"
        text = f"{who} wants to use {tool} outside their scope: {where}"
    return text[:400]


def recheck_scope(stored: object, current: dict) -> str:
    """`""` when the stored proof still holds; a reason when it does not (ADR-0007 §4.7)."""
    if not isinstance(stored, dict) or not stored:
        return "the approval has no scope proof; it is refused"
    if stored.get("class") != current.get("class"):
        return (
            "the approval class changed: "
            f"{stored.get('class')} -> {current.get('class')}"
        )
    if stored.get("targets") != current.get("targets"):
        return "the targets changed since the approval was requested"
    old = set(stored.get("roots") or [])
    new = set(current.get("roots") or [])
    if new - old:
        return "the requester's reach widened since the approval was requested"
    return ""


class ApprovalPolicy:
    def __init__(self, config: ApprovalConfig) -> None:
        self.configure(config)

    def configure(self, config: ApprovalConfig) -> None:
        self.config = config

    def check(self, request: ActionRequest) -> SafetyDecision:
        if self.config.mode == "off":
            return SafetyDecision("allow")
        if self.config.mode == "strict" and request.tool not in READ_ONLY:
            return SafetyDecision("pending", "strict mode: every action waits for /approve")
        if allows(tuple(self.config.sensitive_tools), request.tool):
            return SafetyDecision(
                "pending", f"{request.tool} is a sensitive tool; it needs /approve"
            )
        if request.tool == SHELL_TOOL and scan_command(
            str(request.args.get("command") or ""), self.config.sensitive_commands
        ):
            return SafetyDecision("pending", "shell command is sensitive; needs /approve")
        actions = DESTRUCTIVE_ACTIONS.get(request.tool)
        if actions and str(request.args.get("action") or "").strip().lower() in actions:
            return SafetyDecision(
                "pending",
                f"{request.tool} {request.args.get('action')} is destructive; it needs /approve",
            )
        return SafetyDecision("allow")


def build_safety(config: ApprovalConfig) -> SafetyPolicy:
    if config.mode == "off":
        return AllowAllSafety()
    return ApprovalPolicy(config)
