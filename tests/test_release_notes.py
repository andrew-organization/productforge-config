"""The release workflow's closing section: the module that builds it and the steps that run it."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from productforge_config import release_notes

from .conftest import Stubs

MODULE = Path(release_notes.__file__)
TITLE_LINE = "## To take this release"

# One row for each form of the steps section: message body, section read.
FORMS: dict[str, tuple[str, str | None]] = {
    "the heading and the text under it": (f"Intro.\n\n{TITLE_LINE}\n\nRun the migration.\n", "Run the migration."),
    "a heading of any level": ("# To take this release\n\nA.\n", "A."),
    "the deepest level": ("###### To take this release\nA.", "A."),
    "any case": ("## TO TAKE THIS RELEASE\n\nA.", "A."),
    "a trailing colon": ("## To take this release:\n\nA.", "A."),
    "a closing run of hashes": ("## To take this release ##\n\nA.", "A."),
    "a closing run and a colon": ("## To take this release ##:\n\nA.", "A."),
    "ends at a heading of the same level": (f"{TITLE_LINE}\n\nA.\n\n## Testing\n\nB.", "A."),
    "ends at a heading of a higher level": ("## Notes\n\n### To take this release\n\nA.\n\n## Testing\n\nB.", "A."),
    "a deeper heading is part of the text": (
        f"{TITLE_LINE}\n\nA.\n\n### Detail\n\nB.\n\n## Testing",
        "A.\n\n### Detail\n\nB.",
    ),
    "ends at a BREAKING CHANGE footer": (f"{TITLE_LINE}\n\nA.\n\nBREAKING CHANGE: it moved", "A."),
    "ends at a BREAKING-CHANGE footer": (f"{TITLE_LINE}\n\nA.\nBREAKING-CHANGE: it moved", "A."),
    "a heading inside a backtick fence is not one": (f"```\n{TITLE_LINE}\n\nA.\n```\n", None),
    "a heading inside a tilde fence is not one": (f"~~~\n{TITLE_LINE}\n\nA.\n~~~\n", None),
    "a heading inside a comment is not one": (f"<!--\n{TITLE_LINE}\n\nA.\n-->\n", None),
    "text inside a comment is removed": (f"{TITLE_LINE}\n\nA.<!-- hidden -->\n\n<!-- also\nhidden -->B.", "A.\n\nB."),
    "a heading in a fence ends nothing": (
        f"{TITLE_LINE}\n\n```\n## Testing\n```\n\nA.\n\n## Testing\n\nB.",
        "```\n## Testing\n```\n\nA.",
    ),
    "a section with only comments is none": (f"{TITLE_LINE}\n\n<!-- nothing yet -->\n\n## Testing\n\nB.", None),
    "an empty section is none": (f"{TITLE_LINE}\n\n## Testing\n\nB.", None),
    "a second section is ignored": (f"{TITLE_LINE}\n\nA.\n\n{TITLE_LINE}\n\nB.", "A."),
    "an underlined heading is not one": ("To take this release\n====================\n\nA.", None),
    "a bold line is not one": ("**To take this release**\n\nA.", None),
    "a heading with other words is not one": ("## To take this release (optional)\n\nA.", None),
    "no heading": ("Just a description.", None),
    "a trailer ends the message": (f"{TITLE_LINE}\n\nA.\n\nCo-authored-by: Someone <s@example.invalid>\n", "A."),
    "a trailer block ends the message": (
        f"{TITLE_LINE}\n\nA.\n\nCo-authored-by: One <o@example.invalid>\nCo-authored-by: Two <t@example.invalid>",
        "A.",
    ),
    "a trailer with no blank line before it": (f"{TITLE_LINE}\n\nA.\nCo-authored-by: One <o@example.invalid>", "A."),
    "each known trailer key ends the message": (
        f"{TITLE_LINE}\n\nA.\n\n"
        + "\n".join(
            f"{key}: v"
            for key in (
                "Co-authored-by",
                "Signed-off-by",
                "Reviewed-by",
                "Acked-by",
                "Tested-by",
                "Reported-by",
                "Suggested-by",
                "Helped-by",
                "Cc",
                "Claude-Session",
            )
        ),
        "A.",
    ),
    "a comment inside a fenced block is removed too": (
        f"{TITLE_LINE}\n\n```\n<!-- x -->\ncode\n```",
        "```\n\ncode\n```",
    ),
    "an attribution line and a session link end the message": (
        f"{TITLE_LINE}\n\nA.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n"
        "https://claude.ai/code/session_0123",
        "A.",
    ),
    "a trailer-like line that is not known stays in the text": (
        f"{TITLE_LINE}\n\nA.\n\nFixes: #12",
        "A.\n\nFixes: #12",
    ),
    "a longer closing fence closes the block": (f"{TITLE_LINE}\n\n```\ncode\n````\n\nA.", "```\ncode\n````\n\nA."),
    "a closing fence of the other kind does not close the block": (f"```\n~~~\n{TITLE_LINE}\n\nA.", None),
    "an unclosed fence hides the rest": (f"```\ncode\n{TITLE_LINE}\n\nA.", None),
    "carriage-return line endings are read": (f"Intro.\r\n\r\n{TITLE_LINE}\r\n\r\nA.\r\n\r\n## Testing\r\n", "A."),
    "an empty message has none": ("", None),
    "a heading indented up to three spaces": ("   ## To take this release\n\nA.", "A."),
    "a heading indented four spaces is not one": ("    ## To take this release\n\nA.", None),
    "seven hashes are not a heading": ("####### To take this release\n\nA.", None),
    "a fence indented up to three spaces opens a block": (f"   ```\n{TITLE_LINE}\n\nA.\n   ```\n", None),
    "a shorter closing run does not close a fence": (f"````\n```\n{TITLE_LINE}\n\nA.", None),
    "a footer inside a fence ends nothing": (
        f"{TITLE_LINE}\n\n```\nBREAKING CHANGE: x\n```\n\nA.",
        "```\nBREAKING CHANGE: x\n```\n\nA.",
    ),
    "an empty heading ends the section": (f"{TITLE_LINE}\n\nA.\n\n##\n\nB.", "A."),
    "a later heading of the title one level deeper stays in the text": (
        "## To take this release\n\nA.\n\n### To take this release\n\nB.",
        "A.\n\n### To take this release\n\nB.",
    ),
    "a colon in the text is not a trailer": (f"{TITLE_LINE}\n\nNote: run it.", "Note: run it."),
}


@pytest.mark.parametrize("name", FORMS)
def test_the_steps_section_reads_each_form_of_the_contract(name: str) -> None:
    message, expected = FORMS[name]

    assert release_notes.steps_section(message) == expected


# ─── The module against a throwaway repository ───────────────────────────────

HEADING = "### To take this release"


class World:
    """A throwaway repository, and a `gh` stand-in holding each release's notes in a directory."""

    def __init__(self, tmp_path: Path, stubs: Stubs) -> None:
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        self.notes = tmp_path / "gh-notes"
        self.notes.mkdir()
        self.git("init", "-q")
        (stubs.bin / "gh").write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            f"notes = pathlib.Path({str(self.notes)!r})\n"
            "args = sys.argv[1:]\n"
            "tag = args[2]\n"
            "if args[:2] == ['release', 'view']:\n"
            "    if not (notes / tag).exists():\n"
            "        print('release not found', file=sys.stderr); sys.exit(1)\n"
            "    print(json.dumps({'body': (notes / tag).read_text()}))\n"
            "elif args[:2] == ['release', 'edit']:\n"
            "    (notes / tag).write_text(pathlib.Path(args[args.index('--notes-file') + 1]).read_text())\n"
        )
        (stubs.bin / "gh").chmod(0o755)

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()  # fmt: skip

    def commit(self, subject: str, body: str = "") -> None:
        (self.repo / "file").write_text(f"{subject}\n{body}\n{os.urandom(4).hex()}")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject, *(["-m", body] if body else []))

    def release(self, tag: str, notes: str = "## What changed\n\n- things") -> None:
        self.git("tag", tag)
        (self.notes / tag).write_text(notes)

    def body(self, tag: str) -> str:
        return (self.notes / tag).read_text()

    def run(self, previous: str, tag: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(MODULE), "--previous", previous, "--tag", tag],
            cwd=self.repo, capture_output=True, text=True,
        )  # fmt: skip


