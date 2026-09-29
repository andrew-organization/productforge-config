"""A smoke test of .pre-commit-hooks.yaml itself, via `pre-commit try-repo`
against a small fixture file — proving the hook manifest here is valid and
actually runs the tool it wraps, not just that update() edits text right.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_trailing_whitespace_hook_runs_and_fixes(tmp_path: Path) -> None:
    target = tmp_path / "has_trailing_whitespace.txt"
    target.write_text("a line with trailing whitespace   \n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pre_commit",
            "try-repo",
            str(REPO_ROOT),
            "trailing-whitespace",
            "--files",
            str(target),
        ],
        # try-repo needs a git repository underfoot for its own bookkeeping —
        # this repository's own checkout, not the throwaway tmp_path the
        # target file lives in (an absolute path, so cwd doesn't affect it).
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    # try-repo exits non-zero when a hook makes a fix — that's the hook working.
    assert result.returncode != 0, result.stdout + result.stderr
    assert target.read_text() == "a line with trailing whitespace\n"


def _try_hook(hook: str, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pre_commit", "try-repo", str(REPO_ROOT), hook, "--files", str(target)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_yamllint_hook_runs_strictly(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    # Outside a repository with its .yamllint, yamllint's defaults apply,
    # which want a document start.
    good.write_text("---\nkey: value\n")
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: value   \n")

    assert _try_hook("yamllint", good).returncode == 0
    assert _try_hook("yamllint", bad).returncode != 0


def test_taplo_format_hook_runs_and_fixes(tmp_path: Path) -> None:
    target = tmp_path / "unformatted.toml"
    target.write_text("a   =   1\n")

    result = _try_hook("taplo-format", target)

    # taplo exits cleanly once it has formatted, and pre-commit sees no
    # change outside its own repository, so the file itself is the proof.
    assert "taplo format" in result.stdout, result.stdout + result.stderr
    assert target.read_text() == "a = 1\n"


def test_shellcheck_hook_runs(tmp_path: Path) -> None:
    target = tmp_path / "script.sh"
    target.write_text("#!/usr/bin/env bash\necho $1\n")

    result = _try_hook("shellcheck", target)

    assert result.returncode != 0
    assert "SC2086" in result.stdout


def _without_ci() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("CI", "GIT_INDEX_FILE")}


def test_end_of_file_fixer_fails_outside_a_commit(tmp_path: Path) -> None:
    target = tmp_path / "no_newline.json"
    target.write_text("{}")

    result = subprocess.run(
        [sys.executable, "-m", "pre_commit", "try-repo", str(REPO_ROOT), "end-of-file-fixer", "--files", str(target)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=_without_ci(),
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert target.read_text() == "{}\n"


def test_end_of_file_fixer_stages_its_fix_inside_a_commit(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    target = tmp_path / "no_newline.json"
    target.write_text("{}")
    subprocess.run(["git", "add", "no_newline.json"], cwd=tmp_path, check=True)
    # git hands every commit hook the index it's committing, as GIT_INDEX_FILE.
    env = _without_ci() | {"GIT_INDEX_FILE": str(tmp_path / ".git" / "index")}

    result = subprocess.run(
        # Called directly: try-repo builds its own throwaway repository with git, which a
        # GIT_INDEX_FILE pointing elsewhere would break. The manifest is proven above.
        [sys.executable, "-m", "productforge_config.final_newline", "no_newline.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    staged = subprocess.run(["git", "show", ":no_newline.json"], cwd=tmp_path, capture_output=True, text=True)
    assert staged.stdout == "{}\n"


def test_end_of_file_fixer_still_fails_inside_a_commit_in_ci(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "no_newline.json").write_text("{}")
    subprocess.run(["git", "add", "no_newline.json"], cwd=tmp_path, check=True)
    env = _without_ci() | {"GIT_INDEX_FILE": str(tmp_path / ".git" / "index"), "CI": "true"}

    result = subprocess.run(
        # Called directly: try-repo builds its own throwaway repository with git, which a
        # GIT_INDEX_FILE pointing elsewhere would break. The manifest is proven above.
        [sys.executable, "-m", "productforge_config.final_newline", "no_newline.json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0, result.stdout + result.stderr


def test_django_mypy_hook_is_a_system_hook_over_the_projects_own_environment() -> None:
    import yaml

    hooks = {hook["id"]: hook for hook in yaml.safe_load((REPO_ROOT / ".pre-commit-hooks.yaml").read_text())}
    hook = hooks["django-mypy"]
    assert hook["language"] == "system"
    assert hook["types"] == ["python"]
    assert "uv run mypy" in hook["entry"]
    assert "PYTHONPATH=api" in hook["entry"]
    assert "--config-file=pyproject.toml" in hook["entry"]
    assert hook["entry"].rstrip().endswith("--")  # bash -c '…' -- receives the filenames as "$@"
