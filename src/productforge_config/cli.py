"""Bring a repository's copy of productforge-config up to a release, and
check or apply the GitHub repository settings every ProductForge
repository shares.

`productforge-config update --version vX.Y.Z` (typically through `make
update-config`, which installs and invokes this with uvx from the tag
being moved to): moves the productforge-config hook source's `rev` in
.pre-commit-lint.yaml and the `productforge-config/actions/setup@...` ref
in every .github/workflows/*.yml file to that version, and rewrites the
shared keys this release carries — the markdownlint rules, and, only for
a repository whose .pre-commit-lint.yaml actually takes the corresponding
hook, the black, isort and flake8 settings — into the repository's own
local copies. Everything else in those files, including a repository's
own hooks, ignored paths and excluded paths, is left alone.

A repository with a `productforge.env` at its root also takes a kit — its
build, run, test and CI configuration — named by its `PF_KIND`: `update`
installs the kit whole into `.productforge/` (see kits.py), the settings that
go at fixed paths (`.pre-commit-config.yaml`, and for a web app `.fvmrc` and
the pubspec's SDK constraint), and keeps `.pre-commit-lint.yaml` excluding the
generated `.productforge/`. `update --check` reports what an update would
change and exits 1 when there is any. `init` gives a repository its own values
and the thin files that use them (scaffold.py), and `ports` prints a slot's
ports (ports.py).

`--version` is optional: left unset, or given as "latest", it resolves to
the newest release tag of this repository (a stable vX.Y.Z, or the newest
pre-release when no stable release exists yet) via `git ls-remote --tags`
— the one network call this makes, before anything is written. An explicit
version is validated against VERSION_RE first, so an invalid one is
refused rather than half-applied.

`productforge-config github check --repo <owner/name>` and `... github
apply --repo <owner/name>`: check or apply settings/github.json (see
github.py) against a live repository via `gh api`. `check` never writes
anything; `apply` needs repository admin rights on the target.

Stdlib-only by design: this runs via `uvx --from git+...`, in whatever
repository the person runs it in, and reads the shared settings bundled
into this package at build time (see pyproject.toml's force-include)
rather than fetching them over the network itself — aside from `gh api`,
which the `github` command shells out to deliberately, to reuse the
caller's own GitHub authentication rather than reimplementing it.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import tomllib

from productforge_config import _files, github, kits, ports, scaffold
from productforge_config._bundled import bundled_settings_dir as _bundled_settings_dir

HOOK_SOURCE = "https://github.com/andrew-organization/productforge-config"
# Anything this repository publishes for a workflow to use — an action, or a reusable workflow —
# as the `owner/repo/path` part of a `uses:` value, as a regex.
_REF_SOURCE_PATTERN = r"andrew-organization/productforge-config/(?:actions|\.github/workflows)/[^\s'\"@#]+"

BLACK_KEYS = ("target-version", "line-length")
ISORT_KEYS = ("profile", "line_length")
FLAKE8_KEYS = ("max-line-length", "docstring-convention", "extend-ignore")
MYPY_KEYS = (
    "python_version",
    "check_untyped_defs",
    "disallow_untyped_defs",
    "disallow_incomplete_defs",
    "disallow_untyped_decorators",
    "disallow_any_generics",
    "disallow_any_explicit",
    "warn_return_any",
    "warn_unused_ignores",
    "warn_redundant_casts",
    "warn_unused_configs",
    "ignore_missing_imports",
    "follow_imports",
)
# What the generated `.productforge/` is excluded from linting by, as a pre-commit `exclude` regex.
KIT_EXCLUDE = r"^\.productforge/"

# Validates an explicit --version before anything is written: a release tag,
# vMAJOR.MINOR.PATCH, optionally with a -rc.N suffix.
# A full 40-character commit SHA is accepted too, so a repository can adopt an unreleased config.
VERSION_RE = re.compile(r"^(v\d+\.\d+\.\d+(-rc\.\d+)?|[0-9a-f]{40})$")

# A release tag always carries all three components.
_RELEASE_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")

# This repository's hook block: its `repo:` line, any blank or comment lines, then `rev:`, the
# version optionally quoted — the version itself is group 2, so the quotes stay as written.
_HOOK_REPO_RE = re.compile(
    r"(^[ \t]*-[ \t]*repo:[ \t]*['\"]?"
    + re.escape(HOOK_SOURCE)
    + r"(?:\.git)?['\"]?[ \t]*(?:#[^\n]*)?\n(?:[ \t]*(?:#[^\n]*)?\n)*[ \t]*rev:[ \t]*['\"]?)([^\s'\"#]+)",
    re.MULTILINE,
)
_HOOK_REPO_LINE_RE = re.compile(
    r"^[ \t]*-[ \t]*repo:[ \t]*['\"]?" + re.escape(HOOK_SOURCE) + r"(?:\.git)?['\"]?[ \t]*(?:#[^\n]*)?$",
    re.MULTILINE,
)
_ANY_REPO_LINE_RE = re.compile(r"^[ \t]*-[ \t]*repo:", re.MULTILINE)
# A hook id in either YAML style: a block list item (`- id: black`) or a flow mapping (`{id: black}`).
_HOOK_ID_RE = re.compile(r"(?:^[ \t]*-[ \t]*|\{[ \t]*)id:[ \t]*([\w.-]+)", re.MULTILINE)
# A workflow ref this repository publishes, the `uses:` value optionally quoted; the ref is group 2,
# so a closing quote stays.
_ACTION_REF_RE = re.compile(r"(uses:[ \t]*['\"]?" + _REF_SOURCE_PATTERN + r"@)([^\s'\"#},]+)")
# A section header, allowing a trailing comment as TOML and INI both do.
_SECTION_HEADER_RE = re.compile(r"^\[([^\]]+)\]\s*(?:[#;].*)?$")
_TOML_KEY_RE = re.compile(r'^([A-Za-z0-9_.-]+|"[^"]+")[ \t]*=')
_INI_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+)[ \t]*=")


class UpdateError(ValueError):
    """A repository that takes this repository's hooks but can't be moved to a release."""


class InvalidVersion(ValueError):
    """An explicit --version doesn't have a valid productforge-config shape."""