@pytest.fixture
def world(tmp_path: Path, stubs: Stubs) -> World:
    return World(tmp_path, stubs)


def test_a_version_carries_every_commits_section_oldest_first_each_under_its_subject(world: World) -> None:
    world.commit("feat: the first (#1)")
    world.release("v1.0.0")
    world.commit("feat(users): add a thing (#12)", f"{TITLE_LINE}\n\nRun the migration.\n")
    world.commit("chore: tidy (#13)", "No section here.")
    world.commit("fix(emails): change another (#14)", f"{TITLE_LINE}\n\nRename the setting.\n")
    world.release("v1.1.0", "## 1.1.0\n\n- things")

    done = world.run("v1.0.0", "v1.1.0")

    assert done.returncode == 0, done.stderr
    assert world.body("v1.1.0") == (
        f"## 1.1.0\n\n- things\n\n{HEADING}\n\n"
        "**feat(users): add a thing (#12)**\n\nRun the migration.\n\n"
        "**fix(emails): change another (#14)**\n\nRename the setting.\n"
    )
    assert world.body("v1.0.0") == "## What changed\n\n- things"  # an earlier release is not touched


def test_a_squashed_commits_trailer_is_not_carried_into_the_notes(world: World) -> None:
    world.commit("feat: first")
    world.release("v1.0.0")
    world.commit(
        "feat: add (#2)",
        f"{TITLE_LINE}\n\nRun the migration.\n\nCo-authored-by: Someone <s@example.invalid>",
    )
    world.release("v1.1.0")

    world.run("v1.0.0", "v1.1.0")

    assert world.body("v1.1.0").endswith("**feat: add (#2)**\n\nRun the migration.\n")
    assert "Co-authored-by" not in world.body("v1.1.0")


