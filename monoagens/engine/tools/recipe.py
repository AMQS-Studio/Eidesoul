"""Tool recipe: the agent's single entry point to register/edit/remove user recipes."""


from ..registry import Handler
from . import ToolContext


def _view(recipe) -> str:
    lines = [
        f'Recipe "/{recipe.name}": {recipe.description}',
        f"kind: {recipe.kind}",
        f"usage: {recipe.usage or '-'}",
        f"roles: {', '.join(recipe.roles)}",
    ]
    if recipe.kind == "recipe":
        for i, step in enumerate(recipe.steps, 1):
            lines.append(f"step {i}: {step['tool']} {step['args']}")
    else:
        lines.append(f"prompt: {recipe.prompt}")
    return "\n".join(lines)


async def _recipe(args: dict, ctx: ToolContext) -> str:
    if ctx.recipes is None:
        return "error: recipes are not available"
    action = str(args.get("action") or "list").strip().lower()
    name = str(args.get("name") or "").strip()

    if action == "list":
        return ctx.recipes.index() or "No user recipes yet."

    if action == "view":
        if not name:
            return "error: name is empty"
        recipe = ctx.recipes.get(name)
        if recipe is None:
            return f"error: recipe not found: {name}"
        return _view(recipe)

    if action == "save":
        try:
            saved = ctx.recipes.save(
                name,
                str(args.get("kind") or ""),
                str(args.get("description") or ""),
                usage=str(args.get("usage") or ""),
                roles=args.get("roles") or ("administrator", "member"),
                steps=args.get("steps") or (),
                prompt=str(args.get("prompt") or ""),
                overwrite=bool(args.get("overwrite", False)),
            )
        except (ValueError, FileExistsError, OSError) as exc:
            return f"error: {exc}"
        kind = str(args.get("kind") or "").strip()
        detail = (
            f"{len(args.get('steps') or ())} steps" if kind == "recipe" else "prompt"
        )
        return f"ok: recipe /{saved} saved ({detail})"

    if action == "remove":
        if not name:
            return "error: name is empty"
        try:
            ctx.recipes.remove(name)
        except (KeyError, OSError) as exc:
            return f"error: {exc}"
        return f"ok: recipe /{name} deleted"

    return "error: action must be list, view, save, or remove"


TOOL = Handler(
    name="recipe",
    target="document",
    approval_class="consent",
    destructive=("remove",),
    family="read",
    description=(
        "User recipes: list/view to inspect dynamic commands, save to register one "
        "(kind=recipe holds tool steps with {{args}}, kind=prompt holds one-turn LLM "
        "instructions), remove to delete. Register only when the user asks for repeated "
        "automation; quote {{args}} inside shell commands."
    ),
    fn=_recipe,
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list, view, save, or remove."},
            "name": {"type": "string", "description": "Recipe name (lowercase, hyphen)."},
            "kind": {"type": "string", "description": "recipe or prompt."},
            "description": {"type": "string", "description": "Short description (shown in /help)."},
            "usage": {"type": "string", "description": "Argument hint, e.g. [division]."},
            "roles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Roles allowed to execute; defaults to owner+member.",
            },
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Tool steps [{tool, args}] for kind=recipe.",
            },
            "prompt": {"type": "string", "description": "One-turn instruction for kind=prompt."},
            "overwrite": {
                "type": "boolean",
                "description": "Overwrite an existing recipe when saving.",
            },
        },
        "additionalProperties": False,
    },
)
