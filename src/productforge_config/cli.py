"""Bring a repository's copy of productforge-config up to a release.

Run in a repository (as `productforge-config update --version vX.Y.Z`,
typically through `make update-config`, which installs and invokes this
with uvx from the tag being moved to): moves the productforge-config hook
source's `rev` in .pre-commit-lint.yaml and the
`productforge-config/actions/setup@...` ref in every .github/workflows/*.yml
file to that version, and rewrites the shared keys this release carries —
the markdownlint rules, and the black, isort and flake8 settings — into the
repository's own local copies. Everything else in those files, including a
repository's own hooks, ignored paths and excluded paths, is left alone.

Stdlib-only by design: this runs via `uvx --from git+...`, in whatever
repository the person runs it in, and reads the shared settings bundled
into this package at build time (see pyproject.toml's force-include) rather
than fetching anything over the network itself.
"""

import argparse
import importlib.resources
import json
import re
import sys
from pathlib import Path
from typing import Any

import tomllib

HOOK_SOURCE = "https://github.com/andrew-organization/productforge-config"
ACTION_SOURCE = "andrew-organization/productforge-config/actions/setup"

BLACK_KEYS = ("target-version", "line-length")
ISORT_KEYS = ("profile", "line_length")
FLAKE8_KEYS = ("max-line-length", "docstring-convention", "extend-ignore")

_HOOK_REPO_RE = re.compile(
    r"(^[ \t]*-[ \t]*repo:[ \t]*" + re.escape(HOOK_SOURCE) + r"(?:\.git)?[ \t]*\n" r"[ \t]*rev:[ \t]*)(\S+)",
    re.MULTILINE,
)
_ACTION_REF_RE = re.compile(r"(uses:[ \t]*" + re.escape(ACTION_SOURCE) + r"@)(\S+)")
_SECTION_HEADER_RE = re.compile(r"^\[([^\]]+)\]\s*$")
_TOML_KEY_RE = re.compile(r'^([A-Za-z0-9_.-]+|"[^"]+")[ \t]*=')
_INI_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)[ \t]*=")


def _bundled_settings_dir() -> Path:
    """The settings/ directory this package was built from, at its tag."""
    return Path(str(importlib.resources.files("productforge_config") / "settings"))


def _load_python_toml() -> dict[str, Any]:
    path = _bundled_settings_dir() / "python.toml"
    return tomllib.loads(path.read_text())


def _markdownlint_config_text() -> str:
    """The bundled settings/markdownlint.jsonc's "config" value, verbatim."""
    path = _bundled_settings_dir() / "markdownlint.jsonc"
    _, _, text = _extract_balanced(path.read_text(), "config")
    return text


def _extract_balanced(text: str, key: str) -> tuple[int, int, str]:
    """Find "<key>": { ... } and return (start, end, the braced text).

    `start`/`end` bound the braced value itself (the outer braces
    included), found by counting braces rather than parsing JSON, so a
    file's comments and any other keys around it are never touched.
    """
    marker = re.search(r'"' + re.escape(key) + r'"\s*:\s*', text)
    if marker is None or text[marker.end() :].lstrip()[:1] != "{":
        raise ValueError(f'no "{key}": {{ ... }} block found')
    start = text.index("{", marker.end())
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1, text[start : i + 1]
    raise ValueError(f'unbalanced "{key}": {{ ... }} block')


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    return str(value)


def _section_bounds(lines: list[str], header: str) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        match = _SECTION_HEADER_RE.match(line.strip())
        if match and match.group(1) == header:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _SECTION_HEADER_RE.match(lines[i].strip()):
            end = i
            break
    return start, end


def _set_toml_keys(text: str, section: str, keys: dict[str, Any]) -> str:
    """Set `key = value` for each of `keys` inside `[section]` of a TOML
    file, appending the section (or a missing key within it) as needed,
    and leaving every other key and every comment as they are.
    """
    if not keys:
        return text
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines, section)
    remaining = dict(keys)
    if bounds is None:
        block = [f"\n[{section}]\n"] + [f"{k} = {_toml_scalar(v)}\n" for k, v in remaining.items()]
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        return "".join(lines) + "".join(block)
    start, end = bounds
    for i in range(start + 1, end):
        match = _TOML_KEY_RE.match(lines[i].strip())
        if not match:
            continue
        key = match.group(1).strip('"')
        if key in remaining:
            lines[i] = f"{key} = {_toml_scalar(remaining.pop(key))}\n"
    if remaining:
        insert = [f"{k} = {_toml_scalar(v)}\n" for k, v in remaining.items()]
        lines[end:end] = insert
    return "".join(lines)


