"""Where this package's bundled settings/ and kits/ directories live.

Shared by cli.py's `update` command and github.py's `check`/`apply`
commands, both of which read the settings this repository publishes
rather than fetching them over the network themselves; kits.py reads the
kits the same way.
"""

import importlib.resources
from pathlib import Path


def bundled_settings_dir() -> Path:
    """The settings/ directory this package was built from, at its tag."""
    return _bundled_dir("settings")


def bundled_kits_dir() -> Path:
    """The kits/ directory this package was built from, at its tag."""
    return _bundled_dir("kits")


def _bundled_dir(name: str) -> Path:
    """A top-level directory of this repository, as this package finds it.

    A real `uvx --from git+...` install (or `make test`, which forces a
    non-editable build via UV_NO_EDITABLE) packages it into the
    wheel — see pyproject.toml's force-include — so importlib.resources
    finds it there. A plain editable install (`uv sync`'s default, so a
    bare `uv run pytest` works too) skips that build step and leaves
    importlib.resources pointing at src/productforge_config itself, which
    has none of these directories of its own — so this falls back to the
    repository's real one, resolved relative to this source file rather
    than the current working directory.
    """
    packaged = Path(str(importlib.resources.files("productforge_config") / name))
    if packaged.is_dir():
        return packaged
    source_tree = Path(__file__).resolve().parent.parent.parent / name
    if source_tree.is_dir():
        return source_tree
    raise FileNotFoundError(
        f"productforge_config's {name}/ directory wasn't found packaged ({packaged}) "
        f"or in the source tree ({source_tree})"
    )