def resolve_version(explicit: str | None) -> str:
    """The version to update to.

    `None` or "latest" resolves to the newest release tag of this
    repository (a stable vX.Y.Z, or the newest -rc.N when no stable
    release exists yet). An explicit version is validated against
    VERSION_RE and returned as-is — before anything is written, so an
    invalid one is refused rather than half-applied.
    """
    if explicit is None or explicit == "latest":
        return _latest_release_tag()
    if not VERSION_RE.match(explicit):
        raise InvalidVersion(
            f"{explicit!r} isn't a valid productforge-config version — expected "
            "a release tag, vMAJOR.MINOR.PATCH, optionally with a -rc.N "
            "suffix, e.g. v1.0.0 or v1.0.0-rc.2, or a full 40-character commit SHA"
        )
    return explicit


def _parse_release_tags(
    ls_remote_output: str,
) -> tuple[dict[tuple[int, int, int], str], dict[tuple[int, int, int, int], str]]:
    """Split `git ls-remote --tags` output into stable and pre-release
    release tags, each keyed by its version tuple for ordering. Any other
    tag, anything that isn't a full vX.Y.Z(-rc.N), is ignored.
    """
    stable: dict[tuple[int, int, int], str] = {}
    prerelease: dict[tuple[int, int, int, int], str] = {}
    for line in ls_remote_output.splitlines():
        if "\t" not in line:
            continue
        ref = line.split("\t", 1)[1].strip()
        if not ref.startswith("refs/tags/") or ref.endswith("^{}"):
            continue
        tag = ref[len("refs/tags/") :]
        match = _RELEASE_TAG_RE.match(tag)
        if not match:
            continue
        major, minor, patch, rc = match.groups()
        version = (int(major), int(minor), int(patch))
        if rc is None:
            stable[version] = tag
        else:
            prerelease[version + (int(rc),)] = tag
    return stable, prerelease


