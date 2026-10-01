"""`productforge-config update`: bringing a repository up to a release, and checking whether it is."""

from pathlib import Path

import pytest

from .conftest import VERSION, Repo, Stubs, git_tags, run_config

SHA = "0123456789abcdef0123456789abcdef01234567"
ACTION = "andrew-organization/productforge-config/actions/setup"
WORKFLOWS = "andrew-organization/productforge-config/.github/workflows"


def test_update_brings_a_python_repository_to_a_release(python_repo: Repo, shipped: Path) -> None:
    python_repo.edit("pyproject.toml", "[tool.black]", "[tool.black]  # the repository's own note")
    python_repo.write(".yamllint", "extends: relaxed\n")

    result = python_repo.update()

    assert result.returncode == 0
    assert result.stderr == ""
    reported = result.stdout.removeprefix(f"productforge-config {VERSION}: updated ").strip().split(", ")
    assert set(reported) == {
        ".github/workflows/*.yml",
        ".markdownlint-cli2.jsonc",
        "pyproject.toml",
        "setup.cfg",
        ".editorconfig",
        ".yamllint",
        ".python-version",
        ".productforge/common.mk",
        ".productforge/python.mk",
        ".productforge/pre-commit.yaml",
        ".productforge/release",
        ".productforge/manifest",
    }
    workflow = python_repo.read(".github/workflows/ci.yml")
    assert f"{ACTION}@{VERSION}" in workflow and "v0.9.0" not in workflow
    markdownlint = python_repo.read(".markdownlint-cli2.jsonc")
    assert '"MD060"' in markdownlint  # a rule only the shared config carries
    assert '"ignores": ["CHANGELOG.md"]' in markdownlint  # the repository's own key, untouched
    pyproject = python_repo.read("pyproject.toml")
    for shared in ('target-version = ["py314"]', 'requires-python = ">=3.14,<4.0"', "line-length = 120"):
        assert shared in pyproject
    assert 'profile = "black"' in pyproject and "line_length = 120" in pyproject
    assert 'skip_glob = ["*/migrations/*"]' in pyproject  # the repository's own key, untouched
    assert pyproject.count("[tool.black]") == 1  # a header with a trailing comment is updated, not repeated
    setup_cfg = python_repo.read("setup.cfg")
    assert "max-line-length = 120" in setup_cfg and "docstring-convention = google" in setup_cfg
    assert "extend-ignore = D100,D101,D102,D103,D104,D105,D106,D107,D200,D202,D205,D212,D415,E203,W503" in setup_cfg
    assert "exclude = .tox,.git,*/migrations/*" in setup_cfg  # the repository's own key, untouched
    assert f"    rev: {VERSION}\n" in python_repo.read(".productforge/pre-commit.yaml")
    assert python_repo.read(".productforge/release") == f"{VERSION}\n"
    for name, source in ((".yamllint", "yamllint.yaml"), (".editorconfig", "editorconfig")):
        assert python_repo.read(name).endswith((shipped / "settings" / source).read_text())
    assert "Generated: change it in productforge-config" in python_repo.read(".yamllint")

    before = python_repo.tree()
    again = python_repo.update()

    assert (again.returncode, again.stdout, again.stderr) == (
        0,
        f"productforge-config {VERSION}: already up to date\n",
        "",
    )
    assert python_repo.tree() == before
    assert python_repo.check(version=VERSION).stdout == f"productforge-config {VERSION}: no drift\n"


