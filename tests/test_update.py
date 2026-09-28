"""Tests of `productforge_config.cli.update` against tests/fixture_repo.

Each test copies the fixture to a throwaway directory first — never the
committed fixture itself — so a run never leaves it modified.
"""

import re
import shutil
from pathlib import Path

import pytest

from productforge_config import cli

FIXTURE = Path(__file__).parent / "fixture_repo"
VERSION = "v1.2.3"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def test_moves_the_hook_rev(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / ".pre-commit-lint.yaml").read_text()
    assert f"rev: {VERSION}" in text
    assert "v0.9.0" not in text


def test_keeps_the_repos_own_hook(repo: Path) -> None:
    before = (repo / ".pre-commit-lint.yaml").read_text()
    cli.update(repo, VERSION)
    after = (repo / ".pre-commit-lint.yaml").read_text()
    assert "fixture-own-check" in after
    # Only the rev line should differ.
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    differing = [i for i, (b, a) in enumerate(zip(before_lines, after_lines)) if b != a]
    assert differing == [before_lines.index("    rev: v0.9.0")]


def test_moves_the_action_ref(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / ".github" / "workflows" / "ci.yml").read_text()
    assert f"andrew-organization/productforge-config/actions/setup@{VERSION}" in text
    assert "v0.9.0" not in text


def test_moves_the_shared_release_workflow_ref(repo: Path) -> None:
    release = repo / ".github" / "workflows" / "release.yml"
    release.write_text(
        "jobs:\n"
        "  release:\n"
        "    uses: andrew-organization/productforge-config/.github/workflows/release.yml@v0.9.0\n"
    )

    changed = cli.update(repo, VERSION)

    text = release.read_text()
    assert f"andrew-organization/productforge-config/.github/workflows/release.yml@{VERSION}" in text
    assert "v0.9.0" not in text
    assert ".github/workflows/*.yml" in changed


def test_rewrites_markdownlint_config_and_keeps_ignores(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / ".markdownlint-cli2.jsonc").read_text()
    assert '"MD060"' in text  # a rule only the shared config carries
    assert '"ignores": ["CHANGELOG.md"]' in text  # the repo's own key, untouched


def test_rewrites_black_and_isort_keeping_repo_own_keys(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / "pyproject.toml").read_text()
    assert 'target-version = ["py314"]' in text
    assert "line-length = 120" in text
    assert 'profile = "black"' in text
    assert "line_length = 120" in text
    assert 'skip_glob = ["*/migrations/*"]' in text  # the repo's own key, untouched


def test_rewrites_flake8_keeping_repo_own_exclude(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / "setup.cfg").read_text()
    assert "max-line-length = 120" in text
    assert "docstring-convention = google" in text
    assert "extend-ignore = D100,D101,D102,D103,D104,D105,D106,D107,D200,D202,D205,D212,D415,E203,W503" in text
    assert "exclude = .tox,.git,*/migrations/*" in text  # the repo's own key, untouched


def test_reports_what_it_changed(repo: Path) -> None:
    changed = cli.update(repo, VERSION)
    assert set(changed) == {
        ".pre-commit-lint.yaml",
        ".github/workflows/*.yml",
        ".markdownlint-cli2.jsonc",
        "pyproject.toml",
        "setup.cfg",
    }


def test_running_it_again_changes_nothing(repo: Path) -> None:
    cli.update(repo, VERSION)
    assert cli.update(repo, VERSION) == []


def test_main_runs_end_to_end(repo: Path) -> None:
    exit_code = cli.main(["update", "--version", VERSION, "--path", str(repo)])
    assert exit_code == 0
    assert f"rev: {VERSION}" in (repo / ".pre-commit-lint.yaml").read_text()


def test_no_python_files_left_untouched(tmp_path: Path) -> None:
    """A repository with no Python at all — no pyproject.toml or setup.cfg
    — is left exactly as it is; update() doesn't create either file.
    """
    repo = tmp_path / "no_python_repo"
    repo.mkdir()
    (repo / ".pre-commit-lint.yaml").write_text(
        "repos:\n"
        "  - repo: https://github.com/andrew-organization/productforge-config\n"
        "    rev: v0.9.0\n"
        "    hooks:\n"
        "      - id: trailing-whitespace\n"
        "      - id: markdownlint\n"
    )

    changed = cli.update(repo, VERSION)

    assert not (repo / "pyproject.toml").exists()
    assert not (repo / "setup.cfg").exists()
    assert "pyproject.toml" not in changed
    assert "setup.cfg" not in changed
    # It still moved the hook rev — the absence of Python doesn't stop the
    # rest of the update.
    assert f"rev: {VERSION}" in (repo / ".pre-commit-lint.yaml").read_text()


