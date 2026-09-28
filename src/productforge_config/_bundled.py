"""Where this package's bundled settings/ directory lives.

Shared by cli.py's `update` command and github.py's `check`/`apply`
commands, both of which read the settings this repository publishes
rather than fetching them over the network themselves.
"""

import importlib.resources
from pathlib import Path


def bundled_settings_dir() -> Path:
    """The settings/ directory this package was built from, at its tag.

    A real `uvx --from git+...` install (or `make test`, which forces a
    non-editable build via UV_NO_EDITABLE) packages settings/ into the
    wheel — see pyproject.toml's force-include — so importlib.resources
    finds it there. A plain editable install (`uv sync`'s default, so a
    bare `uv run pytest` works too) skips that build step and leaves
    importlib.resources pointing at src/productforge_config itself, which
    has no settings/ of its own — so this falls back to the repository's
    real settings/ directory, resolved relative to this source file rather
    than the current working directory.
    """
    packaged = Path(str(importlib.resources.files("productforge_config") / "settings"))
    if packaged.is_dir():
        return packaged
    source_tree = Path(__file__).resolve().parent.parent.parent / "settings"
    if source_tree.is_dir():
        return source_tree
    raise FileNotFoundError(
        "productforge_config's settings/ directory wasn't found packaged "
        f"({packaged}) or in the source tree ({source_tree})"
    )
