"""Defaults, not examples (ADR-0027 §4.6): the shipped host files are usable as they stand.

The guard is deliberately narrow: it checks the surfaces we ship (the repository root plus the
documentation, script, test, and package trees) so it stays fast and never walks `.venv`.
"""


from pathlib import Path

import yaml

from eidesoul.admin import templates

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = (ROOT, ROOT / "docs", ROOT / "scripts", ROOT / "tests", ROOT / "eidesoul")


def test_no_example_file_is_shipped():
    found = sorted(path.name for base in SHIPPED for path in base.glob("*.example*"))
    assert found == []


def test_init_template_writes_exactly_the_host_files():
    files = templates.files(provider="openai", model="")
    assert set(files) == {"config.yaml", "roles.yaml", "soul.yaml", ".env"}


def test_init_template_carries_no_placeholder_secret():
    files = templates.files(provider="openai", model="")
    assert "CHANGE_ME" not in files["config.yaml"]
    assert "CHANGE_ME" not in files[".env"]


def test_compose_is_a_working_default():
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "CHANGE_ME" not in text
    assert "${POSTGRES_PASSWORD:?" in text
    parsed = yaml.safe_load(text)
    assert set(parsed["services"]) == {"postgres", "eidesoul", "sandbox-proxy"}
    assert parsed["services"]["sandbox-proxy"]["profiles"] == ["sandbox"]
    assert parsed["services"]["eidesoul"]["build"]["args"]["EXTRAS"] == ""
