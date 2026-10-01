"""What `productforge-config update` writes for a repository's declaration (`productforge.env`): the files of
each kit it takes, what goes with them, what it refuses before writing, and the move from an earlier shape.
"""

import json
import shutil
import stat
from pathlib import Path

import pytest

from .conftest import VERSION, Repo

COMMON_FILES = {".productforge/common.mk", ".productforge/pre-commit.yaml", ".productforge/release"}
API_KIT = COMMON_FILES | {
    ".productforge/CLAUDE.md",
    ".productforge/Dockerfile",
    ".productforge/django-api.mk",
    ".productforge/product.mk",
    ".productforge/python.mk",
    ".productforge/compose.test.yml",
    ".productforge/compose.yml",
    ".productforge/entrypoint",
    ".dockerignore",
}
WEB_KIT = COMMON_FILES | {
    ".productforge/CLAUDE.md",
    ".productforge/analysis_options.yaml",
    ".productforge/check_identity_regeneration.py",
    ".productforge/product.mk",
    ".productforge/generate_identity.py",
    ".productforge/serve_web_build.py",
    ".productforge/flutter-web.mk",
}
SOURCE = "https://github.com/andrew-organization/productforge-config"
HEADER = "Generated: change it in productforge-config"


def installed(repo: Repo) -> set[str]:
    """Every file the kits put in `.productforge/`, and `.dockerignore` where there is one."""
    files = {str(p.relative_to(repo.root)) for p in (repo.root / ".productforge").rglob("*") if p.is_file()}
    return (files - {".productforge/manifest"}) | ({".dockerignore"} if repo.exists(".dockerignore") else set())


def hooks(repo: Repo) -> list[str]:
    return [
        line.split("id:")[1].strip()
        for line in repo.read(".productforge/pre-commit.yaml").splitlines()
        if "- id:" in line
    ]


def reported(stdout: str) -> set[str]:
    return set(stdout.partition(": updated ")[2].strip().split(", "))


# ─── What each kit brings ─────────────────────────────────────────────────


def test_update_installs_the_api_kit_whole(api_repo: Repo) -> None:
    dependencies = (
        '["pre-commit", "pre-commit-hooks>=6.0", "pre-commit; python_version>\'3\'"]'  # a bare name is left alone
    )
    api_repo.write("pyproject.toml", api_repo.read("pyproject.toml") + f"\n[dependency-groups]\ndev = {dependencies}\n")

    result = api_repo.update()

    assert result.returncode == 0 and result.stderr == ""
    assert API_KIT <= reported(result.stdout) and ".pre-commit-config.yaml" in reported(result.stdout)
    assert installed(api_repo) == API_KIT
    for rel in [*API_KIT, ".yamllint", ".editorconfig", ".pre-commit-config.yaml"]:
        if rel != ".productforge/release":  # the release record is one bare line
            assert HEADER in "\n".join(api_repo.read(rel).splitlines()[:6]), rel
    assert api_repo.read(".productforge/release") == f"{VERSION}\n"
    claude = api_repo.read(".productforge/CLAUDE.md")
    assert "## Ports" in claude and "## CI" in claude and "## API targets" in claude and "## Web targets" not in claude
    assert "\n\n\n" not in claude and "\n\n## API targets" in claude  # fragments join without stray blank lines
    assert "`make ports`" in claude and all(
        name in claude for name in ("DJANGO_ALLOWED_HOSTS", "CORS_EXTRA_ORIGINS", "FRONTEND_BASE_URL")
    )
    assert "make check-migrations" in api_repo.read(".pre-commit-config.yaml")
    assert "make check-generated" not in api_repo.read(".pre-commit-config.yaml")
    assert not api_repo.exists(".fvmrc")
    pyproject = api_repo.read("pyproject.toml")
    for strictness in ('python_version = "3.14"', "disallow_any_explicit = true", "warn_return_any = true"):
        assert strictness in pyproject
    assert 'follow_imports = "silent"' in pyproject
    assert 'mypy_path = "api"' in pyproject and 'plugins = ["mypy_django_plugin.main"]' in pyproject  # its own keys

    before = api_repo.tree()
    assert api_repo.update().stdout == f"productforge-config {VERSION}: already up to date\n"
    assert api_repo.tree() == before
    assert api_repo.check(version=VERSION).returncode == 0
    shutil.rmtree(api_repo.root / ".productforge")  # a release writes back only what the kits write
    api_repo.update()
    assert api_repo.tree() == before


