"""Tests of `productforge_config.github` (`check`/`apply`) against a
stubbed `gh` — tests/fixtures/fake_gh.py, installed onto PATH for each
test, never a real network call.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from productforge_config import cli, github

FAKE_GH = Path(__file__).parent / "fixtures" / "fake_gh.py"
REPO = "andrew-organization/example"

SETTINGS = github._bundled_github_settings()


def _mismatched_repo_fields() -> dict[str, Any]:
    """Every settings/github.json `repository` value, deliberately wrong,
    so a check/apply exercises every field rather than only some.
    """
    wrong: dict[str, Any] = {}
    for key, wanted in SETTINGS["repository"].items():
        if isinstance(wanted, bool):
            wrong[key] = not wanted
        else:
            wrong[key] = f"not-{wanted}"
    return wrong


def _mismatched_workflow_permissions() -> dict[str, Any]:
    return {
        "default_workflow_permissions": "write",
        "can_approve_pull_request_reviews": True,
    }


@pytest.fixture
def gh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Installs the stubbed `gh` onto PATH and returns a helper to seed
    its state and read back the calls it recorded.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh_path = bin_dir / "gh"
    fake_gh_path.write_text(FAKE_GH.read_text())
    fake_gh_path.chmod(fake_gh_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    state_path = tmp_path / "state.json"
    calls_path = tmp_path / "calls.jsonl"

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(state_path))
    monkeypatch.setenv("FAKE_GH_CALLS", str(calls_path))

    class Harness:
        def seed(self, repo: dict, workflow_permissions: dict, rulesets: list) -> None:
            state_path.write_text(
                json.dumps({"repo": repo, "workflow_permissions": workflow_permissions, "rulesets": rulesets})
            )

        def state(self) -> dict:
            return json.loads(state_path.read_text())

        def calls(self) -> list[dict]:
            if not calls_path.exists():
                return []
            return [json.loads(line) for line in calls_path.read_text().splitlines() if line.strip()]

    return Harness()


def test_check_reports_every_difference_and_exits_1(gh_state) -> None:
    gh_state.seed(
        repo={"private": False, **_mismatched_repo_fields()},
        workflow_permissions=_mismatched_workflow_permissions(),
        rulesets=[],
    )

    diffs = github.check(REPO)

    for key in SETTINGS["repository"]:
        assert any(line.startswith(f"repository.{key}:") for line in diffs), diffs
    for key in SETTINGS["actions_workflow_permissions"]:
        assert any(line.startswith(f"actions_workflow_permissions.{key}:") for line in diffs), diffs
    assert f"ruleset {SETTINGS['ruleset_public_only']['name']!r}: missing" in diffs

    exit_code = cli.main(["github", "check", "--repo", REPO])
    assert exit_code == 1


def test_check_reports_nothing_and_exits_0_when_matching(gh_state) -> None:
    ruleset = dict(SETTINGS["ruleset_public_only"])
    ruleset["id"] = 1
    gh_state.seed(
        repo={"private": False, **SETTINGS["repository"]},
        workflow_permissions=dict(SETTINGS["actions_workflow_permissions"]),
        rulesets=[ruleset],
    )

    assert github.check(REPO) == []
    assert cli.main(["github", "check", "--repo", REPO]) == 0


def test_apply_issues_the_expected_calls(gh_state) -> None:
    gh_state.seed(
        repo={"private": False, **_mismatched_repo_fields()},
        workflow_permissions=_mismatched_workflow_permissions(),
        rulesets=[],
    )

    exit_code = cli.main(["github", "apply", "--repo", REPO])
    assert exit_code == 0

    calls = gh_state.calls()
    methods_and_endpoints = [(c["method"], c["endpoint"]) for c in calls]

    assert ("PATCH", f"repos/{REPO}") in methods_and_endpoints
    assert ("PUT", f"repos/{REPO}/actions/permissions/workflow") in methods_and_endpoints
    assert ("POST", f"repos/{REPO}/rulesets") in methods_and_endpoints

    patch_call = next(c for c in calls if c["method"] == "PATCH" and c["endpoint"] == f"repos/{REPO}")
    assert patch_call["body"] == SETTINGS["repository"]

    put_call = next(c for c in calls if c["method"] == "PUT" and c["endpoint"].endswith("workflow"))
    assert put_call["body"] == SETTINGS["actions_workflow_permissions"]

    post_call = next(c for c in calls if c["method"] == "POST")
    assert post_call["body"] == SETTINGS["ruleset_public_only"]

    # The live state now matches the file exactly.
    assert github.check(REPO) == []


def test_apply_is_idempotent(gh_state) -> None:
    gh_state.seed(
        repo={"private": False, **_mismatched_repo_fields()},
        workflow_permissions=_mismatched_workflow_permissions(),
        rulesets=[],
    )

    first = github.apply(REPO)
    assert github.check(REPO) == []

    second = github.apply(REPO)
    assert github.check(REPO) == []

    # The second run took the same actions as the first (a create becomes
    # an update, since the ruleset now exists) rather than erroring or
    # silently doing nothing.
    assert len(first) == len(second) == 3
    assert any("created" in line for line in first)
    assert any("updated" in line for line in second)

    calls = gh_state.calls()
    ruleset_calls = [c for c in calls if "/rulesets" in c["endpoint"]]
    # First apply: GET the list (empty), then POST to create.
    # Second apply: GET the list (finds it), then PUT to update.
    assert ("POST", f"repos/{REPO}/rulesets") in [(c["method"], c["endpoint"]) for c in ruleset_calls]
    assert any(c["method"] == "PUT" and "/rulesets/" in c["endpoint"] for c in ruleset_calls)


def test_private_repo_skips_the_ruleset_on_check_and_apply(gh_state) -> None:
    gh_state.seed(
        repo={"private": True, **_mismatched_repo_fields()},
        workflow_permissions=_mismatched_workflow_permissions(),
        rulesets=[],
    )

    diffs = github.check(REPO)
    assert not any("ruleset" in line for line in diffs)

    actions = github.apply(REPO)
    assert any("skipped" in line and "private" in line for line in actions)

    calls = gh_state.calls()
    assert not any("/rulesets" in c["endpoint"] for c in calls)

    # The repository fields and Actions permissions are still applied for
    # a private repository — only the ruleset is skipped.
    state = gh_state.state()
    assert state["repo"] == {"private": True, **SETTINGS["repository"]}
    assert state["workflow_permissions"] == SETTINGS["actions_workflow_permissions"]


def test_apply_returns_0_from_main(gh_state) -> None:
    gh_state.seed(
        repo={"private": True, **SETTINGS["repository"]},
        workflow_permissions=dict(SETTINGS["actions_workflow_permissions"]),
        rulesets=[],
    )
    assert cli.main(["github", "apply", "--repo", REPO]) == 0
