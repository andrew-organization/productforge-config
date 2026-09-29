"""`productforge-config init`: give a repository its own values and the thin files that use them.

Writes `productforge.env`, the project `Makefile` (which includes the kit's `.mk` files and
keeps its own `update-config` target) and `.github/workflows/ci.yml` (which calls the kit's
reusable workflow), renames the repository from the template's name to its own when asked, and
then runs `update` so `.productforge/` and the rest of the kit are in place. A product's
creation calls this once. The two thin files are kits/init/'s templates.
"""

import subprocess
from pathlib import Path

from productforge_config import kits, rename
from productforge_config._bundled import bundled_kits_dir


class InitError(ValueError):
    """An init that can't be carried out."""


def _template(name: str) -> str:
    return (bundled_kits_dir() / "init" / name).read_text()


def makefile_text(env: kits.ProductEnv) -> str:
    """The thin project Makefile: the kit's targets, and this repository's own `update-config`."""
    return _template("Makefile").replace("__KIND__", env.kind)


def ci_text(env: kits.ProductEnv, version: str) -> str:
    """The thin ci.yml: every pull request runs the kit's reusable workflow for this kind."""
    return _template("ci.yml").replace("__KIND__", env.kind).replace("__VERSION__", version)


def run_uv_lock(root: Path) -> None:
    """Regenerate uv.lock after a rename, since the lock file carries the project's name."""
    try:
        subprocess.run(["uv", "lock"], cwd=root, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise InitError(f"`uv lock` failed after the rename: {detail.strip()}") from exc


def init(root: Path, env: kits.ProductEnv, version: str, old_name: str | None = None) -> list[str]:
    """Write the repository's own files, renaming it first when `old_name` is given. Returns what
    it changed, the kit's own files excluded (`update` reports those) and a rename summarised as
    one line, not a line for each file.
    """
    changed: list[str] = []
    if old_name:
        if not kits.NAME_RE.match(old_name.replace("-", "_")):
            raise InitError(f"--from must be a name in snake_case or kebab-case, not {old_name!r}")
        try:
            renamed = rename.rename(root, old_name, env.name)
        except rename.RenameError as exc:
            raise InitError(str(exc)) from exc
        changed.append(f"{len(renamed)} paths renamed from {old_name}")
        if (root / "pyproject.toml").is_file():
            run_uv_lock(root)
            changed.append("uv.lock")
    for relative, text in (
        (kits.ENV_FILE, kits.env_text(env)),
        ("Makefile", makefile_text(env)),
        (".github/workflows/ci.yml", ci_text(env, version)),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            changed.append(relative)
    return changed
