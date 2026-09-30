"""Tests that each decision config carries is stated once and reaches every place that needs it:
the Python version, and the check that what `update` wrote is unchanged.
"""

import re
import shutil
from pathlib import Path

import pytest
import tomllib
import yaml

from productforge_config import cli, kits

from .conftest import VERSION

REPO_ROOT = Path(__file__).parent.parent
SETTINGS = REPO_ROOT / "settings"


@pytest.fixture
def settings_with_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point update at a copy of settings/ whose Python version is the one given."""

    def use(version: str, line_length: int | None = None) -> None:
        copy = tmp_path / "settings"
        shutil.copytree(SETTINGS, copy, dirs_exist_ok=True)
        text = (copy / "python.toml").read_text()
        text = re.sub(r'^version = ".*"$', f'version = "{version}"', text, flags=re.M)
        if line_length is not None:
            text = re.sub(r"^line-length = .*$", f"line-length = {line_length}", text, flags=re.M)
        (copy / "python.toml").write_text(text)
        monkeypatch.setattr(cli, "_bundled_settings_dir", lambda: copy)
        monkeypatch.setattr(kits, "bundled_settings_dir", lambda: copy)

    return use


def _state() -> str:
    return tomllib.loads((SETTINGS / "python.toml").read_text())["python"]["version"]


def test_the_version_derives_the_three_forms_of_it() -> None:
    assert cli.derive_python("3.14") == {"black": "py314", "mypy": "3.14", "requires-python": ">=3.14,<4.0"}
    assert cli.derive_python("3.9")["black"] == "py39"


@pytest.mark.parametrize("bad", ["3", "3.14.1", "py314", "", "3.x"])
def test_a_version_that_is_not_major_dot_minor_is_refused(bad: str) -> None:
    with pytest.raises(cli.UpdateError, match="major.minor"):
        cli.derive_python(bad)


def test_the_settings_state_the_version_and_no_derived_form_of_it() -> None:
    settings = tomllib.loads((SETTINGS / "python.toml").read_text())
    assert settings["python"]["version"] == _state()
    assert "target-version" not in settings["tool"].get("black", {})
    assert "python_version" not in settings["tool"]["mypy"]


def test_pyupgrades_argument_follows_the_stated_version() -> None:
    """Fails when [python] version moves and the hook's `--pyNN-plus` does not."""
    hooks = yaml.safe_load((REPO_ROOT / ".pre-commit-hooks.yaml").read_text())
    (pyupgrade,) = (h for h in hooks if h["id"] == "pyupgrade")
    assert pyupgrade["args"] == [f"--{cli.derive_python(_state())['black']}-plus"]


def test_changing_the_version_moves_every_derived_setting_in_an_updated_repository(
    api_repo: Path, settings_with_python
) -> None:
    settings_with_python("3.15")
    cli.update(api_repo, VERSION)
    text = (api_repo / "pyproject.toml").read_text()
    assert 'requires-python = ">=3.15,<4.0"' in text
    assert 'target-version = ["py315"]' in text
    assert 'python_version = "3.15"' in text
    assert "3.14" not in text and "py314" not in text