def test_update_installs_the_web_kit_whole(web_repo: Repo, shipped: Path) -> None:
    result = web_repo.update()

    assert result.returncode == 0 and result.stderr == ""
    assert installed(web_repo) == WEB_KIT
    for rel in [*WEB_KIT, ".yamllint", ".editorconfig", ".pre-commit-config.yaml"]:
        if rel != ".productforge/release":
            assert HEADER in "\n".join(web_repo.read(rel).splitlines()[:6]), rel
    lines = web_repo.read(".productforge/generate_identity.py").splitlines()
    assert lines[0] == "#!/usr/bin/env python3" and lines[1].startswith("# Installed by productforge-config")
    claude = web_repo.read(".productforge/CLAUDE.md")
    assert "## Ports" in claude and "## CI" in claude and "## Web targets" in claude and "## API targets" not in claude
    assert "`make ports`" in claude
    assert json.loads(web_repo.read(".fvmrc")) == {
        "flutter": json.loads((shipped / "settings" / "fvmrc").read_text())["flutter"]
    }
    assert "make check-generated" in web_repo.read(".pre-commit-config.yaml")
    pubspec = web_repo.read("pubspec.yaml")
    assert "  sdk: ^3.12.2  # the Dart SDK" in pubspec  # moved to the release's, keeping its comment
    assert (
        "    sdk: flutter" in pubspec and "name: fixture_web" in pubspec
    )  # a dependency's sdk key is not the environment's
    assert not any(
        web_repo.exists(rel) for rel in (".dockerignore", ".productforge/django-api.mk", ".productforge/python.mk")
    )

    before = web_repo.tree()
    assert web_repo.update().stdout == f"productforge-config {VERSION}: already up to date\n"
    assert web_repo.tree() == before

    web_repo.write("pubspec.yaml", "name: fixture_web\n")  # no environment to keep in step
    web_repo.update()
    assert web_repo.read("pubspec.yaml") == "name: fixture_web\n"


def test_the_generated_hooks_follow_the_kits_taken(api_repo: Repo, web_repo: Repo) -> None:
    api_repo.write("productforge.env", "PF_KITS=python\nPF_LINT_EXCLUDE=^tests/fixture_\n")
    api_repo.update()

    assert hooks(api_repo) == [
        *("trailing-whitespace", "end-of-file-fixer", "markdownlint", "check-json", "yamllint", "taplo-format"),
        *("taplo-lint", "checkmake", "pyupgrade", "isort", "black", "flake8"),
    ]
    config = api_repo.read(".productforge/pre-commit.yaml")
    assert "minimum_pre_commit_version: '4.6'" in config and f"    rev: {VERSION}\n" in config
    assert "exclude: '^\\.productforge/|^tests/fixture_'" in config  # a declared exclude joins the kit's own
    assert not api_repo.exists(".productforge/CLAUDE.md") and not api_repo.exists(".productforge/product.mk")
    assert 'uv run pytest -n auto tests -k "one"' in api_repo.dry(
        "test", "path=tests", "k=one"
    )  # python.mk's own test target

    api_repo.write("productforge.env", "PF_KITS=python shell release\n")
    api_repo.update()
    assert "shellcheck" in hooks(api_repo)
    api_repo.write("productforge.env", "PF_KITS=python\n")
    api_repo.update()
    assert "shellcheck" not in hooks(api_repo)
    api_repo.write("productforge.env", "PF_KITS=python django-api\nPF_SLOT=3\nPF_NAME=fixture_api\n")
    api_repo.update()
    assert "django-mypy" in hooks(api_repo)
    assert "exclude: '^\\.productforge/|(^|/)migrations/'" in api_repo.read(".productforge/pre-commit.yaml")

    web_repo.update()
    assert {"dart-format", "flutter-analyze"} <= set(hooks(web_repo))
    assert not {"pyupgrade", "isort", "black", "flake8", "django-mypy", "shellcheck"} & set(hooks(web_repo))
    assert "migrations" not in web_repo.read(".productforge/pre-commit.yaml")


