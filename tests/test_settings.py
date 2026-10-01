"""Each decision the shared settings carry is stated once and reaches every place that needs it: the Python
version, the line length, the tool versions.
"""

import os
import re
import shutil
from pathlib import Path

import pytest
import tomllib
import yaml

from productforge_config import cli, kits

from .conftest import JUNK, Repo


@pytest.fixture
def settings_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shipped: Path):
    """Point `update` at a copy of settings/ whose Python version, and optionally line length, are given."""

    def use(version: str, line_length: int | None = None) -> None:
        copy = tmp_path / "settings"
        shutil.copytree(shipped / "settings", copy, dirs_exist_ok=True)
        text = (copy / "python.toml").read_text()
        text = re.sub(r'^version = ".*"$', f'version = "{version}"', text, flags=re.M)
        if line_length is not None:
            text = re.sub(r"^line-length = .*$", f"line-length = {line_length}", text, flags=re.M)
        (copy / "python.toml").write_text(text)
        monkeypatch.setattr(cli, "_bundled_settings_dir", lambda: copy)
        monkeypatch.setattr(kits, "bundled_settings_dir", lambda: copy)

    return use


def test_the_stated_python_version_and_line_length_reach_every_derived_setting(
    api_repo: Repo, web_repo: Repo, settings_with, shipped: Path
) -> None:
    stated = tomllib.loads((shipped / "settings" / "python.toml").read_text())["python"]
    version, line_length = stated["version"], stated["line-length"]
    major_minor = version.replace(".", "")

    api_repo.update()
    web_repo.update()

    pyproject = api_repo.read("pyproject.toml")
    assert f'requires-python = ">={version},<4.0"' in pyproject
    assert f'target-version = ["py{major_minor}"]' in pyproject and f'python_version = "{version}"' in pyproject
    assert f"line-length = {line_length}" in pyproject and f"line_length = {line_length}" in pyproject
    assert f"max-line-length = {line_length}" in api_repo.read("setup.cfg")
    assert f"FROM python:{version}-slim-bookworm" in api_repo.read(".productforge/Dockerfile")
    for repo in (api_repo, web_repo):  # a repository with no Python kit gets the version too, for uv
        assert repo.read(".python-version") == f"{version}\n"
        assert ".python-version" in repo.read(".productforge/manifest")

    settings_with("3.15", line_length=100)
    api_repo.update()
    web_repo.update()

    pyproject = api_repo.read("pyproject.toml")
    assert 'requires-python = ">=3.15,<4.0"' in pyproject and 'target-version = ["py315"]' in pyproject
    assert 'python_version = "3.15"' in pyproject
    assert "line-length = 100" in pyproject and "line_length = 100" in pyproject
    assert "max-line-length = 100" in api_repo.read("setup.cfg")
    assert "120" not in pyproject + api_repo.read("setup.cfg") and "3.14" not in pyproject and "py314" not in pyproject
    dockerfile = api_repo.read(".productforge/Dockerfile")
    assert "FROM python:3.15-slim-bookworm" in dockerfile and "@PYTHON_VERSION@" not in dockerfile
    assert api_repo.read(".python-version") == "3.15\n" and web_repo.read(".python-version") == "3.15\n"


def test_update_refuses_a_python_version_that_is_not_major_dot_minor(api_repo: Repo, settings_with) -> None:
    settings_with("3.14.1")
    before = api_repo.tree()

    result = api_repo.update()

    assert result.returncode == 1
    assert "major.minor" in result.stderr
    assert api_repo.tree() == before


def test_each_decision_is_stated_once(shipped: Path) -> None:
    settings = tomllib.loads((shipped / "settings" / "python.toml").read_text())
    hooks = yaml.safe_load((shipped / ".pre-commit-hooks.yaml").read_text())
    (pyupgrade,) = (h for h in hooks if h["id"] == "pyupgrade")
    version = settings["python"]["version"]

    assert "target-version" not in settings["tool"].get("black", {})  # derived from the version, not restated
    assert "python_version" not in settings["tool"]["mypy"]
    assert "line-length" not in settings["tool"].get("black", {}) and "line_length" not in settings["tool"]["isort"]
    assert "max-line-length" not in settings["tool"]["flake8"]
    assert pyupgrade["args"] == [
        f"--py{version.replace('.', '')}-plus"
    ]  # fails when the version moves and this does not
    action = (shipped / "actions" / "setup" / "action.yml").read_text()
    assert (
        "settings/python.toml" in action and '["python"]["version"]' in action
    )  # CI reads the same file `update` does

    pins = {
        dependency
        for hook in hooks
        for dependency in hook.get("additional_dependencies", [])
        if re.search(r"==|@", dependency)
    }
    assert {"black", "yamllint", "flake8"} <= {re.split(r"==|@", pin)[0] for pin in pins}

    for directory, subdirectories, names in os.walk(shipped):
        subdirectories[:] = [d for d in subdirectories if d not in JUNK and not d.startswith("fixture")]
        for name in names:
            path = Path(directory) / name
            if name in {".pre-commit-hooks.yaml", "uv.lock"}:
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError, OSError:
                continue
            for pin in pins:
                assert pin not in text, f"{path.relative_to(shipped)} restates {pin}"