def _latest_release_tag() -> str:
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", HOOK_SOURCE],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (getattr(exc, "stderr", None) or str(exc)).strip()
        raise InvalidVersion(
            f"couldn't list the release tags at {HOOK_SOURCE} ({detail}); "
            "check the connection, or pass an explicit --version"
        ) from exc
    stable, prerelease = _parse_release_tags(result.stdout)
    if stable:
        return stable[max(stable)]
    if prerelease:
        return prerelease[max(prerelease)]
    raise InvalidVersion(f"no release tags found at {HOOK_SOURCE}")


def _shared_hook_ids(root: Path) -> set[str]:
    """The hook ids a repository actually takes from this repository's own
    block in its .pre-commit-lint.yaml (empty when the file, or that
    block, is absent) — so a rewrite only touches settings a repository's
    hooks actually use.
    """
    path = root / ".pre-commit-lint.yaml"
    if not path.exists():
        return set()
    text = path.read_text()
    match = _HOOK_REPO_LINE_RE.search(text)
    if match is None:
        return set()
    tail = text[match.end() :]
    next_repo = _ANY_REPO_LINE_RE.search(tail)
    block = tail[: next_repo.start()] if next_repo else tail
    return set(_HOOK_ID_RE.findall(block))


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
    file's comments and any other keys around it are never touched. The
    scan is string- and comment-aware: it skips over JSON string literals
    (respecting backslash escapes) and over // and /* */ comments, so a
    `}` inside a string value or inside a comment never miscounts the
    depth.
    """
    marker = re.search(r'"' + re.escape(key) + r'"\s*:\s*', text)
    if marker is None or text[marker.end() :].lstrip()[:1] != "{":
        raise ValueError(f'no "{key}": {{ ... }} block found')
    start = text.index("{", marker.end())
    depth = 0
    n = len(text)
    i = start
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            close = text.find("*/", i + 2)
            i = n if close == -1 else close + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1, text[start : i + 1]
        i += 1
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


_write_if_changed = _files.write_if_changed


def update_pre_commit_lint(root: Path, version: str) -> bool:
    path = root / ".pre-commit-lint.yaml"
    if not path.exists():
        return False
    text = path.read_text()
    new_text, count = _HOOK_REPO_RE.subn(lambda m: m.group(1) + version, text)
    if count == 0:
        if _HOOK_REPO_LINE_RE.search(text):
            raise UpdateError(f"{path}: takes this repository's hooks but no rev could be found to move")
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


def update_yamllint(root: Path) -> bool:
    """Writes the shared YAML lint settings whole as the repository's own
    .yamllint, wherever it takes the yamllint hook from this repository.
    """
    if "yamllint" not in _shared_hook_ids(root):
        return False
    shared = (_bundled_settings_dir() / "yamllint.yaml").read_text()
    return _write_if_changed(root / ".yamllint", shared)


def update_editorconfig(root: Path) -> bool:
    """Writes the shared editor settings whole as the repository's own
    .editorconfig, wherever it takes the end-of-file-fixer or
    trailing-whitespace hook from this repository.
    """
    if not {"end-of-file-fixer", "trailing-whitespace"} & _shared_hook_ids(root):
        return False
    shared = (_bundled_settings_dir() / "editorconfig").read_text()
    return _write_if_changed(root / ".editorconfig", shared)


def update_pyproject(root: Path, python_toml: dict[str, Any]) -> bool:
    path = root / "pyproject.toml"
    if not path.exists():
        return False
    hook_ids = _shared_hook_ids(root)
    tool = python_toml.get("tool", {})
    text = path.read_text()
    if "black" in hook_ids:
        text = _set_toml_keys(
            text, "tool.black", {k: tool["black"][k] for k in BLACK_KEYS if k in tool.get("black", {})}
        )
    if "isort" in hook_ids:
        text = _set_toml_keys(
            text, "tool.isort", {k: tool["isort"][k] for k in ISORT_KEYS if k in tool.get("isort", {})}
        )
    if "django-mypy" in hook_ids:
        text = _set_toml_keys(text, "tool.mypy", {k: tool["mypy"][k] for k in MYPY_KEYS if k in tool.get("mypy", {})})
    return _write_if_changed(path, text)


def update_setup_cfg(root: Path, python_toml: dict[str, Any]) -> bool:
    path = root / "setup.cfg"
    if not path.exists():
        return False
    if "flake8" not in _shared_hook_ids(root):
        return False
    flake8 = python_toml.get("tool", {}).get("flake8", {})
    text = path.read_text()
    text = _set_ini_keys(text, "flake8", {k: flake8[k] for k in FLAKE8_KEYS if k in flake8})
    return _write_if_changed(path, text)


_EXCLUDE_LINE_RE = re.compile(r"^exclude:[ \t]*(?P<value>.*?)[ \t]*$", re.MULTILINE)
_QUOTED_RE = re.compile(r"""^(?P<scalar>'(?:[^']|'')*'|"(?:[^"\\]|\\.)*")(?P<comment>[ \t]+\#.*)?$""")
_PLAIN_RE = re.compile(r"^(?P<scalar>.*?)(?P<comment>[ \t]+#.*)?$")


