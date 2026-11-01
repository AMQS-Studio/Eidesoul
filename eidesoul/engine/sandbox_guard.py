"""Two-layer sandbox guard: deterministic rules first, a small fast LLM second.

Layer 1 always runs and never needs a network call: it blocks host-escape and destructive
patterns outright, escalates long or sensitive commands, and allows plainly read-only
commands. Layer 2 only sees the commands layer 1 could not classify, and the whole gate
is skipped when `engine.guard.enabled` is false. A non-approver role never gets a pending
verdict: anything the guard cannot clear is denied.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from .config import GuardConfig
from .safety import SafetyDecision, scan_command

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
    }
)
# Heads that can execute code even when they look like plain filters.
CODE_HEADS = frozenset({"python", "python3", "node", "nodejs", "perl", "ruby", "php", "awk"})
FIND_EXEC = frozenset({"-exec", "-execdir", "-delete", "-ok", "-okdir"})
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
    if head == "find" and any(word in FIND_EXEC for word in words[1:]):
        return False
    return not (head == "sed" and any(word.startswith("-i") for word in words[1:]))


def screen(
    command: str, sensitive_commands: tuple[str, ...], max_chars: int = 4000
) -> SafetyDecision | None:
    """Layer 1: deny, pending, allow, or `None` when only the classifier can decide."""
    text = str(command or "").strip()
    if not text:
        return _verdict("allow")
    for pattern in BLOCKED_RE:
        if pattern.search(text):
            return _verdict("deny", "sandbox command blocked: host escape/destructive pattern")
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

    async def check(self, command: str, *, can_approve: bool, chat_id: str = "") -> SafetyDecision:
        """Return allow, pending, or deny for one sandbox command."""
        if not self.config.enabled:
            return _verdict("allow")
        decision = self._layer_one(str(command or ""))
        if decision is not None and decision.verdict == "allow":
            return decision
        if decision is not None and decision.verdict == "deny":
            self._log(chat_id, decision)
            return decision
        if not can_approve:
            denied = _verdict("deny", "sandbox command needs an approver role")
            self._log(chat_id, denied)
            return denied
        if decision is not None:
            self._log(chat_id, decision)
            return decision
        classified = await self._classify(str(command or ""))
        self._log(chat_id, classified)
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
                    reasoning_effort="minimal",
                    session="eidesoul-guard",
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

    def _log(self, chat_id: str, decision: SafetyDecision) -> None:
        log.info(
            "tool=sandbox chat=%s verdict=%s reason=%s",
            chat_id or "-",
            decision.verdict,
            decision.reason or "-",
        )