def test_leaves_pyproject_and_setup_cfg_alone_when_hooks_not_taken(repo: Path) -> None:
    """A repository whose .pre-commit-lint.yaml takes only the non-Python
    hooks from this repository's own block — no black, isort or flake8 —
    keeps its pyproject.toml [tool.black]/[tool.isort] and setup.cfg
    [flake8] exactly as they are: only a repository that actually runs
    those hooks gets them rewritten.
    """
    lint_path = repo / ".pre-commit-lint.yaml"
    stripped = re.sub(
        r"\n[ \t]*-[ \t]*id:[ \t]*(black|isort|flake8)\b[^\n]*",
        "",
        lint_path.read_text(),
    )
    # Sanity check the fixture setup actually stripped something.
    assert "id: black" not in stripped
    assert "id: isort" not in stripped
    assert "id: flake8" not in stripped
    lint_path.write_text(stripped)

    before_pyproject = (repo / "pyproject.toml").read_text()
    before_setup_cfg = (repo / "setup.cfg").read_text()

    changed = cli.update(repo, VERSION)

    assert (repo / "pyproject.toml").read_text() == before_pyproject
    assert (repo / "setup.cfg").read_text() == before_setup_cfg
    assert "pyproject.toml" not in changed
    assert "setup.cfg" not in changed


def test_extract_balanced_skips_braces_in_strings_and_comments() -> None:
    """A `}` inside a JSON string value, or inside a // or /* */ comment,
    never miscounts the brace depth — only a real closing `}` ends the
    "config": { ... } block.
    """
    text = (
        "// a leading comment with a } brace that must not count\n"
        "{\n"
        '  "config": {\n'
        '    "note": "a value with a closing } brace inside a string",\n'
        '    "escaped": "a value with an escaped \\" quote and a } brace",\n'
        "    /* a block comment containing a } brace */\n"
        '    "default": false\n'
        "  },\n"
        '  "ignores": ["CHANGELOG.md"]\n'
        "}\n"
    )

    start, end, block = cli._extract_balanced(text, "config")

    assert text[start:end] == block
    assert block.startswith("{") and block.endswith("}")
    assert '"note": "a value with a closing } brace inside a string"' in block
    assert '"escaped": "a value with an escaped \\" quote and a } brace"' in block
    assert '"default": false' in block
    # The block stops at its own matching brace, not the outer document's.
    assert '"ignores"' not in block


def test_resolve_version_accepts_valid_shapes() -> None:
    for version in ("v1.2.3", "v1.2.3-rc.4"):
        assert cli.resolve_version(version) == version


def test_resolve_version_rejects_invalid_shapes() -> None:
    for version in ("1.2.3", "v1", "v1.2", "v1-rc.4", "v1.2.3.4", "va.b.c", "v1.2.3-beta.1", ""):
        with pytest.raises(cli.InvalidVersion):
            cli.resolve_version(version)


def test_resolve_version_none_or_latest_resolves_to_newest_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_latest_release_tag", lambda: "v2.4.0")
    assert cli.resolve_version(None) == "v2.4.0"
    assert cli.resolve_version("latest") == "v2.4.0"


def test_resolve_version_never_writes_on_an_invalid_explicit_version(repo: Path) -> None:
    before = (repo / ".pre-commit-lint.yaml").read_text()
    with pytest.raises(cli.InvalidVersion):
        cli.resolve_version("not-a-version")
    assert (repo / ".pre-commit-lint.yaml").read_text() == before


