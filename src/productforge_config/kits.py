"""The kits a repository takes from productforge-config, and the declaration that names them.

A repository declares the kits it takes in `productforge.env` at its root (dotenv): `PF_KITS`
lists them, `common` is always taken and never named, and a product kit (`django-api` or
`flutter-web`) also needs the product's `PF_NAME` and `PF_SLOT`. `update` writes each taken kit's
files: whole into `.productforge/`, or at fixed paths at the root, each with a header saying it is
generated, and records what it wrote in a manifest so a kit dropped from the declaration has its
files removed. The lint hooks are one generated file listing exactly the hooks of the kits taken.
The kits' own files are the directories of `kits/` in this repository, bundled into the package.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

from productforge_config import _files, ports
from productforge_config._bundled import bundled_kits_dir, bundled_settings_dir

ENV_FILE = "productforge.env"
KIT_DIR = ".productforge"
MANIFEST = f"{KIT_DIR}/manifest"
RELEASE = f"{KIT_DIR}/release"
PRE_COMMIT = f"{KIT_DIR}/pre-commit.yaml"
HOOK_SOURCE = "https://github.com/andrew-organization/productforge-config"

COMMON = "common"
PRODUCT_KITS = ("django-api", "flutter-web")
KITS = ("python", "shell", *PRODUCT_KITS, "release")
# What `init --kind` takes, and so what an earlier `PF_KIND` names.
KIND_KITS = {"api": ("python", "django-api"), "web": ("flutter-web",)}
KINDS = tuple(KIND_KITS)
_KIND_OF = {"django-api": "api", "flutter-web": "web"}

# The directory under `kits/` that holds what every product kit shares (product.mk and the CLAUDE.md
# fragment), written whenever either product kit is taken.
_PRODUCT_DIR = "product"

# A kit's own files by directory: everything under `kits/<kit>/` is written into KIT_DIR, except
# what is under `root/`, which goes at the repository's root at the same relative path, and the
# CLAUDE.md fragments, which are joined into one.
_ROOT_DIR = "root"
_CLAUDE = "CLAUDE.md"

# The hooks each kit carries, in `.pre-commit-hooks.yaml`'s ids, with the settings this kit gives a
# hook at its use.
KIT_HOOKS: dict[str, tuple[tuple[str, dict[str, str]], ...]] = {
    COMMON: (
        ("trailing-whitespace", {}),
        ("end-of-file-fixer", {}),
        ("markdownlint", {"args": "[--fix]"}),
        ("check-json", {}),
        ("yamllint", {}),
        ("taplo-format", {}),
        ("taplo-lint", {}),
        ("checkmake", {}),
    ),
    "python": (("pyupgrade", {}), ("isort", {}), ("black", {}), ("flake8", {})),
    "shell": (("shellcheck", {}),),
    "django-api": (("django-mypy", {}),),
    "flutter-web": (("dart-format", {}), ("flutter-analyze", {})),
}

# A Django app's migrations, which the hooks of a django-api repository skip.
MIGRATIONS_EXCLUDE = r"(^|/)migrations/"

# A name lower-case, starting with a letter: one that is a valid Compose project name, image
# prefix, database name, Python package and Dart package all at once.
# At most 58 characters, so that `<name>_test`, the integration tests' database, fits Postgres's
# 63-character identifier limit.
MAX_NAME_LENGTH = 58
NAME_RE = re.compile(rf"^[a-z][a-z0-9_]{{0,{MAX_NAME_LENGTH - 1}}}$")
_SLOT_RE = re.compile(r"^(0|[1-9][0-9]*)$")

HEADER = (
    "Installed by productforge-config's `update`. Generated: change it in productforge-config "
    "(andrew-organization/productforge-config), not here, or the next update writes it back."
)


class EnvError(ValueError):
    """A productforge.env that declares no usable kits, name or slot."""


@dataclass(frozen=True)
class Declaration:
    """A repository's declaration, from productforge.env."""

    kits: tuple[str, ...]
    slot: int | None
    name: str
    django_project: str
    postgres_db: str
    lint_exclude: str = ""
    legacy: bool = False  # declared with the earlier PF_KIND, which `update` rewrites to PF_KITS

    @property
    def taken(self) -> tuple[str, ...]:
        """Every kit taken: `common` first, then the named kits."""
        return (COMMON, *self.kits)

    @property
    def product_kit(self) -> str | None:
        return next((k for k in self.kits if k in PRODUCT_KITS), None)

    @property
    def kind(self) -> str | None:
        """`api` or `web` for a repository with a product kit, else None."""
        return _KIND_OF.get(self.product_kit or "")