def test_a_heading_inside_a_fenced_block_gives_no_section(world: World) -> None:
    world.commit("feat: first")
    world.release("v1.0.0")
    world.commit("feat: add (#2)", f"```\n{TITLE_LINE}\n\nRun the migration.\n```")
    world.release("v1.1.0")

    world.run("v1.0.0", "v1.1.0")

    assert world.body("v1.1.0").endswith(f"{HEADING}\n\nNothing to do to take this release.\n")


def test_a_section_before_a_breaking_change_footer_appears_once_and_the_footer_once_in_the_breaking_block(
    world: World,
) -> None:
    world.commit("feat: first")
    world.release("v1.0.0")
    world.commit("feat!: change (#2)", f"{TITLE_LINE}\n\nRun the migration.\n\nBREAKING CHANGE: the table moved")
    world.release("v2.0.0", "## 2.0.0\n\n### ⚠ BREAKING CHANGES\n\n* the table moved")

    world.run("v1.0.0", "v2.0.0")

    notes = world.body("v2.0.0")
    assert notes.count("Run the migration.") == 1 and notes.count("the table moved") == 1
    assert notes.endswith("**feat!: change (#2)**\n\nRun the migration.\n")


def test_no_section_and_nothing_breaking_says_there_is_nothing_to_do(world: World) -> None:
    world.commit("feat: first")
    world.release("v1.0.0")
    world.commit("fix: patch (#2)", "A description with no steps.")
    world.release("v1.0.1")

    world.run("v1.0.0", "v1.0.1")

    assert world.body("v1.0.1") == f"## What changed\n\n- things\n\n{HEADING}\n\nNothing to do to take this release.\n"


def test_no_section_beside_a_breaking_change_adds_nothing(world: World) -> None:
    world.commit("feat: first")
    world.release("v1.0.0")
    world.commit("feat!: change (#2)", "BREAKING CHANGE: the table moved")
    notes = "## 2.0.0\n\n### ⚠ BREAKING CHANGES\n\n* the table moved"
    world.release("v2.0.0", notes)

    done = world.run("v1.0.0", "v2.0.0")

    assert done.returncode == 0 and world.body("v2.0.0") == notes


