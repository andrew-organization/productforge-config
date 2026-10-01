"""`productforge-config init`: give a repository its own values and the thin files that use them.

Writes `productforge.env` (the declaration), the project `Makefile` (which includes every
`.productforge/*.mk` the kits write) and `.github/workflows/ci.yml` (which calls the kit's
reusable workflow), renames the repository from the template's name to its own when asked, and
then runs `update` so `.productforge/` and the rest of the kits are in place. A product's
creation calls this once. The two thin files are kits/init/'s templates.
"""

import shutil
import subprocess
from pathlib import Path

from productforge_config import kits, rename
from productforge_config._bundled import bundled_kits_dir


class InitError(ValueError):
    """An init that can't be carried out."""


def _template(name: str) -> str:
    return (bundled_kits_dir() / "init" / name).read_text()


def makefile_text(decl: kits.Declaration) -> str:
    """The thin project Makefile: it includes the kits' makefiles and keeps the repository's own targets."""
    return _template("Makefile")


def ci_text(decl: kits.Declaration, version: str) -> str:
    """The thin ci.yml: every pull request and every push to main runs the reusable workflow for this kind."""
    assert decl.kind is not None  # init only ever declares a product kit
    return _template("ci.yml").replace("__KIND__", decl.kind).replace("__VERSION__", version)


def run_uv_lock(root: Path) -> None:
    """Regenerate uv.lock after a rename, since the lock file carries the project's name."""
    try:
        subprocess.run(["uv", "lock"], cwd=root, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise InitError(f"`uv lock` failed after the rename: {detail.strip()}") from exc


def format_dart(root: Path) -> str | None:
    """Format lib/ and test/ after a rename: a shorter package name lets `dart format` re-wrap lines,
    which `make lint` would otherwise fail on. Through fvm when it is installed. Returns a note when
    there is nothing to format with, None once formatted.
    """
    if shutil.which("fvm"):
        command = ["fvm", "dart", "format"]
    elif shutil.which("dart"):
        command = ["dart", "format"]
    else:
        return "dart not found: run `dart format lib test` before `make lint`"
    directories = [d for d in ("lib", "test") if (root / d).is_dir()]
    if directories:
        try:
            subprocess.run([*command, *directories], cwd=root, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = (getattr(exc, "stderr", None) or str(exc)).strip()
            raise InitError(f"`dart format` failed after the rename: {detail}") from exc
    return None


def init(root: Path, decl: kits.Declaration, version: str, old_name: str | None = None) -> list[str]:
    """Write the repository's own files, renaming it first when `old_name` is given. Returns what
    it changed, the kit's own files excluded (`update` reports those) and a rename summarised as
    one line, not a line for each file.
    """
    changed: list[str] = []
    if old_name:
        if not kits.NAME_RE.match(old_name.replace("-", "_")):
            raise InitError(f"--from must be a name in snake_case or kebab-case, not {old_name!r}")
        try:
            renamed = rename.rename(root, old_name, decl.name)
        except rename.RenameError as exc:
            raise InitError(str(exc)) from exc
        changed.append(f"{len(renamed)} paths renamed from {old_name}")
        if decl.kind == "web":
            note = format_dart(root)
            changed.append(note or "lib and test formatted")
        if (root / "pyproject.toml").is_file():
            run_uv_lock(root)
            changed.append("uv.lock")
    for relative, text in (
        (kits.ENV_FILE, kits.env_text(decl)),
        ("Makefile", makefile_text(decl)),
        (".github/workflows/ci.yml", ci_text(decl, version)),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            changed.append(relative)
    return changed
