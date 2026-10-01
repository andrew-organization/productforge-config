"""The fixtures every test of the command builds its world from.

A case gets its own home directory, git configuration and identity, its own copy of a fixture
repository and its own stub programs, all under a directory pytest made for it. Nothing in a case
reads or writes the person's home, `~/.cache/pre-commit`, their global git configuration or the
checkout the suite runs from: the checkout is read once, to build `shipped`, a copy of what the
repository ships and of the fixtures under `tests/`, and every case reads that copy. The cases share
that copy and the toolchain the hook tests install once per run (`hook_toolchain`), neither of which
a case writes to.
"""

import contextlib
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from productforge_config import cli

REAL_ROOT = Path(__file__).resolve().parent.parent
VERSION = "v1.2.3"

# Where `docker compose` is installed as a plugin, outside the person's home first. Docker Desktop
# keeps the plugin under `~/.docker/cli-plugins`, so with the home isolated the case is told where
# the plugin binary is rather than being given the home: the binary is only executed.
COMPOSE_PLUGIN_DIRS = (
    "/usr/libexec/docker/cli-plugins",
    "/usr/local/lib/docker/cli-plugins",
    "/usr/lib/docker/cli-plugins",
    "/Applications/Docker.app/Contents/Resources/cli-plugins",
    str(Path.home() / ".docker" / "cli-plugins"),
)
PINNED_DATE = "2020-01-01T00:00:00+00:00"
JUNK = {".git", ".venv", ".tmp", "__pycache__", ".pytest_cache", ".mypy_cache", "build", "dist", "node_modules"}


# ─── Keeping every case off the checkout ─────────────────────────────────────


def _inside(path: Path, root: Path) -> bool:
    resolved = Path(path).resolve()
    return resolved == root or root in resolved.parents


