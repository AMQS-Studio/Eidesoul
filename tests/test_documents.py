"""Document mirror contract: seed from files, export/import, documents win, roles aggregate."""

from __future__ import annotations

import yaml
from support import build_config, database

from eidesoul.engine.stores.documents import DocumentStore

SOUL = "name: Eidesoul\nlanguage: en\npronouns: aku\n"


def make_docs(tmp_path, *, sync=True):
    config = build_config(tmp_path)
    return DocumentStore(database(tmp_path), config.paths, sync=sync), config.paths


def write_config(paths, body: dict) -> None:
    paths.config.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


def read_config(paths) -> dict:
    return yaml.safe_load(paths.config.read_text(encoding="utf-8"))


def test_seed_only_missing_documents(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    write_config(
        paths,
        {
            "workspace": ".eidesoul/workspace",
            "engine": {"default_role": "guest"},
            "soul": {"name": "Eidesoul", "language": "id"},
        },
    )
    paths.soul_yaml.write_text(SOUL, encoding="utf-8")
    paths.roles.write_text("roles:\n  guest:\n    name: guest\n", encoding="utf-8")

    assert docs.seed() == 3
    assert "default_role: guest" in docs.get("engine")
    assert docs.get("soul") == SOUL
    assert docs.names("role:") == ["role:guest"]
    assert docs.seed() == 0

    docs.put("soul", "name: Eidesoul\nlanguage: id\n")
    assert docs.seed() == 0
    assert "language: id" in docs.get("soul")
    assert "persona" not in docs.names()


def test_export_and_import_roundtrip(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    write_config(
        paths,
        {"workspace": ".eidesoul/workspace", "gateways": {"cli": {"enabled": True}}},
    )
    docs.put("engine", "default_role: guest\n")
    docs.put("soul", SOUL)
    docs.put("persona", "Persona baru.\n")
    docs.put("role:guest", "name: guest\ntools: []\n")

    assert docs.export_to_files() == 3

    raw = read_config(paths)
    assert raw["workspace"] == ".eidesoul/workspace"
    assert raw["gateways"] == {"cli": {"enabled": True}}
    assert raw["engine"] == {"default_role": "guest"}
    assert "soul" not in raw
    assert paths.soul_yaml.read_text(encoding="utf-8") == SOUL
    roles = yaml.safe_load(paths.roles.read_text(encoding="utf-8"))
    assert roles["roles"]["guest"] == {"name": "guest", "tools": []}

    write_config(
        paths,
        {"workspace": ".eidesoul/workspace", "engine": {"default_role": "owner"}},
    )
    docs.put("engine", "default_role: guest\n")
    assert docs.import_from_files() == 3
    assert "default_role: owner" in docs.get("engine")
    assert "persona" not in docs.names()


def test_import_drops_the_persona_document(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    write_config(paths, {"workspace": ".eidesoul/workspace", "engine": {"default_role": "guest"}})
    paths.soul_yaml.write_text(SOUL, encoding="utf-8")
    docs.put("persona", "Persona lama.\n")

    docs.import_from_files()

    assert docs.get("persona") is None
    assert docs.get("soul") == SOUL


def test_sync_false_never_touches_files(tmp_path) -> None:
    docs, paths = make_docs(tmp_path, sync=False)
    write_config(paths, {"workspace": ".eidesoul/workspace", "engine": {"default_role": "guest"}})
    assert docs.seed() == 0
    assert docs.get("engine") is None
    docs.put("engine", "x")
    assert docs.export_to_files() == 0
    assert read_config(paths)["engine"] == {"default_role": "guest"}