def _set_ini_keys(text: str, section: str, keys: dict[str, Any]) -> str:
    """Set `key = value` for each of `keys` inside `[section]` of a
    setup.cfg-style file, the same way `_set_toml_keys` does for TOML.
    """
    if not keys:
        return text
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines, section)
    remaining = dict(keys)

    def _value(v: Any) -> str:
        return ",".join(v) if isinstance(v, list) else str(v)

    if bounds is None:
        block = [f"\n[{section}]\n"] + [f"{k} = {_value(v)}\n" for k, v in remaining.items()]
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        return "".join(lines) + "".join(block)
    start, end = bounds
    for i in range(start + 1, end):
        match = _INI_KEY_RE.match(lines[i].strip())
        if not match:
            continue
        key = match.group(1)
        if key in remaining:
            lines[i] = f"{key} = {_value(remaining.pop(key))}\n"
    if remaining:
        insert = [f"{k} = {_value(v)}\n" for k, v in remaining.items()]
        lines[end:end] = insert
    return "".join(lines)


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def update_pre_commit_lint(root: Path, version: str) -> bool:
    path = root / ".pre-commit-lint.yaml"
    if not path.exists():
        return False
    text = path.read_text()
    new_text, count = _HOOK_REPO_RE.subn(lambda m: m.group(1) + version, text)
    if count == 0:
        return False
    return _write_if_changed(path, new_text)


def update_workflows(root: Path, version: str) -> bool:
    changed = False
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return False
    for path in sorted(workflows_dir.glob("*.yml")):
        text = path.read_text()
        new_text, count = _ACTION_REF_RE.subn(lambda m: m.group(1) + version, text)
        if count and _write_if_changed(path, new_text):
            changed = True
    return changed


def update_markdownlint(root: Path) -> bool:
    path = root / ".markdownlint-cli2.jsonc"
    if not path.exists():
        return False
    text = path.read_text()
    try:
        _, _, current = _extract_balanced(text, "config")
    except ValueError:
        return False
    shared = _markdownlint_config_text()
    if current == shared:
        return False
    start, end, _ = _extract_balanced(text, "config")
    return _write_if_changed(path, text[:start] + shared + text[end:])


def update_pyproject(root: Path, python_toml: dict[str, Any]) -> bool:
    path = root / "pyproject.toml"
    if not path.exists():
        return False
    tool = python_toml.get("tool", {})
    text = path.read_text()
    text = _set_toml_keys(text, "tool.black", {k: tool["black"][k] for k in BLACK_KEYS if k in tool.get("black", {})})
    text = _set_toml_keys(text, "tool.isort", {k: tool["isort"][k] for k in ISORT_KEYS if k in tool.get("isort", {})})
    return _write_if_changed(path, text)


def update_setup_cfg(root: Path, python_toml: dict[str, Any]) -> bool:
    path = root / "setup.cfg"
    if not path.exists():
        return False
    flake8 = python_toml.get("tool", {}).get("flake8", {})
    text = path.read_text()
    text = _set_ini_keys(text, "flake8", {k: flake8[k] for k in FLAKE8_KEYS if k in flake8})
    return _write_if_changed(path, text)


def update(root: Path, version: str) -> list[str]:
    """Bring the repository at `root` up to `version`. Returns the names
    of whatever it actually changed, for reporting; changes nothing when
    the repository is already at that version and those settings.
    """
    python_toml = _load_python_toml()
    changed = []
    if update_pre_commit_lint(root, version):
        changed.append(".pre-commit-lint.yaml")
    if update_workflows(root, version):
        changed.append(".github/workflows/*.yml")
    if update_markdownlint(root):
        changed.append(".markdownlint-cli2.jsonc")
    if update_pyproject(root, python_toml):
        changed.append("pyproject.toml")
    if update_setup_cfg(root, python_toml):
        changed.append("setup.cfg")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="productforge-config")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update", help="Bring this repository up to a productforge-config release.")
    update_parser.add_argument(
        "--version",
        required=True,
        help="The release to move to, e.g. v1.0.0.",
    )
    update_parser.add_argument(
        "--path",
        default=".",
        help="The repository to update (default: the current directory).",
    )

    args = parser.parse_args(argv)
    root = Path(args.path).resolve()
    changed = update(root, args.version)
    if changed:
        print(f"productforge-config {args.version}: updated {', '.join(changed)}")
    else:
        print(f"productforge-config {args.version}: already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
