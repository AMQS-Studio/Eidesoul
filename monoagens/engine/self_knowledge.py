"""Capability manifest: generated-first self knowledge (ADR-0001 §4.8).

Four generators, one source each and no LLM:

* `catalog` — `registry.discover` + `Handler.parameters` (the registries are the only list);
* `config_schema` — the dataclass tree (`schema_paths`), dot-paths and defaults, never values;
* `behavior` — the only prose, each entry annotated `behavior:<id>`;
* `topology` — `ast.parse` over `monoagens/engine/**`, the dependency map.

`info` only looks up strings; the capability index is assembled deterministically (name order,
stable per role) so the prompt prefix stays byte-identical (ADR-0003 §4.1). Turning the feature
off stops the index, the topics, `config`, and `topology`; the pinned Layer 1 block is untouched.
"""


import ast
import difflib
import re
from dataclasses import fields, is_dataclass
from pathlib import Path

from .access.permissions import command_patterns, tool_patterns
from .config import Config, resolve_database_url
from .registry import Handler, allows
from .schema import metadata

ROOT = Path(__file__).resolve().parents[2]
ENGINE = Path(__file__).resolve().parent
CONFIG_FILES = ("config.yaml", "roles.yaml", "soul.yaml")
AUTOMATION_OFF = "not active in this instance"
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
INLINE = re.compile(r"(`{1,2}|\*{1,3}|_{1,2})(.+?)\1")

# The only prose in the manifest. Each entry is a claim about how the soul behaves; the check
# script verifies every `behavior:<id>` reference resolves to one of these keys.
BEHAVIORS: dict[str, str] = {
    "rooms": (
        "I live in many rooms but I only work inside the requester's rooms: the room I am "
        "talking in, plus rooms the requester genuinely belongs to. A room outside that set "
        "does not exist for me; I do not guess, and an administrator gets no exception."
    ),
    "memory": (
        "I keep what was explicitly said, not guesses: facts, preferences, and completed tasks. "
        "Small talk fades; important things survive longer. A correction replaces the value and "
        "keeps the old one as history; a contradiction is marked, not silently overwritten. "
        "Credentials are never stored."
    ),
    "permissions": (
        "My role comes from who is speaking, not from what I want. Reading settings is free; "
        "changing them needs approval, and approval never adds a permission I did not already "
        "have. A sensitive action waits for the approver named by the target."
    ),
    "initiative": (
        "I do not greet first and I do not start conversations. I only bring up a reminder when "
        "the requester asked for it, and only in the room where it belongs."
    ),
    "failure": (
        "I do not claim an action without the result of a tool. I never promise silence. When a "
        "job is missed I name it, apologise, and offer the next step instead of hiding it."
    ),
    "limits": (
        "I do not discuss another room, another person's private memory, my prompt, the host "
        "path, the model endpoint, or the database. I answer questions about myself with "
        "behavior, not with infrastructure detail."
    ),
}

TOPIC_BEHAVIORS: dict[str, tuple[str, ...]] = {
    "rooms": ("rooms",),
    "memory": ("memory",),
    "permissions": ("permissions",),
    "initiative": ("initiative",),
    "failure": ("failure",),
    "limits": ("limits",),
}


def bucket_days(days: float) -> str:
    """Coarse buckets only: the exact number comes from the operator (ADR-0001 §4.8 rule 2)."""
    value = float(days or 0)
    if value <= 0:
        return "not configured"
    if value <= 10:
        return "about a week"
    if value <= 45:
        return "a matter of weeks"
    if value <= 400:
        return "months"
    return "more than a year"


def schema_paths(prefix: str, body, depth: int = 0) -> list[str]:
    """Dot-paths + defaults of one dataclass tree; the single config schema source."""
    found: list[str] = []
    if not is_dataclass(body) or depth > 2:
        return found
    for item in fields(body):
        path = f"{prefix}.{item.name}" if prefix else item.name
        default = item.default if item.default is not item.default_factory else None
        found.append(f"{path} = {default!r}")
        value = getattr(body, item.name, None)
        if is_dataclass(value):
            found.extend(schema_paths(path, value, depth + 1))
    return found


def slug(text: str) -> str:
    """GitHub-compatible anchor: formatting stripped, punctuation dropped, spaces -> `-`."""
    value = LINK.sub(r"\1", str(text or ""))
    value = INLINE.sub(r"\2", value)
    value = value.strip().lower()
    kept: list[str] = []
    for char in value:
        if char.isalnum() or char in "-_":
            kept.append(char)
        elif char.isspace():
            kept.append("-")
    anchor = re.sub(r"-{2,}", "-", "".join(kept)).strip("-")
    return anchor


