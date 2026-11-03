"""Command /skill: the owner's surface for derived procedures (ADR-0020 §4.1).

Members get nothing: a procedure enters their prompt only through retrieval. The owner can
inspect, pin, forget, restore a revision, and import a procedure file.
"""


from pathlib import Path

from ..i18n import t
from ..registry import Handler
from . import CommandContext

WRITE_ACTIONS = {"pin", "forget", "restore", "import", "save"}


def _owner(ctx: CommandContext) -> bool:
    return any(role.memory_admin for role in ctx.roles)


def _list(ctx: CommandContext, store, rest: str, lang: str) -> str:
    return store.catalog() or t("command.skill.none", lang)


def _view(ctx: CommandContext, store, rest: str, lang: str) -> str:
    return store.view(rest.strip()) if rest.strip() else _list(ctx, store, rest, lang)


def _read(ctx: CommandContext, store, rest: str, lang: str) -> str:
    name, _, filename = rest.partition(" ")
    if not name or not filename:
        return t("command.skill.read_usage", lang)
    return store.read(name, filename)


def _pin(ctx: CommandContext, store, rest: str, lang: str) -> str:
    return t("command.skill.pinned", lang, name=store.pin(rest.strip()))


def _forget(ctx: CommandContext, store, rest: str, lang: str) -> str:
    return t("command.skill.forgotten", lang, name=store.forget(rest.strip()))


def _restore(ctx: CommandContext, store, rest: str, lang: str) -> str:
    name, _, revision = rest.partition(" ")
    if not name:
        return t("command.skill.usage", lang)
    if revision.strip().isdigit():
        store.restore(name, int(revision.strip()))
        return t("command.skill.restored", lang, name=name)
    return t("command.skill.restored", lang, name=store.restore_archived(name))


def _import(ctx: CommandContext, store, rest: str, lang: str) -> str:
    path = Path(rest.strip()).expanduser()
    from ..stores.skills import parse_frontmatter

    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    name = str(meta.get("name") or path.stem)
    store.save(
        name,
        str(meta.get("description") or ""),
        body,
        overwrite=bool(meta.get("overwrite", False)),
        scope=str(meta.get("scope") or "thread"),
        via="command",
        stated_by=str(ctx.sender or ""),
        trust=str(meta.get("trust") or "T2"),
        approved=bool(meta.get("approved", False)),
        language=lang,
    )
    return t("command.skill.imported", lang, name=name)


def _save(ctx: CommandContext, store, rest: str, lang: str) -> str:
    name, _, payload = rest.partition(" ")
    description, _, body = payload.partition("|")
    if not name or not description.strip() or not body.strip():
        return t("command.skill.usage", lang)
    store.save(
        name,
        description.strip(),
        body.strip(),
        overwrite=True,
        scope="thread",
        via="command",
        stated_by=str(ctx.sender or ""),
        trust="T0",
        language=lang,
    )
    return t("command.skill.saved", lang, name=name)


ACTIONS = {
    "list": _list,
    "daftar": _list,
    "view": _view,
    "load": _view,
    "show": _view,
    "read": _read,
    "file": _read,
    "pin": _pin,
    "forget": _forget,
    "restore": _restore,
    "import": _import,
    "save": _save,
    "tambah": _save,
}


async def _skill(ctx: CommandContext) -> str:
    lang = ctx.lang
    store = ctx.agent.skills
    if store is None:
        return t("command.skill.unavailable", lang)
    raw = ctx.args.strip()
    action, _, rest = raw.partition(" ")
    action = action.lower() if action else "list"
    handler = ACTIONS.get(action)
    if handler is None and raw and " " not in raw:
        handler, rest = _view, raw
    if handler is None:
        return t("command.skill.usage", lang)
    if action in WRITE_ACTIONS and not _owner(ctx):
        return t("command.skill.denied", lang)
    try:
        return handler(ctx, store, rest, lang)
    except (KeyError, ValueError, OSError) as exc:
        return f"error: {exc}"


COMMAND = Handler(
    name="skill",
    description="📚 Owner: list, view, pin, forget, restore, or import derived procedures.",
    fn=_skill,
    aliases=("skills", "sop"),
    usage="[list|view|read|pin|forget|restore|import|save] [name] [file|revision]",
)