def test_update_removes_what_a_previous_release_or_kit_installed_and_this_one_does_not(
    api_repo: Repo, tmp_path
) -> None:
    api_repo.update()
    api_repo.write(".productforge/gone.mk", "# a file an older kit carried\n")
    api_repo.write(".productforge/old/script.py", "# and another, in a directory\n")
    api_repo.write(".productforge/mine.txt", "not from a kit\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n")
    api_repo.write(
        ".productforge/manifest",
        api_repo.read(".productforge/manifest")
        + ".productforge/gone.mk\n.productforge/old/script.py\n../outside.txt\n",
    )

    drift = api_repo.check(version=VERSION)
    result = api_repo.update()

    assert ".productforge/gone.mk" in drift.stdout
    assert not api_repo.exists(".productforge/gone.mk") and not api_repo.exists(".productforge/old")
    assert {".productforge/gone.mk", ".productforge/old/script.py"} <= reported(result.stdout)
    assert ".productforge/gone.mk" not in api_repo.read(".productforge/manifest")
    assert api_repo.exists(".productforge/mine.txt")  # no manifest lists it
    assert outside.read_text() == "keep me\n"  # a manifest cannot reach outside the repository

    api_repo.write(
        "productforge.env", "PF_KITS=flutter-web\nPF_SLOT=3\nPF_NAME=fixture_api\n"
    )  # another kit altogether
    api_repo.update()
    assert installed(api_repo) - {".productforge/mine.txt"} == WEB_KIT
    assert not api_repo.exists(".dockerignore") and api_repo.exists(".fvmrc")
    assert api_repo.exists("pyproject.toml") and api_repo.exists(".productforge/mine.txt")  # the repository's own files


def test_check_lists_every_kind_of_drift_and_changes_nothing(api_repo: Repo) -> None:
    never_updated = api_repo.check(version=VERSION)
    assert (never_updated.returncode, never_updated.stderr) == (1, "")
    assert API_KIT <= reported(
        never_updated.stdout.replace("; run `make update-config`", "").replace("drift in", "updated")
    )
    assert not api_repo.exists(".productforge")
    api_repo.update()
    assert api_repo.check(version=VERSION).stdout == f"productforge-config {VERSION}: no drift\n"
    pristine = api_repo.tree()

    edits = {
        ".productforge/django-api.mk": lambda text: text + "\nlocal-tweak:\n",  # an installed file edited by hand
        ".yamllint": lambda text: text.replace("max: 120", "max: 121"),  # a setting changed by one character
        ".github/workflows/ci.yml": lambda text: text.replace(VERSION, "v1.2.2"),  # a workflow ref one release behind
        ".productforge/pre-commit.yaml": lambda text: text.replace("      - id: black\n", ""),  # the generated hooks
        ".python-version": lambda text: "3.11\n",
    }
    for rel, edit in edits.items():
        api_repo.write(rel, edit(api_repo.read(rel)))
        drifted = api_repo.check(version=VERSION)
        listed = ".github/workflows/*.yml" if rel.startswith(".github") else rel

        assert drifted.returncode == 1 and listed in drifted.stdout, rel
        api_repo.update()
        assert api_repo.tree() == pristine, rel


# ─── The declaration ──────────────────────────────────────────────────────

_API = "PF_SLOT=3\nPF_NAME=demo\n"
FLOW_LINT = f"repos:\n  - {{repo: {SOURCE}, rev: v1.4.0, hooks: [{{id: black}}]}}\n"


