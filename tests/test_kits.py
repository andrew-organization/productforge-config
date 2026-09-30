"""Tests of what `update` does with a repository that has a productforge.env: the kit it installs,
what goes with it, and `update --check`. Each test works on a throwaway copy of a fixture.
"""

import json
from pathlib import Path

import pytest

from productforge_config import cli, kits

from .conftest import VERSION

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


def _installed(repo: Path) -> set[str]:
    return {str(p.relative_to(repo)) for p in (repo / ".productforge").rglob("*") if p.is_file()} - {
        ".productforge/manifest"
    }


def _tree(repo: Path) -> dict[str, str]:
    return {str(p.relative_to(repo)): p.read_text() for p in sorted(repo.rglob("*")) if p.is_file()}


def test_installs_the_api_kit_whole(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    assert _installed(api_repo) | {".dockerignore"} == API_KIT
    assert (api_repo / ".dockerignore").is_file()
    assert "checkmake" not in (api_repo / ".productforge" / "common.mk").read_text()


def test_installs_the_web_kit_whole(web_repo: Path) -> None:
    cli.update(web_repo, VERSION)
    assert _installed(web_repo) == WEB_KIT
    assert not (web_repo / ".dockerignore").exists()


def test_a_kind_installs_nothing_of_the_others(api_repo: Path, web_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    cli.update(web_repo, VERSION)
    assert not (api_repo / ".productforge" / "web.mk").exists()
    assert not (web_repo / ".productforge" / "django-api.mk").exists()
    assert not (web_repo / ".productforge" / "compose.yml").exists()
    assert not (web_repo / ".productforge" / "python.mk").exists()


@pytest.mark.parametrize("fixture", ["api_repo", "web_repo"])
def test_every_installed_file_carries_the_generated_header(fixture: str, request: pytest.FixtureRequest) -> None:
    repo: Path = request.getfixturevalue(fixture)
    cli.update(repo, VERSION)
    for rel in [*_installed(repo), ".dockerignore", ".yamllint", ".editorconfig", ".pre-commit-config.yaml"]:
        path = repo / rel
        if not path.exists() or rel == ".productforge/release":  # the release record is one bare line
            continue
        head = "\n".join(path.read_text().splitlines()[:6])
        assert "Generated: change it in productforge-config" in head, rel


def test_the_header_follows_a_shebang(web_repo: Path) -> None:
    cli.update(web_repo, VERSION)
    lines = (web_repo / ".productforge" / "generate_identity.py").read_text().splitlines()
    assert lines[0] == "#!/usr/bin/env python3"
    assert lines[1].startswith("# Installed by productforge-config")


def test_the_claude_fragment_joins_the_common_and_the_kinds_own(api_repo: Path, web_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    cli.update(web_repo, VERSION)
    api = (api_repo / ".productforge" / "CLAUDE.md").read_text()
    web = (web_repo / ".productforge" / "CLAUDE.md").read_text()
    for text in (api, web):
        assert "## Ports" in text and "## CI" in text
    assert "## API targets" in api and "## Web targets" not in api
    assert "## Web targets" in web and "## API targets" not in web


def test_writes_the_settings_that_go_at_fixed_paths(web_repo: Path, api_repo: Path) -> None:
    cli.update(web_repo, VERSION)
    cli.update(api_repo, VERSION)
    assert json.loads((web_repo / ".fvmrc").read_text()) == {"flutter": "3.44.7"}
    assert "make check-generated" in (web_repo / ".pre-commit-config.yaml").read_text()
    assert "make check-migrations" in (api_repo / ".pre-commit-config.yaml").read_text()
    assert "make check-generated" not in (api_repo / ".pre-commit-config.yaml").read_text()
    assert not (api_repo / ".fvmrc").exists()


def test_syncs_the_pubspecs_sdk_keeping_its_comment(web_repo: Path) -> None:
    cli.update(web_repo, VERSION)
    text = (web_repo / "pubspec.yaml").read_text()
    assert "  sdk: ^3.12.2  # the Dart SDK" in text
    assert "name: fixture_web" in text
    assert "    sdk: flutter" in text  # a dependency's sdk key, not the environment's


def test_leaves_a_pubspec_without_an_environment_alone(web_repo: Path) -> None:
    (web_repo / "pubspec.yaml").write_text("name: fixture_web\n")
    cli.update(web_repo, VERSION)
    assert (web_repo / "pubspec.yaml").read_text() == "name: fixture_web\n"


def test_adds_the_mypy_strictness_to_pyproject_where_django_mypy_is_taken(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    text = (api_repo / "pyproject.toml").read_text()
    assert 'python_version = "3.14"' in text
    assert "disallow_any_explicit = true" in text
    assert "warn_return_any = true" in text
    assert 'follow_imports = "silent"' in text
    assert 'mypy_path = "api"' in text  # the repository's own key, untouched
    assert 'plugins = ["mypy_django_plugin.main"]' in text


def test_leaves_mypy_alone_without_the_django_api_kit(api_repo: Path) -> None:
    (api_repo / "productforge.env").write_text("PF_KITS=python\n")
    cli.update(api_repo, VERSION)
    assert 'python_version = "3.12"' in (api_repo / "pyproject.toml").read_text()


def test_running_it_again_changes_nothing(api_repo: Path, web_repo: Path) -> None:
    for repo in (api_repo, web_repo):
        cli.update(repo, VERSION)
        before = _tree(repo)
        assert cli.update(repo, VERSION) == []
        assert _tree(repo) == before


def test_reports_the_kits_files(api_repo: Path) -> None:
    changed = cli.update(api_repo, VERSION)
    assert API_KIT <= set(changed)
    assert ".productforge/manifest" in changed
    assert ".pre-commit-config.yaml" in changed


# ─── Stale files ──────────────────────────────────────────────────────────


def test_removes_a_file_a_previous_kit_installed_that_this_one_does_not(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    (api_repo / ".productforge" / "gone.mk").write_text("# a file an older kit carried\n")
    (api_repo / ".productforge" / "old").mkdir()
    (api_repo / ".productforge" / "old" / "script.py").write_text("# and another, in a directory\n")
    manifest = api_repo / ".productforge" / "manifest"
    manifest.write_text(manifest.read_text() + ".productforge/gone.mk\n.productforge/old/script.py\n")

    changed = cli.update(api_repo, VERSION)

    assert not (api_repo / ".productforge" / "gone.mk").exists()
    assert not (api_repo / ".productforge" / "old").exists()
    assert {".productforge/gone.mk", ".productforge/old/script.py"} <= set(changed)
    assert ".productforge/gone.mk" not in manifest.read_text()


def test_leaves_a_file_no_manifest_lists(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    (api_repo / ".productforge" / "mine.txt").write_text("not from a kit\n")
    cli.update(api_repo, VERSION)
    assert (api_repo / ".productforge" / "mine.txt").exists()


def test_switching_kits_removes_the_old_kits_files(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    env = api_repo / "productforge.env"
    env.write_text(env.read_text().replace("PF_KITS=python django-api", "PF_KITS=flutter-web"))
    cli.update(api_repo, VERSION)
    assert _installed(api_repo) == WEB_KIT
    assert not (api_repo / ".dockerignore").exists()


def test_a_manifest_cannot_reach_outside_the_repository(api_repo: Path, tmp_path: Path) -> None:
    cli.update(api_repo, VERSION)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n")
    manifest = api_repo / ".productforge" / "manifest"
    manifest.write_text(manifest.read_text() + "../outside.txt\n")
    cli.update(api_repo, VERSION)
    assert outside.read_text() == "keep me\n"


# ─── update --check ───────────────────────────────────────────────────────


def test_check_changes_nothing_and_reports_the_drift(api_repo: Path) -> None:
    before = _tree(api_repo)
    changed = cli.update(api_repo, VERSION, check=True)
    assert _tree(api_repo) == before
    assert API_KIT <= set(changed)


def test_check_exits_1_on_drift_and_0_without(api_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["update", "--version", VERSION, "--path", str(api_repo), "--check"]
    assert cli.main(args) == 1
    assert "drift in" in capsys.readouterr().out
    assert cli.main(["update", "--version", VERSION, "--path", str(api_repo)]) == 0
    assert cli.main(args) == 0
    assert "no drift" in capsys.readouterr().out


def test_check_finds_an_installed_file_edited_by_hand(web_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.update(web_repo, VERSION)
    mk = web_repo / ".productforge" / "flutter-web.mk"
    mk.write_text(mk.read_text() + "\nlocal-tweak:\n")
    assert cli.main(["update", "--version", VERSION, "--path", str(web_repo), "--check"]) == 1
    assert ".productforge/flutter-web.mk" in capsys.readouterr().out
    assert "local-tweak" in mk.read_text()


def test_check_finds_a_stale_file_it_would_remove(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    stale = api_repo / ".productforge" / "gone.mk"
    stale.write_text("# stale\n")
    manifest = api_repo / ".productforge" / "manifest"
    manifest.write_text(manifest.read_text() + ".productforge/gone.mk\n")
    assert ".productforge/gone.mk" in cli.update(api_repo, VERSION, check=True)
    assert stale.exists()


def test_check_finds_a_workflow_ref_behind(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    ci = api_repo / ".github" / "workflows" / "ci.yml"
    ci.write_text(ci.read_text().replace(VERSION, "v0.9.0"))
    assert cli.update(api_repo, VERSION, check=True) == [".github/workflows/*.yml"]


# ─── productforge.env ─────────────────────────────────────────────────────


def test_parses_a_dotenv_file() -> None:
    values = kits.parse_dotenv(
        "# a comment\n\nPF_KITS=python\nexport PF_SLOT = 3 # the slot\nPF_NAME=\"quoted_name\"\nnot a pair\nPF_X='y'\n"
    )
    assert values == {"PF_KITS": "python", "PF_SLOT": "3", "PF_NAME": "quoted_name", "PF_X": "y"}


def test_the_optional_overrides_default_to_the_name(api_repo: Path) -> None:
    decl = kits.read_env(api_repo)
    assert decl is not None
    assert (decl.kits, decl.kind, decl.slot, decl.name) == (("python", "django-api"), "api", 3, "fixture_api")
    assert decl.django_project == decl.postgres_db == "fixture_api"
    (api_repo / "productforge.env").write_text(
        "PF_KITS=python django-api\nPF_SLOT=0\nPF_NAME=fixture_api\nPF_DJANGO_PROJECT=core\nPF_POSTGRES_DB=fixture_db\n"
    )
    decl = kits.read_env(api_repo)
    assert decl is not None
    assert (decl.django_project, decl.postgres_db) == ("core", "fixture_db")


def test_the_main_reports_a_bad_productforge_env_without_writing(
    api_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (api_repo / "productforge.env").write_text("PF_KITS=python django-api\nPF_SLOT=28\nPF_NAME=demo\n")
    assert cli.main(["update", "--version", VERSION, "--path", str(api_repo)]) == 1
    assert "6665" in capsys.readouterr().err
    assert not (api_repo / ".productforge").exists()


# ─── Workflow refs ────────────────────────────────────────────────────────


def test_moves_the_reusable_ci_workflows_ref(api_repo: Path) -> None:
    ci = api_repo / ".github" / "workflows" / "ci.yml"
    assert "productforge-config/.github/workflows/ci-api.yml@v0.9.0" in ci.read_text()
    cli.update(api_repo, VERSION)
    assert f"productforge-config/.github/workflows/ci-api.yml@{VERSION}" in ci.read_text()
    assert "v0.9.0" not in ci.read_text()


def test_moves_any_action_or_workflow_of_this_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "productforge.env").write_text("PF_KITS=\n")
    (repo / ".github" / "workflows" / "x.yml").write_text(
        "a: {uses: andrew-organization/productforge-config/actions/other@v0.1.0}\n"
        "b: andrew-organization/productforge-config/.github/workflows/ci-web.yml@v0.1.0\n"
        "uses: 'andrew-organization/productforge-config/.github/workflows/future.yml@main'  # x\n"
        "uses: someone-else/productforge-config/actions/setup@v0.1.0\n"
        "uses: andrew-organization/productforge-config/README@v0.1.0\n"
    )
    cli.update(repo, VERSION)
    text = (repo / ".github" / "workflows" / "x.yml").read_text().splitlines()
    assert text[0] == "a: {uses: andrew-organization/productforge-config/actions/other@v1.2.3}"
    assert text[1] == "b: andrew-organization/productforge-config/.github/workflows/ci-web.yml@v0.1.0"  # no `uses:`
    assert text[2] == "uses: 'andrew-organization/productforge-config/.github/workflows/future.yml@v1.2.3'  # x"
    assert "@v0.1.0" in text[3] and "@v0.1.0" in text[4]


SHA = "0123456789abcdef0123456789abcdef01234567"


def test_pins_hooks_and_workflow_refs_to_a_commit_sha(api_repo: Path) -> None:
    assert cli.main(["update", "--version", SHA, "--path", str(api_repo)]) == 0
    assert f"rev: {SHA}" in (api_repo / ".productforge" / "pre-commit.yaml").read_text()
    assert f"ci-api.yml@{SHA}" in (api_repo / ".github" / "workflows" / "ci.yml").read_text()


def test_init_accepts_a_commit_sha(tmp_path: Path) -> None:
    code = cli.main(
        ["init", "--kind", "web", "--name", "acme_web", "--slot", "1", "--version", SHA, "--path", str(tmp_path)]
    )
    assert code == 0
    assert f"ci-web.yml@{SHA}" in (tmp_path / ".github" / "workflows" / "ci.yml").read_text()


def test_the_kits_name_no_product_of_the_pipeline() -> None:
    from productforge_config._bundled import bundled_kits_dir

    for path in bundled_kits_dir().rglob("*"):
        if path.is_file():
            assert "the stack" not in path.read_text().lower(), path


def test_check_exits_2_on_an_error_and_1_on_drift(api_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["update", "--version", "nope", "--path", str(api_repo), "--check"]) == 2
    (api_repo / "productforge.env").write_text("PF_KITS=python django-api\nPF_SLOT=28\nPF_NAME=demo\n")
    assert cli.main(["update", "--version", VERSION, "--path", str(api_repo), "--check"]) == 2
    assert "6665" in capsys.readouterr().err
    (api_repo / "productforge.env").write_text("PF_KITS=python django-api\nPF_SLOT=1\nPF_NAME=demo\n")
    assert cli.main(["update", "--version", VERSION, "--path", str(api_repo), "--check"]) == 1


def test_the_claude_fragments_join_without_leading_blank_lines(api_repo: Path) -> None:
    cli.update(api_repo, VERSION)
    text = (api_repo / ".productforge" / "CLAUDE.md").read_text()
    assert "\n\n\n" not in text
    assert "\n\n## API targets" in text


def test_the_claude_fragments_point_at_make_ports_and_document_the_env_overrides(
    api_repo: Path, web_repo: Path
) -> None:
    cli.update(api_repo, VERSION)
    cli.update(web_repo, VERSION)
    api = (api_repo / ".productforge" / "CLAUDE.md").read_text()
    for name in ("DJANGO_ALLOWED_HOSTS", "CORS_EXTRA_ORIGINS", "FRONTEND_BASE_URL"):
        assert name in api
    for text in (api, (web_repo / ".productforge" / "CLAUDE.md").read_text()):
        assert "`make ports`" in text
        assert "6100" not in text.replace("6100 + 20", "")


def test_only_an_api_env_file_offers_the_django_and_database_overrides(tmp_path: Path) -> None:
    assert "PF_DJANGO_PROJECT" in kits.env_text(kits.for_kind("api", 0, "demo"))
    assert "PF_DJANGO_PROJECT" not in kits.env_text(kits.for_kind("web", 0, "demo"))


@pytest.mark.parametrize("fixture", ["api_repo", "web_repo"])
def test_no_installed_kit_file_speaks_of_a_template(fixture: str, request: pytest.FixtureRequest) -> None:
    """A kit lands in every product, so it says only what holds in any repository."""
    repo: Path = request.getfixturevalue(fixture)
    cli.update(repo, VERSION)
    decl = kits.read_env(repo)
    assert decl is not None
    files = [repo / rel for rel in kits.kit_files(decl, VERSION)]
    assert files
    for path in files:
        assert "template" not in path.read_text().lower(), path.relative_to(repo)
