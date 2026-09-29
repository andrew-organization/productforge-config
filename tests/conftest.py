"""Fixtures shared by the kit tests: a throwaway copy of each kind's fixture repository."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
VERSION = "v1.2.3"


def _copy(name: str, tmp_path: Path) -> Path:
    dest = tmp_path / name
    shutil.copytree(TESTS / name, dest)
    return dest


@pytest.fixture
def api_repo(tmp_path: Path) -> Path:
    """A repository that takes the api kit at slot 3, not yet updated."""
    return _copy("fixture_api", tmp_path)


@pytest.fixture
def web_repo(tmp_path: Path) -> Path:
    """A repository that takes the web kit at slot 3, not yet updated."""
    return _copy("fixture_web", tmp_path)


def run_make(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run make in `cwd` as a developer's shell would, not as a recipe of the `make test` running
    this: with none of its MAKEFLAGS, which make 4 turns into "Entering directory" lines.
    """
    env = {k: v for k, v in os.environ.items() if k not in {"MAKEFLAGS", "MAKELEVEL", "MFLAGS"}}
    return subprocess.run(["make", "--no-print-directory", *args], cwd=cwd, env=env, capture_output=True, text=True)