def test_the_settings_carry_the_stated_version_into_a_repository(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    text = (api_repo / "pyproject.toml").read_text()
    assert 'requires-python = ">=3.14,<4.0"' in text
    assert 'target-version = ["py314"]' in text
    assert 'python_version = "3.14"' in text


def test_a_pyproject_without_a_project_table_gets_no_requires_python(tmp_path: Path) -> None:
    (tmp_path / "productforge.env").write_text("PF_KITS=python\n")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    cli.update(tmp_path, VERSION)
    assert "requires-python" not in (tmp_path / "pyproject.toml").read_text()


def test_every_repository_gets_the_stated_python_as_a_python_version_file(web_repo: Path, api_repo: Path) -> None:
    """A repository with no Python kit (the web template) states no `requires-python`, so uv would pick
    whatever Python the machine has; the common kit writes the one this release states.
    """
    for repo in (web_repo, api_repo):
        cli.update(repo, VERSION)
        assert (repo / ".python-version").read_text() == f"{_state()}\n"
    assert ".python-version" in (web_repo / ".productforge" / "manifest").read_text()


def test_changing_the_version_moves_the_python_version_file(api_repo: Path, settings_with_python) -> None:
    settings_with_python("3.15")
    cli.update(api_repo, VERSION)
    assert (api_repo / ".python-version").read_text() == "3.15\n"


def test_check_fails_on_a_hand_edited_python_version_file(web_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.update(web_repo, VERSION)
    (web_repo / ".python-version").write_text("3.11\n")
    assert _check(web_repo) == 1
    assert ".python-version" in capsys.readouterr().out
    cli.update(web_repo, VERSION)
    assert _check(web_repo) == 0


def _line_length() -> int:
    return int(tomllib.loads((SETTINGS / "python.toml").read_text())["python"]["line-length"])


def test_the_settings_state_the_line_length_once() -> None:
    tool = tomllib.loads((SETTINGS / "python.toml").read_text())["tool"]
    assert "line-length" not in tool.get("black", {})
    assert "line_length" not in tool["isort"]
    assert "max-line-length" not in tool["flake8"]


def test_the_one_line_length_reaches_black_isort_and_flake8(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    n = _line_length()
    assert f"line-length = {n}" in (api_repo / "pyproject.toml").read_text()
    assert f"line_length = {n}" in (api_repo / "pyproject.toml").read_text()
    assert f"max-line-length = {n}" in (api_repo / "setup.cfg").read_text()


def test_changing_the_line_length_moves_all_three(api_repo: Path, settings_with_python) -> None:
    settings_with_python("3.14", line_length=100)
    cli.update(api_repo, VERSION)
    assert "line-length = 100" in (api_repo / "pyproject.toml").read_text()
    assert "line_length = 100" in (api_repo / "pyproject.toml").read_text()
    assert "max-line-length = 100" in (api_repo / "setup.cfg").read_text()
    assert "120" not in (api_repo / "pyproject.toml").read_text() + (api_repo / "setup.cfg").read_text()


def test_the_images_python_is_the_stated_version(api_repo: Path, settings_with_python) -> None:
    cli.update(api_repo, VERSION)
    assert f"FROM python:{_state()}-slim-bookworm" in (api_repo / ".productforge" / "Dockerfile").read_text()
    settings_with_python("3.15")
    cli.update(api_repo, VERSION)
    dockerfile = (api_repo / ".productforge" / "Dockerfile").read_text()
    assert "FROM python:3.15-slim-bookworm" in dockerfile
    assert "@PYTHON_VERSION@" not in dockerfile and "3.14" not in dockerfile


def test_the_setup_action_reads_the_same_file_the_update_reads() -> None:
    action = (REPO_ROOT / "actions" / "setup" / "action.yml").read_text()
    assert "settings/python.toml" in action
    assert '["python"]["version"]' in action


# ─── The check ────────────────────────────────────────────────────────────


def _check(repo: Path, *extra: str) -> int:
    return cli.main(["update", "--path", str(repo), "--check", *extra])


def test_check_passes_on_an_untouched_copy_while_a_newer_release_exists(
    api_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.update(api_repo, VERSION)
    monkeypatch.setattr(cli, "_latest_release_tag", lambda: "v9.9.9")  # never asked for
    assert _check(api_repo) == 0


def test_check_reports_a_newer_release_as_drift_only_when_asked_for_the_latest(
    api_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.update(api_repo, VERSION)
    monkeypatch.setattr(cli, "_latest_release_tag", lambda: "v9.9.9")
    assert _check(api_repo, "--version", "latest") == 1


def test_check_fails_on_a_hand_edited_common_mk(api_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.update(api_repo, VERSION)
    mk = api_repo / ".productforge" / "common.mk"
    mk.write_text(mk.read_text() + "\n# tweaked\n")
    assert _check(api_repo) == 1
    assert ".productforge/common.mk" in capsys.readouterr().out


def test_check_fails_on_a_yamllint_changed_by_one_character(api_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.update(api_repo, VERSION)
    yamllint = api_repo / ".yamllint"
    yamllint.write_text(yamllint.read_text().replace("max: 120", "max: 121"))
    assert _check(api_repo) == 1
    assert ".yamllint" in capsys.readouterr().out


def test_check_fails_on_a_workflow_ref_one_release_behind(api_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.update(api_repo, VERSION)
    ci = api_repo / ".github" / "workflows" / "ci.yml"
    ci.write_text(ci.read_text().replace(VERSION, "v1.2.2"))
    assert _check(api_repo) == 1
    assert ".github/workflows/*.yml" in capsys.readouterr().out


def test_check_fails_on_a_generated_lint_config_changed_by_hand(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    config = api_repo / ".productforge" / "pre-commit.yaml"
    config.write_text(config.read_text().replace("      - id: black\n", ""))
    assert _check(api_repo) == 1


def test_check_config_runs_the_check_at_the_recorded_release(api_repo: Path) -> None:
    from .conftest import run_make

    cli.update(api_repo, VERSION)
    out = run_make(api_repo, "-n", "check-config").stdout
    assert "update --check --version $release" in out
    assert "sed -n '1p' .productforge/release" in out


# ─── The tool versions ────────────────────────────────────────────────────


def test_the_tool_versions_are_stated_in_the_hooks_file_only() -> None:
    """Each tool a hook installs is pinned once, in .pre-commit-hooks.yaml; nothing else restates the pin."""
    import subprocess

    hooks = yaml.safe_load((REPO_ROOT / ".pre-commit-hooks.yaml").read_text())
    pins = {
        dependency
        for hook in hooks
        for dependency in hook.get("additional_dependencies", [])
        if re.search(r"==|@", dependency)
    }
    assert {"black", "yamllint", "flake8"} <= {re.split(r"==|@", pin)[0] for pin in pins}
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for name in tracked:
        if (
            name in {".pre-commit-hooks.yaml", "uv.lock"}
            or name.startswith("tests/fixture")
            or not (REPO_ROOT / name).is_file()
        ):
            continue
        try:
            text = (REPO_ROOT / name).read_text()
        except UnicodeDecodeError:
            continue
        for pin in pins:
            assert pin not in text, f"{name} restates {pin}"