def test_update_moves_every_ref_to_this_repository_and_only_those(python_repo: Repo) -> None:
    python_repo.edit(".github/workflows/ci.yml", f"uses: {ACTION}@v0.9.0", f'uses: "{ACTION}@v0.9.0"')
    python_repo.write(".github/workflows/release.yml", f"jobs:\n  release:\n    uses: {WORKFLOWS}/release.yml@v0.9.0\n")
    python_repo.write(
        ".github/workflows/x.yml",
        f"#       uses: {WORKFLOWS}/ci-api.yml@vX.Y.Z\n"
        f"a: {{uses: andrew-organization/productforge-config/actions/other@v0.1.0}}\n"
        f"b: {WORKFLOWS}/ci-web.yml@v0.1.0\n"
        f"uses: '{WORKFLOWS}/future.yml@main'  # x\n"
        "uses: someone-else/productforge-config/actions/setup@v0.1.0\n"
        "uses: andrew-organization/productforge-config/README@v0.1.0\n"
        f"jobs:\n  ci:\n    uses: {WORKFLOWS}/ci-api.yml@v0.1.0\n",
    )

    result = python_repo.update(version=SHA)

    assert result.returncode == 0
    assert f'uses: "{ACTION}@{SHA}"' in python_repo.read(".github/workflows/ci.yml")  # its quotes stay
    assert f"{WORKFLOWS}/release.yml@{SHA}" in python_repo.read(".github/workflows/release.yml")
    assert python_repo.read(".github/workflows/x.yml").splitlines() == [
        f"#       uses: {WORKFLOWS}/ci-api.yml@vX.Y.Z",  # a ref shown in a comment keeps its placeholder
        f"a: {{uses: andrew-organization/productforge-config/actions/other@{SHA}}}",
        f"b: {WORKFLOWS}/ci-web.yml@v0.1.0",  # no `uses:`
        f"uses: '{WORKFLOWS}/future.yml@{SHA}'  # x",
        "uses: someone-else/productforge-config/actions/setup@v0.1.0",
        "uses: andrew-organization/productforge-config/README@v0.1.0",  # neither an action nor a workflow
        "jobs:",
        "  ci:",
        f"    uses: {WORKFLOWS}/ci-api.yml@{SHA}",
    ]
    assert f"rev: {SHA}" in python_repo.read(".productforge/pre-commit.yaml")


def test_update_takes_a_tag_a_release_candidate_or_a_commit_and_refuses_any_other_shape(python_repo: Repo) -> None:
    for version in ("v1.2.3", "v1.2.3-rc.4", SHA):
        result = python_repo.update(version=version)

        assert result.returncode == 0, result.stderr
        assert f"rev: {version}\n" in python_repo.read(".productforge/pre-commit.yaml")
    before = python_repo.tree()

    for version in ("1.2", "v1.2.3-beta.1"):
        updating = python_repo.update(version=version)
        checking = python_repo.check("--version", version)

        assert (updating.returncode, checking.returncode) == (1, 2)
        assert "isn't a valid productforge-config version" in updating.stderr
        assert updating.stdout == "" and python_repo.tree() == before  # nothing half-applied


@pytest.mark.parametrize(
    ("version_args", "tags", "expected"),
    [
        ([], ["v1.9.0", "v1.10.0", "v1.11.0-rc.1", "v1", "v1.10.0^{}"], "v1.10.0"),  # a stable tag beats a newer -rc
        (["--version", "latest"], ["v1.0.0-rc.2", "v1.0.0-rc.10", "v0.9.0-rc.11", "nonsense"], "v1.0.0-rc.10"),
    ],
)
def test_update_without_a_version_takes_the_newest_stable_tag_else_the_newest_release_candidate(
    python_repo: Repo, stubs: Stubs, version_args: list[str], tags: list[str], expected: str
) -> None:
    stubs.add("git", stdout=git_tags(tags))

    result = run_config("update", "--path", python_repo.root, *version_args)

    assert result.returncode == 0
    assert result.stdout.startswith(f"productforge-config {expected}: updated ")
    assert f"rev: {expected}\n" in python_repo.read(".productforge/pre-commit.yaml")
    assert len(stubs.calls) == 1 and stubs.calls[0].startswith("git ls-remote --tags ")


