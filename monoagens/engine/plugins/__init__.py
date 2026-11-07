"""Plugin loader: one manifest, one digest, one import (ADR-0005).

A plugin is an installable unit fetched at enable time, never a core tool and never a
build-time extra. The manifest is data: it is read and verified before anything is imported.
The loader never keeps a second registry — it hands validated `Handler`s back to the caller,
which passes them to the existing registries (tools, commands). `tool` is the registration DX:
a typed, documented function becomes a `Handler` whose JSON Schema is derived from its hints.

Digest semantics: `digest` is the SHA-256 of the published code tree (sorted relative paths and
their bytes), excluding `plugin.yaml` itself, `deps/`, and bytecode. `tree_digest` additionally
covers `deps/`, so an installed dependency change is visible to an operator or a test. The
manifest-pinned digest is always `code_digest`; a local `deps/` never changes what was published.
`deps/` is importable through a finder appended after the normal path finder, so it can never
shadow a core package.

`local: true` marks a development tree: the manifest digest and the config pin are not enforced,
so an edited file loads without republishing. A local tree is never fetched — it is placed under
the plugin root by its author, and the rest of the manifest (name, entry, engine range, layout,
secrets, credits) is still verified before any import.

The package splits that surface by concern: `manifest` (data + digest + ranges), `tools` (the
decorator DX), `loader` (verification and import), and `install` (lifecycle and config writes).
"""

from .install import disable as disable
from .install import enable as enable
from .install import install as install
from .install import remove as remove
from .loader import Loaded as Loaded
from .loader import PluginLoader as PluginLoader
from .loader import load as load
from .loader import names as names
from .loader import states as states
from .manifest import ARTIFACT_LIMIT as ARTIFACT_LIMIT
from .manifest import CODE_SKIP_DIRS as CODE_SKIP_DIRS
from .manifest import CONSTRAINT as CONSTRAINT
from .manifest import CREDIT_KEYS as CREDIT_KEYS
from .manifest import DEPS_TIMEOUT as DEPS_TIMEOUT
from .manifest import DIGEST as DIGEST
from .manifest import ENTRY as ENTRY
from .manifest import FETCH_TIMEOUT as FETCH_TIMEOUT
from .manifest import HANDLER as HANDLER
from .manifest import MANIFEST as MANIFEST
from .manifest import MANIFEST_KEYS as MANIFEST_KEYS
from .manifest import MODULE as MODULE
from .manifest import NAME as NAME
from .manifest import REMOVE_NAME as REMOVE_NAME
from .manifest import SECRET as SECRET
from .manifest import SKIP_DIRS as SKIP_DIRS
from .manifest import VERSION as VERSION
from .manifest import Credits as Credits
from .manifest import Manifest as Manifest
from .manifest import Plugin as Plugin
from .manifest import PluginError as PluginError
from .manifest import Provides as Provides
from .manifest import code_digest as code_digest
from .manifest import compatible as compatible
from .manifest import engine_version as engine_version
from .manifest import module_name as module_name
from .manifest import normalize_digest as normalize_digest
from .manifest import read_manifest as read_manifest
from .manifest import tree_digest as tree_digest
from .tools import tool as tool

__all__ = [
    "ARTIFACT_LIMIT",
    "CODE_SKIP_DIRS",
    "CONSTRAINT",
    "CREDIT_KEYS",
    "DEPS_TIMEOUT",
    "DIGEST",
    "ENTRY",
    "FETCH_TIMEOUT",
    "HANDLER",
    "MANIFEST",
    "MANIFEST_KEYS",
    "MODULE",
    "NAME",
    "REMOVE_NAME",
    "SECRET",
    "SKIP_DIRS",
    "VERSION",
    "Credits",
    "Loaded",
    "Manifest",
    "Plugin",
    "PluginError",
    "PluginLoader",
    "Provides",
    "code_digest",
    "compatible",
    "disable",
    "enable",
    "engine_version",
    "install",
    "load",
    "module_name",
    "names",
    "normalize_digest",
    "read_manifest",
    "remove",
    "states",
    "tool",
    "tree_digest",
]
