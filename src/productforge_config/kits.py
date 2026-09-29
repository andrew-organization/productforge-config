"""The build, run and CI kits a repository installs, and the values it supplies itself.

A repository names its own values in `productforge.env` at its root (dotenv)
and takes the kit its `PF_KIND` names: `update` installs the kit's files whole
into `.productforge/` (and a few at fixed paths at the root), each with a
header saying it is generated, and records what it installed in a manifest so
a later kit can remove what an earlier one left behind. The kits themselves
are the `kits/common/`, `kits/api/` and `kits/web/` directories of this
repository, bundled into the package.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from productforge_config import _files, ports
from productforge_config._bundled import bundled_kits_dir

ENV_FILE = "productforge.env"
KIT_DIR = ".productforge"
MANIFEST = f"{KIT_DIR}/manifest"
KINDS = ("api", "web")

# A kit's own files by directory: everything under `kits/common/` and `kits/<kind>/` is installed
# into KIT_DIR, except what is under `root/`, which goes at the repository's root at the same
# relative path, and the CLAUDE.md fragments, which are joined into one.
_ROOT_DIR = "root"
_CLAUDE = "CLAUDE.md"

# A name lower-case, starting with a letter: one that is a valid Compose project name, image
# prefix, database name, Python package and Dart package all at once.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SLOT_RE = re.compile(r"^(0|[1-9][0-9]*)$")

HEADER = (
    "Installed by productforge-config's `update`. Generated: change it in productforge-config "
    "(andrew-organization/productforge-config), not here, or the next update writes it back."
)


class EnvError(ValueError):
    """A productforge.env that names no usable kit, name or slot."""


@dataclass(frozen=True)
class ProductEnv:
    """A repository's own values, from productforge.env."""

    kind: str
    slot: int
    name: str
    django_project: str
    postgres_db: str


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
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = re.split(r"[ \t]+#", value, maxsplit=1)[0].strip()
        values[key.strip()] = value
    return values


def validate(kind: str, slot: str | int, name: str, django_project: str = "", postgres_db: str = "") -> ProductEnv:
    """The values as a ProductEnv, or an EnvError naming what is wrong with them."""
    if kind not in KINDS:
        raise EnvError(f"PF_KIND must be one of {', '.join(KINDS)}, not {kind!r}")
    if not _SLOT_RE.match(str(slot)):
        raise EnvError(f"PF_SLOT must be a whole number without leading zeros, not {str(slot)!r}")
    try:
        ports.block(int(slot))
    except ports.PortError as exc:
        raise EnvError(f"PF_SLOT: {exc}") from exc
    for key, value in (("PF_NAME", name), ("PF_DJANGO_PROJECT", django_project), ("PF_POSTGRES_DB", postgres_db)):
        if value and not NAME_RE.match(value):
            raise EnvError(
                f"{key} must be lower-case letters, digits and underscores, starting with a letter, not {value!r}"
            )
    if not name:
        raise EnvError("PF_NAME is not set")
    return ProductEnv(kind, int(slot), name, django_project or name, postgres_db or name)


def read_env(root: Path) -> ProductEnv | None:
    """The repository's own values, or None when it has no productforge.env (and so takes no kit)."""
    path = root / ENV_FILE
    if not path.is_file():
        return None
    values = parse_dotenv(path.read_text())
    try:
        return validate(
            values.get("PF_KIND", ""),
            values.get("PF_SLOT", ""),
            values.get("PF_NAME", ""),
            values.get("PF_DJANGO_PROJECT", ""),
            values.get("PF_POSTGRES_DB", ""),
        )
    except EnvError as exc:
        raise EnvError(f"{path}: {exc}") from exc


def env_text(env: ProductEnv) -> str:
    """The productforge.env a repository with these values commits."""
    return (
        "# This repository's own values for the build, run, test and CI kit it takes from\n"
        "# productforge-config (see .productforge/CLAUDE.md). Dotenv: read by make, by Docker\n"
        "# Compose and by CI.\n"
        f"PF_KIND={env.kind}\n"
        f"PF_SLOT={env.slot}\n"
        f"PF_NAME={env.name}\n"
        "# Optional, each defaulting to PF_NAME:\n"
        "# PF_DJANGO_PROJECT=\n"
        "# PF_POSTGRES_DB=\n"
    )


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


def kit_files(kind: str) -> dict[str, str]:
    """Every file the kit for `kind` installs, keyed by its path from the repository's root, each
    with its header. The common kit's files first, then the kind's own.
    """
    kits = bundled_kits_dir()
    files: dict[str, str] = {}
    claude: list[str] = []
    for source in (kits / "common", kits / kind):
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            relative = path.relative_to(source)
            if relative == Path(_CLAUDE):
                claude.append(path.read_text().rstrip("\n") + "\n")
            elif relative.parts[0] == _ROOT_DIR:
                files[str(Path(*relative.parts[1:]))] = path.read_text()
            else:
                files[f"{KIT_DIR}/{relative}"] = path.read_text()
    files[f"{KIT_DIR}/{_CLAUDE}"] = "\n".join(claude)
    return {rel: _with_header(rel, text) for rel, text in sorted(files.items())}


def _manifest_text(installed: set[str]) -> str:
    return (
        "# The files productforge-config's `update` installed from a kit, so that a later kit can\n"
        "# remove the ones it no longer carries. Generated: do not edit.\n"
        + "".join(f"{p}\n" for p in sorted(installed))
    )


def _previous_manifest(root: Path) -> set[str]:
    path = root / MANIFEST
    if not path.is_file():
        return set()
    entries = {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}
    # A manifest is data a repository holds, so a path in it never reaches outside the repository.
    return {e for e in entries if not Path(e).is_absolute() and ".." not in Path(e).parts}


def install_kit(root: Path, env: ProductEnv) -> list[str]:
    """Install the kit `env.kind` names whole, and remove the files a previous kit installed that
    this one doesn't. Returns the paths (from the repository's root) it wrote or removed.
    """
    files = kit_files(env.kind)
    changed = [rel for rel, text in files.items() if _files.write_if_changed(root / rel, text)]
    for stale in sorted(_previous_manifest(root) - set(files)):
        if _files.remove(root / stale):
            changed.append(stale)
    _files.prune_empty_dirs(root / KIT_DIR)
    if _files.write_if_changed(root / MANIFEST, _manifest_text(set(files))):
        changed.append(MANIFEST)
    return changed
