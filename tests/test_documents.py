"""Document cache contract: files are authoritative, the cache is derived (ADR-0002 §4.1)."""


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


def test_sync_false_never_touches_files(tmp_path) -> None:
    docs, paths = make_docs(tmp_path, sync=False)
    write_config(paths, {"workspace": ".monoagens/workspace", "engine": {"default_role": "guest"}})
    assert docs.seed() == 0
    assert docs.get("engine") is None
    docs.put("engine", "x")
    docs.sync()
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


def test_sync_exports_a_db_only_change_when_the_file_is_missing(tmp_path) -> None:
    docs, paths = _bootstrap(tmp_path)
    changed = SOUL.replace("language: en", "language: id")
    docs.put("soul", changed)
    paths.soul_yaml.unlink()

    docs.sync()

    assert paths.soul_yaml.read_text(encoding="utf-8") == changed


def test_sync_conflict_keeps_the_file_and_updates_the_cache(tmp_path, caplog) -> None:
    docs, paths = _bootstrap(tmp_path)
    file_version = SOUL.replace("language: en", "language: id")
    db_version = SOUL.replace("language: en", "language: jv")
    paths.soul_yaml.write_text(file_version, encoding="utf-8")
    docs.put("soul", db_version)

    docs.sync()

    assert docs.get("soul") == file_version
    assert paths.soul_yaml.read_text(encoding="utf-8") == file_version
    assert "database wins" not in caplog.text


def test_sync_first_run_uses_the_file(tmp_path, caplog) -> None:
    docs, paths = make_docs(tmp_path)
    db_version = SOUL.replace("language: en", "language: id")
    docs.put("soul", db_version)
    paths.soul_yaml.write_text(SOUL, encoding="utf-8")

    docs.sync()

    assert docs.get("soul") == SOUL
    assert paths.soul_yaml.read_text(encoding="utf-8") == SOUL
    assert "database wins" not in caplog.text


def test_sync_missing_side_uses_the_other(tmp_path) -> None:
    docs, paths = _bootstrap(tmp_path)
    paths.soul_yaml.unlink()
    docs.sync()
    assert paths.soul_yaml.read_text(encoding="utf-8") == docs.get("soul")

    paths.soul_yaml.write_text(SOUL.replace("language: en", "language: id"), encoding="utf-8")
    docs.delete("soul")
    docs.sync()
    assert "language: id" in docs.get("soul")


def test_roles_file_removes_a_stale_cache_row(tmp_path) -> None:
    docs, _paths = _bootstrap(tmp_path)
    docs.put("role:member", "name: member\n")

    docs.sync()

    assert docs.names("role:") == ["role:guest"]


def test_import_from_files_refreshes_the_cache(tmp_path) -> None:
    docs, paths = _bootstrap(tmp_path)
    write_config(
        paths,
        {"workspace": ".monoagens/workspace", "engine": {"default_role": "member"}},
    )
    paths.soul_yaml.write_text(SOUL.replace("language: en", "language: id"), encoding="utf-8")

    assert docs.import_from_files() >= 2
    assert "default_role: member" in docs.get("engine")
    assert "language: id" in docs.get("soul")


def test_invalid_roles_file_leaves_the_cache_untouched(tmp_path) -> None:
    docs, paths = _bootstrap(tmp_path)
    before = docs.get("role:guest")
    paths.roles.write_text("roles: [not-a-mapping]\n", encoding="utf-8")

    docs.sync()

    assert docs.get("role:guest") == before


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