def parse_dotenv(text: str) -> dict[str, str]:
    """A dotenv file's `KEY=value` lines: comments and blanks skipped, an `export ` prefix and a
    pair of quotes around a value dropped, an unquoted value ending at a ` #` comment.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        stripped = stripped.removeprefix("export ").lstrip()
        key, separator, value = stripped.partition("=")
        if not separator:
            continue
        value = value.strip()
        closing = value.find(value[0], 1) if value[:1] in "'\"" and value else -1
        if closing > 0:
            value = value[1:closing]
        else:
            value = re.split(r"[ \t]+#", value, maxsplit=1)[0].strip()
        values[key.strip()] = value
    return values


def validate(
    kit_names: list[str] | tuple[str, ...],
    slot: str | int = "",
    name: str = "",
    django_project: str = "",
    postgres_db: str = "",
    lint_exclude: str = "",
    legacy: bool = False,
) -> Declaration:
    """The values as a Declaration, or an EnvError naming what is wrong with them."""
    for kit in kit_names:
        if kit == COMMON:
            raise EnvError("PF_KITS never names `common`: every repository takes it")
        if kit not in KITS:
            raise EnvError(f"PF_KITS names an unknown kit, {kit!r}: the kits are {', '.join(KITS)}")
    duplicated = sorted({k for k in kit_names if list(kit_names).count(k) > 1})
    if duplicated:
        raise EnvError(f"PF_KITS names {', '.join(duplicated)} more than once")
    product_kits = [k for k in kit_names if k in PRODUCT_KITS]
    if len(product_kits) > 1:
        raise EnvError(
            f"PF_KITS names both {' and '.join(product_kits)}: a repository is an API or a web app, not both"
        )
    ordered = tuple(k for k in KITS if k in kit_names)
    slot_text = str(slot)
    if not product_kits:
        given = [
            key
            for key, value in (
                ("PF_SLOT", slot_text),
                ("PF_NAME", name),
                ("PF_DJANGO_PROJECT", django_project),
                ("PF_POSTGRES_DB", postgres_db),
            )
            if value
        ]
        if given:
            raise EnvError(
                f"{', '.join(given)} only belong with a product kit ({' or '.join(PRODUCT_KITS)}), and none is taken"
            )
        slot_value = None
    else:
        if not _SLOT_RE.match(slot_text):
            raise EnvError(f"PF_SLOT must be a whole number without leading zeros, not {slot_text!r}")
        try:
            ports.block(int(slot_text))
        except ports.PortError as exc:
            raise EnvError(f"PF_SLOT: {exc}") from exc
        for key, value in (("PF_NAME", name), ("PF_DJANGO_PROJECT", django_project), ("PF_POSTGRES_DB", postgres_db)):
            if value and not NAME_RE.match(value):
                raise EnvError(
                    f"{key} must be lower-case letters, digits and underscores, starting with a letter, "
                    f"at most {MAX_NAME_LENGTH} characters, not {value!r}"
                )
        if not name:
            raise EnvError("PF_NAME is not set")
        slot_value = int(slot_text)
    if lint_exclude:
        try:
            re.compile(lint_exclude)
        except re.error as exc:
            raise EnvError(f"PF_LINT_EXCLUDE isn't a valid regular expression ({exc})") from exc
    return Declaration(ordered, slot_value, name, django_project or name, postgres_db or name, lint_exclude, legacy)


def for_kind(kind: str, slot: str | int, name: str) -> Declaration:
    """The declaration `init --kind` writes."""
    if kind not in KIND_KITS:
        raise EnvError(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
    return validate(KIND_KITS[kind], slot, name)


def read_env(root: Path) -> Declaration | None:
    """The repository's declaration, or None when it has no productforge.env."""
    path = root / ENV_FILE
    if not path.is_file():
        return None
    values = parse_dotenv(path.read_text())
    try:
        if "PF_KIND" in values and "PF_KITS" in values:
            raise EnvError("PF_KIND and PF_KITS are both set: PF_KITS replaces PF_KIND, so keep only PF_KITS")
        legacy = "PF_KIND" in values
        if legacy:
            kind = values["PF_KIND"]
            if kind not in KIND_KITS:
                raise EnvError(f"PF_KIND must be one of {', '.join(KINDS)}, not {kind!r}")
            names = list(KIND_KITS[kind])
        else:
            names = values.get("PF_KITS", "").split()
        return validate(
            names,
            values.get("PF_SLOT", ""),
            values.get("PF_NAME", ""),
            values.get("PF_DJANGO_PROJECT", ""),
            values.get("PF_POSTGRES_DB", ""),
            values.get("PF_LINT_EXCLUDE", ""),
            legacy,
        )
    except EnvError as exc:
        raise EnvError(f"{path}: {exc}") from exc


