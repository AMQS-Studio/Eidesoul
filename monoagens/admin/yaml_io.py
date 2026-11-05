"""Comment-preserving YAML updates for the host files (ADR-0007 §4.1, ADR-0008 §4.4).

`monoagens init` writes whole files; a CLI command changes one value, and the operator's
comments and ordering must survive that edit. The editor rewrites the smallest line range it
can, keeps trailing `# ...` notes, validates the new text by loading it, and only then writes.

The write is atomic (a sibling temp file plus `os.replace`). A bind-mounted file can refuse the
rename with `EBUSY`; the fallback truncates and rewrites the same inode, which the mount sees.
"""


import errno
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

import yaml

Validator = Callable[[str], object]
_KEY = re.compile(r"^(?P<pad>\s*)(?P<key>[^#\s][^:#]*?):(?P<rest>.*)$")


class YamlUpdateError(ValueError):
    """The update would produce a document the loader cannot read."""


def _scalar(value: object) -> str:
    flow = isinstance(value, (dict, list))
    rendered = yaml.safe_dump(
        value, default_flow_style=flow, sort_keys=False, allow_unicode=True
    ).strip()
    return rendered.removesuffix("\n...")


def _split_comment(rest: str) -> tuple[str, str]:
    """Split ` value  # note` into value and note without touching a quoted `#`."""
    quote = ""
    for index, char in enumerate(rest):
        if quote:
            if char == quote and (index == 0 or rest[index - 1] != "\\"):
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or rest[index - 1].isspace()):
            return rest[:index].rstrip(), rest[index:].rstrip()
    return rest.strip(), ""


def _indent(line: str) -> int | None:
    """The indentation of a content line; None for a blank line, a comment, or a marker."""
    stripped = line.lstrip(" ")
    if not stripped or stripped.startswith("#") or stripped in {"---", "..."}:
        return None
    return len(line) - len(stripped)


def _match(line: str, key: str) -> re.Match[str] | None:
    found = _KEY.match(line)
    if found is None:
        return None
    declared = found.group("key").strip().strip("'\"")
    return found if declared == key else None


def _find_child(lines: list[str], start: int, end: int, key: str) -> int | None:
    """The line index of a direct child mapping key inside `lines[start:end]`."""
    child_indent: int | None = None
    for index in range(start, end):
        indent = _indent(lines[index])
        if indent is None:
            continue
        if child_indent is None:
            child_indent = indent
        if indent < child_indent:
            break
        if indent == child_indent and _match(lines[index], key) is not None:
            return index
    return None


def _block_end(lines: list[str], parent: int) -> int:
    """One past the last line inside `lines[parent]`'s nested block."""
    parent_indent = _indent(lines[parent])
    if parent_indent is None:
        parent_indent = len(lines[parent]) - len(lines[parent].lstrip(" "))
    for index in range(parent + 1, len(lines)):
        indent = _indent(lines[index])
        if indent is not None and indent <= parent_indent:
            return index
    return len(lines)


def _value_end(lines: list[str], index: int) -> int:
    """One past the lines that belong to `lines[index]`'s own value.

    A scalar ends on its own line: the comment or blank line below it belongs to the next
    key, so replacing it must not remove those lines.
    """
    found = _KEY.match(lines[index])
    if found is None:
        return index + 1
    value, _ = _split_comment(found.group("rest"))
    if value and value not in {"|", ">", "|-", ">-", "|+", ">+"}:
        return index + 1
    return _block_end(lines, index)


def _mapping_indent(lines: list[str], start: int, end: int, fallback: int) -> int:
    for index in range(start, end):
        indent = _indent(lines[index])
        if indent is not None:
            return indent
    return fallback


def _insert_position(lines: list[str], start: int, end: int, indent: int) -> int:
    """Insert before trailing blank lines and lower-indented comments of the next section."""
    position = end
    while position > start:
        raw = lines[position - 1]
        stripped = raw.strip()
        if not stripped:
            position -= 1
            continue
        own_indent = len(raw) - len(raw.lstrip(" "))
        if stripped.startswith("#") and own_indent < indent:
            position -= 1
            continue
        break
    return position


def _render_entry(pad: str, key: str, value: object, comment: str = "") -> list[str]:
    """One mapping entry, including the nested lines a mapping, list, or block scalar needs."""
    suffix = f"  {comment}" if comment else ""
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{key}: {{}}{suffix}"]
        return [f"{pad}{key}:{suffix}", *_render_tree(value, pad + "  ")]
    if isinstance(value, list):
        if not value:
            return [f"{pad}{key}: []{suffix}"]
        return [f"{pad}{key}:{suffix}", *(
            f"{pad}  - {_scalar(item)}" for item in value
        )]
    if isinstance(value, str) and "\n" in value:
        body = value.rstrip("\n").split("\n")
        return [f"{pad}{key}: |{suffix}", *(
            f"{pad}  {line}" if line else "" for line in body
        )]
    return [f"{pad}{key}: {_scalar(value)}{suffix}"]


def _render_tree(mapping: Mapping, pad: str) -> list[str]:
    lines: list[str] = []
    for key, value in mapping.items():
        lines.extend(_render_entry(pad, str(key), value))
    return lines


def _render_path(pad: str, parts: list[str], value: object, comment: str = "") -> list[str]:
    """A missing nested path: `a:` / `b:` / `c: value`."""
    if len(parts) == 1:
        return _render_entry(pad, parts[0], value, comment)
    head = [f"{pad}{parts[0]}:"]
    return [*head, *_render_path(pad + "  ", parts[1:], value, comment)]