@pytest.mark.parametrize(
    ("how", "message"),
    [
        ("fails", "offline"),
        ("is missing", "couldn't list the release tags"),
        ("lists nothing", "no release tags found"),
    ],
)
def test_update_says_why_when_it_cannot_resolve_the_latest_release(
    python_repo: Repo, stubs: Stubs, monkeypatch: pytest.MonkeyPatch, how: str, message: str
) -> None:
    if how == "fails":
        stubs.add("git", stderr="offline", status=128)
    elif how == "lists nothing":
        stubs.add("git", stdout=git_tags(["not-a-release"]))
    else:
        monkeypatch.setenv("PATH", str(stubs.bin))
    before = python_repo.tree()

    updating = run_config("update", "--path", python_repo.root)
    checking = run_config("update", "--path", python_repo.root, "--check", "--version", "latest")

    assert (updating.returncode, checking.returncode) == (1, 2)
    assert message in updating.stderr and message in checking.stderr
    assert updating.stdout == "" and python_repo.tree() == before


def test_check_compares_with_the_recorded_release_unless_the_latest_is_asked_for(
    python_repo: Repo, stubs: Stubs
) -> None:
    stubs.add("git", stdout=git_tags(["v9.9.9"]))
    without_a_record = python_repo.check()
    python_repo.update()

    recorded = python_repo.check()
    latest = python_repo.check("--version", "latest")

    assert without_a_record.returncode == 2
    assert "needs the release recorded" in without_a_record.stderr
    assert (recorded.returncode, recorded.stdout) == (0, f"productforge-config {VERSION}: no drift\n")
    assert latest.returncode == 1
    assert latest.stdout.startswith("productforge-config v9.9.9: drift in ")


def test_update_keeps_the_repositorys_own_markdownlint_keys_whatever_braces_they_hold(python_repo: Repo) -> None:
    python_repo.write(
        ".markdownlint-cli2.jsonc",
        "// a leading comment with a } brace that must not count\n"
        "{\n"
        '  "config": {\n'
        '    "note": "a value with a closing } brace inside a string",\n'
        '    "escaped": "a value with an escaped \\" quote and a } brace",\n'
        "    /* a block comment containing a } brace */\n"
        '    "default": false\n'
        "  },\n"
        '  "ignores": ["CHANGELOG.md"]\n'
        "}\n",
    )

    python_repo.update()

    text = python_repo.read(".markdownlint-cli2.jsonc")
    assert text.startswith("// a leading comment with a } brace that must not count\n{\n")
    assert '"MD060"' in text and "a closing } brace" not in text
    assert "brace */" not in text and '"escaped"' not in text  # the whole old block is replaced, comment included
    assert text.endswith('  },\n  "ignores": ["CHANGELOG.md"]\n}\n')

    python_repo.write(".markdownlint-cli2.jsonc", '{\n  "ignores": ["CHANGELOG.md"]\n}\n')  # no config block to keep
    result = python_repo.update()

    assert python_repo.read(".markdownlint-cli2.jsonc") == '{\n  "ignores": ["CHANGELOG.md"]\n}\n'
    assert ".markdownlint-cli2.jsonc" not in result.stdout


def test_update_writes_the_python_settings_only_where_the_python_kit_is_taken(
    python_repo: Repo, api_repo: Repo, tmp_path: Path
) -> None:
    python_repo.write("productforge.env", "PF_KITS=\n")
    own_files = {name: python_repo.read(name) for name in ("pyproject.toml", "setup.cfg")}
    bare = Repo(tmp_path / "bare")
    bare.write("productforge.env", "PF_KITS=python\n")
    no_project = Repo(tmp_path / "no_project")
    no_project.write("productforge.env", "PF_KITS=python\n")
    no_project.write("pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    api_repo.write("productforge.env", "PF_KITS=python\n")  # the python kit without django-api

    without_python = python_repo.update()
    with_no_python_files = bare.update()
    no_project.update()
    api_repo.update()

    assert {name: python_repo.read(name) for name in own_files} == own_files
    assert "pyproject.toml" not in without_python.stdout and "setup.cfg" not in without_python.stdout
    assert not python_repo.exists(".productforge/python.mk")
    assert "setup.cfg" in with_no_python_files.stdout and bare.read("setup.cfg").startswith("[flake8]\n")
    assert not bare.exists("pyproject.toml")
    assert "requires-python" not in no_project.read("pyproject.toml")  # a pyproject with no [project] table
    assert 'python_version = "3.12"' in api_repo.read("pyproject.toml")  # mypy strictness is django-api's