def test_a_first_release_says_so_however_many_commits_it_holds(world: World) -> None:
    world.commit("feat: one", f"{TITLE_LINE}\n\nA.")
    world.commit("feat: two", f"{TITLE_LINE}\n\nB.")
    world.release("v1.0.0")

    world.run("", "v1.0.0")

    assert world.body("v1.0.0") == f"## What changed\n\n- things\n\n{HEADING}\n\nFirst release.\n"


def test_a_repeat_run_adds_nothing_a_second_time(world: World) -> None:
    world.commit("feat: first")
    world.release("v1.0.0")
    world.commit("feat: add (#2)", f"{TITLE_LINE}\n\nRun the migration.")
    world.release("v1.1.0")
    world.run("v1.0.0", "v1.1.0")
    once = world.body("v1.1.0")

    again = world.run("v1.0.0", "v1.1.0")

    assert again.returncode == 0 and world.body("v1.1.0") == once and once.count(HEADING) == 1


def test_a_docs_merge_gives_its_section_to_the_next_release(world: World) -> None:
    world.commit("feat: first")
    world.release("v1.0.0")
    world.commit("docs: explain (#2)", f"{TITLE_LINE}\n\nRead the new page.")  # releases nothing
    world.commit("fix: patch (#3)")
    world.release("v1.0.1")

    world.run("v1.0.0", "v1.0.1")

    assert "**docs: explain (#2)**\n\nRead the new page." in world.body("v1.0.1")


def test_a_tag_that_does_not_exist_and_a_tag_with_no_release_are_refused_before_anything_is_written(
    world: World,
) -> None:
    world.commit("feat: first")
    world.release("v1.0.0")
    world.git("tag", "v1.1.0")  # a tag with no GitHub release

    missing = world.run("v1.0.0", "v9.9.9")
    unreleased = world.run("v1.0.0", "v1.1.0")
    bad_previous = world.run("v0.0.1", "v1.0.0")

    assert bad_previous.returncode == 1 and "failed" in bad_previous.stderr
    assert missing.returncode == 1 and "v9.9.9 does not exist" in missing.stderr
    assert unreleased.returncode == 1 and "no GitHub release exists for v1.1.0" in unreleased.stderr
    assert sorted(path.name for path in world.notes.iterdir()) == ["v1.0.0"]


# ─── The workflow's steps ────────────────────────────────────────────────────


def release_job(shipped: Path) -> dict:
    return yaml.safe_load((shipped / ".github" / "workflows" / "release.yml").read_text())["jobs"]["release"]


def step_named(shipped: Path, name: str) -> dict:
    return next(s for s in release_job(shipped)["steps"] if s.get("name") == name)


def test_the_release_workflow_refuses_without_the_token_then_releases_checks_and_adds_the_section(
    shipped: Path,
) -> None:
    workflow = yaml.safe_load((shipped / ".github" / "workflows" / "release.yml").read_text())
    job = release_job(shipped)
    names = [s.get("name") or s.get("uses") for s in job["steps"]]
    added = [
        "Check the read token is given",
        "Release",
        "Find whether this run released",
        "Read productforge-config",
        "Add the closing section to the release's notes",
        "Check the release is complete",
    ]

    assert [n for n in names if n in added] == added
    assert (
        names.index("Check the read token is given") == 0
    )  # before the first checkout, so before anything is released
    secret = workflow[True]["workflow_call"]["secrets"]["PF_READ_TOKEN"]
    assert secret["required"] is False
    assert job["permissions"] == {"contents": "write", "issues": "write", "pull-requests": "write"}
    checkout = step_named(shipped, "Read productforge-config")
    assert checkout["with"]["repository"] == "${{ job.workflow_repository }}"
    assert checkout["with"]["ref"] == "${{ job.workflow_sha }}"
    assert checkout["with"]["token"] == "${{ secrets.PF_READ_TOKEN || github.token }}"
    assert checkout["with"]["persist-credentials"] is False
    for name in ("Read productforge-config", "Add the closing section to the release's notes"):
        assert step_named(shipped, name)["if"] == "${{ steps.released.outputs.releasing == 'true' }}"
    keep = step_named(shipped, "Keep the checkout of productforge-config out of the caller's status")
    assert keep["if"] == "${{ steps.released.outputs.releasing == 'true' }}"
    assert ".productforge-config" in keep["run"] and ".git/info/exclude" in keep["run"]
    module_step = step_named(shipped, "Add the closing section to the release's notes")
    assert module_step["env"]["GH_REPO"] == "${{ github.repository }}"
    assert "secrets.PF_READ_TOKEN" not in str(module_step["env"])
    for step in release_job(shipped)["steps"]:
        if step.get("name") in added:
            assert "${{" not in step.get("run", ""), step["name"]
    assert "python3 -I " in step_named(shipped, "Add the closing section to the release's notes")["run"]
    assert "release_notes.py" in step_named(shipped, "Add the closing section to the release's notes")["run"]