def _replace(lines: list[str], index: int, end: int, value: object, comment: str) -> list[str]:
    found = _KEY.match(lines[index])
    if found is None:
        raise YamlUpdateError(f"cannot rewrite line {index + 1}: {lines[index]!r}")
    pad = found.group("pad")
    key = found.group("key").strip()
    _, old_comment = _split_comment(found.group("rest"))
    note = comment if comment is not None else old_comment
    block_end = min(_value_end(lines, index), end)
    head_indent = len(pad)
    kept = [
        line
        for line in lines[index + 1:block_end]
        if line.lstrip().startswith("#")
        and len(line) - len(line.lstrip(" ")) > head_indent
    ]
    rendered = _render_entry(pad, key, value, note)
    if kept:
        rendered = [rendered[0], *kept, *rendered[1:]]
    return [*lines[:index], *rendered, *lines[block_end:]]


def _insert(
    lines: list[str],
    start: int,
    end: int,
    parts: list[str],
    value: object,
    fallback_indent: int,
    comment: str,
) -> list[str]:
    indent = _mapping_indent(lines, start, end, fallback_indent)
    position = _insert_position(lines, start, end, indent)
    rendered = _render_path(" " * indent, parts, value, comment)
    return [*lines[:position], *rendered, *lines[position:]]


def _assign(
    lines: list[str],
    start: int,
    end: int,
    parts: list[str],
    value: object,
    fallback_indent: int,
    comment: str,
) -> list[str]:
    index = _find_child(lines, start, end, parts[0])
    if len(parts) == 1:
        if index is None:
            return _insert(lines, start, end, parts, value, fallback_indent, comment)
        return _replace(lines, index, end, value, comment)
    if index is None:
        return _insert(lines, start, end, parts, value, fallback_indent, comment)
    child_indent = _indent(lines[index])
    nested = (child_indent if child_indent is not None else fallback_indent) + 2
    child_end = min(_block_end(lines, index), end)
    return _assign(lines, index + 1, child_end, parts[1:], value, nested, comment)


def _flatten(updates: Mapping, prefix: str = "") -> list[tuple[str, object]]:
    leaves: list[tuple[str, object]] = []
    for key, value in updates.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping) and value:
            leaves.extend(_flatten(value, path))
        else:
            leaves.append((path, value))
    return leaves


def render_update(text: str, updates: Mapping, *, comments: Mapping | None = None) -> str:
    """The updated document text; comments, spacing, and ordering stay where they were."""
    changed = "\n".join(text.splitlines())
    for path, value in _flatten(updates):
        if not path.strip("."):
            raise YamlUpdateError("an update path cannot be empty")
        note = (comments or {}).get(path, "")
        lines = changed.split("\n")
        changed = "\n".join(_assign(lines, 0, len(lines), path.split("."), value, 0, note))
    return changed + ("\n" if text.endswith("\n") else "")


def _validate(text: str, validate: Validator | None) -> None:
    """Load the result before it reaches the disk; a broken edit never replaces the file."""
    loader = validate or yaml.safe_load
    try:
        parsed = loader(text)
    except Exception as exc:  # the loader's own error class is not part of this contract
        raise YamlUpdateError(f"the updated YAML does not load: {exc}") from exc
    if parsed is not None and not isinstance(parsed, Mapping):
        raise YamlUpdateError("the updated YAML is not a mapping")


def _write_in_place(path: Path, text: str) -> None:
    """Rewrite the same inode: what a bind-mounted file needs when rename is refused."""
    with open(path, "r+", encoding="utf-8") as handle:
        handle.write(text)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def write_text(
    path: str | Path,
    text: str,
    *,
    validate: Validator | None = None,
    mode: int | None = None,
) -> None:
    """Validate, then write atomically; an `EBUSY` rename falls back to an in-place write."""
    target = Path(path)
    _validate(text, validate)
    target.parent.mkdir(parents=True, exist_ok=True)
    target_mode = target.stat().st_mode & 0o777 if target.exists() else (mode or 0o644)
    temp = target.with_name(f".{target.name}.tmp")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, target_mode)
        os.replace(temp, target)
    except OSError as exc:
        if exc.errno != errno.EBUSY:
            raise
        _write_in_place(target, text)
    finally:
        temp.unlink(missing_ok=True)


def update_file(
    path: str | Path,
    updates: Mapping,
    *,
    validate: Validator | None = None,
    comments: Mapping | None = None,
) -> str:
    """Apply dotted-path updates to one YAML file and return the new text."""
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    changed = render_update(text, updates, comments=comments)
    _validate(changed, validate)
    write_text(target, changed)
    return changed


def update_config(
    path: str | Path,
    updates: Mapping,
    *,
    comments: Mapping | None = None,
    require_key: bool = False,
    require_model: bool = False,
) -> str:
    """Update config.yaml and prove it with the real loader before replacing the file."""
    from ..engine.config import load_config

    target = Path(path)
    text = target.read_text(encoding="utf-8")
    changed = render_update(text, updates, comments=comments)
    _validate(changed, None)
    probe = target.with_name(f".{target.name}.{os.getpid()}.probe")
    probe.write_text(changed, encoding="utf-8")
    try:
        load_config(probe, require_key=require_key, require_model=require_model)
    finally:
        probe.unlink(missing_ok=True)
    write_text(target, changed)
    return changed
