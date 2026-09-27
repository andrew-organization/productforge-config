"""Tests of `productforge_config.cli.update` against tests/fixture_repo.

Each test copies the fixture to a throwaway directory first — never the
committed fixture itself — so a run never leaves it modified.
"""

import shutil
from pathlib import Path

import pytest

from productforge_config import cli

FIXTURE = Path(__file__).parent / "fixture_repo"
VERSION = "v1.2.3"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def test_moves_the_hook_rev(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / ".pre-commit-lint.yaml").read_text()
    assert f"rev: {VERSION}" in text
    assert "v0.9.0" not in text


def test_keeps_the_repos_own_hook(repo: Path) -> None:
    before = (repo / ".pre-commit-lint.yaml").read_text()
    cli.update(repo, VERSION)
    after = (repo / ".pre-commit-lint.yaml").read_text()
    assert "fixture-own-check" in after
    # Only the rev line should differ.
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    differing = [i for i, (b, a) in enumerate(zip(before_lines, after_lines)) if b != a]
    assert differing == [before_lines.index("    rev: v0.9.0")]


def test_moves_the_action_ref(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / ".github" / "workflows" / "ci.yml").read_text()
    assert f"andrew-organization/productforge-config/actions/setup@{VERSION}" in text
    assert "v0.9.0" not in text


def test_rewrites_markdownlint_config_and_keeps_ignores(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / ".markdownlint-cli2.jsonc").read_text()
    assert '"MD060"' in text  # a rule only the shared config carries
    assert '"ignores": ["CHANGELOG.md"]' in text  # the repo's own key, untouched


def test_rewrites_black_and_isort_keeping_repo_own_keys(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / "pyproject.toml").read_text()
    assert 'target-version = ["py314"]' in text
    assert "line-length = 120" in text
    assert 'profile = "black"' in text
    assert "line_length = 120" in text
    assert 'skip_glob = ["*/migrations/*"]' in text  # the repo's own key, untouched


def test_rewrites_flake8_keeping_repo_own_exclude(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / "setup.cfg").read_text()
    assert "max-line-length = 120" in text
    assert "docstring-convention = google" in text
    assert "extend-ignore = D100,D101,D102,D103,D104,D105,D106,D107,D200,D202,D205,D212,D415,E203,W503" in text
    assert "exclude = .tox,.git,*/migrations/*" in text  # the repo's own key, untouched


def test_reports_what_it_changed(repo: Path) -> None:
    changed = cli.update(repo, VERSION)
    assert set(changed) == {
        ".pre-commit-lint.yaml",
        ".github/workflows/*.yml",
        ".markdownlint-cli2.jsonc",
        "pyproject.toml",
        "setup.cfg",
    }


def test_running_it_again_changes_nothing(repo: Path) -> None:
    cli.update(repo, VERSION)
    assert cli.update(repo, VERSION) == []


def test_main_runs_end_to_end(repo: Path) -> None:
    exit_code = cli.main(["update", "--version", VERSION, "--path", str(repo)])
    assert exit_code == 0
    assert f"rev: {VERSION}" in (repo / ".pre-commit-lint.yaml").read_text()