def test_a_caller_with_no_token_is_refused_naming_it_and_config_itself_and_a_caller_with_one_are_not(
    shipped: Path, tmp_path: Path
) -> None:
    script = step_named(shipped, "Check the read token is given")["run"]

    def run(token: str, caller: str) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ["PATH"], "PF_TOKEN": token, "CALLER_REPOSITORY": caller}
        env["CONFIG_REPOSITORY"] = "andrew-organization/productforge-config"
        return subprocess.run(["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True)

    refused = run("", "andrew-organization/productforge-marketplace")
    given = run("ghp_placeholderonly", "andrew-organization/productforge-marketplace")
    itself = run("", "andrew-organization/productforge-config")

    assert refused.returncode == 1 and "PF_READ_TOKEN" in refused.stdout
    assert given.returncode == 0 and itself.returncode == 0


def run_released_check(shipped: Path, repo: Path) -> dict[str, str]:
    script = step_named(shipped, "Find whether this run released")["run"]
    output = repo.parent / "github_output"
    output.write_text("")
    env = {**os.environ, "GITHUB_OUTPUT": str(output)}
    subprocess.run(["bash", "-ec", script], cwd=repo, env=env, capture_output=True, text=True, check=True)
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


def test_a_run_released_when_the_newest_stable_tag_sits_on_the_commit_it_is_at(shipped: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "checkout"

    def git(*args: str, cwd: Path = repo) -> None:
        subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)

    def commit(subject: str) -> None:
        (repo / "file").write_text(subject)
        git("add", "-A")
        git("commit", "-q", "-m", subject)

    git("init", "-q", "--bare", str(remote), cwd=tmp_path)
    git("clone", "-q", str(remote), str(repo), cwd=tmp_path)
    commit("feat: first")
    git("tag", "v1.0.0")
    commit("feat: second")
    git("tag", "v1.1.0")
    git("tag", "v1.2.0-rc.1")  # a prerelease is never the run's tag
    git("push", "-q", "origin", "main", "--tags")

    first_release = run_released_check(shipped, repo)
    commit("docs: explain")  # a merge that released nothing: the newest tag sits on an earlier commit
    git("push", "-q", "origin", "main")
    nothing_released = run_released_check(shipped, repo)

    assert first_release == {"tag": "v1.1.0", "releasing": "true", "previous": "v1.0.0"}
    assert nothing_released == {"tag": "v1.1.0", "releasing": "false", "previous": ""}


def test_a_repositorys_first_run_has_no_previous_tag(shipped: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "checkout"
    for args in (["init", "-q", "--bare", str(remote)], ["clone", "-q", str(remote), str(repo)]):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, text=True, check=True)
    (repo / "file").write_text("x")
    for args in (
        ["add", "-A"],
        ["commit", "-q", "-m", "feat: first"],
        ["tag", "v1.0.0"],
        ["push", "-q", "origin", "main", "--tags"],
    ):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)

    assert run_released_check(shipped, repo) == {"tag": "v1.0.0", "releasing": "true", "previous": ""}
