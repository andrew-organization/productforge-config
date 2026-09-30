"""Tests of the declaration (`PF_KITS`), the refusals `update` makes before writing, what each kit
brings, and the move from an earlier repository shape. Each works on a throwaway copy of a fixture.
"""

import shutil
import stat
from pathlib import Path

import pytest

from productforge_config import cli, kits

from .conftest import VERSION, run_make

SOURCE = "https://github.com/andrew-organization/productforge-config"
FLOW_LINT = f"repos:\n  - {{repo: {SOURCE}, rev: v1.4.0, hooks: [{{id: black}}]}}\n"


def _tree(repo: Path) -> dict[str, str]:
    return {str(p.relative_to(repo)): p.read_text() for p in sorted(repo.rglob("*")) if p.is_file()}


def _hooks(repo: Path) -> list[str]:
    text = (repo / ".productforge" / "pre-commit.yaml").read_text()
    return [line.split("id:")[1].strip() for line in text.splitlines() if "- id:" in line]


# ─── What each kit brings ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("declaration", "hooks"),
    [
        (
            "PF_KITS=python",
            ["trailing-whitespace", "end-of-file-fixer", "markdownlint", "check-json", "yamllint", "taplo-format"]
            + ["taplo-lint", "checkmake", "pyupgrade", "isort", "black", "flake8"],
        ),
        ("PF_KITS=python shell release", None),
        ("PF_KITS=\n", None),
    ],
)
def test_the_generated_hooks_are_exactly_those_of_the_kits_taken(
    api_repo: Path, declaration: str, hooks: list[str] | None
) -> None:
    (api_repo / "productforge.env").write_text(declaration + "\n")
    cli.update(api_repo, VERSION)
    listed = _hooks(api_repo)
    if hooks is not None:
        assert listed == hooks
    assert ("shellcheck" in listed) == ("shell" in declaration)
    assert ("pyupgrade" in listed) == ("python" in declaration)
    assert not {"django-mypy", "dart-format", "flutter-analyze"} & set(listed)


