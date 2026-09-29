"""Tests of the reusable CI workflows, ci-api.yml and ci-web.yml, against the kits they run."""

import re
import subprocess
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
def test_inlines_its_setup_rather_than_referencing_an_action_of_this_repository(kind: str) -> None:
    text = (WORKFLOWS / f"ci-{kind}.yml").read_text()
    assert "productforge-config/actions/setup" not in text
    assert "uses: ./" not in text


@pytest.mark.parametrize("kind", ["api", "web"])
def test_pins_the_same_actions_as_the_setup_action(kind: str) -> None:
    pinned = set(re.findall(r"uses: (\S+@[0-9a-f]{40})", (WORKFLOWS / f"ci-{kind}.yml").read_text()))
    setup = (REPO_ROOT / "actions" / "setup" / "action.yml").read_text()
    for reference in pinned:
        assert reference in setup or reference.startswith("actions/checkout@"), reference


def test_the_api_runs_the_checks_the_template_runs_today() -> None:
    assert _make_targets("api") == [
        "install",
        "lint",
        "check-migrations",
        "test",
        "test-integration-ci",
    ]


def test_the_web_runs_the_checks_the_template_runs_today() -> None:
    assert _make_targets("web") == [
        "install",
        "check-generated",
        "check-identity-regeneration",
        "lint",
        "test",
    ]


def test_the_web_installs_the_flutter_its_fvmrc_pins() -> None:
    (flutter,) = (s for s in _steps("web") if s.get("uses", "").startswith("subosito/flutter-action@"))
    assert flutter["with"]["flutter-version-file"] == ".fvmrc"
    assert not [s for s in _steps("api") if "flutter" in s.get("uses", "")]


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
def test_reads_productforge_env_into_the_job_environment(kind: str, tmp_path: Path) -> None:
    (read,) = (s for s in _steps(kind) if s.get("name") == "Read productforge.env")
    env = tmp_path / "productforge.env"
    env.write_text("# a comment\nPF_KIND=api\nPF_SLOT=3\nPF_NAME=demo\nOTHER=ignored\n")
    github_env = tmp_path / "github_env"
    subprocess.run(["bash", "-e", "-c", read["run"]], cwd=tmp_path, env={"GITHUB_ENV": str(github_env)}, check=True)
    assert github_env.read_text().splitlines() == ["PF_KIND=api", "PF_SLOT=3", "PF_NAME=demo"]
