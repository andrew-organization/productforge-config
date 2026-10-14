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

from productforge_config import github

from .conftest import run_config

REPO = "andrew-organization/example"

SETTINGS = github._bundled_github_settings()
MAIN = "require-pull-request-to-change-main"
TAGS = "protect-release-tags"
BLOCK = "block-main-rewrites"
RULESETS = {"standard": [MAIN, TAGS], "product": [MAIN, TAGS], "publishes": [MAIN, TAGS], "block-only": [BLOCK]}
DEFAULT_CHECK = {"standard": "ci", "product": "ci / ci", "publishes": "ci"}
OWNER_BYPASS = {"actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}
DEPLOY_KEY_BYPASS = {"actor_type": "DeployKey", "bypass_mode": "always"}
PRODUCTION = "production"
PRODUCTION_POLICY = {"protected_branches": False, "custom_branch_policies": True}
NO_RULES = "Upgrade to GitHub Pro or make this repository public to enable this feature."


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
def gh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shipped: Path):
    """Installs the stubbed `gh` onto PATH and returns a helper to seed
    its state and read back the calls it recorded.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_gh_path = bin_dir / "gh"
    fake_gh_path.write_text((shipped / "tests" / "fixtures" / "fake_gh.py").read_text())
    fake_gh_path.chmod(fake_gh_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    state_path = tmp_path / "state.json"
    calls_path = tmp_path / "calls.jsonl"

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(state_path))
    monkeypatch.setenv("FAKE_GH_CALLS", str(calls_path))

    class Harness:
        def seed(self, repo: dict, workflow_permissions: dict, rulesets: list, **extra: Any) -> None:
            state_path.write_text(
                json.dumps({"repo": repo, "workflow_permissions": workflow_permissions, "rulesets": rulesets, **extra})
            )

        def seed_matching(self, **extra: Any) -> None:
            self.seed(
                repo={"private": True, **SETTINGS["repository"]},
                workflow_permissions=dict(SETTINGS["actions_workflow_permissions"]),
                rulesets=[],
                **extra,
            )

        def reset_calls(self) -> None:
            calls_path.unlink(missing_ok=True)

        def state(self) -> dict:
            return json.loads(state_path.read_text())

        def calls(self) -> list[dict]:
            if not calls_path.exists():
                return []
            return [json.loads(line) for line in calls_path.read_text().splitlines() if line.strip()]

    return Harness()


def _silent(result) -> bool:
    return (result.returncode, result.stdout, result.stderr) == (0, "", "")


def _sent_rulesets(calls: list[dict]) -> dict[str, dict]:
    return {
        c["body"]["name"]: c["body"] for c in calls if c["method"] in ("POST", "PUT") and "/rulesets" in c["endpoint"]
    }


def _required_checks(ruleset: dict) -> list[str]:
    rule = next(r for r in ruleset["rules"] if r["type"] == "required_status_checks")
    return [c["context"] for c in rule["parameters"]["required_status_checks"]]


def test_each_variant_applies_its_rulesets_and_a_second_apply_changes_nothing(gh_state) -> None:
    for variant, names in RULESETS.items():
        gh_state.seed(
            repo={"private": True, **_mismatched_repo_fields()},
            workflow_permissions=_mismatched_workflow_permissions(),
            rulesets=[],
            properties=[{"property_name": "product", "value": "productforge"}],
        )

        gh_state.reset_calls()
        first = run_config("github", "apply", "--repo", REPO, "--variant", variant)
        assert (first.returncode, first.stderr) == (0, "")
        first_state = gh_state.state()
        first_calls = gh_state.calls()

        assert _silent(run_config("github", "check", "--repo", REPO, "--variant", variant))
        gh_state.reset_calls()
        second = run_config("github", "apply", "--repo", REPO, "--variant", variant)
        assert second.returncode == 0

        assert gh_state.state() == first_state
        assert _silent(run_config("github", "check", "--repo", REPO, "--variant", variant))
        assert [r["name"] for r in first_state["rulesets"]] == names
        assert [c for c in gh_state.calls() if c["method"] != "GET"] == []
        assert not any("properties" in c["endpoint"] for c in first_calls)

        patch_call = next(c for c in first_calls if c["method"] == "PATCH" and c["endpoint"] == f"repos/{REPO}")
        assert patch_call["body"] == SETTINGS["repository"]
        assert second.stdout.splitlines() and all(line.endswith(": unchanged") for line in second.stdout.splitlines())

        sent = _sent_rulesets(first_calls)
        assert ("created" in first.stdout, "updated" in second.stdout) == (True, False)
        if variant == "block-only":
            assert set(sent) == {BLOCK}
            assert sent[BLOCK]["conditions"]["ref_name"]["include"] == ["refs/heads/main"]
            continue
        assert sent[MAIN]["conditions"]["ref_name"] == {"include": ["refs/heads/main"], "exclude": []}
        assert sent[TAGS]["conditions"]["ref_name"]["include"] == ["refs/tags/v*"]
        assert _required_checks(sent[MAIN]) == [DEFAULT_CHECK[variant]]
        assert sent[MAIN]["bypass_actors"] == ([OWNER_BYPASS] if variant == "publishes" else [])
        assert sent[TAGS]["bypass_actors"] == []


def test_required_check_overrides_the_variants_default(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])

    result = run_config(
        "github",
        "apply",
        "--repo",
        REPO,
        "--variant",
        "product",
        "--required-check",
        "build",
        "--required-check",
        "lint",
    )

    assert result.returncode == 0
    assert _required_checks(_sent_rulesets(gh_state.calls())[MAIN]) == ["build", "lint"]
    assert (
        run_config(
            "github",
            "check",
            "--repo",
            REPO,
            "--variant",
            "product",
            "--required-check",
            "build",
            "--required-check",
            "lint",
        ).stdout
        == ""
    )
    assert "required_status_checks" in run_config("github", "check", "--repo", REPO, "--variant", "product").stdout


def test_unknown_variant_and_a_check_with_block_only_are_refused_before_any_call(gh_state) -> None:
    gh_state.seed_matching()

    unknown = run_config("github", "apply", "--repo", REPO, "--variant", "bogus")
    assert unknown.returncode == 2
    assert unknown.stdout == "" and "unknown variant 'bogus'" in unknown.stderr

    block = run_config("github", "check", "--repo", REPO, "--variant", "block-only", "--required-check", "ci")
    assert block.returncode == 2
    assert block.stdout == "" and "block-only" in block.stderr

    assert gh_state.calls() == []


def test_product_is_set_and_compared_only_when_given(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "team", "value": "core"}])

    without = run_config("github", "check", "--repo", REPO, "--variant", "block-only")
    assert (without.returncode, without.stdout.splitlines()[0]) == (1, "product: missing")
    with_product = run_config("github", "check", "--repo", REPO, "--variant", "block-only", "--product", "productforge")
    assert (with_product.returncode, with_product.stdout.splitlines()[0]) == (1, "product: missing -> 'productforge'")

    gh_state.reset_calls()
    run_config("github", "apply", "--repo", REPO, "--variant", "block-only", "--product", "productforge")
    patch = next(
        c for c in gh_state.calls() if c["endpoint"] == f"repos/{REPO}/properties/values" and c["method"] != "GET"
    )
    assert patch["method"] == "PATCH"
    assert patch["body"] == {"properties": [{"property_name": "product", "value": "productforge"}]}
    assert gh_state.state()["properties"] == [
        {"property_name": "team", "value": "core"},
        {"property_name": "product", "value": "productforge"},
    ]

    assert run_config("github", "check", "--repo", REPO, "--variant", "block-only").stdout == ""
    assert (
        run_config("github", "check", "--repo", REPO, "--variant", "block-only", "--product", "productforge").stdout
        == ""
    )
    differs = run_config("github", "check", "--repo", REPO, "--variant", "block-only", "--product", "Other")
    assert (differs.returncode, differs.stdout) == (1, "product: 'productforge' -> 'Other'\n")

    gh_state.reset_calls()
    run_config("github", "apply", "--repo", REPO, "--variant", "block-only")
    assert not any("properties" in c["endpoint"] for c in gh_state.calls())


def test_apply_names_the_property_when_the_organisation_has_none(gh_state) -> None:
    gh_state.seed_matching(properties=[], no_product_property=True)

    result = run_config("github", "apply", "--repo", REPO, "--product", "productforge")

    assert result.returncode == 2
    assert "'product'" in result.stderr


def test_a_plan_without_rules_is_reported_skipped_and_apply_succeeds(gh_state) -> None:
    gh_state.seed(
        repo={"private": True, **_mismatched_repo_fields()},
        workflow_permissions=_mismatched_workflow_permissions(),
        rulesets=[],
        properties=[{"property_name": "product", "value": "productforge"}],
        rulesets_error=NO_RULES,
    )

    applied = run_config("github", "apply", "--repo", REPO, "--product", "productforge")
    assert (applied.returncode, applied.stderr) == (0, "")
    assert applied.stdout.splitlines()[-1] == f"ruleset: skipped ({NO_RULES})"
    assert gh_state.state()["repo"] == {"private": True, **SETTINGS["repository"]}
    assert not any(c["method"] in ("POST", "PUT") and "/rulesets" in c["endpoint"] for c in gh_state.calls())

    checked = run_config("github", "check", "--repo", REPO, "--product", "productforge")
    assert checked.stdout.count("ruleset: skipped") == 1 and "ruleset '" not in checked.stdout


def test_an_existing_ruleset_on_a_public_repository_is_updated_in_place(gh_state) -> None:
    gh_state.seed(
        repo={"private": False, **SETTINGS["repository"]},
        workflow_permissions=dict(SETTINGS["actions_workflow_permissions"]),
        rulesets=[{"id": 7, "name": MAIN, "target": "branch", "enforcement": "disabled", "rules": []}],
        properties=[],
    )

    assert any(f"ruleset '{MAIN}'.enforcement" in line for line in github.check(REPO))
    run_config("github", "apply", "--repo", REPO)

    calls = gh_state.calls()
    assert [(c["method"], c["endpoint"]) for c in calls if "/rulesets" in c["endpoint"] and c["method"] != "GET"] == [
        ("PUT", f"repos/{REPO}/rulesets/7"),
        ("POST", f"repos/{REPO}/rulesets"),
    ]
    assert gh_state.state()["rulesets"][0]["enforcement"] == "active"
    assert len(gh_state.state()["rulesets"]) == 2


def test_a_second_apply_with_a_product_writes_nothing_and_reports_unchanged(gh_state) -> None:
    gh_state.seed(
        repo={"private": True, **_mismatched_repo_fields()},
        workflow_permissions=_mismatched_workflow_permissions(),
        rulesets=[],
        properties=[{"property_name": "product", "value": "other"}],
    )
    first = run_config("github", "apply", "--repo", REPO, "--product", "productforge")
    assert "product: set to 'productforge'" in first.stdout

    gh_state.reset_calls()
    second = run_config("github", "apply", "--repo", REPO, "--product", "productforge")

    assert second.returncode == 0
    assert [c for c in gh_state.calls() if c["method"] != "GET"] == []
    assert second.stdout.splitlines() == [
        "repository: unchanged",
        "actions_workflow_permissions: unchanged",
        "product: unchanged",
        f"ruleset '{MAIN}': unchanged",
        f"ruleset '{TAGS}': unchanged",
    ]


def test_an_organisation_ruleset_of_the_same_name_is_never_matched(gh_state) -> None:
    inherited = {"id": 9, "name": MAIN, "source_type": "Organization", "target": "branch", "enforcement": "active"}
    gh_state.seed_matching(properties=[])
    state = gh_state.state()
    gh_state.seed(**{**state, "rulesets": [inherited]})

    assert f"ruleset '{MAIN}': missing" in run_config("github", "check", "--repo", REPO).stdout
    gh_state.reset_calls()
    applied = run_config("github", "apply", "--repo", REPO)

    assert f"ruleset '{MAIN}': created" in applied.stdout
    writes = [(c["method"], c["endpoint"]) for c in gh_state.calls() if c["method"] != "GET"]
    assert ("PUT", f"repos/{REPO}/rulesets/9") not in writes
    assert ("POST", f"repos/{REPO}/rulesets") in writes
    listing = next(c for c in gh_state.calls() if c["endpoint"].startswith(f"repos/{REPO}/rulesets?"))
    assert listing["endpoint"] == f"repos/{REPO}/rulesets?includes_parents=false"


def test_the_order_of_rules_and_status_checks_is_not_drift(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])
    run_config("github", "apply", "--repo", REPO, "--required-check", "build", "--required-check", "lint")
    state = gh_state.state()
    for ruleset in state["rulesets"]:
        ruleset["rules"].reverse()
        for rule in ruleset["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"].reverse()
    gh_state.seed(**state)
    args = ("--repo", REPO, "--required-check", "build", "--required-check", "lint")

    assert run_config("github", "check", *args).stdout == ""
    gh_state.reset_calls()
    second = run_config("github", "apply", *args)
    assert [c for c in gh_state.calls() if c["method"] != "GET"] == []
    assert "updated" not in second.stdout


def test_check_reports_nothing_for_an_owner_bypass_read_back_with_a_null_actor_id(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])
    run_config("github", "apply", "--repo", REPO, "--variant", "publishes")
    state = gh_state.state()
    for ruleset in state["rulesets"]:
        for actor in ruleset["bypass_actors"]:
            actor["actor_id"] = None
    gh_state.seed(**{**state, "properties": state["properties"]})

    assert run_config("github", "check", "--repo", REPO, "--variant", "publishes").stdout == ""

    state["rulesets"][0]["bypass_actors"][0]["bypass_mode"] = "pull_request"
    gh_state.seed(**state)
    assert "bypass_actors" in run_config("github", "check", "--repo", REPO, "--variant", "publishes").stdout


def test_bypass_deploy_keys_adds_the_deploy_key_actor_to_the_main_ruleset_only(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])
    args = ("--repo", REPO, "--variant", "publishes", "--bypass-deploy-keys")

    gh_state.reset_calls()
    assert run_config("github", "apply", *args).returncode == 0
    sent = _sent_rulesets(gh_state.calls())
    assert sent[MAIN]["bypass_actors"] == [OWNER_BYPASS, DEPLOY_KEY_BYPASS]
    assert sent[TAGS]["bypass_actors"] == []

    assert _silent(run_config("github", "check", *args))
    gh_state.reset_calls()
    run_config("github", "apply", *args)
    assert [c for c in gh_state.calls() if c["method"] != "GET"] == []

    without = run_config("github", "check", "--repo", REPO, "--variant", "publishes")
    assert without.returncode == 1 and "bypass_actors" in without.stdout


def test_check_reports_nothing_for_deploy_keys_read_back_with_a_null_actor_id_in_any_order(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])
    args = ("--repo", REPO, "--variant", "publishes", "--bypass-deploy-keys")
    run_config("github", "apply", *args)
    state = gh_state.state()
    for ruleset in state["rulesets"]:
        for actor in ruleset["bypass_actors"]:
            actor["actor_id"] = None
        ruleset["bypass_actors"].reverse()
    gh_state.seed(**state)

    assert run_config("github", "check", *args).stdout == ""

    state["rulesets"][0]["bypass_actors"] = [OWNER_BYPASS]
    gh_state.seed(**state)
    assert "bypass_actors" in run_config("github", "check", *args).stdout


def test_bypass_deploy_keys_is_refused_before_any_call_on_a_variant_without_one(gh_state) -> None:
    gh_state.seed_matching()
    gh_state.reset_calls()
    for variant in ("standard", "product", "block-only"):
        result = run_config("github", "apply", "--repo", REPO, "--variant", variant, "--bypass-deploy-keys")
        assert result.returncode == 2 and "--bypass-deploy-keys" in result.stderr
    assert gh_state.calls() == []


def test_check_accepts_the_defaults_github_adds_and_reports_a_differing_value(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])
    run_config("github", "apply", "--repo", REPO)
    state = gh_state.state()
    for ruleset in state["rulesets"]:
        for rule in ruleset["rules"]:
            rule.setdefault("parameters", {}).update({"required_reviewers": [], "created_by_github": True})
    gh_state.seed(**state)

    assert run_config("github", "check", "--repo", REPO).stdout == ""

    state["rulesets"][0]["enforcement"] = "disabled"
    gh_state.seed(**state)
    assert ".enforcement" in run_config("github", "check", "--repo", REPO).stdout


def _environment_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "/environments" in c["endpoint"]]


def _writes(calls: list[dict]) -> list[tuple[str, str]]:
    return [(c["method"], c["endpoint"].removeprefix(f"repos/{REPO}/")) for c in calls if c["method"] != "GET"]


def _policy_names(state: dict) -> list[str]:
    return [p["name"] for p in state["environments"][PRODUCTION]["policies"]]


def _apply_product(gh_state) -> list[dict]:
    gh_state.reset_calls()
    result = run_config("github", "apply", "--repo", REPO, "--variant", "product")
    assert (result.returncode, result.stderr) == (0, "")
    return gh_state.calls()


def test_product_creates_the_production_environment_and_limits_it_to_main(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])
    assert (
        f"environment '{PRODUCTION}': missing"
        in run_config("github", "check", "--repo", REPO, "--variant", "product").stdout
    )

    calls = _apply_product(gh_state)

    env = f"environments/{PRODUCTION}"
    puts = [c for c in calls if c["method"] == "PUT" and c["endpoint"] == f"repos/{REPO}/{env}"]
    assert [c["body"] for c in puts] == [{"deployment_branch_policy": PRODUCTION_POLICY}]
    posts = [c for c in calls if c["method"] == "POST" and "deployment-branch-policies" in c["endpoint"]]
    assert [(c["endpoint"], c["body"]) for c in posts] == [
        (f"repos/{REPO}/{env}/deployment-branch-policies", {"name": "main", "type": "branch"})
    ]
    assert not any(c["method"] == "DELETE" for c in calls)
    assert _silent(run_config("github", "check", "--repo", REPO, "--variant", "product"))


def test_product_removes_a_star_policy_and_keeps_main(gh_state) -> None:
    gh_state.seed_matching(
        properties=[{"property_name": "product", "value": "productforge"}],
        environments={
            PRODUCTION: {
                "deployment_branch_policy": PRODUCTION_POLICY,
                "policies": [{"id": 1, "name": "*", "type": "branch"}, {"id": 2, "name": "main", "type": "branch"}],
            }
        },
    )
    checked = run_config("github", "check", "--repo", REPO, "--variant", "product")
    assert (checked.returncode, f"environment '{PRODUCTION}'.branch_policies" in checked.stdout) == (1, True)

    calls = _apply_product(gh_state)

    assert [(m, e) for m, e in _writes(calls) if "deployment-branch-policies" in e] == [
        ("DELETE", f"environments/{PRODUCTION}/deployment-branch-policies/1")
    ]
    assert _policy_names(gh_state.state()) == ["main"]
    assert _silent(run_config("github", "check", "--repo", REPO, "--variant", "product"))


def test_product_changes_nothing_on_a_correct_environment(gh_state) -> None:
    gh_state.seed_matching(
        properties=[{"property_name": "product", "value": "productforge"}],
        environments={
            PRODUCTION: {
                "deployment_branch_policy": PRODUCTION_POLICY,
                "policies": [{"id": 5, "name": "main", "type": "branch"}],
            }
        },
    )
    before = gh_state.state()["environments"]

    calls = _apply_product(gh_state)

    assert gh_state.state()["environments"] == before
    assert not any(c["method"] in ("POST", "DELETE") and "/environments" in c["endpoint"] for c in calls)
    assert _silent(run_config("github", "check", "--repo", REPO, "--variant", "product"))


def test_an_existing_matching_environment_is_never_put_again_and_is_reported_unchanged(gh_state) -> None:
    gh_state.seed_matching(
        properties=[{"property_name": "product", "value": "productforge"}],
        environments={
            PRODUCTION: {
                "deployment_branch_policy": PRODUCTION_POLICY,
                "policies": [{"id": 5, "name": "main", "type": "branch"}],
            }
        },
    )
    gh_state.reset_calls()
    result = run_config("github", "apply", "--repo", REPO, "--variant", "product")

    assert f"environment '{PRODUCTION}': unchanged" in result.stdout
    assert [c for c in _environment_calls(gh_state.calls()) if c["method"] != "GET"] == []


def test_an_existing_environment_with_other_flags_is_put_and_its_policies_made_equal(gh_state) -> None:
    gh_state.seed_matching(
        properties=[{"property_name": "product", "value": "productforge"}],
        environments={
            PRODUCTION: {
                "deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False},
                "policies": [],
            }
        },
    )

    calls = _apply_product(gh_state)

    assert [(m, e) for m, e in _writes(calls) if "/deployment" not in e and e.startswith("environments")] == [
        ("PUT", f"environments/{PRODUCTION}")
    ]
    assert _policy_names(gh_state.state()) == ["main"]
    assert _silent(run_config("github", "check", "--repo", REPO, "--variant", "product"))


def test_a_second_product_apply_sends_no_write(gh_state) -> None:
    gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])
    _apply_product(gh_state)

    calls = _apply_product(gh_state)

    assert _writes(calls) == []


def test_environments_and_rulesets_on_a_plan_without_either_are_skipped_with_githubs_reason(gh_state) -> None:
    gh_state.seed(
        repo={"private": True, **_mismatched_repo_fields()},
        workflow_permissions=_mismatched_workflow_permissions(),
        rulesets=[],
        properties=[{"property_name": "product", "value": "productforge"}],
        rulesets_error=NO_RULES,
        environments_error=NO_RULES,
    )

    applied = run_config("github", "apply", "--repo", REPO, "--variant", "product")
    assert (applied.returncode, applied.stderr) == (0, "")
    assert applied.stdout.splitlines()[-2:] == [
        f"environment '{PRODUCTION}': skipped ({NO_RULES})",
        f"ruleset: skipped ({NO_RULES})",
    ]
    assert gh_state.state()["repo"] == {"private": True, **SETTINGS["repository"]}

    checked = run_config("github", "check", "--repo", REPO, "--variant", "product")
    assert checked.stdout.count(f"environment '{PRODUCTION}': skipped") == 1
    assert checked.stdout.count("ruleset: skipped") == 1
    assert f"environment '{PRODUCTION}': missing" not in checked.stdout


def test_an_environment_error_other_than_a_missing_one_propagates(gh_state) -> None:
    gh_state.seed_matching(
        properties=[{"property_name": "product", "value": "productforge"}], environments_failure="Server Error"
    )

    applied = run_config("github", "apply", "--repo", REPO, "--variant", "product")
    checked = run_config("github", "check", "--repo", REPO, "--variant", "product")

    assert applied.returncode == 1 and "Server Error" in applied.stderr
    assert checked.returncode == 1 and "Server Error" in checked.stderr


def test_check_reports_an_environment_that_is_not_limited_to_custom_branch_policies(gh_state) -> None:
    gh_state.seed_matching(
        properties=[{"property_name": "product", "value": "productforge"}],
        environments={PRODUCTION: {"deployment_branch_policy": None, "policies": []}},
    )

    checked = run_config("github", "check", "--repo", REPO, "--variant", "product")

    assert f"environment '{PRODUCTION}'.deployment_branch_policy: None" in checked.stdout
    assert f"environment '{PRODUCTION}'.branch_policies: [] -> " in checked.stdout


def test_other_variants_send_no_environment_call(gh_state) -> None:
    for variant in ("standard", "publishes", "block-only"):
        gh_state.seed_matching(properties=[{"property_name": "product", "value": "productforge"}])
        gh_state.reset_calls()

        run_config("github", "check", "--repo", REPO, "--variant", variant)
        run_config("github", "apply", "--repo", REPO, "--variant", variant)

        assert _environment_calls(gh_state.calls()) == []
        assert "environments" not in gh_state.state()
