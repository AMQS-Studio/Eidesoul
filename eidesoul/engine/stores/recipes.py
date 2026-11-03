"""User recipes: dynamic `/name` commands the agent registers through the recipe tool.

One recipe is one row of the `recipes` table, its body is YAML. Two kinds: `recipe`
(ordered, deterministic tool steps) and `prompt` (delegates one LLM turn).
Strictly separate from the harness command registry: clashing names are rejected.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml
from sqlalchemy import delete, select

from ..db import Database
from ..schema import recipes as recipes_table
from .skills import MAX_CONTENT, MAX_DESCRIPTION, NAME

if TYPE_CHECKING:
    from ..tools import ToolContext

log = logging.getLogger(__name__)

KINDS = ("recipe", "prompt")
MAX_STEPS = 10
DEFAULT_ROLES = ("administrator", "member")


def _clip(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _builtin(name: str) -> bool:
    from ..commands import REGISTRY as COMMANDS  # lazy: avoid a cycle during discovery

    return COMMANDS.get(name) is not None


@dataclass(frozen=True)
class Recipe:
    name: str
    kind: str
    description: str
    usage: str
    roles: tuple[str, ...]
    steps: tuple[dict, ...]
    prompt: str


def _parse_roles(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    roles = tuple(str(item).strip() for item in value)
    if not all(roles) or any(not isinstance(item, str) for item in value):
        return None
    return roles


def _parse_steps(value: Any) -> list[dict] | None:
    if not isinstance(value, list) or not value or len(value) > MAX_STEPS:
        return None
    steps: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        tool = str(item.get("tool") or "").strip()
        args = item.get("args", {})
        if not tool or not isinstance(args, dict):
            return None
        steps.append({"tool": tool, "args": args})
    return steps


def _parse(name: str, data: Any) -> Recipe | None:
    if not isinstance(data, dict):
        log.warning("recipe %s is not a mapping; skipped", name)
        return None
    slug = str(data.get("name") or "").strip().lower()
    if not NAME.fullmatch(slug):
        log.warning("recipe %s has an invalid name; skipped", name)
        return None
    if slug != name:
        log.warning("recipe %s name '%s' does not match the document; skipped", name, slug)
        return None
    if _builtin(slug):
        log.warning("recipe %s clashes with a built-in command; skipped", name)
        return None
    kind = str(data.get("kind") or "").strip()
    if kind not in KINDS:
        log.warning("recipe %s has an invalid kind; skipped", name)
        return None
    description = _clip(data.get("description") or "", MAX_DESCRIPTION)
    if not description:
        log.warning("recipe %s has no description; skipped", name)
        return None
    usage = " ".join(str(data.get("usage") or "").split())
    roles = _parse_roles(data.get("roles", list(DEFAULT_ROLES)))
    if roles is None:
        log.warning("recipe %s has invalid roles; skipped", name)
        return None
    steps: tuple[dict, ...] = ()
    prompt = ""
    if kind == "recipe":
        parsed = _parse_steps(data.get("steps"))
        if parsed is None:
            log.warning("recipe %s has invalid steps; skipped", name)
            return None
        steps = tuple(parsed)
    else:
        prompt = str(data.get("prompt") or "").strip()
        if not prompt:
            log.warning("recipe %s has no prompt; skipped", name)
            return None
    return Recipe(
        name=slug,
        kind=kind,
        description=description,
        usage=usage,
        roles=roles,
        steps=steps,
        prompt=prompt,
    )


class RecipeStore:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _decode(name: str, text: str) -> Recipe | None:
        if len(text) > MAX_CONTENT:
            log.warning("recipe %s is too large (%d chars); skipped", name, len(text))
            return None
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            log.warning("recipe %s has broken YAML; skipped: %s", name, exc)
            return None
        return _parse(name, data)

    def _all(self) -> dict[str, Recipe]:
        recipes: dict[str, Recipe] = {}
        rows = self.db.fetch_all(select(recipes_table.c.name, recipes_table.c.body))
        for row in rows:
            name = str(row["name"])
            recipe = self._decode(name, str(row["body"] or ""))
            if recipe is not None:
                recipes[name] = recipe
        return recipes

    def names(self) -> list[str]:
        return sorted(self._all())

    def get(self, name: str) -> Recipe | None:
        slug = str(name or "").strip().lower()
        if not slug:
            return None
        row = self.db.fetch_one(select(recipes_table.c.body).where(recipes_table.c.name == slug))
        if row is None:
            return None
        return self._decode(slug, str(row["body"] or ""))

    def index(self, roles: tuple[str, ...] | None = None) -> str:
        """List `- /name usage — description`; filtered by executor role when given."""
        found = self._all()
        lines = []
        for name in sorted(found):
            recipe = found[name]
            if roles is not None and not (set(recipe.roles) & set(roles)):
                continue
            usage = f" {recipe.usage}" if recipe.usage else ""
            lines.append(f"- /{name}{usage} — {recipe.description}")
        return "\n".join(lines)

    def save(
        self,
        name: str,
        kind: str,
        description: str,
        *,
        usage: str = "",
        roles: tuple[str, ...] | list[str] = DEFAULT_ROLES,
        steps: list[dict] | tuple[dict, ...] = (),
        prompt: str = "",
        overwrite: bool = False,
    ) -> str:
        slug = str(name or "").strip().lower()
        if not NAME.fullmatch(slug):
            raise ValueError("a recipe name must be lowercase [a-z0-9_-], max 64 chars")
        if _builtin(slug):
            raise ValueError(f"name already used by a built-in command: {slug}")
        kind = str(kind or "").strip()
        if kind not in KINDS:
            raise ValueError("kind must be recipe or prompt")
        desc = _clip(description, MAX_DESCRIPTION)
        if not desc:
            raise ValueError("description is required")
        parsed_roles = _parse_roles(list(roles) if isinstance(roles, tuple) else roles)
        if parsed_roles is None:
            raise ValueError("roles must be a non-empty list of strings")
        payload: dict[str, Any] = {
            "name": slug,
            "kind": kind,
            "description": desc,
            "roles": list(parsed_roles),
        }
        clean_usage = " ".join(str(usage or "").split())
        if clean_usage:
            payload["usage"] = clean_usage
        if kind == "recipe":
            if isinstance(steps, tuple):
                steps = list(steps)
            if len(steps) > MAX_STEPS:
                raise ValueError(f"at most {MAX_STEPS} steps")
            parsed = _parse_steps(steps)
            if parsed is None:
                raise ValueError("steps must be a non-empty list of {tool, args}")
            payload["steps"] = parsed
        else:
            text = str(prompt or "").strip()
            if not text:
                raise ValueError("prompt is required for kind=prompt")
            payload["prompt"] = text
        body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        now = time.time()
        with self.db.connect() as conn:
            existing = conn.execute(
                select(recipes_table.c.name).where(recipes_table.c.name == slug)
            ).first()
            if existing is not None and not overwrite:
                raise FileExistsError(f"recipe already exists: {slug} (use overwrite=true)")
            self.db.upsert(
                conn,
                recipes_table,
                [{"name": slug, "body": body, "updated_at": now}],
                ["name"],
                {"body": body, "updated_at": now},
            )
        return slug

    def remove(self, name: str) -> None:
        slug = str(name or "").strip().lower()
        with self.db.connect() as conn:
            result = conn.execute(delete(recipes_table).where(recipes_table.c.name == slug))
        if not int(result.rowcount or 0):
            raise KeyError(f"recipe not found: {slug}")


def _render(value: Any, args: str) -> Any:
    if isinstance(value, str):
        return value.replace("{{args}}", args)
    if isinstance(value, dict):
        return {key: _render(item, args) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, args) for item in value]
    return value


async def run_recipe(
    recipe: Recipe,
    args: str,
    ctx: ToolContext,
    specs: dict | None = None,
) -> str:
    """Run kind=recipe: every step goes through `execute` (permissions = executor role)."""
    from ..tools import REGISTRY as TOOLS
    from ..tools import execute

    table = specs if specs is not None else TOOLS.by_name
    cleaned = str(args or "").strip()
    last = ""
    for i, step in enumerate(recipe.steps, 1):
        result = await execute(table, step["tool"], _render(step["args"], cleaned), ctx)
        if result.startswith("error:"):
            return f"error: step {i} ({step['tool']}) failed:\n{result}"
        last = result
    return last
