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
    assert 'requires-python = ">=3.14,<4.0"' in text
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
        ".github/workflows/*.yml",
        ".markdownlint-cli2.jsonc",
        "pyproject.toml",
        "setup.cfg",
        ".editorconfig",
        ".yamllint",
        ".productforge/common.mk",
        ".productforge/python.mk",
        ".productforge/pre-commit.yaml",
        ".productforge/release",
        ".productforge/manifest",
    }


def test_pins_the_hooks_to_the_release(repo: Path) -> None:
    cli.update(repo, VERSION)
    text = (repo / ".productforge" / "pre-commit.yaml").read_text()
    assert f"    rev: {VERSION}\n" in text
    assert (repo / ".productforge" / "release").read_text() == f"{VERSION}\n"


def test_main_runs_end_to_end(repo: Path) -> None:
    assert cli.main(["update", "--version", VERSION, "--path", str(repo)]) == 0
    assert f"rev: {VERSION}" in (repo / ".productforge" / "pre-commit.yaml").read_text()


def test_writes_the_shared_yamllint_and_editorconfig_whole_with_the_generated_header(repo: Path) -> None:
    (repo / ".yamllint").write_text("extends: relaxed\n")
    cli.update(repo, VERSION)
    settings = Path(__file__).parent.parent / "settings"
    assert (repo / ".yamllint").read_text().endswith((settings / "yamllint.yaml").read_text())
    assert (repo / ".editorconfig").read_text().endswith((settings / "editorconfig").read_text())
    assert "Generated: change it in productforge-config" in (repo / ".yamllint").read_text()


def test_leaves_pyproject_and_setup_cfg_alone_without_the_python_kit(repo: Path) -> None:
    (repo / "productforge.env").write_text("PF_KITS=\n")
    before_pyproject = (repo / "pyproject.toml").read_text()
    before_setup_cfg = (repo / "setup.cfg").read_text()
    changed = cli.update(repo, VERSION)
    assert (repo / "pyproject.toml").read_text() == before_pyproject
    assert (repo / "setup.cfg").read_text() == before_setup_cfg
    assert not {"pyproject.toml", "setup.cfg"} & set(changed)
    assert not (repo / ".productforge" / "python.mk").exists()


def test_a_repository_with_no_python_files_gets_a_flake8_file_and_no_pyproject(tmp_path: Path) -> None:
    repo = tmp_path / "no_python_repo"
    repo.mkdir()
    (repo / "productforge.env").write_text("PF_KITS=python\n")
    changed = cli.update(repo, VERSION)
    assert not (repo / "pyproject.toml").exists()
    assert (repo / "setup.cfg").read_text().startswith("[flake8]\n")
    assert "setup.cfg" in changed


def test_a_repository_with_no_declaration_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["update", "--version", VERSION, "--path", str(tmp_path)]) == 1
    assert "PF_KITS" in capsys.readouterr().err
    assert not (tmp_path / ".productforge").exists()


def test_running_it_again_changes_nothing(repo: Path) -> None:
    cli.update(repo, VERSION)
    assert cli.update(repo, VERSION) == []


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
    for version in ("v1.2.3", "v1.2.3-rc.4", "a" * 40, "0123456789abcdef0123456789abcdef01234567"):
        assert cli.resolve_version(version) == version


def test_resolve_version_rejects_invalid_shapes() -> None:
    for version in (
        "1.2.3",
        "v1",
        "v1.2",
        "v1-rc.4",
        "v1.2.3.4",
        "va.b.c",
        "v1.2.3-beta.1",
        "",
        "a" * 39,
        "a" * 41,
        "A" * 40,
        "g" * 40,
    ):
        with pytest.raises(cli.InvalidVersion):
            cli.resolve_version(version)


def test_resolve_version_none_or_latest_resolves_to_newest_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_latest_release_tag", lambda: "v2.4.0")
    assert cli.resolve_version(None) == "v2.4.0"
    assert cli.resolve_version("latest") == "v2.4.0"


def test_resolve_version_never_writes_on_an_invalid_explicit_version(repo: Path) -> None:
    with pytest.raises(cli.InvalidVersion):
        cli.resolve_version("not-a-version")
    assert not (repo / ".productforge").exists()


def test_main_resolves_latest_when_version_omitted(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_latest_release_tag", lambda: "v3.0.0")
    exit_code = cli.main(["update", "--path", str(repo)])
    assert exit_code == 0
    assert "rev: v3.0.0" in (repo / ".productforge" / "pre-commit.yaml").read_text()


def test_main_refuses_an_invalid_version_before_writing_anything(repo: Path) -> None:
    exit_code = cli.main(["update", "--version", "not-a-version", "--path", str(repo)])
    assert exit_code == 1
    assert not (repo / ".productforge").exists()


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


def test_a_failing_ls_remote_is_a_clean_error_not_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(*args: object, **kwargs: object) -> None:
        raise cli.subprocess.CalledProcessError(128, "git", stderr="fatal: unable to access\n")

    monkeypatch.setattr(cli.subprocess, "run", offline)
    with pytest.raises(cli.InvalidVersion, match="unable to access"):
        cli.resolve_version(None)


def test_a_missing_git_is_a_clean_error_too(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(cli.subprocess, "run", no_git)
    with pytest.raises(cli.InvalidVersion, match="couldn't list the release tags"):
        cli.resolve_version("latest")


@pytest.mark.parametrize(("extra", "code"), [(["--check", "--version", "latest"], 2), ([], 1)])
def test_main_exits_cleanly_when_the_latest_tag_cannot_be_resolved(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], extra: list[str], code: int
) -> None:
    def offline(*args: object, **kwargs: object) -> None:
        raise cli.subprocess.CalledProcessError(128, "git", stderr="offline")

    monkeypatch.setattr(cli.subprocess, "run", offline)
    assert cli.main(["update", "--path", str(repo), *extra]) == code
    assert "offline" in capsys.readouterr().err
