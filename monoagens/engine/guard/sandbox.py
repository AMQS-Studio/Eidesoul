"""Two-layer sandbox guard: deterministic rules first, a small fast LLM second.

Layer 1 always runs and never needs a network call: it blocks host-escape and destructive
patterns outright, escalates long or sensitive commands, and allows plainly read-only
commands. Layer 2 only sees the commands layer 1 could not classify, and the whole gate
is skipped when `engine.guard.enabled` is false. A non-approver role never gets a pending
verdict: anything the guard cannot clear is denied.
"""


import asyncio
import json
import logging
import re
from pathlib import Path

from ..config import GuardConfig
from .policy import SafetyDecision, hidden_expansion, scan_command

log = logging.getLogger(__name__)

BLOCKED = (
    r"(?i)\brm\s+(-[^\s]+\s+)*-(rf|fr)\s+/(\s|$)",
    r"(?i)\brm\s+(-[^\s]+\s+)*-(rf|fr)\s+/\*",
    r"(?i)\bmkfs(\.[a-z0-9]+)?\b",
    r"(?i)\bdd\b[^\n]*\bof=/dev/",
    r"(?i):\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    r"(?i)\b(shutdown|reboot|poweroff|halt)\b",
    r"(?i)\b(mount|umount|nsenter|chroot|unshare|pivot_root)\b",
    r"(?i)\b(docker|podman)\b",
    r"(?i)\b(iptables|ip6tables|nft)\b",
    r"(?i)\b(curl|wget)\b[^\n|;]*\|\s*(ba|z)?sh\b",
    r"(?i)\b(nc|ncat|netcat)\b[^\n]*\s-e\s",
    r"/dev/tcp/",
    r"(?i)\b(base64|xxd|openssl)\b[^\n]*\|\s*(ba|z)?sh\b",
    r"(?i)\bchmod\s+(-R\s+)?777\s+/(\s|$)",
    r"(?i)\b(chown|chgrp)\s+(-R\s+)?\S+\s+/(\s|$)",
    r"(?i)\b(sudo|su)\b",
    r"(?i)\b(passwd|useradd|usermod|userdel|groupadd|groupdel)\b",
    r"(?i)\b(crontab|systemctl|service)\b",
    r"(?i)\bkill\s+-9\s+1\b",
)
BLOCKED_RE = tuple(re.compile(pattern) for pattern in BLOCKED)
SAFE_HEADS = frozenset(
    {
        "ls",
        "pwd",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "rg",
        "find",
        "sort",
        "uniq",
        "cut",
        "tr",
        "diff",
        "stat",
        "file",
        "which",
        "date",
        "echo",
        "printf",
        "true",
        "false",
        "tar",
        "git",
        "base64",
        "xxd",
        "od",
    }
)
# Heads that can execute code even when they look like plain filters.
CODE_HEADS = frozenset({"python", "python3", "node", "nodejs", "perl", "ruby", "php", "awk"})
# Heads whose whole output is the command; their plain path arguments are file names, so a
# read named `shutdown.log` is not a `shutdown` call (ADR-0049 §4.1.6).
READ_ONLY_HEADS = frozenset(
    {"cat", "head", "tail", "less", "wc", "stat", "file", "grep", "rg", "ls", "diff"}
)
# `git` subcommands whose path arguments are inputs to a read, not a command to run.
READ_ONLY_GIT = frozenset({"diff", "log", "show", "status"})
# Heads that need the write/exec and workspace checks below before they may run unattended.
WORKSPACE_HEADS = frozenset({"find", "tar", "git", "base64", "xxd", "od", "sort", "uniq"})
# Flags that turn a read-only head into a writer, an executor, or a directory escape.
WRITE_FLAGS = frozenset(
    {
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-delete",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-fls",
        "-o",
        "--output",
        "--to-command",
    }
)
CHDIR_FLAGS = frozenset({"-C", "--directory", "--chdir", "--git-dir", "--work-tree"})
# `tar` modes that create, extract, append, or update an archive instead of listing one.
TAR_WRITE_MODES = frozenset({"c", "x", "r", "u", "A"})
GIT_READ_VERBS = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "rev-parse",
        "ls-files",
        "ls-tree",
        "cat-file",
        "blame",
        "describe",
        "shortlog",
        "show-ref",
        "for-each-ref",
        "whatchanged",
        "grep",
    }
)
ONE_PATH_HEADS = frozenset({"base64", "xxd", "od", "uniq"})
SHELL_CHARS = re.compile(r"[;&|><$()`{}]")
GUARD_SYSTEM = (
    "You are a sandbox security classifier. Decide if this shell command, run inside a "
    "disposable container with only /work/public and /work/scratch mounted, is trying to "
    "escape, attack infrastructure, exfiltrate secrets, persist, or destroy host data. "
    "Treat unknown as suspicious. Reply JSON only: "
    '{"suspicious": true|false, "reason": "<=120 chars"}.'
)