def declare(env: str):
    return lambda repo: repo.write("productforge.env", env)


def depend_on(requirement: str):
    def add(repo: Repo) -> None:
        repo.write("pyproject.toml", repo.read("pyproject.toml") + f'\n[dependency-groups]\ndev = ["{requirement}"]\n')

    return add


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda repo: (repo.root / "productforge.env").unlink(), "not found: a repository declares the kits it takes"),
        (declare("PF_KITS=python nonsense\n"), "unknown kit, 'nonsense'"),
        (declare("PF_KITS=common python\n"), "never names `common`"),
        (declare("PF_KITS=python python\n"), "more than once"),
        (declare("PF_KIND=api\nPF_KITS=python\n" + _API), "PF_KIND and PF_KITS"),
        (declare("PF_KITS=django-api flutter-web\n" + _API), "both django-api and flutter-web"),
        (declare("PF_KITS=django-api\nPF_SLOT=3\n"), "PF_NAME is not set"),
        (declare("PF_KITS=flutter-web\nPF_SLOT=x\nPF_NAME=demo\n"), "PF_SLOT"),
        (declare("PF_KITS=django-api\nPF_SLOT=28\nPF_NAME=demo\n"), "6665"),
        (declare("PF_KITS=python\nPF_SLOT=3\nPF_NAME=demo\n"), "PF_SLOT, PF_NAME only belong with a product kit"),
        (declare("PF_KITS=python\nPF_LINT_EXCLUDE=(\n"), "PF_LINT_EXCLUDE"),
        (lambda repo: repo.write(".pre-commit-lint.yaml", FLOW_LINT), "flow style"),
        (depend_on("pre_commit==4.6.0"), "versions pre-commit"),
    ],
)
def test_update_refuses_what_it_cannot_apply_before_writing_anything(api_repo: Repo, setup, message: str) -> None:
    setup(api_repo)
    before = api_repo.tree()

    updating = api_repo.update()
    checking = api_repo.check(version=VERSION)

    assert (updating.returncode, checking.returncode) == (1, 2)
    assert message in updating.stderr and message in checking.stderr
    assert updating.stdout == "" and api_repo.tree() == before


def test_a_declaration_with_quotes_comments_and_an_export_prefix_reads_as_the_bare_one(api_repo: Repo) -> None:
    api_repo.write(
        "productforge.env",
        "export PF_KITS=\"python django-api\"  # both\nexport PF_SLOT='3'\nPF_NAME=fixture_api # the name\n",
    )

    result = api_repo.update()
    ports = api_repo.make("ports")

    assert result.returncode == 0, result.stderr
    assert "productforge.env" not in reported(result.stdout)  # not rewritten: it is the current form already
    assert api_repo.update().stdout == f"productforge-config {VERSION}: already up to date\n"
    assert installed(api_repo) == API_KIT  # the same kits as the bare declaration
    assert ports.stdout.splitlines()[0] == "slot 3: 6160-6179"  # make reads it the same way


# ─── The earlier repository shape ─────────────────────────────────────────

BLOCK = f"""\
  # The shared hooks.
  - repo: {SOURCE}
    rev: v1.4.0
    hooks:
      - id: black
      - id: markdownlint
        args: [--fix]
"""
LOCAL = """\
  - repo: local
    hooks:
      - id: mine
        name: mine
        entry: "true"
        language: system
"""
OLD_MAKEFILE = """\
include productforge.env
include .productforge/common.mk
include .productforge/api.mk

.PHONY: all clean test update-config

# The kit's makefiles give these their recipes; they are named here for checkmake, which reads this file alone.
all:
clean:
test:

VERSION ?= latest

## Bring this repository up to a productforge-config release.
update-config:
@version="$(VERSION)"; \\
uvx --from git+SOURCE@$$version productforge-config update --version $$version
""".replace("SOURCE", SOURCE).replace("\n@", "\n\t@").replace("\nuvx", "\n\tuvx")


