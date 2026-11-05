"""Approval classification contract: only destructive shell and internal actions wait."""


import pytest

from monoagens.engine.config import (
    DEFAULT_SENSITIVE_COMMANDS,
    ApprovalConfig,
    ConfigError,
)
from monoagens.engine.guard import (
    ActionRequest,
    ApprovalPolicy,
    approval_class,
    credential_read,
    recheck_scope,
    scan_command,
    scope_proof,
)
from monoagens.engine.tools import REGISTRY as TOOLS


def declared(tool: str) -> dict:
    """The same metadata `execute` passes from the handler declaration."""
    spec = TOOLS.by_name.get(tool)
    if spec is None:
        return {}
    return {
        "target": spec.target,
        "declared_class": spec.approval_class,
        "destructive": spec.destructive,
        "path_arg": spec.path_arg,
        "scan": spec.scan,
    }


def scoped(tool: str, *, roots=("/w/public", "/w/scratch/c"), roles=("member",), **args):
    return ActionRequest(
        tool=tool,
        args=args,
        thread_id="c",
        sender="p",
        gateway="g",
        roles=roles,
        workspace="/w",
        roots=roots,
        **declared(tool),
    )


def test_consent_inside_the_requester_scope() -> None:
    request = scoped("write", path="/w/scratch/c/note.txt")

    proof = scope_proof(request)

    assert proof["class"] == "consent"
    assert proof["targets"] == [
        {"kind": "path", "value": "/w/scratch/c/note.txt"},
        {"kind": "body", "value": "g"},
    ]


def test_authorization_outside_the_scope_or_on_the_host() -> None:
    assert approval_class(scoped("write", path="/w/scratch/other/x")) == "authorization"
    assert approval_class(scoped("shell", command="ls")) == "authorization"
    assert approval_class(scoped("settings", file="engine", path="llm.model")) == "authorization"


def test_credentials_are_always_authorization() -> None:
    for command in ("env", "printenv", "cat .env", "cat /proc/self/environ"):
        assert credential_read(command) is True, command
        request = scoped("shell", command=command, roots=("/w",), roles=("administrator",))
        assert approval_class(request) == "authorization"


def test_a_widened_reach_cancels_the_proof() -> None:
    before = scope_proof(scoped("write", path="/w/scratch/c/x"))
    after = scope_proof(scoped("write", path="/w/scratch/c/x", roots=("/w",)))

    assert recheck_scope(before, after) != ""
    assert recheck_scope(before, before) == ""


def request(tool: str, **args) -> ActionRequest:
    return ActionRequest(
        tool=tool,
        args=args,
        thread_id="c",
        sender="s",
        gateway="g",
        roles=("owner",),
        **declared(tool),
    )


def verdict(config: ApprovalConfig, tool: str, **args) -> str:
    return ApprovalPolicy(config).check(request(tool, **args)).verdict


ALLOWED_SHELL = [
    "ls -la",
    "git status",
    "printf monoagens-ok",
    "python -c 'print(1)'",
    "echo hi > notes.txt",
]

BLOCKED_SHELL = [
    "rm -rf x",
    "/bin/rm x",
    "sudo -u root rm -rf x",
    "bash -c 'rm -rf x'",
    "echo $(rm -rf x)",
    "dd if=/dev/zero of=x",
    "git reset --hard HEAD",
    "git clean -fd",
    "git push --force origin main",
    "chmod 777 x",
    "systemctl restart x",
    "find . -delete",
    'echo "unterminated',
]


@pytest.mark.parametrize("command", ALLOWED_SHELL)
def test_ordinary_shell_command_runs_without_approval(command: str) -> None:
    assert verdict(ApprovalConfig(mode="yolo"), "shell", command=command) == "allow"


@pytest.mark.parametrize("command", BLOCKED_SHELL)
def test_destructive_shell_command_waits_for_approval(command: str) -> None:
    assert verdict(ApprovalConfig(mode="yolo"), "shell", command=command) == "pending"


def test_strict_mode_gates_every_tool_call() -> None:
    config = ApprovalConfig(mode="strict")
    assert verdict(config, "read", file="a.txt") == "pending"
    assert verdict(config, "shell", command="ls") == "pending"


def test_mode_off_is_gone_and_points_at_yolo() -> None:
    with pytest.raises(ConfigError, match="use 'yolo'"):
        ApprovalConfig(mode="off")


def test_default_mode_pauses_state_changes_and_runs_reads() -> None:
    config = ApprovalConfig(mode="default")
    assert verdict(config, "read", file="a.txt") == "allow"
    assert verdict(config, "memory", action="search", query="x") == "allow"
    assert verdict(config, "memory", action="add", text="a") == "pending"
    assert verdict(config, "memory", action="forget", query="x") == "pending"
    assert verdict(config, "recipe", action="list") == "allow"
    assert verdict(config, "recipe", action="remove", name="x") == "pending"
    assert verdict(config, "write", path="/w/scratch/c/a.txt") == "pending"


def test_yolo_mode_trusts_ordinary_tools_but_not_sensitive_ones() -> None:
    config = ApprovalConfig(mode="yolo")
    assert verdict(config, "memory", action="forget") == "allow"
    assert verdict(config, "recipe", action="remove", name="x") == "allow"
    assert verdict(config, "shell", command="ls") == "allow"
    assert verdict(config, "shell", command="rm -rf x") == "pending"


def test_empty_sensitive_commands_disables_command_gate() -> None:
    assert (
        verdict(ApprovalConfig(mode="yolo", sensitive_commands=()), "shell", command="rm -rf x")
        == "allow"
    )


def test_scan_command_is_the_shared_scanner() -> None:
    assert scan_command("rm -rf x", DEFAULT_SENSITIVE_COMMANDS) is True
    assert scan_command("bash -c 'systemctl restart x'", DEFAULT_SENSITIVE_COMMANDS) is True
    assert scan_command("ls -la", DEFAULT_SENSITIVE_COMMANDS) is False
    assert scan_command("ls -la", ()) is False
