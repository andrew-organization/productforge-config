"""The reusable CI workflows, ci-api.yml and ci-web.yml, against the kits they run."""

import re
from pathlib import Path

import yaml

from .conftest import Repo

CHECKS = {
    "api": ["lint", "check-migrations", "test", "test-integration-ci", "check-config"],
    "web": ["check-generated", "check-identity-regeneration", "lint", "test", "check-config"],
}


def load(shipped: Path, kind: str) -> dict:
    return yaml.safe_load((shipped / ".github" / "workflows" / f"ci-{kind}.yml").read_text())


def steps(shipped: Path, kind: str) -> list[dict]:
    """The job's steps, with the parallel group's own steps flattened in, in order."""
    flat: list[dict] = []
    for step in load(shipped, kind)["jobs"]["ci"]["steps"]:
        flat.extend(step["parallel"] if "parallel" in step else [step])
    return flat


def test_each_workflow_sets_up_from_this_repository_and_runs_make_targets_its_kit_provides(
    api_repo: Repo, web_repo: Repo, shipped: Path
) -> None:
    for kind, repo in (("api", api_repo), ("web", web_repo)):
        workflow, flat = load(shipped, kind), steps(shipped, kind)

        assert list(workflow[True]) == ["workflow_call"]  # YAML reads a bare `on` as True
        assert workflow["jobs"]["ci"]["runs-on"] == "ubuntu-latest"
        checkout = next(s for s in flat if s.get("with", {}).get("path") == ".productforge-config")
        setup = next(s for s in flat if s.get("uses") == "./.productforge-config/actions/setup")
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"]["repository"] == "${{ job.workflow_repository }}"
        assert checkout["with"]["ref"] == "${{ job.workflow_sha }}"
        assert flat.index(checkout) < flat.index(setup)
        assert ("with" in setup and setup["with"] == {"flutter": "true"}) == (kind == "web")
        targets = [m.group(1) for s in flat if (m := re.fullmatch(r"make ([\w-]+)", s.get("run", "")))]
        assert targets == CHECKS[kind]
        parallel = workflow["jobs"]["ci"]["steps"][-1]["parallel"]
        assert {"name": "Config unchanged", "run": "make check-config"} in parallel  # beside the other checks
        assert not any(
            "GITHUB_ENV" in s.get("run", "") for s in flat
        )  # make and Compose read productforge.env themselves
        assert repo.update().returncode == 0
        for target in targets:
            assert repo.make("-n", target).returncode == 0, f"{kind}: make {target}"
    names = [s.get("name") for s in load(shipped, "web")["jobs"]["ci"]["steps"]]
    assert names.index("Check generated code is up to date") < names.index(
        "Check identity regenerates from product.yaml"
    )


def test_the_setup_is_written_once_in_the_setup_action(shipped: Path) -> None:
    pattern = re.compile(r"astral-sh/setup-uv|subosito/flutter-action|actions/cache@|make install")
    holders = {
        str(path.relative_to(shipped))
        for path in [*shipped.glob(".github/workflows/*.yml"), *shipped.glob("actions/*/action.yml")]
        if pattern.search(path.read_text())
    }
    action_text = (shipped / "actions" / "setup" / "action.yml").read_text()
    action = yaml.safe_load(action_text)

    assert holders == {"actions/setup/action.yml"}
    assert list(action["inputs"]) == ["flutter"]  # no postgres: each kit starts what it needs
    own_setup = yaml.safe_load((shipped / ".github" / "workflows" / "ci.yml").read_text())["jobs"]["ci"]["steps"][1]
    assert (
        own_setup["uses"] == "./actions/setup" and "with" not in own_setup
    )  # this repository's CI takes no input either
    assert "flutter-version-file: .fvmrc" in action_text  # the Flutter its fvmrc pins
    assert (
        "python-version: ${{ steps.python.outputs.version }}" in action_text and "settings/python.toml" in action_text
    )
    for kind in ("api", "web"):
        pinned = set(
            re.findall(r"uses: (\S+@[0-9a-f]{40})", (shipped / ".github" / "workflows" / f"ci-{kind}.yml").read_text())
        )
        assert pinned and all(ref.startswith("actions/checkout@") or ref in action_text for ref in pinned)
