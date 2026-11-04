"""Document mirror contract: seed from files, export/import, documents win, roles aggregate."""


import yaml
from support import build_config, database

from monoagens.engine.stores.documents import DocumentStore

SOUL = "name: MonoAgens\nlanguage: en\nlocale: en-US\ntimezone: UTC\npronouns: aku\n"


def make_docs(tmp_path, *, sync=True):
    config = build_config(tmp_path)
    return DocumentStore(database(), config.paths, sync=sync), config.paths


def write_config(paths, body: dict) -> None:
    paths.config.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


def read_config(paths) -> dict:
    return yaml.safe_load(paths.config.read_text(encoding="utf-8"))


def test_seed_only_missing_documents(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    write_config(
        paths,
        {
            "workspace": ".monoagens/workspace",
            "engine": {"default_role": "guest"},
            "soul": {"name": "MonoAgens", "language": "id"},
        },
    )
    paths.soul_yaml.write_text(SOUL, encoding="utf-8")
    paths.roles.write_text("roles:\n  guest:\n    name: guest\n", encoding="utf-8")

    assert docs.seed() == 3
    assert "default_role: guest" in docs.get("engine")
    assert docs.get("soul") == SOUL
    assert docs.names("role:") == ["role:guest"]
    assert docs.seed() == 0

    docs.put("soul", "name: MonoAgens\nlanguage: id\nlocale: id-ID\ntimezone: UTC\n")
    assert docs.seed() == 0
    assert "language: id" in docs.get("soul")
    assert "persona" not in docs.names()


def test_export_and_import_roundtrip(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    write_config(
        paths,
        {"workspace": ".monoagens/workspace", "gateways": {"cli": {"enabled": True}}},
    )
    docs.put("engine", "default_role: guest\n")
    docs.put("soul", SOUL)
    docs.put("persona", "Persona baru.\n")
    docs.put("role:guest", "name: guest\ntools: []\n")

    assert docs.export_to_files() == 3

    raw = read_config(paths)
    assert raw["workspace"] == ".monoagens/workspace"
    assert raw["gateways"] == {"cli": {"enabled": True}}
    assert raw["engine"] == {"default_role": "guest"}
    assert "soul" not in raw
    assert paths.soul_yaml.read_text(encoding="utf-8") == SOUL
    roles = yaml.safe_load(paths.roles.read_text(encoding="utf-8"))
    assert roles["roles"]["guest"] == {"name": "guest", "tools": []}

    write_config(
        paths,
        {"workspace": ".monoagens/workspace", "engine": {"default_role": "owner"}},
    )
    docs.put("engine", "default_role: guest\n")
    assert docs.import_from_files() == 3
    assert "default_role: owner" in docs.get("engine")
    assert "persona" not in docs.names()


def test_import_drops_the_persona_document(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    write_config(paths, {"workspace": ".monoagens/workspace", "engine": {"default_role": "guest"}})
    paths.soul_yaml.write_text(SOUL, encoding="utf-8")
    docs.put("persona", "Persona lama.\n")

    docs.import_from_files()

    assert docs.get("persona") is None
    assert docs.get("soul") == SOUL


def test_sync_false_never_touches_files(tmp_path) -> None:
    docs, paths = make_docs(tmp_path, sync=False)
    write_config(paths, {"workspace": ".monoagens/workspace", "engine": {"default_role": "guest"}})
    assert docs.seed() == 0
    assert docs.get("engine") is None
    docs.put("engine", "x")
    assert docs.export_to_files() == 0
    assert read_config(paths)["engine"] == {"default_role": "guest"}


def _bootstrap(tmp_path):
    docs, paths = make_docs(tmp_path)
    write_config(paths, {"workspace": ".monoagens/workspace", "engine": {"default_role": "guest"}})
    paths.soul_yaml.write_text(SOUL, encoding="utf-8")
    paths.roles.write_text("roles:\n  guest:\n    name: guest\n", encoding="utf-8")
    docs.seed()
    docs.sync()
    return docs, paths


def test_sync_imports_a_changed_file(tmp_path) -> None:
    docs, paths = _bootstrap(tmp_path)
    changed = SOUL.replace("language: en", "language: id")
    paths.soul_yaml.write_text(changed, encoding="utf-8")

    assert docs.sync() >= 1
    assert docs.get("soul") == changed


def test_sync_exports_a_db_only_change(tmp_path) -> None:
    docs, paths = _bootstrap(tmp_path)
    changed = SOUL.replace("language: en", "language: id")
    docs.put("soul", changed)

    docs.sync()

    assert paths.soul_yaml.read_text(encoding="utf-8") == changed


def test_sync_conflict_exports_the_database_and_warns(tmp_path, caplog) -> None:
    docs, paths = _bootstrap(tmp_path)
    file_version = SOUL.replace("language: en", "language: id")
    db_version = SOUL.replace("language: en", "language: jv")
    paths.soul_yaml.write_text(file_version, encoding="utf-8")
    docs.put("soul", db_version)

    docs.sync()

    assert docs.get("soul") == db_version
    assert paths.soul_yaml.read_text(encoding="utf-8") == db_version
    assert "database wins" in caplog.text


def test_sync_first_run_uses_the_database(tmp_path, caplog) -> None:
    docs, paths = make_docs(tmp_path)
    db_version = SOUL.replace("language: en", "language: id")
    docs.put("soul", db_version)
    paths.soul_yaml.write_text(SOUL, encoding="utf-8")

    docs.sync()

    assert docs.get("soul") == db_version
    assert paths.soul_yaml.read_text(encoding="utf-8") == db_version
    assert "database wins" in caplog.text


def test_sync_missing_side_uses_the_other(tmp_path) -> None:
    docs, paths = _bootstrap(tmp_path)
    paths.soul_yaml.unlink()
    docs.sync()
    assert paths.soul_yaml.read_text(encoding="utf-8") == docs.get("soul")

    paths.soul_yaml.write_text(SOUL.replace("language: en", "language: id"), encoding="utf-8")
    docs.delete("soul")
    docs.sync()
    assert "language: id" in docs.get("soul")


def test_sync_false_touches_nothing(tmp_path) -> None:
    docs, paths = make_docs(tmp_path, sync=False)
    write_config(paths, {"workspace": ".monoagens/workspace", "engine": {"default_role": "guest"}})
    assert docs.sync() == 0
    assert not paths.soul_yaml.exists()


def test_sync_yields_to_another_writer(tmp_path) -> None:
    from sqlalchemy import text

    from monoagens.engine.stores.documents import SYNC_LOCK_KEY

    docs, paths = _bootstrap(tmp_path)
    changed = SOUL.replace("language: en", "language: id")
    paths.soul_yaml.write_text(changed, encoding="utf-8")
    with docs.db.engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": SYNC_LOCK_KEY})
        try:
            assert docs.sync() == 0
            assert docs.get("soul") != changed
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SYNC_LOCK_KEY})
