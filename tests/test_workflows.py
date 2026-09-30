"""Tests of the reusable CI workflows, ci-api.yml and ci-web.yml, against the kits they run."""

import re
from pathlib import Path

import pytest
import yaml

from productforge_config import cli

from .conftest import VERSION, run_make

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load(kind: str) -> dict:
    return yaml.safe_load((WORKFLOWS / f"ci-{kind}.yml").read_text())


def _steps(kind: str) -> list[dict]:
    """The job's steps, with the parallel group's own steps flattened in, in order."""
    steps: list[dict] = []
    for step in _load(kind)["jobs"]["ci"]["steps"]:
        steps.extend(step["parallel"] if "parallel" in step else [step])
    return steps


def _make_targets(kind: str) -> list[str]:
    return [m.group(1) for s in _steps(kind) if (m := re.fullmatch(r"make ([\w-]+)", s.get("run", "")))]


@pytest.mark.parametrize("kind", ["api", "web"])
def test_is_a_reusable_workflow(kind: str) -> None:
    workflow = _load(kind)
    assert list(workflow[True]) == ["workflow_call"]  # YAML reads a bare `on` as True
    assert workflow["jobs"]["ci"]["runs-on"] == "ubuntu-latest"


@pytest.mark.parametrize("kind", ["api", "web"])
def test_calls_the_setup_action_from_config_checked_out_at_its_own_commit(kind: str) -> None:
    steps = _steps(kind)
    checkout = next(s for s in steps if s.get("with", {}).get("path") == ".productforge-config")
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["repository"] == "${{ job.workflow_repository }}"
    assert checkout["with"]["ref"] == "${{ job.workflow_sha }}"
    setup = next(s for s in steps if s.get("uses") == "./.productforge-config/actions/setup")
    assert steps.index(checkout) < steps.index(setup)
    text = (WORKFLOWS / f"ci-{kind}.yml").read_text()
    assert "productforge-config/actions/setup" not in text.replace("./.productforge-config/actions/setup", "")


@pytest.mark.parametrize("kind", ["api", "web"])
def test_restates_none_of_the_setup(kind: str) -> None:
    text = (WORKFLOWS / f"ci-{kind}.yml").read_text()
    for step in ("astral-sh/setup-uv", "actions/cache", "subosito/flutter-action", "make install"):
        assert step not in text


def test_the_setup_steps_are_written_in_the_setup_action_only() -> None:
    pattern = re.compile(r"astral-sh/setup-uv|subosito/flutter-action|actions/cache@|make install")
    holders = {
        str(path.relative_to(REPO_ROOT))
        for path in [*REPO_ROOT.glob(".github/workflows/*.yml"), *REPO_ROOT.glob("actions/*/action.yml")]
        if pattern.search(path.read_text())
    }
    assert holders == {"actions/setup/action.yml"}


@pytest.mark.parametrize("kind", ["api", "web"])
def test_pins_only_actions_the_setup_action_or_checkout_pins(kind: str) -> None:
    pinned = set(re.findall(r"uses: (\S+@[0-9a-f]{40})", (WORKFLOWS / f"ci-{kind}.yml").read_text()))
    assert pinned
    setup = (REPO_ROOT / "actions" / "setup" / "action.yml").read_text()
    for reference in pinned:
        assert reference.startswith("actions/checkout@") or reference in setup, reference


def test_the_api_runs_the_checks_the_template_runs_today() -> None:
    assert _make_targets("api") == ["lint", "check-migrations", "test", "test-integration-ci", "check-config"]


def test_the_web_runs_the_checks_the_template_runs_today() -> None:
    assert _make_targets("web") == ["check-generated", "check-identity-regeneration", "lint", "test", "check-config"]


def test_the_web_asks_the_setup_for_the_flutter_its_fvmrc_pins() -> None:
    (setup,) = (s for s in _steps("web") if s.get("uses") == "./.productforge-config/actions/setup")
    assert setup["with"] == {"flutter": "true"}
    (api_setup,) = (s for s in _steps("api") if s.get("uses") == "./.productforge-config/actions/setup")
    assert "with" not in api_setup
    assert "flutter" in (REPO_ROOT / "actions" / "setup" / "action.yml").read_text()
    assert "flutter-version-file: .fvmrc" in (REPO_ROOT / "actions" / "setup" / "action.yml").read_text()


@pytest.mark.parametrize("kind", ["api", "web"])
def test_checks_the_written_files_are_unchanged_in_parallel_with_the_other_checks(kind: str) -> None:
    parallel = _load(kind)["jobs"]["ci"]["steps"][-1]["parallel"]
    assert {"name": "Config unchanged", "run": "make check-config"} == next(
        s for s in parallel if s["name"] == "Config unchanged"
    )


def test_the_setup_action_takes_no_postgres_input() -> None:
    action = yaml.safe_load((REPO_ROOT / "actions" / "setup" / "action.yml").read_text())
    assert list(action["inputs"]) == ["flutter"]
    assert "postgres" not in (REPO_ROOT / "actions" / "setup" / "action.yml").read_text().lower()
    assert "with" not in yaml.safe_load((WORKFLOWS / "ci.yml").read_text())["jobs"]["ci"]["steps"][1]


def test_the_setup_action_installs_the_python_the_settings_state() -> None:
    action = (REPO_ROOT / "actions" / "setup" / "action.yml").read_text()
    assert "python-version: ${{ steps.python.outputs.version }}" in action
    assert "settings/python.toml" in action
    assert not re.search(r'python-version:\s*"?3\.', action)


def test_the_web_checks_generated_code_before_anything_else_reads_it() -> None:
    top_level = [s.get("name") for s in _load("web")["jobs"]["ci"]["steps"]]
    assert top_level.index("Check generated code is up to date") < top_level.index(
        "Check identity regenerates from product.yaml"
    )
    assert "parallel" in _load("web")["jobs"]["ci"]["steps"][-1]


@pytest.mark.parametrize("kind", ["api", "web"])
def test_every_make_target_it_runs_exists_in_the_kit(kind: str, tmp_path: Path) -> None:
    import shutil

    repo = tmp_path / kind
    shutil.copytree(Path(__file__).parent / f"fixture_{kind}", repo)
    cli.update(repo, VERSION)
    for target in _make_targets(kind):
        result = run_make(repo, "-n", target)
        assert result.returncode == 0, f"make {target}: {result.stderr}"


@pytest.mark.parametrize("kind", ["api", "web"])
def test_needs_no_step_to_read_productforge_env(kind: str) -> None:
    """Make and Docker Compose read the file themselves, so the workflows copy nothing into the job."""
    assert "GITHUB_ENV" not in (WORKFLOWS / f"ci-{kind}.yml").read_text()


@pytest.mark.parametrize("kind", ["api", "web"])
def test_says_its_setup_is_written_once_in_the_setup_action(kind: str) -> None:
    assert "written once, in that action" in " ".join(
        (WORKFLOWS / f"ci-{kind}.yml").read_text().split("on:")[0].split()
    )