def test_an_earlier_shape_moves_to_the_kits_in_one_run(api_repo: Repo) -> None:
    api_repo.write("productforge.env", "# my values\nexport PF_KIND=api # earlier\nPF_SLOT=3\nPF_NAME=fixture_api\n")
    api_repo.write("Makefile", OLD_MAKEFILE)
    api_repo.write(
        ".pre-commit-lint.yaml",
        f"exclude: '^\\.git/'\nfail_fast: true\n\nrepos:\n{LOCAL}\n{BLOCK}\n{LOCAL.replace('mine', 'other')}",
    )
    api_repo.write(".productforge/api.mk", "# old\n")
    api_repo.write(".productforge/manifest", ".productforge/api.mk\n.productforge/common.mk\n")

    result = api_repo.update()

    assert result.returncode == 0, result.stderr
    assert {"productforge.env", "Makefile", ".pre-commit-lint.yaml", ".productforge/pre-commit.yaml"} <= reported(
        result.stdout
    )
    assert (
        api_repo.read("productforge.env")
        == "# my values\nexport PF_KITS=python django-api\nPF_SLOT=3\nPF_NAME=fixture_api\n"
    )
    assert api_repo.read("Makefile").startswith(
        "include productforge.env\ninclude $(sort $(wildcard .productforge/*.mk))\n"
    )
    assert "uvx" not in api_repo.read("Makefile")  # the update-config recipe now comes from common.mk
    other = LOCAL.replace(
        "mine", "other"
    )  # the block and its comment gone, both local hooks and the file's settings kept
    assert api_repo.read(".pre-commit-lint.yaml") == f"exclude: '^\\.git/'\nfail_fast: true\n\nrepos:\n{LOCAL}\n{other}"
    assert not api_repo.exists(".productforge/api.mk")
    assert api_repo.dry("lint")
    assert api_repo.update().stdout == f"productforge-config {VERSION}: already up to date\n"
    assert api_repo.check(version=VERSION).returncode == 0

    api_repo.write(".pre-commit-lint.yaml", f"default_stages: [pre-commit]\nfail_fast: false\n\nrepos:\n{BLOCK}")
    assert ".pre-commit-lint.yaml" in reported(api_repo.update().stdout)
    assert not api_repo.exists(".pre-commit-lint.yaml")  # nothing of its own left in it: the file goes

    api_repo.write("productforge.env", "export PF_KIND=web # earlier\nPF_SLOT=3\nPF_NAME=fixture_api\n")
    api_repo.update()
    assert api_repo.read("productforge.env") == "export PF_KITS=flutter-web\nPF_SLOT=3\nPF_NAME=fixture_api\n"


# ─── make lint ────────────────────────────────────────────────────────────


def _fake_pre_commit(repo: Repo, failing: str = "") -> str:
    script = repo.root / "fake-pre-commit"
    script.write_text(
        '#!/bin/sh\necho "ran $*" >> pre-commit.log\n'
        f'case "$*" in *"{failing}"*) [ -n "{failing}" ] && exit 1;; esac\nexit 0\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return f"PRE_COMMIT=./{script.name}"


def test_lint_runs_the_kits_hooks_and_the_repositorys_own_and_fails_when_either_does(
    api_repo: Repo, web_repo: Repo
) -> None:
    api_repo.update()
    web_repo.update()

    clean = api_repo.make("lint", _fake_pre_commit(api_repo))
    log = api_repo.read("pre-commit.log")
    assert clean.returncode == 0
    assert "--config .productforge/pre-commit.yaml" in log and "--config .pre-commit-lint.yaml" in log
    for failing in (".productforge/pre-commit.yaml", ".pre-commit-lint.yaml"):
        assert api_repo.make("lint", _fake_pre_commit(api_repo, failing)).returncode != 0, failing
    assert web_repo.make("lint", _fake_pre_commit(web_repo)).returncode == 0  # no file of its own: the kit's alone
    assert web_repo.read("pre-commit.log").count("ran run") == 1