def test_a_web_app_takes_none_of_the_python_hooks_and_an_api_no_shellcheck(api_repo: Path, web_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    cli.update(web_repo, VERSION)
    assert not {"pyupgrade", "isort", "black", "flake8", "django-mypy", "shellcheck"} & set(_hooks(web_repo))
    assert {"dart-format", "flutter-analyze"} <= set(_hooks(web_repo))
    assert "shellcheck" not in _hooks(api_repo)
    assert "django-mypy" in _hooks(api_repo)
    text = (api_repo / ".productforge" / "pre-commit.yaml").read_text()
    assert "exclude: '^\\.productforge/|(^|/)migrations/'" in text  # generated code, for every hook
    assert "migrations" not in (web_repo / ".productforge" / "pre-commit.yaml").read_text()


def test_the_generated_config_states_the_floor_and_the_release(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    text = (api_repo / ".productforge" / "pre-commit.yaml").read_text()
    assert "minimum_pre_commit_version: '4.6'" in text
    assert f"    rev: {VERSION}\n" in text
    assert "exclude: '^\\.productforge/|(^|/)migrations/'" in text


def test_a_declared_lint_exclude_joins_the_kits_own(api_repo: Path) -> None:
    (api_repo / "productforge.env").write_text("PF_KITS=python\nPF_LINT_EXCLUDE=^tests/fixture_\n")
    cli.update(api_repo, VERSION)
    assert (
        "exclude: '^\\.productforge/|^tests/fixture_'" in (api_repo / ".productforge" / "pre-commit.yaml").read_text()
    )


def test_common_carries_no_claude_page_and_only_product_kits_do(api_repo: Path) -> None:
    (api_repo / "productforge.env").write_text("PF_KITS=python\n")
    cli.update(api_repo, VERSION)
    assert not (api_repo / ".productforge" / "CLAUDE.md").exists()
    assert not (api_repo / ".productforge" / "product.mk").exists()


def test_python_mk_offers_parallel_pytest_and_a_test_target_unless_django_api_is_taken(api_repo: Path) -> None:
    (api_repo / "productforge.env").write_text("PF_KITS=python\n")
    cli.update(api_repo, VERSION)
    plain = run_make(api_repo, "-n", "test", "path=tests", "k=one")
    assert plain.returncode == 0, plain.stderr
    assert 'uv run pytest -n auto tests -k "one"' in plain.stdout


def test_dropping_a_kit_removes_its_files_and_keeps_the_rest(api_repo: Path) -> None:
    (api_repo / "productforge.env").write_text("PF_KITS=python shell release\n")
    cli.update(api_repo, VERSION)
    assert "shellcheck" in _hooks(api_repo)
    (api_repo / "productforge.env").write_text("PF_KITS=python\n")
    cli.update(api_repo, VERSION)
    assert "shellcheck" not in _hooks(api_repo)
    (api_repo / "productforge.env").write_text("PF_KITS=flutter-web\nPF_SLOT=3\nPF_NAME=x\n")
    cli.update(api_repo, VERSION)
    assert not (api_repo / ".productforge" / "python.mk").exists()
    assert not (api_repo / ".dockerignore").exists()
    assert (api_repo / ".fvmrc").is_file()


# ─── Refusals ─────────────────────────────────────────────────────────────

_API = "PF_SLOT=3\nPF_NAME=demo\n"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ("PF_KITS=python nonsense\n", "unknown kit, 'nonsense'"),
        ("PF_KITS=common python\n", "never names `common`"),
        ("PF_KITS=python python\n", "more than once"),
        ("PF_KIND=api\nPF_KITS=python\n" + _API, "PF_KIND and PF_KITS"),
        ("PF_KITS=django-api flutter-web\n" + _API, "both django-api and flutter-web"),
        ("PF_KITS=django-api\nPF_SLOT=3\n", "PF_NAME is not set"),
        ("PF_KITS=django-api\nPF_NAME=demo\n", "PF_SLOT"),
        ("PF_KITS=flutter-web\nPF_SLOT=x\nPF_NAME=demo\n", "PF_SLOT"),
        ("PF_KITS=python\nPF_SLOT=3\n", "PF_SLOT only belong with a product kit"),
        ("PF_KITS=python\nPF_NAME=demo\n", "PF_NAME only belong with a product kit"),
        ("PF_KITS=python\nPF_DJANGO_PROJECT=core\n", "PF_DJANGO_PROJECT"),
        ("PF_KITS=python\nPF_POSTGRES_DB=shop\n", "PF_POSTGRES_DB"),
        ("PF_KITS=python\nPF_LINT_EXCLUDE=(\n", "PF_LINT_EXCLUDE"),
    ],
)
def test_update_refuses_a_bad_declaration_before_writing_anything(
    api_repo: Path, env: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    (api_repo / "productforge.env").write_text(env)
    before = _tree(api_repo)
    assert cli.main(["update", "--version", VERSION, "--path", str(api_repo)]) == 1
    assert message in capsys.readouterr().err
    assert _tree(api_repo) == before
    assert cli.main(["update", "--version", VERSION, "--path", str(api_repo), "--check"]) == 2


def test_update_refuses_a_lint_file_naming_productforge_config_in_flow_style(
    api_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (api_repo / ".pre-commit-lint.yaml").write_text(FLOW_LINT)
    before = _tree(api_repo)
    assert cli.main(["update", "--version", VERSION, "--path", str(api_repo)]) == 1
    assert "flow style" in capsys.readouterr().err
    assert _tree(api_repo) == before
    assert cli.main(["update", "--version", VERSION, "--path", str(api_repo), "--check"]) == 2


def test_check_with_no_version_and_no_release_record_is_refused(
    api_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["update", "--path", str(api_repo), "--check"]) == 2
    assert "release recorded" in capsys.readouterr().err


def test_a_malformed_version_is_refused_with_the_stated_codes(api_repo: Path) -> None:
    before = _tree(api_repo)
    assert cli.main(["update", "--version", "1.2", "--path", str(api_repo)]) == 1
    assert cli.main(["update", "--version", "1.2", "--path", str(api_repo), "--check"]) == 2
    assert _tree(api_repo) == before


# ─── The declaration round-trips ──────────────────────────────────────────


def test_a_value_with_an_inline_comment_quotes_and_an_export_prefix_reads_as_the_bare_form(api_repo: Path) -> None:
    bare = kits.read_env(api_repo)
    (api_repo / "productforge.env").write_text(
        "export PF_KITS=\"python django-api\"  # both\nexport PF_SLOT='3'\nPF_NAME=fixture_api # the name\n"
    )
    assert kits.read_env(api_repo) == bare
    cli.update(api_repo, VERSION)
    assert cli.update(api_repo, VERSION) == []
    assert "PF_KITS" in (api_repo / "productforge.env").read_text()  # not rewritten: it is already the new form


def test_make_reads_a_quoted_kit_list_and_a_commented_value_the_same(api_repo: Path) -> None:
    (api_repo / "productforge.env").write_text(
        'export PF_KITS="python django-api"  # both\nPF_SLOT="3"\nPF_NAME=fixture_api # the name\n'
    )
    cli.update(api_repo, VERSION)
    result = run_make(api_repo, "ports")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "slot 3: 6160-6179"


@pytest.mark.parametrize(("old", "new"), [("api", "python django-api"), ("web", "flutter-web")])
def test_pf_kind_becomes_pf_kits_once(api_repo: Path, old: str, new: str) -> None:
    (api_repo / "productforge.env").write_text(
        f"# my values\nexport PF_KIND={old} # earlier\nPF_SLOT=3\nPF_NAME=fixture_api\n"
    )
    assert "productforge.env" in cli.update(api_repo, VERSION)
    text = (api_repo / "productforge.env").read_text()
    assert text == f"# my values\nexport PF_KITS={new}\nPF_SLOT=3\nPF_NAME=fixture_api\n"
    assert cli.update(api_repo, VERSION) == []


# ─── The earlier repository shape ─────────────────────────────────────────

BLOCK = """\
  # The shared hooks.
  - repo: https://github.com/andrew-organization/productforge-config
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
OLD_MAKEFILE = (
    """\
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
""".replace("SOURCE", "https://github.com/andrew-organization/productforge-config")
    .replace("\n@", "\n\t@")
    .replace("\nuvx", "\n\tuvx")
)  # the recipe lines are tab-indented


def test_a_lint_file_holding_the_block_and_a_local_hook_keeps_the_hook_and_loses_the_block(api_repo: Path) -> None:
    lint = api_repo / ".pre-commit-lint.yaml"
    lint.write_text(f"exclude: '^\\.git/'\nfail_fast: true\n\nrepos:\n{BLOCK}\n{LOCAL}")
    assert ".pre-commit-lint.yaml" in cli.update(api_repo, VERSION)
    text = lint.read_text()
    assert "productforge-config" not in text and "The shared hooks" not in text
    assert text == f"exclude: '^\\.git/'\nfail_fast: true\n\nrepos:\n{LOCAL}"
    assert cli.update(api_repo, VERSION) == []


def test_a_lint_file_holding_only_the_block_is_deleted(api_repo: Path) -> None:
    lint = api_repo / ".pre-commit-lint.yaml"
    lint.write_text(f"default_stages: [pre-commit]\nfail_fast: false\n\nrepos:\n{BLOCK}")
    assert ".pre-commit-lint.yaml" in cli.update(api_repo, VERSION)
    assert not lint.exists()


def test_a_block_between_two_local_repositories_leaves_them_both(api_repo: Path) -> None:
    lint = api_repo / ".pre-commit-lint.yaml"
    other = LOCAL.replace("mine", "other")
    lint.write_text(f"repos:\n{LOCAL}\n{BLOCK}\n{other}")
    cli.update(api_repo, VERSION)
    text = lint.read_text()
    assert "id: mine" in text and "id: other" in text and "black" not in text


def test_an_earlier_shape_moves_to_the_kits_in_one_run(api_repo: Path) -> None:
    (api_repo / "productforge.env").write_text("PF_KIND=api\nPF_SLOT=3\nPF_NAME=fixture_api\n")
    (api_repo / "Makefile").write_text(OLD_MAKEFILE)
    (api_repo / ".pre-commit-lint.yaml").write_text(f"repos:\n{BLOCK}\n{LOCAL}")
    changed = cli.update(api_repo, VERSION)
    assert {"productforge.env", "Makefile", ".pre-commit-lint.yaml", ".productforge/pre-commit.yaml"} <= set(changed)
    makefile = (api_repo / "Makefile").read_text()
    assert makefile.startswith("include productforge.env\ninclude $(sort $(wildcard .productforge/*.mk))\n")
    assert "update-config" not in makefile.replace(".PHONY: all clean test update-config", "")
    assert run_make(api_repo, "-n", "lint").returncode == 0
    assert cli.update(api_repo, VERSION, check=True) == []


def test_an_earlier_manifest_naming_the_old_files_is_cleaned(api_repo: Path) -> None:
    (api_repo / ".productforge").mkdir()
    (api_repo / ".productforge" / "api.mk").write_text("# old\n")
    (api_repo / ".productforge" / "manifest").write_text(".productforge/api.mk\n.productforge/common.mk\n")
    cli.update(api_repo, VERSION)
    assert not (api_repo / ".productforge" / "api.mk").exists()
    assert (api_repo / ".productforge" / "django-api.mk").is_file()


# ─── make lint ────────────────────────────────────────────────────────────


def _fake_pre_commit(repo: Path, failing: str = "") -> str:
    script = repo / "fake-pre-commit"
    script.write_text(
        '#!/bin/sh\necho "ran $*" >> pre-commit.log\n'
        f'case "$*" in *"{failing}"*) [ -n "{failing}" ] && exit 1;; esac\nexit 0\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return f"PRE_COMMIT=./{script.name}"


@pytest.mark.parametrize("failing", ["", ".productforge/pre-commit.yaml", ".pre-commit-lint.yaml"])
def test_lint_runs_both_configurations_and_fails_when_either_does(api_repo: Path, failing: str) -> None:
    cli.update(api_repo, VERSION)
    result = run_make(api_repo, "lint", _fake_pre_commit(api_repo, failing))
    log = (api_repo / "pre-commit.log").read_text()
    assert "--config .productforge/pre-commit.yaml" in log and "--config .pre-commit-lint.yaml" in log
    assert (result.returncode != 0) == bool(failing)


def test_lint_runs_the_kits_configuration_alone_without_a_repository_file(web_repo: Path) -> None:
    cli.update(web_repo, VERSION)
    assert run_make(web_repo, "lint", _fake_pre_commit(web_repo)).returncode == 0
    assert (web_repo / "pre-commit.log").read_text().count("ran run") == 1


def test_a_release_moves_only_what_the_kits_write(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    tree = _tree(api_repo)
    shutil.rmtree(api_repo / ".productforge")
    cli.update(api_repo, VERSION)
    assert _tree(api_repo) == tree
