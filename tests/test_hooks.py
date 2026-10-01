"""The hooks `.pre-commit-hooks.yaml` publishes, run by pre-commit for real against throwaway repositories.

Pre-commit clones a copy of the manifest and its package (`hook_source`), never the checkout, and installs
each tool into the run's shared toolchain home (`hook_toolchain`); the repository a hook runs in, its
files, its git configuration and its identity are the case's own.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from .conftest import git, pre_commit, run_config


def consumer(root: Path, hook_source: Path, hooks: list[str]) -> Path:
    """A git repository whose pre-commit config takes `hooks` from the hook source."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    revision = git(hook_source, "rev-parse", "HEAD").stdout.strip()
    config = {"repos": [{"repo": str(hook_source), "rev": revision, "hooks": [{"id": hook} for hook in hooks]}]}
    (root / ".pre-commit-config.yaml").write_text(yaml.safe_dump(config))
    git(root, "add", ".pre-commit-config.yaml")
    git(root, "commit", "-q", "-m", "config")
    return root


def statuses(output: str) -> dict[str, str]:
    """Each hook's name and its Passed, Failed or Skipped, from pre-commit's report."""
    return {m.group(1): m.group(2) for m in re.finditer(r"^(.+?)\.{2,}(Passed|Failed|Skipped)", output, re.MULTILINE)}


def test_the_manifest_hooks_run_the_tools_they_wrap(tmp_path: Path, hook_source: Path, hook_toolchain: Path) -> None:
    repo = consumer(
        tmp_path / "consumer",
        hook_source,
        ["trailing-whitespace", "end-of-file-fixer", "yamllint", "taplo-format", "shellcheck"],
    )
    files = {
        "has_trailing_whitespace.txt": "a line with trailing whitespace   \n",
        "no_newline.json": "{}",
        "good.yaml": "---\nkey: value\n",
        "bad.yaml": "key: value\n",
        "unformatted.toml": "a   =   1\n",
        "script.sh": "#!/usr/bin/env bash\necho $1\n",
    }
    for name, text in files.items():
        (repo / name).write_text(text)

    result = pre_commit(repo, hook_toolchain, "run", "--files", *files)

    assert result.returncode != 0, result.stdout + result.stderr  # fixers that fix fail, as any fixer does
    report = statuses(result.stdout)
    assert (repo / "has_trailing_whitespace.txt").read_text() == "a line with trailing whitespace\n"
    assert report["trailing whitespace"] == "Failed"
    assert (repo / "no_newline.json").read_text() == "{}\n"
    assert report["fix end of files"] == "Failed"  # outside a commit it fixes and fails
    assert (repo / "unformatted.toml").read_text() == "a = 1\n"
    assert report["yamllint"] == "Failed"  # strict: a missing document start fails, as a trailing space would
    assert "bad.yaml" in result.stdout and "good.yaml" not in result.stdout
    assert report["shellcheck"] == "Failed" and "SC2086" in result.stdout


def test_checkmake_passes_the_makefile_init_writes(tmp_path: Path, hook_source: Path, hook_toolchain: Path) -> None:
    product = tmp_path / "product"
    assert (
        run_config(
            "init", "--kind", "web", "--name", "acme_web", "--slot", "1", "--version", "v1.2.3", "--path", product
        ).returncode
        == 0
    )
    repo = consumer(tmp_path / "consumer", hook_source, ["checkmake"])
    (repo / "Makefile").write_text((product / "Makefile").read_text())

    result = pre_commit(repo, hook_toolchain, "run", "--files", "Makefile")

    assert result.returncode == 0, result.stdout + result.stderr
    assert statuses(result.stdout) == {"checkmake": "Passed"}


def test_end_of_file_fixer_stages_its_fix_inside_a_commit_and_fails_there_in_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    # git hands every commit hook the index it is committing, as GIT_INDEX_FILE
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / ".git" / "index"))

    def fix() -> subprocess.CompletedProcess[str]:
        (tmp_path / "no_newline.json").write_text("{}")
        git(tmp_path, "add", "no_newline.json")
        return subprocess.run(
            [sys.executable, "-m", "productforge_config.final_newline", "no_newline.json"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

    monkeypatch.setenv("CI", "true")
    in_ci = fix()
    monkeypatch.delenv("CI")
    in_a_commit = fix()

    assert in_ci.returncode != 0 and in_ci.stdout == "Fixing no_newline.json\n"
    assert in_a_commit.returncode == 0, in_a_commit.stdout + in_a_commit.stderr
    assert git(tmp_path, "show", ":no_newline.json").stdout == "{}\n"  # the fix is staged, so the commit carries it


def test_the_django_mypy_hook_is_a_system_hook_over_the_projects_own_environment(shipped: Path) -> None:
    hooks = {hook["id"]: hook for hook in yaml.safe_load((shipped / ".pre-commit-hooks.yaml").read_text())}

    hook = hooks["django-mypy"]

    assert hook["language"] == "system" and hook["types"] == ["python"]
    assert "uv run mypy" in hook["entry"] and "PYTHONPATH=api" in hook["entry"]
    assert "--config-file=pyproject.toml" in hook["entry"]
    assert hook["entry"].rstrip().endswith("--")  # bash -c '…' -- receives the filenames as "$@"
