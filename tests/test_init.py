"""Tests of `productforge-config init`, and of the rename its `--from` does."""

import subprocess
from pathlib import Path

import pytest

from productforge_config import cli, kits, rename, scaffold

from .conftest import TESTS, VERSION

# scaffold.run_uv_lock as it is before the autouse fixture below replaces it.
REAL_RUN_UV_LOCK = scaffold.run_uv_lock
REAL_FORMAT_DART = scaffold.format_dart
TEMPLATE_API = "productforge_api_template"
TEMPLATE_WEB = "productforge_web_template"


@pytest.fixture(autouse=True)
def no_dart_format(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Whatever dart is on this machine, formatting is recorded, not run."""
    calls: list[Path] = []
    monkeypatch.setattr(scaffold, "format_dart", lambda root: calls.append(root))
    return calls


@pytest.fixture(autouse=True)
def no_uv_lock(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """`uv lock` needs a network and a real project: record its calls instead of making them."""
    calls: list[Path] = []
    monkeypatch.setattr(scaffold, "run_uv_lock", calls.append)
    return calls


def _init(repo: Path, *extra: str, kind: str = "api", name: str = "acme_api", slot: str = "5") -> int:
    return cli.main(
        ["init", "--kind", kind, "--name", name, "--slot", slot, "--version", VERSION, "--path", str(repo), *extra]
    )


def test_writes_the_three_files_and_installs_the_kit(tmp_path: Path) -> None:
    assert _init(tmp_path) == 0
    assert (tmp_path / "productforge.env").read_text().splitlines()[3:6] == [
        "PF_KIND=api",
        "PF_SLOT=5",
        "PF_NAME=acme_api",
    ]
    assert (tmp_path / ".productforge" / "api.mk").is_file()
    assert (tmp_path / ".productforge" / "manifest").is_file()
    ci = (tmp_path / ".github" / "workflows" / "ci.yml").read_text()
    assert f"uses: andrew-organization/productforge-config/.github/workflows/ci-api.yml@{VERSION}" in ci
    assert "pull_request:" in ci
    assert "push:\n    branches:\n      - main" in ci
    assert "group: ci-${{ github.event.pull_request.number || github.ref }}" in ci
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in ci


def test_the_read_back_values_are_the_ones_given(tmp_path: Path) -> None:
    _init(tmp_path, kind="web", name="acme_web", slot="12")
    env = kits.read_env(tmp_path)
    assert env is not None
    assert (env.kind, env.slot, env.name) == ("web", 12, "acme_web")


def test_the_thin_makefile_includes_the_kit_and_keeps_update_config(tmp_path: Path) -> None:
    _init(tmp_path, kind="web", name="acme_web")
    makefile = (tmp_path / "Makefile").read_text()
    lines = makefile.splitlines()
    assert lines[:3] == ["include productforge.env", "include .productforge/common.mk", "include .productforge/web.mk"]
    assert ".PHONY: all clean test update-config" in lines
    assert "update-config:" in lines
    assert "uvx --from git+https://github.com/andrew-organization/productforge-config@$$version" in makefile


@pytest.mark.parametrize("kind", ["api", "web"])
def test_the_thin_makefile_passes_checkmake(kind: str, tmp_path: Path) -> None:
    from .test_hooks import _try_hook

    _init(tmp_path, kind=kind, name=f"acme_{kind}")
    result = _try_hook("checkmake", tmp_path / "Makefile")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["api", "web"])
def test_the_fixtures_thin_files_are_what_init_writes(kind: str) -> None:
    env = kits.validate(kind, 3, f"fixture_{kind}")
    fixture = TESTS / f"fixture_{kind}"
    assert (fixture / "Makefile").read_text() == scaffold.makefile_text(env)
    assert (fixture / ".github" / "workflows" / "ci.yml").read_text() == scaffold.ci_text(env, "v0.9.0")


def test_running_it_twice_changes_nothing(tmp_path: Path) -> None:
    _init(tmp_path)
    before = {p: p.read_text() for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    assert _init(tmp_path) == 0
    assert {p: p.read_text() for p in sorted(tmp_path.rglob("*")) if p.is_file()} == before


def test_replaces_a_full_makefile_with_the_thin_one(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\techo the old, full Makefile\n")
    _init(tmp_path)
    assert "the old, full Makefile" not in (tmp_path / "Makefile").read_text()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"slot": "28"}, "6665"),
        ({"slot": "07"}, "PF_SLOT"),
        ({"name": "Acme-API"}, "PF_NAME"),
        ({"name": "9api"}, "PF_NAME"),
    ],
)
def test_refuses_bad_values_before_writing_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], args: dict[str, str], message: str
) -> None:
    assert _init(tmp_path, **args) == 1
    assert message in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_refuses_an_invalid_version_before_writing_anything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        ["init", "--kind", "api", "--name", "acme", "--slot", "0", "--version", "1.0", "--path", str(tmp_path)]
    )
    assert code == 1
    assert "isn't a valid" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_refuses_a_bad_from_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _init(tmp_path, "--from", "Not A Name") == 1
    assert "--from" in capsys.readouterr().err


# ─── --from: the rename ───────────────────────────────────────────────────


def _api_template(root: Path, old: str = TEMPLATE_API) -> None:
    """A repository shaped like the API template, made under the name `old`."""
    kebab = old.replace("_", "-")
    files = {
        "README.md": f"# {kebab}\n\nThe {kebab} API. Its images are {old}_django and {old}_celery.\n",
        "CLAUDE.md": f"# {kebab} — Claude Code Instructions\n\nSee api/{old}/settings/base.py.\n",
        "pyproject.toml": (
            f'[project]\nname = "{kebab}"\n\n' f'[tool.pytest.ini_options]\naddopts = "--ds={old}.settings.test"\n'
        ),
        "api/manage.py": f'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "{old}.settings.base")\n',
        f"api/{old}/__init__.py": "",
        f"api/{old}/settings/base.py": f'"""Base Django settings for {kebab}."""\nROOT_URLCONF = "{old}.urls"\n',
        f"api/{old}/urls.py": f'"""URLs of {old}."""\n',
        "api/common/celery/app.py": f'app = Celery("{old}")\n',
        "uv.lock": f'[[package]]\nname = "{kebab}"\n',
        ".venv/lib/site.py": f"# {old} in a virtual environment\n",
        "build/out.txt": f"{old}\n",
        "logo.bin": None,
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            path.write_bytes(b"\xff\xfe" + old.encode())
        else:
            path.write_text(text)


def test_renames_every_form_of_the_old_name_and_moves_the_django_project(tmp_path: Path) -> None:
    _api_template(tmp_path)
    assert _init(tmp_path, "--from", TEMPLATE_API, name="acme_api") == 0

    assert not (tmp_path / "api" / TEMPLATE_API).exists()
    assert (tmp_path / "api" / "acme_api" / "settings" / "base.py").is_file()
    assert (tmp_path / "api" / "acme_api" / "__init__.py").is_file()
    assert "acme-api" in (tmp_path / "README.md").read_text()
    assert "acme_api_django and acme_api_celery" in (tmp_path / "README.md").read_text()
    assert "api/acme_api/settings/base.py" in (tmp_path / "CLAUDE.md").read_text()
    assert 'name = "acme-api"' in (tmp_path / "pyproject.toml").read_text()
    assert "--ds=acme_api.settings.test" in (tmp_path / "pyproject.toml").read_text()
    assert '"acme_api.settings.base"' in (tmp_path / "api" / "manage.py").read_text()
    assert 'ROOT_URLCONF = "acme_api.urls"' in (tmp_path / "api" / "acme_api" / "settings" / "base.py").read_text()
    assert 'Celery("acme_api")' in (tmp_path / "api" / "common" / "celery" / "app.py").read_text()


def test_the_rename_leaves_no_trace_of_the_old_name(tmp_path: Path) -> None:
    _api_template(tmp_path)
    _init(tmp_path, "--from", TEMPLATE_API, name="acme_api")
    for path in tmp_path.rglob("*"):
        rel = path.relative_to(tmp_path)
        if not path.is_file() or rel.parts[0] in {".venv", "build"} or rel.name in {"uv.lock", "logo.bin"}:
            continue
        assert TEMPLATE_API not in path.read_text(), rel
        assert TEMPLATE_API.replace("_", "-") not in path.read_text(), rel
        assert TEMPLATE_API not in str(rel)


def test_the_rename_leaves_alone_what_is_not_the_repositorys_own(tmp_path: Path) -> None:
    _api_template(tmp_path)
    _init(tmp_path, "--from", TEMPLATE_API, name="acme_api")
    assert TEMPLATE_API in (tmp_path / ".venv" / "lib" / "site.py").read_text()
    assert TEMPLATE_API in (tmp_path / "build" / "out.txt").read_text()
    assert (tmp_path / "logo.bin").read_bytes() == b"\xff\xfe" + TEMPLATE_API.encode()
    assert TEMPLATE_API.replace("_", "-") in (tmp_path / "uv.lock").read_text()  # uv lock's to regenerate


def test_the_rename_accepts_the_old_name_in_kebab_case(tmp_path: Path) -> None:
    _api_template(tmp_path)
    assert _init(tmp_path, "--from", "productforge-api-template", name="acme_api") == 0
    assert (tmp_path / "api" / "acme_api").is_dir()
    assert "acme-api" in (tmp_path / "README.md").read_text()


def test_an_api_rename_regenerates_the_lock_file(tmp_path: Path, no_uv_lock: list[Path]) -> None:
    _api_template(tmp_path)
    _init(tmp_path, "--from", TEMPLATE_API, name="acme_api")
    assert no_uv_lock == [tmp_path.resolve()]


def test_a_failing_uv_lock_stops_init_with_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(root: Path) -> None:
        raise scaffold.InitError("`uv lock` failed after the rename: no network")

    _api_template(tmp_path)
    monkeypatch.setattr(scaffold, "run_uv_lock", fail)
    assert _init(tmp_path, "--from", TEMPLATE_API, name="acme_api") == 1
    assert "no network" in capsys.readouterr().err


def test_uv_lock_reports_what_it_ran_and_what_went_wrong(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> None:
        ran.append(command)
        raise subprocess.CalledProcessError(1, command, stderr="no solution\n")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(scaffold.InitError, match="no solution"):
        REAL_RUN_UV_LOCK(tmp_path)
    assert ran == [["uv", "lock"]]


def test_a_web_rename_covers_dart_imports_and_the_pubspec_name(tmp_path: Path) -> None:
    kebab = TEMPLATE_WEB.replace("_", "-")
    (tmp_path / "lib").mkdir()
    (tmp_path / "pubspec.yaml").write_text(f'name: {TEMPLATE_WEB}\ndescription: "{kebab} — Flutter web app"\n')
    (tmp_path / "lib" / "main.dart").write_text(f"import 'package:{TEMPLATE_WEB}/config/product.dart';\n")
    (tmp_path / "README.md").write_text(f"# {kebab}\n")
    assert _init(tmp_path, "--from", TEMPLATE_WEB, kind="web", name="acme_web") == 0
    assert (tmp_path / "pubspec.yaml").read_text().splitlines()[0] == "name: acme_web"
    assert "acme-web — Flutter web app" in (tmp_path / "pubspec.yaml").read_text()
    assert "package:acme_web/config/product.dart" in (tmp_path / "lib" / "main.dart").read_text()
    assert (tmp_path / "README.md").read_text() == "# acme-web\n"


def test_a_name_without_a_separator_is_renamed_only_where_it_is_unambiguous(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "api" / "app").mkdir(parents=True)
    (tmp_path / "pubspec.yaml").write_text("name: app\ndescription: A web app named app\n")
    (tmp_path / "lib" / "main.dart").write_text("import 'package:app/main.dart';\n// an app\n")
    rename.rename(tmp_path, "app", "acme_web")
    assert (tmp_path / "pubspec.yaml").read_text() == "name: acme_web\ndescription: A web app named app\n"
    assert (tmp_path / "lib" / "main.dart").read_text() == "import 'package:acme_web/main.dart';\n// an app\n"
    assert (tmp_path / "api" / "acme_web").is_dir()


def test_renaming_to_the_same_name_changes_nothing(tmp_path: Path) -> None:
    _api_template(tmp_path)
    assert rename.rename(tmp_path, TEMPLATE_API, TEMPLATE_API.replace("_", "-")) == []


def test_refuses_to_move_the_django_project_onto_an_existing_directory(tmp_path: Path) -> None:
    _api_template(tmp_path)
    (tmp_path / "api" / "acme_api").mkdir()
    with pytest.raises(rename.RenameError, match="already exists"):
        rename.rename(tmp_path, TEMPLATE_API, "acme_api")


def test_a_name_that_contains_the_old_one_is_renamed_once(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("acme_api and acme-api\n")
    rename.rename(tmp_path, "acme_api", "acme_api_v2")
    assert (tmp_path / "a.txt").read_text() == "acme_api_v2 and acme-api-v2\n"


def test_init_reports_a_rename_as_one_line_not_one_per_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _api_template(tmp_path)
    _init(tmp_path, "--from", TEMPLATE_API, name="acme_api")
    out = capsys.readouterr().out
    assert "paths renamed from productforge_api_template" in out
    assert "README.md" not in out


@pytest.mark.parametrize(
    ("kind", "template"),
    [("api", TEMPLATE_API), ("web", TEMPLATE_WEB)],
)
def test_a_products_copy_names_the_product_and_nothing_names_the_template(
    tmp_path: Path, kind: str, template: str
) -> None:
    kebab = template.replace("_", "-")
    (tmp_path / "CLAUDE.md").write_text(f"# {kebab}\n\nThis is {kebab}, built from {template}.\n")
    (tmp_path / "README.md").write_text(f"# {kebab}\n\nRun `{template}` locally.\n")
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "{kebab}"\n')
    assert _init(tmp_path, "--from", template, kind=kind, name="acme_shop") == 0
    for path in sorted(tmp_path.rglob("*")):
        rel = path.relative_to(tmp_path)
        if path.is_file() and rel.parts[0] != ".git":
            text = path.read_text()
            assert template not in text and kebab not in text, rel
    assert (tmp_path / "CLAUDE.md").read_text() == "# acme-shop\n\nThis is acme-shop, built from acme_shop.\n"
    assert 'name = "acme-shop"' in (tmp_path / "pyproject.toml").read_text()


def test_a_web_rename_re_locks_a_pyproject_too(tmp_path: Path, no_uv_lock: list[Path]) -> None:
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "{TEMPLATE_WEB.replace("_", "-")}"\n')
    _init(tmp_path, "--from", TEMPLATE_WEB, kind="web", name="acme_web")
    assert no_uv_lock == [tmp_path.resolve()]
    no_uv_lock.clear()
    empty = tmp_path / "other"
    empty.mkdir()
    _init(empty, "--from", TEMPLATE_WEB, kind="web", name="acme_web")
    assert no_uv_lock == []


def _stub_tools(bin_dir: Path, *names: str) -> Path:
    """Executables that log their arguments and their directory, standing in for fvm and dart."""
    bin_dir.mkdir(exist_ok=True)
    log = bin_dir / "log"
    for name in names:
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\necho "{name} $* @ $(basename "$PWD")" >> "{log}"\n')
        tool.chmod(0o755)
    return log


def test_a_web_rename_formats_lib_and_test_through_dart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "shop"
    (repo / "lib").mkdir(parents=True)
    (repo / "test").mkdir()
    log = _stub_tools(tmp_path / "bin", "dart")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    assert REAL_FORMAT_DART(repo) is None
    assert log.read_text().splitlines() == ["dart format lib test @ shop"]


def test_a_web_rename_prefers_fvm_when_it_is_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "shop"
    (repo / "lib").mkdir(parents=True)
    log = _stub_tools(tmp_path / "bin", "dart", "fvm")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    assert REAL_FORMAT_DART(repo) is None
    assert log.read_text().splitlines() == ["fvm dart format lib @ shop"]  # only the directories there are


def test_a_web_rename_says_so_when_there_is_no_dart_to_format_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "bin").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    note = REAL_FORMAT_DART(tmp_path)
    assert note is not None and "dart format lib test" in note


def test_init_formats_only_a_web_rename(tmp_path: Path, no_dart_format: list[Path]) -> None:
    _api_template(tmp_path / "api")
    _init(tmp_path / "api", "--from", TEMPLATE_API, name="acme_api")
    assert no_dart_format == []
    (tmp_path / "web").mkdir()
    _init(tmp_path / "web", "--from", TEMPLATE_WEB, kind="web", name="acme_web")
    assert no_dart_format == [(tmp_path / "web").resolve()]
    no_dart_format.clear()
    _init(tmp_path / "web", kind="web", name="acme_web")  # no rename, nothing to format
    assert no_dart_format == []


def test_a_failing_dart_format_stops_init_with_its_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "bin").mkdir()
    tool = tmp_path / "bin" / "dart"
    tool.write_text("#!/bin/sh\necho 'syntax error' >&2\nexit 65\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    with pytest.raises(scaffold.InitError, match="syntax error"):
        REAL_FORMAT_DART(tmp_path)


# ─── Names ────────────────────────────────────────────────────────────────


def test_a_name_leaves_room_for_the_test_database_suffix() -> None:
    longest = "a" * kits.MAX_NAME_LENGTH
    assert kits.validate("api", 0, longest).name == longest
    assert len(f"{longest}_test") <= 63
    with pytest.raises(kits.EnvError, match="at most 58 characters"):
        kits.validate("api", 0, longest + "a")


def test_init_refuses_a_name_too_long_for_postgres(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _init(tmp_path, name="a" * 59) == 1
    assert "at most 58" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