def env_text(decl: Declaration) -> str:
    """The productforge.env a repository with this declaration commits."""
    text = (
        "# This repository's declaration of what it takes from productforge-config, and its own\n"
        "# values. Dotenv: read by make, by Docker Compose and by `update`.\n"
        "# Kits, space-separated: python, shell, django-api, flutter-web, release. `common` is\n"
        "# always taken and never named.\n"
        f"PF_KITS={' '.join(decl.kits)}\n"
    )
    if decl.product_kit:
        text += f"PF_SLOT={decl.slot}\nPF_NAME={decl.name}\n"
    if decl.product_kit == "django-api":
        text += "# Optional, each defaulting to PF_NAME:\n# PF_DJANGO_PROJECT=\n# PF_POSTGRES_DB=\n"
    return text


_KIND_LINE_RE = re.compile(r"^([ \t]*(?:export[ \t]+)?)PF_KIND[ \t]*=[^\n]*$", re.MULTILINE)


def rewrite_legacy_kind(root: Path, decl: Declaration) -> bool:
    """Rewrite an earlier `PF_KIND` line in productforge.env to `PF_KITS`, leaving the rest as it is."""
    path = root / ENV_FILE
    text = path.read_text()
    new_text = _KIND_LINE_RE.sub(lambda m: f"{m.group(1)}PF_KITS={' '.join(decl.kits)}", text)
    return _files.write_if_changed(path, new_text)


def _with_header(rel: str, text: str) -> str:
    """`text` with the generated-file header in the file's own comment syntax."""
    if rel.endswith(".md"):
        return f"<!-- {HEADER} -->\n\n{text}"
    comment = "".join(f"# {line}\n" for line in _wrap(HEADER))
    if text.startswith("#!"):
        first, _, rest = text.partition("\n")
        return f"{first}\n{comment}{rest}"
    return f"{comment}{text}"


def _wrap(text: str, width: int = 100) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return [*lines, line] if line else lines


