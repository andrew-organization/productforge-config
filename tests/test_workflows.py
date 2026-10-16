"""The reusable CI workflows, ci-api.yml and ci-web.yml, against the kits they run."""

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from .conftest import Repo, Stubs

CHECKS = {
    "api": ["lint", "check-migrations", "test", "test-integration-ci", "check-config"],
    "web": ["check-generated", "check-identity-regeneration", "lint", "test", "check-config"],
}

RUNS_ON = "${{ fromJSON(vars.PF_RUNS_ON || '\"ubuntu-latest\"') }}"


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
        assert workflow["jobs"]["ci"]["runs-on"] == RUNS_ON
        checkout = next(s for s in flat if s.get("with", {}).get("path") == ".productforge-config")
        setup = next(s for s in flat if s.get("uses") == "./.productforge-config/actions/setup")
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"]["repository"] == "${{ job.workflow_repository }}"
        assert checkout["with"]["ref"] == "${{ job.workflow_sha }}"
        assert flat.index(checkout) < flat.index(setup)
        token = {"token": "${{ secrets.PF_READ_TOKEN }}"}
        assert setup["with"] == ({"flutter": "true", **token} if kind == "web" else token)
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
    assert list(action["inputs"]) == ["flutter", "token"]  # no postgres: each kit starts what it needs
    own_setup = yaml.safe_load((shipped / ".github" / "workflows" / "ci.yml").read_text())["jobs"]["ci"]["steps"][1]
    assert own_setup["uses"] == "./actions/setup"
    assert own_setup["with"] == {"token": "${{ secrets.PF_READ_TOKEN }}"}  # this repository's CI gives its own token
    assert "flutter-version-file: .fvmrc" in action_text  # the Flutter its fvmrc pins
    assert (
        "python-version: ${{ steps.python.outputs.version }}" in action_text and "settings/python.toml" in action_text
    )
    for kind in ("api", "web"):
        pinned = set(
            re.findall(r"uses: (\S+@[0-9a-f]{40})", (shipped / ".github" / "workflows" / f"ci-{kind}.yml").read_text())
        )
        assert pinned and all(ref.startswith("actions/checkout@") or ref in action_text for ref in pinned)


def test_every_reusable_job_reads_the_runner_from_the_organisation_variable_and_this_repository_stays_hosted(
    shipped: Path,
) -> None:
    workflows = shipped / ".github" / "workflows"
    for name in ("ci-api.yml", "ci-web.yml", "release.yml"):
        jobs = yaml.safe_load((workflows / name).read_text())["jobs"]
        assert jobs and all(job["runs-on"] == RUNS_ON for job in jobs.values()), name
    own = yaml.safe_load((workflows / "ci.yml").read_text())["jobs"]
    assert all(job["runs-on"] == "ubuntu-latest" for job in own.values())  # public: hosted minutes are free


def flutter_lookup(shipped: Path) -> tuple[str, str]:
    """The setup action's Flutter lookup: its script, and the `if` that guards flutter-action."""
    steps_ = yaml.safe_load((shipped / "actions" / "setup" / "action.yml").read_text())["runs"]["steps"]
    lookup = next(s for s in steps_ if s.get("id") == "flutter")
    setup = next(s for s in steps_ if s.get("uses", "").startswith("subosito/flutter-action@"))
    script = lookup["run"].split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0]
    return script, setup["if"]