def _checkout_digest() -> str:
    """Every file of the checkout the suite runs from, bar what a run itself never leaves alone."""
    digest = hashlib.sha256()
    for directory, subdirectories, names in os.walk(REAL_ROOT):
        subdirectories[:] = sorted(d for d in subdirectories if d not in JUNK and not d.endswith(".egg-info"))
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_file():
                digest.update(str(path.relative_to(REAL_ROOT)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture(scope="session", autouse=True)
def _checkout_is_left_as_found() -> Iterator[None]:
    before = _checkout_digest()
    yield
    assert _checkout_digest() == before, "the suite changed a file of the checkout it runs from"


@pytest.fixture(autouse=True)
def _forbid_the_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """A program a case starts never runs inside the checkout, unless pytest's temporary directory is there."""
    scratch = tmp_path_factory.getbasetemp().resolve()
    real_popen = subprocess.Popen.__init__

    def guarded(self, args, *popen_args, **kwargs):  # type: ignore[no-untyped-def]
        cwd = kwargs.get("cwd")
        if cwd is not None and _inside(cwd, REAL_ROOT) and not _inside(cwd, scratch):
            raise AssertionError(f"a test started a program inside the checkout: {cwd}")
        real_popen(self, args, *popen_args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded)


# ─── The environment every case runs in ──────────────────────────────────────

GIT_CONFIG = (
    "[gc]\n\tauto = 0\n[maintenance]\n\tauto = false\n[core]\n\thooksPath = /dev/null\n[init]\n\tdefaultBranch = main\n"
)


def git_env(home: Path) -> dict[str, str]:
    """Git pinned to a configuration file under `home`, a fixed identity and fixed dates."""
    config = home / "gitconfig"
    if not config.exists():
        config.write_text(GIT_CONFIG)
    return {
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_SYSTEM": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Config Test",
        "GIT_AUTHOR_EMAIL": "config-test@example.invalid",
        "GIT_COMMITTER_NAME": "Config Test",
        "GIT_COMMITTER_EMAIL": "config-test@example.invalid",
        "GIT_AUTHOR_DATE": PINNED_DATE,
        "GIT_COMMITTER_DATE": PINNED_DATE,
    }


@pytest.fixture(autouse=True)
def home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The case's own home directory, with its own git configuration, caches and Docker configuration.

    Everything that would otherwise reach the person's home (git, pre-commit, uv, Docker, make) is
    pointed here, and the variables a surrounding make, pre-commit or CI run sets are dropped.
    """
    home = tmp_path_factory.mktemp("home")
    for name in list(os.environ):
        if name.startswith(("GIT_", "PF_", "PRE_COMMIT", "UV_CACHE")) or name in {
            "CI",
            "MAKEFLAGS",
            "MAKELEVEL",
            "MFLAGS",
        }:
            monkeypatch.delenv(name)
    plugins = [directory for directory in COMPOSE_PLUGIN_DIRS if (Path(directory) / "docker-compose").exists()]
    (home / ".docker").mkdir()
    (home / ".docker" / "config.json").write_text(json.dumps({"cliPluginsExtraDirs": plugins}))
    for name, value in {
        "HOME": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share",
        "DOCKER_CONFIG": home / ".docker",
        "PYTHONDONTWRITEBYTECODE": "1",
        **git_env(home),
    }.items():
        monkeypatch.setenv(name, str(value))
    return home


# ─── Stub programs ───────────────────────────────────────────────────────────


@dataclass
class Stubs:
    """Executables on a directory placed ahead of the real PATH; each records its calls."""

    bin: Path
    log: Path

    def add(self, name: str, stdout: str = "", stderr: str = "", status: int = 0) -> None:
        script = f'#!/bin/sh\necho "{name} $* @ ${{PWD##*/}}" >> "{self.log}"\n'
        if stdout:
            script += f"printf '%s' '{stdout}'\n"
        if stderr:
            script += f"printf '%s' '{stderr}' >&2\n"
        script += f"exit {status}\n"
        (self.bin / name).write_text(script)
        (self.bin / name).chmod(0o755)

    @property
    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def stubs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Stubs:
    """An empty stub directory at the front of PATH; a case adds the programs it stands in for."""
    directory = tmp_path_factory.mktemp("stubs")
    (directory / "bin").mkdir()
    monkeypatch.setenv("PATH", f"{directory / 'bin'}{os.pathsep}{os.environ['PATH']}")
    return Stubs(directory / "bin", directory / "calls.log")


def git_tags(tags: list[str]) -> str:
    """What `git ls-remote --tags` prints for `tags`, as a stub's single-quoted stdout."""
    return "".join(f"0123456789abcdef0123456789abcdef01234567\trefs/tags/{tag}\n" for tag in tags)


# ─── Running the command ─────────────────────────────────────────────────────


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str


def run_config(*argv: object) -> Result:
    """`productforge-config` as a person runs it: its `main` with an argument vector, output captured."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main([str(arg) for arg in argv])
        except SystemExit as exit_:
            code = exit_.code if isinstance(exit_.code, int) else 1
    return Result(code, out.getvalue(), err.getvalue())


def run_make(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Make as a developer's shell runs it (the environment is already free of a surrounding make's)."""
    return subprocess.run(["make", "--no-print-directory", *args], cwd=cwd, capture_output=True, text=True)


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@dataclass
class Repo:
    """A repository the command works on, a copy of a fixture under the case's own directory."""

    root: Path

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def edit(self, rel: str, old: str, new: str) -> None:
        text = self.read(rel)
        assert old in text, f"{old!r} is not in {rel}"
        self.write(rel, text.replace(old, new))

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def tree(self) -> dict[str, bytes]:
        return tree(self.root)

    def update(self, *extra: str, version: str | None = VERSION) -> Result:
        version_args = ["--version", version] if version else []
        return run_config("update", *version_args, "--path", self.root, *extra)

    def check(self, *extra: str, version: str | None = None) -> Result:
        return self.update("--check", *extra, version=version)

    def make(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_make(self.root, *args)

    def dry(self, *args: str) -> str:
        """The commands `make -n` says a target runs."""
        result = self.make("-n", *args)
        assert result.returncode == 0, result.stderr
        return result.stdout


def _copy(name: str, tmp_path: Path, shipped: Path) -> Repo:
    dest = tmp_path / name
    shutil.copytree(shipped / "tests" / name, dest)
    return Repo(dest)


@pytest.fixture
def api_repo(tmp_path: Path, shipped: Path) -> Repo:
    """A repository that takes the python and django-api kits at slot 3, not yet updated."""
    return _copy("fixture_api", tmp_path, shipped)


@pytest.fixture
def web_repo(tmp_path: Path, shipped: Path) -> Repo:
    """A repository that takes the flutter-web kit at slot 3, not yet updated."""
    return _copy("fixture_web", tmp_path, shipped)


@pytest.fixture
def python_repo(tmp_path: Path, shipped: Path) -> Repo:
    """A repository that takes the python kit only, with its own markdownlint, pyproject and setup.cfg keys."""
    return _copy("fixture_repo", tmp_path, shipped)


# ─── What the repository ships, read once ────────────────────────────────────


def _run_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The directory the run's workers share: pytest's base temporary directory, not a worker's own."""
    base = tmp_path_factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


def _locked(path: Path) -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def lock() -> Iterator[None]:
        with open(path, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    return lock()


@pytest.fixture(scope="session")
def shipped(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the checkout: the settings, kits, actions, workflows and hook manifest the repository ships, and the
    fixtures under `tests/` the cases copy from.

    Made once per run, by the first worker to need it; the checkout is read here and nowhere else, and no case
    writes to the copy.
    """
    base = _run_base(tmp_path_factory)
    destination = base / "shipped"
    with _locked(base / "shipped.lock"):
        if not (destination / ".copied").exists():
            shutil.copytree(
                REAL_ROOT,
                destination,
                ignore=lambda _directory, names: [n for n in names if n in JUNK or n.endswith(".egg-info")],
            )
            (destination / ".copied").write_text("")
    return destination


# ─── The hooks the repository publishes, run for real ────────────────────────


@pytest.fixture(scope="session")
def hook_toolchain(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The one home the hook tests install their tools into: pre-commit's environments, pip, Go.

    Under pytest's temporary directory and shared by the run's workers, so each tool is installed once
    per run; never the person's. Pre-commit locks its own store, so workers can use it together.
    `PRODUCTFORGE_CONFIG_TEST_TOOLCHAIN` names a directory to keep it in between runs instead, which saves
    the install on every run after the first.
    """
    kept = os.environ.get("PRODUCTFORGE_CONFIG_TEST_TOOLCHAIN")
    toolchain = Path(kept) if kept else _run_base(tmp_path_factory) / "toolchain"
    toolchain.mkdir(parents=True, exist_ok=True)
    return toolchain


@pytest.fixture(scope="session")
def hook_source(hook_toolchain: Path, shipped: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The repository's hook manifest and the package its hooks install, committed in a repository of its own.

    So that pre-commit clones a repository of the case's making and never the checkout. The commit has a fixed
    date, so its revision is the same in every worker, and the same between runs of the same files.
    """
    destination = hook_toolchain / "hook-source"
    run = str(_run_base(tmp_path_factory))
    with _locked(hook_toolchain / "hook-source.lock"):
        if not (destination / ".run").exists() or (destination / ".run").read_text() != run:
            shutil.rmtree(destination, ignore_errors=True)
            shutil.copytree(
                shipped,
                destination,
                ignore=lambda _directory, names: [n for n in names if n == "tests" or n == ".copied"],
            )
            env = {**os.environ, **git_env(hook_toolchain)}
            for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "hooks"]):
                subprocess.run(["git", *args], cwd=destination, env=env, check=True, capture_output=True)
            (destination / ".run").write_text(run)
    return destination


def pre_commit(cwd: Path, hook_toolchain: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`pre-commit` in `cwd` with the shared toolchain as its home; git stays pinned by the case's variables."""
    env = {
        **os.environ,
        "HOME": str(hook_toolchain),
        "XDG_CACHE_HOME": str(hook_toolchain / ".cache"),
        "PRE_COMMIT_HOME": str(hook_toolchain / ".cache" / "pre-commit"),
    }
    return subprocess.run([sys.executable, "-m", "pre_commit", *args], cwd=cwd, env=env, capture_output=True, text=True)


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Real git, with the case's pinned configuration."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