def minimum_pre_commit_version() -> str:
    """The one statement of the pre-commit floor, from settings/common.toml."""
    text = (bundled_settings_dir() / "common.toml").read_text()
    return str(tomllib.loads(text)["pre-commit"]["minimum_version"])


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pre_commit_text(decl: Declaration, version: str) -> str:
    """The generated lint configuration: exactly the hooks of the kits taken, at the release."""
    excluded = [r"^\.productforge/"]
    if "django-api" in decl.kits:
        excluded.append(MIGRATIONS_EXCLUDE)  # generated code, which no hook holds to a style
    if decl.lint_exclude:
        excluded.append(decl.lint_exclude)
    exclude = "|".join(excluded)
    lines = [
        f"minimum_pre_commit_version: {_quoted(minimum_pre_commit_version())}",
        f"exclude: {_quoted(exclude)}",
        "default_stages: [pre-commit]",
        "fail_fast: false",
        "",
        "repos:",
        f"  - repo: {HOOK_SOURCE}",
        f"    rev: {version}",
        "    hooks:",
    ]
    for kit in decl.taken:
        for hook, settings in KIT_HOOKS.get(kit, ()):
            lines.append(f"      - id: {hook}")
            lines.extend(f"        {key}: {value}" for key, value in settings.items())
    return _with_header(PRE_COMMIT, "\n".join(lines) + "\n")


def _files_of(source: Path, files: dict[str, str], claude: list[str]) -> None:
    if not source.is_dir():
        return
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = path.relative_to(source)
        if relative == Path(_CLAUDE):
            claude.append(path.read_text().strip("\n") + "\n")
        elif relative.parts[0] == _ROOT_DIR:
            files[str(Path(*relative.parts[1:]))] = path.read_text()
        else:
            files[f"{KIT_DIR}/{relative}"] = path.read_text()


def kit_files(decl: Declaration, version: str) -> dict[str, str]:
    """Every file the kits taken write whole, keyed by its path from the repository's root, each
    with its header where its syntax allows one.
    """
    kits_dir = bundled_kits_dir()
    settings = bundled_settings_dir()
    files: dict[str, str] = {}
    claude: list[str] = []
    for kit in (COMMON, *([_PRODUCT_DIR] if decl.product_kit else []), *decl.kits):
        _files_of(kits_dir / kit, files, claude)
    if decl.product_kit:
        files[f"{KIT_DIR}/{_CLAUDE}"] = "\n".join(claude)
        files[".pre-commit-config.yaml"] = (settings / f"pre-commit-config-{decl.product_kit}.yaml").read_text()
    files[".editorconfig"] = (settings / "editorconfig").read_text()
    files[".yamllint"] = (settings / "yamllint.yaml").read_text()
    files[PRE_COMMIT] = pre_commit_text(decl, version)
    written = {rel: (text if rel == PRE_COMMIT else _with_header(rel, text)) for rel, text in files.items()}
    if decl.product_kit == "flutter-web":
        written[".fvmrc"] = (settings / "fvmrc").read_text()
    written[RELEASE] = f"{version}\n"
    return dict(sorted(written.items()))


def release_record(root: Path) -> str | None:
    """The release the repository was last brought to, or None when none is recorded."""
    path = root / RELEASE
    if not path.is_file():
        return None
    lines = path.read_text().splitlines()
    return lines[0].strip() if lines and lines[0].strip() else None


def _manifest_text(installed: set[str]) -> str:
    return (
        "# The files productforge-config's `update` wrote, so that a later declaration can\n"
        "# remove the ones its kits do not carry. Generated: do not edit.\n"
        + "".join(f"{p}\n" for p in sorted(installed))
    )


def _previous_manifest(root: Path) -> set[str]:
    path = root / MANIFEST
    if not path.is_file():
        return set()
    entries = {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}
    # A manifest is data a repository holds, so a path in it never reaches outside the repository.
    return {e for e in entries if not Path(e).is_absolute() and ".." not in Path(e).parts}


def install_kits(root: Path, decl: Declaration, version: str) -> list[str]:
    """Write every file the kits taken write, and remove the files a previous declaration wrote that
    this one doesn't. Returns the paths (from the repository's root) it wrote or removed.
    """
    files = kit_files(decl, version)
    changed = [rel for rel, text in files.items() if _files.write_if_changed(root / rel, text)]
    for stale in sorted(_previous_manifest(root) - set(files)):
        if _files.remove(root / stale):
            changed.append(stale)
    _files.prune_empty_dirs(root / KIT_DIR)
    if _files.write_if_changed(root / MANIFEST, _manifest_text(set(files))):
        changed.append(MANIFEST)
    return changed