def _verdict(kind: str, reason: str = "") -> SafetyDecision:
    return SafetyDecision(kind, reason)


def _inside_workspace(value: str) -> bool:
    """A path argument that stays under the mounted workdir: relative, no `..` climb."""
    text = value.strip("'\"")
    if not text or text.startswith("-"):
        return True
    if text == "/work" or text.startswith("/work/"):
        return True
    if text.startswith(("/", "~")):
        return False
    return ".." not in Path(text).parts


def _command_view(text: str) -> str:
    """The text matched against BLOCKED: quotes neutralized, file arguments of reads dropped.

    `cat docker-compose.yml` and `grep sudo notes.txt` are reads, so neither the file name nor the
    search pattern may match a blocked word; `shutdown -h now` keeps its command position.
    """
    parts = re.split(r"(\|\||&&|[;|&])", text)
    kept: list[str] = []
    for part in parts:
        if part in {"||", "&&", ";", "|", "&"}:
            kept.append(part)
            continue
        words = part.split()
        if not words:
            continue
        head = words[0].rsplit("/", 1)[-1]
        if head in READ_ONLY_HEADS:
            words = [words[0], *(word for word in words[1:] if word.startswith("-"))]
        elif head == "git" and len(words) > 1 and words[1] in READ_ONLY_GIT:
            words = [words[0], words[1], *(word for word in words[2:] if word.startswith("-"))]
        kept.append(" ".join(words))
    return " ".join(kept).replace("'", " ").replace('"', " ")


def _tar_read_only(words: list[str]) -> bool:
    """`tar` may list (`t`) an archive; creating, extracting, or updating writes files."""
    for word in words:
        if word.startswith("--"):
            if word.split("=", 1)[0] in {"--create", "--extract", "--append", "--update"}:
                return False
            continue
        if word.startswith("-"):
            letters = word[1:]
        elif word[:1].isalpha():
            letters = word  # old-style bundle: `tar tf archive`
        else:
            continue
        if set(letters) & TAR_WRITE_MODES:
            return False
    return True


def _git_read_only(words: list[str]) -> bool:
    """Only a read-only git subcommand is auto-allowed; `push`, `reset`, and friends are not."""
    index = 0
    while index < len(words):
        word = words[index]
        flag = word.split("=", 1)[0]
        if flag in CHDIR_FLAGS:
            index += 1 if "=" in word else 2
            continue
        if flag.startswith("-"):
            index += 1
            continue
        return flag in GIT_READ_VERBS
    return False


def _write_flag(words: list[str]) -> bool:
    """A flag that writes, executes, or changes directory out of the mounted workspace."""
    for index, word in enumerate(words):
        flag = word.split("=", 1)[0]
        if flag in WRITE_FLAGS:
            return True
        if flag in CHDIR_FLAGS:
            if "=" in word:
                target = word.split("=", 1)[1]
            else:
                target = words[index + 1] if index + 1 < len(words) else ""
            if not _inside_workspace(target):
                return True
    return False


def _read_only_violation(text: str) -> bool:
    """A read-only head carrying a write/exec flag or changing directory out of the workspace."""
    words = [word.strip("'\"") for word in text.split()]
    if not words:
        return False
    head = words[0].rsplit("/", 1)[-1]
    if head not in WORKSPACE_HEADS:
        return False
    if _write_flag(words[1:]):
        return True
    return head == "tar" and not _tar_read_only(words[1:])