def update_lint_excludes_kit(root: Path) -> bool:
    """Makes .pre-commit-lint.yaml's top-level `exclude` cover `.productforge/`, whose files are
    generated and are linted where they are written, adding the line when there is none and
    widening the pattern when there is one — whatever it already excludes stays.
    """
    path = root / ".pre-commit-lint.yaml"
    if not path.exists():
        return False
    text = path.read_text()
    match = _EXCLUDE_LINE_RE.search(text)
    if match is None:
        lines = text.splitlines(keepends=True)
        at = next((i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")), len(lines))
        lines.insert(at, f"exclude: '{KIT_EXCLUDE}'\n")
        return _write_if_changed(path, "".join(lines))

    value = match.group("value")
    if value[:1] in ("|", ">") or not value:
        raise UpdateError(f"{path}: its `exclude` is a multi-line value; add {KIT_EXCLUDE} to it by hand")
    quoted = _QUOTED_RE.match(value)
    if quoted:
        scalar, comment = quoted.group("scalar"), quoted.group("comment") or ""
        quote = scalar[0]
        body = scalar[1:-1]
        pattern = body.replace("''", "'") if quote == "'" else re.sub(r"\\(.)", r"\1", body)
        widened = body + "|" + (KIT_EXCLUDE if quote == "'" else KIT_EXCLUDE.replace("\\", "\\\\"))
        new_value = f"{quote}{widened}{quote}{comment}"
    else:
        plain = _PLAIN_RE.match(value)
        assert plain is not None  # _PLAIN_RE matches any string
        pattern, comment = plain.group("scalar"), plain.group("comment") or ""
        new_value = f"{pattern}|{KIT_EXCLUDE}{comment}"
    try:
        if re.search(pattern, f"{kits.KIT_DIR}/manifest"):
            return False
    except re.error as exc:
        raise UpdateError(f"{path}: its `exclude` isn't a valid regular expression ({exc})") from exc
    return _write_if_changed(path, text[: match.start("value")] + new_value + text[match.end("value") :])


def update_kit_settings(root: Path, env: kits.ProductEnv) -> list[str]:
    """The settings a kind takes at fixed paths: its git hook config, and for a web app the Flutter
    version and the pubspec's Dart SDK constraint that goes with it.
    """
    settings = _bundled_settings_dir()
    changed = []
    if _write_if_changed(
        root / ".pre-commit-config.yaml", (settings / f"pre-commit-config-{env.kind}.yaml").read_text()
    ):
        changed.append(".pre-commit-config.yaml")
    if env.kind == "web":
        if _write_if_changed(root / ".fvmrc", (settings / "fvmrc").read_text()):
            changed.append(".fvmrc")
        if update_pubspec_sdk(root):
            changed.append("pubspec.yaml")
    return changed


def update_pubspec_sdk(root: Path) -> bool:
    """Sets `environment: sdk:` in pubspec.yaml to the Dart constraint that goes with the pinned Flutter."""
    path = root / "pubspec.yaml"
    if not path.exists():
        return False
    sdk = tomllib.loads((_bundled_settings_dir() / "dart.toml").read_text())["environment"]["sdk"]
    lines = path.read_text().splitlines(keepends=True)
    in_environment = False
    for i, line in enumerate(lines):
        if re.match(r"^environment:[ \t]*(#.*)?$", line):
            in_environment = True
        elif in_environment and re.match(r"^\S", line) and not line.startswith("#"):
            break
        elif in_environment:
            match = re.match(r"^([ \t]+)sdk:[ \t]*[^#\n]*?([ \t]+#.*)?$", line.rstrip("\n"))
            if match:
                lines[i] = f"{match.group(1)}sdk: {sdk}{match.group(2) or ''}\n"
                break
    return _write_if_changed(path, "".join(lines))


def update_kit(root: Path) -> list[str]:
    """Installs the kit a repository's productforge.env names, and what goes with it. Does nothing
    in a repository without one.
    """
    env = kits.read_env(root)
    if env is None:
        return []
    changed = kits.install_kit(root, env)
    changed += update_kit_settings(root, env)
    if update_lint_excludes_kit(root):
        changed.append(".pre-commit-lint.yaml")
    return changed


def update(root: Path, version: str, check: bool = False) -> list[str]:
    """Bring the repository at `root` up to `version`. Returns the names
    of whatever it actually changed, for reporting; changes nothing when
    the repository is already at that version and those settings. With
    `check`, changes nothing at all and returns what it would have.
    """
    if check:
        with _files.check_only():
            return update(root, version)
    python_toml = _load_python_toml()
    changed: list[str] = []
    # First, so a kit's exclude is settled before this release's other changes to the same file.
    for name in update_kit(root):
        if name not in changed:
            changed.append(name)
    if update_pre_commit_lint(root, version) and ".pre-commit-lint.yaml" not in changed:
        changed.append(".pre-commit-lint.yaml")
    if update_workflows(root, version):
        changed.append(".github/workflows/*.yml")
    if update_markdownlint(root):
        changed.append(".markdownlint-cli2.jsonc")
    if update_yamllint(root):
        changed.append(".yamllint")
    if update_editorconfig(root):
        changed.append(".editorconfig")
    if update_pyproject(root, python_toml):
        changed.append("pyproject.toml")
    if update_setup_cfg(root, python_toml):
        changed.append("setup.cfg")
    return changed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="productforge-config")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update_parser = subparsers.add_parser("update", help="Bring this repository up to a productforge-config release.")
    update_parser.add_argument(
        "--version",
        default=None,
        help="The release to move to, e.g. v1.0.0 (default, or 'latest': the newest release tag).",
    )
    update_parser.add_argument(
        "--path",
        default=".",
        help="The repository to update (default: the current directory).",
    )
    update_parser.add_argument(
        "--check",
        action="store_true",
        help="Change nothing: list what an update would change, and exit 1 if there is anything to.",
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Give a repository its own values and the thin files that use them: productforge.env, "
        "Makefile and .github/workflows/ci.yml, then run update.",
    )
    init_parser.add_argument("--kind", required=True, choices=kits.KINDS, help="Which kit the repository takes.")
    init_parser.add_argument("--name", required=True, help="The repository's name, in snake_case (PF_NAME).")
    init_parser.add_argument("--slot", required=True, help="The product's port slot, shared by its API and web app.")
    init_parser.add_argument(
        "--from",
        dest="old_name",
        default=None,
        help="The name the repository was made under (a template's): every form of it, in snake_case and "
        "kebab-case, is renamed to --name first.",
    )
    init_parser.add_argument(
        "--version",
        default=None,
        help="The release to take, e.g. v1.0.0 (default, or 'latest': the newest release tag).",
    )
    init_parser.add_argument(
        "--path", default=".", help="The repository to initialise (default: the current directory)."
    )

    ports_parser = subparsers.add_parser("ports", help="Print the local ports a slot owns.")
    ports_parser.add_argument("--slot", required=True, type=int, help="The port slot.")
    ports_parser.add_argument(
        "--env", action="store_true", help="Print PF_PORT_<NAME>=<port> lines, as make and Docker Compose read them."
    )

    github_parser = subparsers.add_parser(
        "github",
        help="Check or apply the GitHub repository settings every ProductForge repository shares "
        "(settings/github.json).",
    )
    github_subparsers = github_parser.add_subparsers(dest="github_command", required=True)

    check_parser = github_subparsers.add_parser(
        "check",
        help="Print each setting whose live value differs from settings/github.json; "
        "exit 1 if any differ, 0 if none do.",
    )
    check_parser.add_argument("--repo", required=True, help="The repository to check, as owner/name.")

    apply_parser = github_subparsers.add_parser(
        "apply",
        help="Apply settings/github.json to a live repository, idempotently. Needs repository admin rights.",
    )
    apply_parser.add_argument("--repo", required=True, help="The repository to apply settings to, as owner/name.")

    return parser


def _main_update(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    try:
        version = resolve_version(args.version)
    except InvalidVersion as exc:
        print(f"productforge-config: {exc}", file=sys.stderr)
        return 2 if args.check else 1
    try:
        changed = update(root, version, check=args.check)
    except (UpdateError, kits.EnvError) as exc:
        print(f"productforge-config: {exc}", file=sys.stderr)
        return 2 if args.check else 1
    if args.check:
        if not changed:
            print(f"productforge-config {version}: no drift")
            return 0
        print(f"productforge-config {version}: drift in {', '.join(changed)}; run `make update-config`")
        return 1
    if changed:
        print(f"productforge-config {version}: updated {', '.join(changed)}")
    else:
        print(f"productforge-config {version}: already up to date")
    return 0


def _main_init(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    try:
        env = kits.validate(args.kind, args.slot, args.name)
        version = resolve_version(args.version)
        changed = scaffold.init(root, env, version, args.old_name)
        changed += update(root, version)
    except (InvalidVersion, UpdateError, kits.EnvError, scaffold.InitError) as exc:
        print(f"productforge-config: {exc}", file=sys.stderr)
        return 1
    print(f"productforge-config {version}: initialised {env.name} ({env.kind}, slot {env.slot}): {', '.join(changed)}")
    return 0


def _main_ports(args: argparse.Namespace) -> int:
    try:
        lines = ports.env_lines(args.slot) if args.env else ports.table(args.slot)
    except ports.PortError as exc:
        print(f"productforge-config: {exc}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


def _main_github(args: argparse.Namespace) -> int:
    try:
        if args.github_command == "check":
            diffs = github.check(args.repo)
            for line in diffs:
                print(line)
            return 1 if diffs else 0
        applied = github.apply(args.repo)
        for line in applied:
            print(line)
        return 0
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or str(exc)).strip()
        print(f"productforge-config: gh api failed: {stderr}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "update":
        return _main_update(args)
    if args.command == "init":
        return _main_init(args)
    if args.command == "ports":
        return _main_ports(args)
    return _main_github(args)


if __name__ == "__main__":
    sys.exit(main())