def test_main_resolves_latest_when_version_omitted(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_latest_release_tag", lambda: "v3.0.0")
    exit_code = cli.main(["update", "--path", str(repo)])
    assert exit_code == 0
    assert "rev: v3.0.0" in (repo / ".pre-commit-lint.yaml").read_text()


def test_main_refuses_an_invalid_version_before_writing_anything(repo: Path) -> None:
    before = (repo / ".pre-commit-lint.yaml").read_text()
    exit_code = cli.main(["update", "--version", "not-a-version", "--path", str(repo)])
    assert exit_code == 1
    assert (repo / ".pre-commit-lint.yaml").read_text() == before


def test_latest_release_tag_prefers_stable_over_prerelease() -> None:
    ls_remote_output = (
        "abc\trefs/tags/v1.0.0\n"
        "def\trefs/tags/v1.0.0-rc.1\n"
        "ghi\trefs/tags/v1.1.0-rc.1\n"
        "jkl\trefs/tags/v1\n"  # a moving major tag — not a release of its own
        "mno\trefs/tags/v1.0.0^{}\n"  # an annotated tag's dereferenced commit
    )
    stable, prerelease = cli._parse_release_tags(ls_remote_output)
    assert stable == {(1, 0, 0): "v1.0.0"}
    assert prerelease == {(1, 0, 0, 1): "v1.0.0-rc.1", (1, 1, 0, 1): "v1.1.0-rc.1"}


def test_latest_release_tag_falls_back_to_prerelease_when_no_stable_exists() -> None:
    ls_remote_output = "abc\trefs/tags/v1.0.0-rc.1\ndef\trefs/tags/v1.0.0-rc.2\n"
    stable, prerelease = cli._parse_release_tags(ls_remote_output)
    assert stable == {}
    assert prerelease[max(prerelease)] == "v1.0.0-rc.2"


def test_rewrites_python_settings_when_hooks_are_listed_in_flow_style(repo: Path) -> None:
    """A repository listing this repository's hooks in flow style, as the
    README's own example does, still gets its Python settings rewritten.
    """
    lint_path = repo / ".pre-commit-lint.yaml"
    text = lint_path.read_text()
    ids = cli._shared_hook_ids(repo)
    assert {"black", "isort", "flake8"} <= ids
    block = re.search(r"([ \t]*)hooks:\n(?:\1[ \t]*-[ \t]*id:[^\n]*\n(?:\1[ \t]+[^\n-][^\n]*\n)*)+", text)
    assert block is not None
    indent = block.group(1)
    flow = f"{indent}hooks: [" + ", ".join(f"{{id: {i}}}" for i in sorted(ids)) + "]\n"
    lint_path.write_text(text[: block.start()] + flow + text[block.end() :])
    assert cli._shared_hook_ids(repo) == ids

    changed = cli.update(repo, VERSION)

    assert "pyproject.toml" in changed
    assert "setup.cfg" in changed


def test_moves_a_quoted_action_ref_keeping_its_quotes(repo: Path) -> None:
    workflow = next((repo / ".github" / "workflows").glob("*.yml"))
    text = workflow.read_text()
    quoted = re.sub(r"uses:[ \t]*(andrew-organization/productforge-config/actions/setup@)(\S+)", r'uses: "\1\2"', text)
    assert quoted != text
    workflow.write_text(quoted)

    cli.update(repo, VERSION)

    assert f'uses: "andrew-organization/productforge-config/actions/setup@{VERSION}"' in workflow.read_text()


def test_updates_a_section_whose_header_carries_a_comment_without_duplicating_it(repo: Path) -> None:
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("[tool.black]", "[tool.black]  # the repository's own note", 1))

    cli.update(repo, VERSION)

    assert pyproject.read_text().count("[tool.black]") == 1


def test_moves_a_rev_with_a_comment_line_between_it_and_its_repo(repo: Path) -> None:
    lint = repo / ".pre-commit-lint.yaml"
    text = lint.read_text()
    commented = re.sub(
        r"(repo:[ \t]*https://github.com/andrew-organization/productforge-config[^\n]*\n)", r"\1    # pinned\n", text
    )
    assert commented != text
    lint.write_text(commented)

    cli.update(repo, VERSION)

    assert f"rev: {VERSION}" in lint.read_text()


def test_refuses_a_repo_it_takes_hooks_from_but_whose_rev_it_cannot_find(repo: Path) -> None:
    lint = repo / ".pre-commit-lint.yaml"
    lint.write_text(re.sub(r"\n[ \t]*rev:[^\n]*", "", lint.read_text(), count=1))

    with pytest.raises(cli.UpdateError):
        cli.update(repo, VERSION)


def test_writes_the_shared_yamllint_settings_where_the_hook_is_taken(repo: Path) -> None:
    lint = repo / ".pre-commit-lint.yaml"
    lint.write_text(lint.read_text().replace("      - id: flake8\n", "      - id: flake8\n      - id: yamllint\n"))
    (repo / ".yamllint").write_text("extends: relaxed\n")

    changed = cli.update(repo, VERSION)

    shared = (Path(__file__).parent.parent / "settings" / "yamllint.yaml").read_text()
    assert (repo / ".yamllint").read_text() == shared
    assert ".yamllint" in changed
    assert cli.update(repo, VERSION) == []


def test_leaves_yamllint_alone_without_the_hook(repo: Path) -> None:
    cli.update(repo, VERSION)
    assert not (repo / ".yamllint").exists()