def _simple_safe(text: str) -> bool:
    """Read-only pipeline-free command whose head is on the safe allow-list."""
    if SHELL_CHARS.search(text) or "\n" in text or "`" in text:
        return False
    words = text.split()
    if not words:
        return False
    head = words[0].rsplit("/", 1)[-1]
    if head in CODE_HEADS or head not in SAFE_HEADS:
        return False
    operands = [word for word in words[1:] if not word.startswith("-")]
    if any(not _inside_workspace(word) for word in operands):
        return False
    if head in WORKSPACE_HEADS:
        if _write_flag(words[1:]):
            return False
        if head == "tar" and not _tar_read_only(words[1:]):
            return False
        if head == "git" and not _git_read_only(words[1:]):
            return False
        if head in ONE_PATH_HEADS and len(operands) > 1:
            return False  # the second operand is an output file
    return not (head == "sed" and any(word.startswith("-i") for word in words[1:]))


def screen(
    command: str, sensitive_commands: tuple[str, ...], max_chars: int = 4000
) -> SafetyDecision | None:
    """Layer 1: deny, pending, allow, or `None` when only the classifier can decide."""
    text = str(command or "").strip()
    if not text:
        return _verdict("allow")
    if hidden_expansion(text):
        return _verdict("deny", "sandbox command blocked: shell expansion hides the command")
    view = _command_view(text)
    for pattern in BLOCKED_RE:
        if pattern.search(view):
            return _verdict("deny", "sandbox command blocked: host escape/destructive pattern")
    if _read_only_violation(text):
        return _verdict("deny", "sandbox command blocked: read-only command with a write flag")
    if len(text) > max(1, int(max_chars)):
        return _verdict("pending", "sandbox command too long")
    if scan_command(text, tuple(sensitive_commands)):
        return _verdict("pending", "sandbox command is sensitive; needs approval")
    if _simple_safe(text):
        return _verdict("allow")
    return None


def _parse(raw: str) -> dict:
    body = str(raw or "").strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class SandboxGuard:
    def __init__(self, config: GuardConfig, sensitive: tuple[str, ...], llm=None) -> None:
        self.config = config
        self.sensitive = tuple(sensitive)
        self.llm = llm

    async def check(
        self, command: str, *, can_approve: bool, thread_id: str = ""
    ) -> SafetyDecision:
        """Return allow, pending, or deny for one sandbox command."""
        if not self.config.enabled:
            return _verdict("allow")
        decision = self._layer_one(str(command or ""))
        if decision is not None and decision.verdict == "allow":
            return decision
        if decision is not None and decision.verdict == "deny":
            self._log(thread_id, decision)
            return decision
        if not can_approve:
            denied = _verdict("deny", "sandbox command needs an approver role")
            self._log(thread_id, denied)
            return denied
        if decision is not None:
            self._log(thread_id, decision)
            return decision
        classified = await self._classify(str(command or ""))
        self._log(thread_id, classified)
        return classified

    def _layer_one(self, command: str) -> SafetyDecision | None:
        return screen(command, self.sensitive, self.config.max_command_chars)

    async def _classify(self, command: str) -> SafetyDecision:
        if self.llm is None:
            return _verdict("pending", "sandbox guard unavailable")
        payload = json.dumps({"command": command}, ensure_ascii=False)
        try:
            raw = await asyncio.wait_for(
                self.llm.complete(
                    GUARD_SYSTEM,
                    payload,
                    model=self.config.model,
                    max_tokens=120,
                    temperature=0,
                    session="monoagens-guard",
                ),
                float(self.config.timeout_seconds),
            )
        except Exception:
            log.warning("sandbox guard classifier failed; asking for approval")
            return _verdict("pending", "sandbox guard unavailable")
        data = _parse(str(raw or ""))
        suspicious = data.get("suspicious")
        reason = " ".join(str(data.get("reason") or "").split())[:120]
        if suspicious is False:
            return _verdict("allow")
        if suspicious is True:
            detail = f"sandbox command looks suspicious: {reason}" if reason else (
                "sandbox command looks suspicious; needs approval"
            )
            return _verdict("pending", detail)
        return _verdict("pending", "sandbox guard unavailable")

    def _log(self, thread_id: str, decision: SafetyDecision) -> None:
        log.info(
            "tool=sandbox chat=%s verdict=%s reason=%s",
            thread_id or "-",
            decision.verdict,
            decision.reason or "-",
        )
