"""Guard contract: wrapper peeling, expansion rejection, read-only heads, and egress ranges.

ADR-0006 §4.4: `timeout 5 rm -rf /` and `env -S "rm -rf /"` are judged on the inner command,
`${IFS}`/`{a,b}` cannot split a command past the screen, read-only heads stay inside the mounted
workspace, and a file merely *named* `shutdown.log` is not a `shutdown` call.
"""

from pathlib import Path

from monoagens.engine.config import DEFAULT_SENSITIVE_COMMANDS
from monoagens.engine.guard import screen
from monoagens.engine.guard.policy import scan_command

SENSITIVE = DEFAULT_SENSITIVE_COMMANDS
SQUID = Path(__file__).resolve().parents[1] / "deploy" / "sandbox-proxy" / "squid.conf"


def verdict(command: str) -> str | None:
    decision = screen(command, SENSITIVE)
    return None if decision is None else decision.verdict


def test_wrapper_duration_is_peeled_before_the_inner_command() -> None:
    assert scan_command("timeout 5 git push --force", SENSITIVE) is True
    assert scan_command("timeout -s KILL 5 rm -rf x", SENSITIVE) is True
    assert verdict("timeout 5 rm -rf /") == "deny"


def test_env_split_string_is_scanned_not_dropped() -> None:
    assert scan_command("env -S 'git push --force'", SENSITIVE) is True
    assert scan_command("env -S bash -c 'systemctl restart x'", SENSITIVE) is True
    assert verdict("env -S bash -c 'rm -rf /'") == "deny"


def test_other_wrappers_reach_the_inner_command() -> None:
    assert scan_command("flock /tmp/lock rm -rf x", SENSITIVE) is True
    assert scan_command("flock -w 5 /tmp/lock rm -rf x", SENSITIVE) is True
    assert scan_command("setsid bash -c 'systemctl restart x'", SENSITIVE) is True
    assert scan_command("script -c 'rm -rf x' /tmp/out", SENSITIVE) is True
    assert scan_command("watch -n 5 rm -rf x", SENSITIVE) is True
    assert scan_command("doas root rm -rf x", SENSITIVE) is True
    assert scan_command("pkexec --user root rm -rf x", SENSITIVE) is True
    assert scan_command("timeout 5 ls -la", SENSITIVE) is False


def test_ifs_expansion_cannot_hide_a_command() -> None:
    assert verdict("rm${IFS}-rf${IFS}/") == "deny"
    assert verdict("rm$IFS-rf$IFS/") == "deny"


def test_brace_expansion_is_rejected() -> None:
    assert verdict("cat /etc/{passwd,shadow}") == "deny"
    assert verdict("rm -rf /{tmp,var}") == "deny"


def test_quoted_ifs_literal_is_not_expansion() -> None:
    assert verdict("echo '${IFS}'") != "deny"
    assert scan_command("echo '${IFS}'", SENSITIVE) is False


def test_read_only_heads_reject_write_and_exec_flags() -> None:
    assert verdict("find . -delete") == "deny"
    assert verdict("find . -exec rm {} ;") == "deny"
    assert verdict("find . -fprint hacked.txt") == "deny"
    assert verdict("sort -o hacked.txt notes.txt") == "deny"
    assert verdict("tar --to-command=sh -xf archive.tar") == "deny"
    assert verdict("tar xf archive.tar") == "deny"
    assert verdict("git -C / status") == "deny"
    assert verdict("git --git-dir=/root status") == "deny"


def test_read_only_heads_stay_inside_the_workspace() -> None:
    assert verdict("cat /etc/shadow") != "allow"
    assert verdict("cat /work/public/notes.txt") == "allow"
    assert verdict("git status") == "allow"
    assert verdict("sort notes.txt") == "allow"


def test_file_arguments_do_not_trip_blocked_patterns() -> None:
    assert verdict("cat reboot-notes.txt") == "allow"
    assert verdict("cat shutdown.log") == "allow"
    assert verdict("cat docker-compose.yml") == "allow"
    assert verdict("grep sudo notes.txt") == "allow"
    assert verdict("head -n 5 poweroff-notes.txt") == "allow"


def test_real_blocked_commands_still_denied() -> None:
    assert verdict("shutdown -h now") == "deny"
    assert verdict("reboot") == "deny"
    assert verdict("sudo -i") == "deny"
    assert verdict("rm -rf /") == "deny"


def test_squid_proxy_denies_private_ranges() -> None:
    text = SQUID.read_text(encoding="utf-8")
    for block in (
        "0.0.0.0/8",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "::ffff:0:0/96",
    ):
        assert f"acl internal dst {block}" in text
    assert text.index("http_access deny internal") < text.index("http_access allow all")
