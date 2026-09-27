"""A smoke test of .pre-commit-hooks.yaml itself, via `pre-commit try-repo`
against a small fixture file — proving the hook manifest here is valid and
actually runs the tool it wraps, not just that update() edits text right.
"""

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