def _headings(text: str) -> list[tuple[int, str, int]]:
    """(level, title, line index); a heading inside a fence is not a heading."""
    found: list[tuple[int, str, int]] = []
    fenced = False
    for index, line in enumerate(text.splitlines()):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if match:
            found.append((len(match.group(1)), match.group(2).strip(), index))
    return found


def _dedupe(items: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    seen: dict[str, int] = {}
    result: list[tuple[str, int, str]] = []
    for anchor, level, title in items:
        count = seen.get(anchor, 0) + 1
        seen[anchor] = count
        result.append((anchor if count == 1 else f"{anchor}-{count}", level, title))
    return result


def forbidden_tokens(config: Config) -> list[str]:
    """The runtime facts a topic or document must never repeat (ADR-0001 §4.8 guard)."""
    tokens = [
        str(config.paths.config),
        str(config.paths.workspace),
        str(config.workspace),
        str(config.llm.base_url or ""),
        str(config.llm.model or ""),
        str(config.database.url_env or ""),
        str(resolve_database_url(config.database) or ""),
    ]
    # Only distinctive schema names; a bare `messages`/`actions` is ordinary prose.
    tokens.extend(name for name in metadata.tables if "_" in str(name))
    unique: list[str] = []
    for token in tokens:
        value = token.strip()
        if len(value) >= 4 and value not in unique:
            unique.append(value)
    return unique


def forbidden_hits(text: str, config: Config) -> list[str]:
    body = str(text or "")
    return [token for token in forbidden_tokens(config) if token in body]


def _role_reason(roles, name: str) -> str:
    names = ", ".join(role.name for role in roles) or "-"
    return f"role {names} cannot use {name} yet"


class SelfKnowledge:
    """Four generators plus the index; constructed once and cached by the caller."""

    def __init__(self, config: Config, tools=None, commands=None) -> None:
        self.config = config
        self._tools = tools
        self._commands = commands
        self._cache: dict[str, object] = {}
        self._docs: dict[str, tuple[float, int, str]] = {}

    # ------------------------------------------------------------ sources

    @property
    def enabled(self) -> bool:
        return bool(self.config.self_knowledge.enabled)

    def registries(self):
        if self._tools is None:
            from .tools import REGISTRY as tools

            self._tools = tools
        if self._commands is None:
            from .commands import REGISTRY as commands

            self._commands = commands
        return self._tools, self._commands

    # ------------------------------------------------------------ catalog

    def catalog_entries(self, roles) -> list[dict]:
        tools, commands = self.registries()
        tool_allow = tool_patterns(roles)
        command_allow = command_patterns(roles)
        found: list[dict] = []
        for handler in sorted(tools.by_name.values(), key=lambda item: item.name):
            if handler.hidden:
                continue
            allowed = allows(tool_allow, handler.name)
            found.append(
                {
                    "kind": "tool",
                    "name": handler.name,
                    "family": handler.family,
                    "state": "allowed" if allowed else "locked",
                    "reason": "" if allowed else _role_reason(roles, handler.name),
                    "description": handler.description,
                }
            )
        for handler in sorted(commands.by_name.values(), key=lambda item: item.name):
            if handler.hidden:
                continue
            allowed = allows(command_allow, handler.name)
            found.append(
                {
                    "kind": "command",
                    "name": handler.name,
                    "family": "",
                    "state": "allowed" if allowed else "locked",
                    "reason": "" if allowed else _role_reason(roles, handler.name),
                    "description": handler.description,
                }
            )
        return found

    def catalog(self, roles) -> str:
        lines = ["tools and commands for this role:"]
        for entry in self.catalog_entries(roles):
            marker = entry["state"]
            reason = f" — {entry['reason']}" if entry["reason"] else ""
            lines.append(f"- {entry['kind']} {entry['name']} [{marker}]{reason}")
        return "\n".join(lines)

    def detail(self, name: str, roles) -> str:
        tools, commands = self.registries()
        handler: Handler | None = tools.get(name) or commands.get(name)
        if handler is None:
            return f"no tool or command named '{name}'"
        allowed = (
            allows(tool_patterns(roles), handler.name)
            if handler in tools.by_name.values()
            else allows(command_patterns(roles), handler.name)
        )
        state = "allowed" if allowed else f"locked — {_role_reason(roles, handler.name)}"
        lines = [
            f"{handler.name} ({'command' if handler in commands.by_name.values() else 'tool'})",
            f"state: {state}",
            f"description: {handler.description}",
        ]
        if handler.usage:
            lines.append(f"usage: {handler.usage}")
        if handler.family:
            lines.append(f"family: {handler.family}")
        if handler.parameters:
            lines.append(f"parameters: {handler.parameters}")
        return "\n".join(lines)

    def family(self, family: str, roles) -> str:
        entries = [
            entry
            for entry in self.catalog_entries(roles)
            if entry["kind"] == "tool" and entry["family"] == family
        ]
        if not entries:
            return f"no tool family named '{family}'"
        return "\n".join(
            f"- {entry['name']} [{entry['state']}]: {entry['description']}"
            for entry in entries
        )

    # ------------------------------------------------------------ config

    def config_schema(self) -> str:
        lines = [
            "config file: config.yaml (the engine/soul/role documents override their sections)",
            "values are not shown; read one with settings get",
        ]
        lines.extend(f"- {line}" for line in schema_paths("engine", self.config))
        lines.append("- soul.<field> = the soul.yaml document")
        lines.append("- role:<name>.<field> = one role document")
        return "\n".join(lines)

    # ------------------------------------------------------------ behavior

    def topic(self, name: str) -> str:
        key = str(name or "").strip().lower()
        if key not in TOPIC_BEHAVIORS:
            return ""
        lines = [f"## {key}"]
        for behavior_id in TOPIC_BEHAVIORS[key]:
            lines.append(BEHAVIORS[behavior_id])
            lines.append(f"(behavior:{behavior_id})")
        if key == "permissions":
            lines.append(f"Approval mode in this instance: {self.config.approval.mode}.")
        if key == "initiative":
            lines.append(f"Automation: {AUTOMATION_OFF}.")
        if key == "memory":
            lines.append(
                "Cost and call records are kept for "
                f"{bucket_days(self.config.metrics.retention_days)}; procedures are renewed "
                f"when used and fade after {bucket_days(self.config.skills.use_renewal_days)}."
            )
        body = "\n".join(lines)
        limit = int(self.config.self_knowledge.max_chars_per_topic)
        if len(body) > limit:
            body = body[:limit] + "\n[next: topic is longer than the limit; ask the operator]"
        return body

    def topics(self) -> list[str]:
        return list(self.config.self_knowledge.topics)

    def behavior_ids(self) -> list[str]:
        return sorted(BEHAVIORS)

    # ------------------------------------------------------------ topology

    def topology(self) -> dict[str, list[str]]:
        cached = self._cache.get("topology")
        if isinstance(cached, dict):
            return cached
        graph: dict[str, list[str]] = {}
        for path in sorted(ENGINE.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            module = path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            graph[module] = sorted(_imports(tree, module, path.name == "__init__.py"))
        self._cache["topology"] = graph
        return graph

    def topology_report(self) -> str:
        graph = self.topology()
        lines = [f"engine modules: {len(graph)}"]
        leaks = [
            (source, target)
            for source, targets in graph.items()
            for target in targets
            if target.startswith("monoagens.gateways")
        ]
        lines.append(f"engine -> gateways imports: {len(leaks)}")
        lines.extend(f"- {source} -> {target}" for source, target in leaks[:20])
        return "\n".join(lines)

    # ------------------------------------------------------------ index

    def index(self, roles) -> str:
        """Layer 2: deterministic, stable per role, bounded by `capability_index_max_chars`."""
        settings = self.config.self_knowledge
        prompt = self.config.prompt
        if not settings.enabled or not prompt.capability_index:
            return ""
        tools, commands = self.registries()
        tool_allow = tool_patterns(roles)
        command_allow = command_patterns(roles)
        allowed_tools = sorted(
            name for name in tools.names() if allows(tool_allow, name)
        )
        locked_tools = sorted(
            name for name in tools.names() if not allows(tool_allow, name)
        )
        allowed_commands = sorted(
            name for name in commands.names() if allows(command_allow, name)
        )
        locked_commands = sorted(
            name for name in commands.names() if not allows(command_allow, name)
        )
        total = len(tools.by_name) + len(commands.by_name)
        two_level = total >= int(prompt.capability_index_two_level_at)
        lines: list[str] = []
        if two_level:
            families = sorted(
                {
                    spec.family
                    for spec in (tools.get(name) for name in allowed_tools)
                    if spec is not None and spec.family
                }
            )
            lines.append(
                "- tools: active, grouped per family: "
                + ", ".join(families)
                + '; call info(kind="family", name=...)'
            )
            lines.append('- commands: active, grouped; call info(kind="catalog") for the list')
        else:
            lines.append("- tools: " + (", ".join(allowed_tools) or "-"))
            if locked_tools:
                lines.append(
                    "- other tools (core/gateway) you do not have yet: "
                    + ", ".join(locked_tools)
                )
            lines.append("- commands: " + (", ".join(allowed_commands) or "-"))
            if locked_commands:
                lines.append(
                    "- commands locked for your role: " + ", ".join(locked_commands)
                )
        by_family: dict[str, list[str]] = {}
        for name in locked_tools:
            handler = tools.get(name)
            by_family.setdefault(handler.family if handler else "", []).append(name)
        for family, names in sorted(by_family.items()):
            label = f"{family} family" if family else "core family"
            lines.append(
                f"- locked tools ({label}): {', '.join(names)} — "
                f"{_role_reason(roles, names[0])}"
            )
        lines.append(f"- automation: {AUTOMATION_OFF}")
        lines.append(
            "- memory: important → long; small → brief; level 0-4; credentials are never stored"
        )
        lines.append(
            "- Need detail/parameters/examples? call "
            'info(kind="tool"|"command"|"family"|"catalog", name="...")'
        )
        body = "\n".join(lines)
        limit = int(prompt.capability_index_max_chars)
        if len(body) > limit:
            kept: list[str] = []
            for line in lines:
                candidate = "\n".join([*kept, line])
                if len(candidate) > limit - 48:
                    break
                kept.append(line)
            kept.append('- (more via info(kind="catalog"))')
            body = "\n".join(kept)
        return body

    # ------------------------------------------------------------ documents

    def _doc_path(self, name: str) -> Path | None:
        allowed = tuple(self.config.self_knowledge.docs)
        raw = str(name or "").strip().lstrip("/")
        if not raw:
            raw = "README.md"
        language = str(self.config.soul.language)
        candidates = [raw]
        if raw.lower() == "readme.md" and language not in {"en"}:
            candidates.insert(0, f"README.{language}.md")
        if raw not in allowed:
            return None
        for candidate in candidates:
            path = (ROOT / candidate).resolve()
            if not path.is_relative_to(ROOT.resolve()):
                continue
            if path.is_file():
                return path
        return None

    def _read_doc(self, path: Path) -> str:
        stamp = path.stat()
        key = str(path)
        cached = self._docs.get(key)
        if cached and cached[0] == stamp.st_mtime and cached[1] == stamp.st_size:
            return cached[2]
        text = path.read_text(encoding="utf-8", errors="replace")
        self._docs[key] = (stamp.st_mtime, stamp.st_size, text)
        return text

    def doc_index(self, name: str = "README.md") -> str:
        path = self._doc_path(name)
        if path is None:
            return "document not available (not in engine.self_knowledge.docs)"
        text = self._read_doc(path)
        headings = _headings(text)
        title = next((title for level, title, _ in headings if level == 1), path.name)
        sections = _dedupe(
            [(slug(title), level, title) for level, title, _ in headings if level >= 2]
        )
        lines = [f"{title} ({path.relative_to(ROOT).as_posix()})", "sections:"]
        lines.extend(f"- {anchor} ({level}): {heading}" for anchor, level, heading in sections)
        lines.append(
            'read one with info(kind="doc", name="'
            + path.relative_to(ROOT).as_posix()
            + '", section="<slug>")'
        )
        return "\n".join(lines)

    def doc_section(self, name: str, section: str) -> str:
        path = self._doc_path(name)
        if path is None:
            return "document not available (not in engine.self_knowledge.docs)"
        text = self._read_doc(path)
        headings = _headings(text)
        if not headings:
            return "document not available (no headings)"
        entries = _dedupe([(slug(title), level, title) for level, title, _ in headings])
        lines = text.splitlines()
        wanted = str(section or "").strip().lower().lstrip("#")
        match = next((item for item in entries if item[0] == wanted), None)
        if match is None:
            close = difflib.get_close_matches(
                wanted, [item[0] for item in entries], n=2, cutoff=0.6
            )
            if len(close) == 1:
                match = next(item for item in entries if item[0] == close[0])
        if match is None:
            listing = "\n".join(f"- {anchor}: {title}" for anchor, _, title in entries)
            return f"section '{section}' is ambiguous or missing; choose one:\n{listing}"
        anchor, level, title = match
        base_slug = anchor.split("-")[0]
        start = next(
            index
            for index, (line_level, line_title, line_index) in enumerate(headings)
            if line_title == title
            and line_level == level
            and slug(line_title) == base_slug
        )
        first = headings[start][2]
        last = len(lines)
        for other_level, _, other_index in headings[start + 1 :]:
            if other_level <= level:
                last = other_index
                break
        limit = int(self.config.self_knowledge.docs_max_chars)
        kept: list[str] = []
        size = 0
        for line in lines[first:last]:
            if size + len(line) + 1 > limit:
                kept.append(f"\n[next: section '{anchor}' is longer; read a narrower heading]")
                break
            kept.append(line)
            size += len(line) + 1
        body = "\n".join(kept).strip()
        hits = forbidden_hits(body, self.config)
        if hits:
            body += f"\n[redacted: {len(hits)} internal token(s) removed]"
            for token in hits:
                body = body.replace(token, "***")
        return body

    # ------------------------------------------------------------ entry

    def info(self, kind: str, name: str, roles, section: str = "") -> str:
        mode = str(kind or "catalog").strip().lower()
        if not self.enabled and mode not in {"tool", "command", "family"}:
            return (
                "self-knowledge is disabled in this instance; ask the operator. "
                "The honesty and privacy rules still apply."
            )
        if mode in {"catalog", "capability", "capabilities"}:
            return self.catalog(roles)
        if mode in {"tool", "command"}:
            return self.detail(name, roles)
        if mode == "family":
            return self.family(name, roles)
        if mode == "config":
            return self.config_schema()
        if mode in {"self", "behavior", "topic"}:
            body = self.topic(name)
            return body or f"no self topic named '{name}'; topics: {', '.join(self.topics())}"
        if mode == "topology":
            return self.topology_report()
        if mode == "doc":
            return self.doc_section(name, section) if section else self.doc_index(name)
        return (
            "kind must be catalog, tool, command, family, config, self, topology, or doc"
        )


def _config_paths(config) -> set[str]:
    try:
        return {
            line.split(" = ", 1)[0]
            for line in schema_paths("engine", config)
            if " = " in line
        }
    except Exception:
        return set()


def _engine_sources():
    for path in sorted(ENGINE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except OSError:
            continue


def _behavior_problems() -> list[str]:
    known = set(BEHAVIORS)
    problems: list[str] = []
    for path, text in _engine_sources():
        for match in re.finditer(r"behavior:([a-z0-9_]+)", text):
            if match.group(1) not in known:
                problems.append(
                    f"{path.relative_to(ROOT).as_posix()}: unregistered behavior:{match.group(1)}"
                )
    return problems


def _topic_problems(manifest: SelfKnowledge, config) -> list[str]:
    problems: list[str] = []
    for topic in tuple(config.self_knowledge.topics):
        if topic not in TOPIC_BEHAVIORS:
            problems.append(f"topic '{topic}' has no behavior entry")
            continue
        body = manifest.topic(topic)
        if not body.strip():
            problems.append(f"topic '{topic}' is empty")
        if len(body) > int(config.self_knowledge.max_chars_per_topic):
            problems.append(f"topic '{topic}' exceeds max_chars_per_topic")
        for token in forbidden_hits(body, config):
            problems.append(f"topic '{topic}' leaks a forbidden token: {token}")
    return problems


def _topology_problems(manifest: SelfKnowledge, config) -> list[str]:
    if not config.self_knowledge.topology:
        return []
    problems: list[str] = []
    for source, targets in manifest.topology().items():
        for target in targets:
            if source.startswith("monoagens.engine") and target.startswith("monoagens.gateways"):
                problems.append(f"engine imports a gateway: {source} -> {target}")
    return problems


def _reference_problems(config) -> list[str]:
    paths = _config_paths(config)
    if not paths:
        return []
    problems: list[str] = []
    for path, text in _engine_sources():
        for match in re.finditer(r"config:([a-z][a-z0-9_.]+)", text):
            key = f"engine.{match.group(1)}"
            if not any(item == key or item.startswith(key + ".") for item in paths):
                problems.append(
                    f"{path.relative_to(ROOT).as_posix()}: unknown config path {key}"
                )
    return problems


def check(config) -> list[str]:
    """ADR-0001 §4.8 guard: every reference resolves and no forbidden token leaks."""
    manifest = SelfKnowledge(config)
    return [
        *_behavior_problems(),
        *_topic_problems(manifest, config),
        *_topology_problems(manifest, config),
        *_reference_problems(config),
    ]


def _imports(tree: ast.AST, module: str, package: bool) -> set[str]:
    """Every `monoagens` module this file imports, with relative imports resolved."""
    base = module if package else module.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("monoagens"):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = base.split(".")
                keep = len(parts) - (node.level - 1)
                prefix = ".".join(parts[:keep]) if keep > 0 else ""
                target = f"{prefix}.{node.module}" if node.module else prefix
            else:
                target = node.module or ""
            if target.startswith("monoagens"):
                found.add(target)
    return found