def run_lookup(shipped: Path, tmp_path: Path, wanted: str | None, reports: str | None, fvmrc: str | None = None) -> str:
    """What the lookup writes for a repository pinning `wanted` (or with `fvmrc` as .fvmrc), given `flutter` on PATH."""
    script, _ = flutter_lookup(shipped)
    if fvmrc is not None:
        (tmp_path / ".fvmrc").write_text(fvmrc)
    elif wanted is not None:
        (tmp_path / ".fvmrc").write_text(json.dumps({"flutter": wanted}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if reports is not None:
        stub = bin_dir / "flutter"
        stub.write_text(
            f"#!/bin/sh\necho 'Flutter is checking something'\necho '{{\"frameworkVersion\": \"{reports}\"}}'\n"
        )
        stub.chmod(0o755)
    path = f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path, env={"PATH": path}, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return done.stdout.strip()


def test_the_setup_skips_flutter_action_only_when_the_flutter_on_path_is_the_one_fvmrc_pins(
    shipped: Path, tmp_path: Path
) -> None:
    _, condition = flutter_lookup(shipped)
    assert condition == "inputs.flutter == 'true' && steps.flutter.outputs.preinstalled != 'true'"
    cases = {
        "the same version": ("3.44.7", "3.44.7"),
        "another version": ("3.44.7", "3.29.0"),
        "no flutter": ("3.44.7", None),
    }
    outcomes = {}
    for name, (wanted, reports) in cases.items():
        case = tmp_path / name.replace(" ", "-")
        case.mkdir()
        outcomes[name] = run_lookup(shipped, case, wanted, reports)
    assert outcomes == {
        "the same version": "preinstalled=true",
        "another version": "preinstalled=false",
        "no flutter": "preinstalled=false",
    }


def test_the_setup_reports_not_preinstalled_when_fvmrc_is_missing_or_names_no_flutter(
    shipped: Path, tmp_path: Path
) -> None:
    cases = {
        "no fvmrc": {"wanted": None},
        "no flutter key": {"wanted": None, "fvmrc": json.dumps({"flavors": {}})},
        "not json": {"wanted": None, "fvmrc": "not json"},
    }
    for name, kwargs in cases.items():
        case = tmp_path / name.replace(" ", "-")
        case.mkdir()
        assert run_lookup(shipped, case, reports="3.44.7", **kwargs) == "preinstalled=false", name


# ─── The read token ──────────────────────────────────────────────────────────

TOKEN = "ghp_0123456789placeholderonly"


def read_access_script(shipped: Path, kind: str) -> str:
    step = next(
        s
        for s in load(shipped, kind)["jobs"]["ci"]["steps"]
        if s.get("name") == "Check productforge-config can be read"
    )
    return step["run"]


def run_shell(script: str, tmp_path: Path, stubs: Stubs | None = None, **env: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    base = {"PATH": os.environ["PATH"], "HOME": str(home)}
    return subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env={**base, **env}, capture_output=True, text=True
    )  # fmt: skip


def test_the_reusable_workflows_take_the_optional_token_read_config_with_it_and_hand_it_to_the_setup(
    shipped: Path,
) -> None:
    for kind in ("api", "web"):
        workflow = load(shipped, kind)
        secret = workflow[True]["workflow_call"]["secrets"]["PF_READ_TOKEN"]
        job_steps = workflow["jobs"]["ci"]["steps"]
        names = [s.get("name") for s in job_steps]
        checkout = next(s for s in job_steps if s.get("with", {}).get("path") == ".productforge-config")

        assert secret["required"] is False
        assert checkout["with"]["token"] == "${{ secrets.PF_READ_TOKEN || github.token }}"
        assert checkout["with"]["persist-credentials"] is False
        assert names.index("Check productforge-config can be read") < job_steps.index(checkout)
        assert "inherit" not in (shipped / ".github" / "workflows" / f"ci-{kind}.yml").read_text().replace(
            "`secrets: inherit`", ""
        )


def test_a_token_a_public_config_and_no_token_with_a_private_config_each_behave_as_the_contract_says(
    shipped: Path, tmp_path: Path, stubs: Stubs
) -> None:
    for kind in ("api", "web"):
        script = read_access_script(shipped, kind)
        env = {"CONFIG_REPOSITORY": "andrew-organization/productforge-config"}

        stubs.add("git", status=128)  # config private: no anonymous read
        given = run_shell(script, tmp_path, PF_TOKEN=TOKEN, **env)
        refused = run_shell(script, tmp_path, PF_TOKEN="", **env)
        stubs.add("git")  # config public
        public = run_shell(script, tmp_path, PF_TOKEN="", **env)

        assert given.returncode == 0 and public.returncode == 0
        assert refused.returncode == 1 and "PF_READ_TOKEN" in refused.stdout
        assert "andrew-organization/productforge-config can't be read" in refused.stdout


def action_steps(shipped: Path) -> dict[str, dict]:
    steps_ = yaml.safe_load((shipped / "actions" / "setup" / "action.yml").read_text())["runs"]["steps"]
    return {s["name"]: s for s in steps_ if "name" in s}


def test_the_setup_checks_its_repository_only_without_a_token_and_names_the_input_when_it_cannot_read_it(
    shipped: Path, tmp_path: Path, stubs: Stubs
) -> None:
    check = action_steps(shipped)["Check this repository can be read"]

    assert check["if"] == "inputs.token == '' && github.action_repository != ''"
    stubs.add("git", status=128)
    refused = run_shell(check["run"], tmp_path, ACTION_REPOSITORY="andrew-organization/productforge-config")
    stubs.add("git")
    readable = run_shell(check["run"], tmp_path, ACTION_REPOSITORY="andrew-organization/productforge-config")

    assert refused.returncode == 1 and "PF_READ_TOKEN" in refused.stdout and "token input" in refused.stdout
    assert readable.returncode == 0


def test_the_setup_holds_the_token_in_the_environment_alone_masked_and_in_no_git_configuration_file(
    shipped: Path, tmp_path: Path
) -> None:
    step = action_steps(shipped)["Authenticate Git reads with the token"]
    action = shipped / "actions" / "setup"
    github_env = tmp_path / "github_env"
    github_env.write_text("")
    env = {
        "PF_TOKEN": TOKEN,
        "GITHUB_ENV": str(github_env),
        "GITHUB_REPOSITORY_OWNER": "andrew-organization",
        "GITHUB_ACTION_PATH": str(action),
        "GIT_CONFIG_COUNT": "1",  # a configuration the job already carries is kept
        "GIT_CONFIG_KEY_0": "user.name",
        "GIT_CONFIG_VALUE_0": "someone",
    }

    assert step["if"] == "inputs.token != ''" and step["env"] == {"PF_TOKEN": "${{ inputs.token }}"}
    done = run_shell(step["run"], tmp_path, **env)

    header = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    exported = {
        k: v for k, v in env.items() if k.startswith("GIT_CONFIG_")
    }  # what the job's environment holds after the step...
    exported.update(line.split("=", 1) for line in github_env.read_text().splitlines())  # ...and what it adds
    assert done.returncode == 0, done.stderr
    assert f"::add-mask::{TOKEN}" in done.stdout and f"::add-mask::{header}" in done.stdout  # both forms masked
    assert exported["GIT_CONFIG_COUNT"] == "2" and exported["GIT_CONFIG_KEY_0"] == "user.name"
    assert exported["GIT_CONFIG_KEY_1"] == "http.https://github.com/andrew-organization/.extraheader"
    assert exported["GIT_CONFIG_VALUE_1"] == f"AUTHORIZATION: basic {header}"
    assert TOKEN not in github_env.read_text()  # only its encoding

    # Git, given that environment, shows the entry from the command line and nowhere else.
    listing = run_shell("git config --list --show-origin", tmp_path, **{**exported, "PATH": os.environ["PATH"]})
    origins = {line.split("\t")[0] for line in listing.stdout.splitlines() if header in line}
    assert origins == {"command line:"}
    assert list((tmp_path / "home").iterdir()) == []  # no .gitconfig written
    checked = run_shell(
        str(shipped / ".github" / "scripts" / "check_token_scope.sh"), tmp_path, **{**exported, "PF_TOKEN": TOKEN}
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr

    (tmp_path / "home" / ".gitconfig").write_text(f"[http]\n\textraHeader = AUTHORIZATION: basic {header}\n")
    leaked = run_shell(
        str(shipped / ".github" / "scripts" / "check_token_scope.sh"), tmp_path, **{**exported, "PF_TOKEN": TOKEN}
    )
    assert leaked.returncode == 1  # a file holding it is caught
