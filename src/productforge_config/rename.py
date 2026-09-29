"""Rename a product's repository from the name it was made under to its own.

A product starts as a copy of a template, named for the template
(`productforge_api_template`, `productforge-web-template`); `init --from` gives
it its own name everywhere the template's carries: the snake_case and
kebab-case forms in any text file, the Django project directory
(`api/<old>/`), Dart `package:<old>/` imports and the pubspec's name. A name
without a separator (`app`) is too likely to be an ordinary word to replace
everywhere, so it gets only the directory, the imports and the pubspec name.
"""

import os
import re
import shutil
from pathlib import Path

from productforge_config import _files

# Directories that hold nothing of the repository's own, and the generated file `uv lock`
# rewrites itself.
_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", ".dart_tool", "build", ".mypy_cache", ".pytest_cache", "__pycache__"}
    | {".idea", ".fvm", ".productforge"}
)
_SKIP_FILES = frozenset({"uv.lock"})


class RenameError(ValueError):
    """A rename that can't be carried out."""


def _forms(name: str) -> tuple[str, str]:
    return name.replace("-", "_"), name.replace("_", "-")


def _text_files(root: Path) -> list[Path]:
    """The repository's own files, in a stable order, without descending into a directory that
    holds nothing of its own.
    """
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d not in _SKIP_DIRS)
        for name in sorted(names):
            path = Path(directory) / name
            if name not in _SKIP_FILES and path.is_file() and not path.is_symlink():
                found.append(path)
    return found


def rename(root: Path, old: str, new: str) -> list[str]:
    """Rename `old` to `new` throughout the repository at `root`. Returns the paths (from `root`)
    it changed, the moved directory included.
    """
    old_snake, old_kebab = _forms(old)
    new_snake, new_kebab = _forms(new)
    if old_snake == new_snake:
        return []

    replacements: dict[str, str] = {f"package:{old_snake}/": f"package:{new_snake}/"}
    if old_snake != old_kebab:
        replacements[old_snake] = new_snake
        replacements[old_kebab] = new_kebab
    pattern = re.compile("|".join(re.escape(k) for k in sorted(replacements, key=len, reverse=True)))
    pubspec_name = re.compile(rf"^(name:[ \t]*['\"]?){re.escape(old_snake)}(['\"]?[ \t]*)$", re.MULTILINE)

    changed: list[str] = []
    for path in _text_files(root):
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        updated = pattern.sub(lambda m: replacements[m.group(0)], text)
        if path.name == "pubspec.yaml":
            updated = pubspec_name.sub(lambda m: f"{m.group(1)}{new_snake}{m.group(2)}", updated)
        if _files.write_if_changed(path, updated):
            changed.append(str(path.relative_to(root)))

    project = root / "api" / old_snake
    if project.is_dir():
        target = root / "api" / new_snake
        if target.exists():
            raise RenameError(f"can't move api/{old_snake}/ to api/{new_snake}/: the latter already exists")
        shutil.move(str(project), str(target))
        changed.append(f"api/{old_snake}/ -> api/{new_snake}/")
    return changed
