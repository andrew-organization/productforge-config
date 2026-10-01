"""`productforge-config init`: a repository's first declaration, its thin files, and the rename its `--from` does."""

from pathlib import Path

import pytest

from .conftest import VERSION, Stubs, run_config, tree

TEMPLATE_API = "productforge_api_template"
TEMPLATE_WEB = "productforge_web_template"


@pytest.fixture(autouse=True)
def only_the_stubs(stubs: Stubs, monkeypatch: pytest.MonkeyPatch) -> None:
    """`uv`, `dart` and `fvm` are whatever a case stubs: PATH holds nothing else, so no real one can run."""
    monkeypatch.setenv("PATH", str(stubs.bin))


def init(root: Path, *extra: str, kind: str = "api", name: str = "acme_api", slot: str = "5", version: str = VERSION):
    return run_config(
        "init", "--kind", kind, "--name", name, "--slot", slot, "--version", version, "--path", root, *extra
    )


def api_template(root: Path, old: str = TEMPLATE_API) -> None:
    """A repository shaped like the API template, made under the name `old`."""
    kebab = old.replace("_", "-")
    files = {
        "README.md": f"# {kebab}\n\nThe {kebab} API. Its images are {old}_django and {old}_celery.\n",
        "CLAUDE.md": f"# {kebab} — Claude Code Instructions\n\nSee api/{old}/settings/base.py.\n",
        "pyproject.toml": (
            f'[project]\nname = "{kebab}"\n\n[tool.pytest.ini_options]\naddopts = "--ds={old}.settings.test"\n'
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


def web_template(root: Path, old: str = TEMPLATE_WEB) -> None:
    kebab = old.replace("_", "-")
    (root / "lib").mkdir(parents=True)
    (root / "pubspec.yaml").write_text(f'name: {old}\ndescription: "{kebab} — Flutter web app"\n')
    (root / "lib" / "main.dart").write_text(f"import 'package:{old}/config/product.dart';\n")
    (root / "README.md").write_text(f"# {kebab}\n\nRun `{old}` locally.\n")


def test_init_writes_the_declaration_the_thin_files_and_the_kit(tmp_path: Path, stubs: Stubs, shipped: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\techo the old, full Makefile\n")

    result = init(tmp_path)

    assert result.returncode == 0 and result.stderr == ""
    assert result.stdout.startswith(f"productforge-config {VERSION}: initialised acme_api (api, slot 5): ")
    assert (tmp_path / "productforge.env").read_text().splitlines()[4:7] == [
        "PF_KITS=python django-api",
        "PF_SLOT=5",
        "PF_NAME=acme_api",
    ]
    assert "PF_DJANGO_PROJECT" in (tmp_path / "productforge.env").read_text()
    assert (tmp_path / ".productforge" / "django-api.mk").is_file()
    assert (tmp_path / ".productforge" / "release").read_text() == f"{VERSION}\n"
    makefile = (tmp_path / "Makefile").read_text()
    assert makefile.splitlines()[:2] == ["include productforge.env", "include $(sort $(wildcard .productforge/*.mk))"]
    assert ".PHONY: all clean test" in makefile.splitlines()
    assert "the old, full Makefile" not in makefile and "update-config" not in makefile  # common.mk carries the recipe
    ci = (tmp_path / ".github" / "workflows" / "ci.yml").read_text()
    assert f"uses: andrew-organization/productforge-config/.github/workflows/ci-api.yml@{VERSION}" in ci
    assert "pull_request:" in ci and "push:\n    branches:\n      - main" in ci
    assert "group: ci-${{ github.event.pull_request.number || github.ref }}" in ci
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in ci
    assert stubs.calls == []  # no rename: no formatting, no lock

    before = tree(tmp_path)
    assert init(tmp_path).returncode == 0
    assert tree(tmp_path) == before

    for kind in ("api", "web"):  # the fixtures' own thin files are what init writes for them
        fixture_root = tmp_path / f"fixture_{kind}"
        init(fixture_root, kind=kind, name=f"fixture_{kind}", slot="3", version="v0.9.0")
        for rel in ("Makefile", ".github/workflows/ci.yml", "productforge.env"):
            assert (fixture_root / rel).read_text() == (shipped / "tests" / f"fixture_{kind}" / rel).read_text(), (
                kind,
                rel,
            )
        # only an API's declaration offers the Django project and database overrides
        assert ("PF_DJANGO_PROJECT" in (fixture_root / "productforge.env").read_text()) == (kind == "api")

    longest = "a" * 58  # the most that leaves room for `<name>_test` within Postgres's 63 characters
    assert init(tmp_path / "long", name=longest).returncode == 0
    assert f"PF_NAME={longest}\n" in (tmp_path / "long" / "productforge.env").read_text()


@pytest.mark.parametrize(
    ("extra", "args", "message"),
    [
        ([], {"slot": "28"}, "6665"),
        ([], {"slot": "07"}, "PF_SLOT"),
        ([], {"name": "a" * 59}, "at most 58"),
        ([], {"version": "1.0"}, "isn't a valid"),
        (["--from", "Not A Name"], {}, "--from must be a name in snake_case or kebab-case"),
    ],
)
def test_init_refuses_bad_values_before_writing_anything(
    tmp_path: Path, extra: list[str], args: dict[str, str], message: str
) -> None:
    result = init(tmp_path, *extra, **args)

    assert result.returncode == 1
    assert message in result.stderr and result.stdout == ""
    assert list(tmp_path.iterdir()) == []


# ─── --from: the rename ───────────────────────────────────────────────────


def test_init_renames_every_form_of_the_template_name_and_leaves_alone_what_is_not_the_repositorys_own(
    tmp_path: Path, stubs: Stubs
) -> None:
    stubs.add("uv")
    stubs.add("dart")
    api = tmp_path / "shop"
    api_template(api)

    result = init(api, "--from", TEMPLATE_API)

    assert result.returncode == 0, result.stderr
    assert "paths renamed from productforge_api_template" in result.stdout and "README.md" not in result.stdout
    assert not (api / "api" / TEMPLATE_API).exists()
    assert (api / "api" / "acme_api" / "settings" / "base.py").is_file() and (
        api / "api" / "acme_api" / "__init__.py"
    ).is_file()
    readme = (api / "README.md").read_text()
    assert "acme-api" in readme and "acme_api_django and acme_api_celery" in readme
    assert "api/acme_api/settings/base.py" in (api / "CLAUDE.md").read_text()
    pyproject = (api / "pyproject.toml").read_text()
    assert 'name = "acme-api"' in pyproject and "--ds=acme_api.settings.test" in pyproject
    assert '"acme_api.settings.base"' in (api / "api" / "manage.py").read_text()
    assert 'ROOT_URLCONF = "acme_api.urls"' in (api / "api" / "acme_api" / "settings" / "base.py").read_text()
    assert 'Celery("acme_api")' in (api / "api" / "common" / "celery" / "app.py").read_text()
    for path in api.rglob("*"):  # no trace of either form of the old name in the repository's own files
        rel = path.relative_to(api)
        if path.is_file() and rel.parts[0] not in {".venv", "build"} and rel.name not in {"uv.lock", "logo.bin"}:
            assert TEMPLATE_API not in path.read_text() and TEMPLATE_API.replace("_", "-") not in path.read_text(), rel
            assert TEMPLATE_API not in str(rel)
    assert TEMPLATE_API in (api / ".venv" / "lib" / "site.py").read_text()
    assert TEMPLATE_API in (api / "build" / "out.txt").read_text()
    assert (api / "logo.bin").read_bytes() == b"\xff\xfe" + TEMPLATE_API.encode()
    assert TEMPLATE_API.replace("_", "-") in (api / "uv.lock").read_text()  # uv lock's to regenerate
    assert stubs.calls == ["uv lock @ shop"]  # re-locked once, nothing formatted

    kebab = tmp_path / "kebab"
    api_template(kebab)
    assert init(kebab, "--from", "productforge-api-template").returncode == 0  # the kebab-case form names it too
    assert (kebab / "api" / "acme_api").is_dir() and "acme-api" in (kebab / "README.md").read_text()

    stubs.add("uv", stderr="no solution", status=1)
    failing = tmp_path / "failing"
    api_template(failing)
    result = init(failing, "--from", TEMPLATE_API)
    assert result.returncode == 1
    assert result.stderr == "productforge-config: `uv lock` failed after the rename: no solution\n"


def test_init_renames_a_web_template_and_formats_with_fvm_else_dart_else_says_so(tmp_path: Path, stubs: Stubs) -> None:
    for tool in ("uv", "dart", "fvm"):
        stubs.add(tool)
    kebab = TEMPLATE_WEB.replace("_", "-")
    web, bare = tmp_path / "shop", tmp_path / "bare"
    web_template(web)
    (web / "pyproject.toml").write_text(f'[project]\nname = "{kebab}"\n')
    (web / "test").mkdir()
    (web / "CLAUDE.md").write_text(f"# {kebab}\n\nThis is {kebab}, from {TEMPLATE_WEB}.\n")
    web_template(bare)

    assert init(web, "--from", TEMPLATE_WEB, kind="web", name="acme_shop").returncode == 0
    assert init(bare, "--from", TEMPLATE_WEB, kind="web", name="acme_web").returncode == 0

    assert (web / "pubspec.yaml").read_text().splitlines()[0] == "name: acme_shop"
    assert "acme-shop — Flutter web app" in (web / "pubspec.yaml").read_text()
    assert "package:acme_shop/config/product.dart" in (web / "lib" / "main.dart").read_text()
    assert (web / "CLAUDE.md").read_text() == "# acme-shop\n\nThis is acme-shop, from acme_shop.\n"
    assert (web / "README.md").read_text() == "# acme-shop\n\nRun `acme_shop` locally.\n"
    assert stubs.calls == [  # fvm is preferred to dart; a repository with a pyproject is re-locked, one without is not
        "fvm dart format lib test @ shop",
        "uv lock @ shop",
        "fvm dart format lib @ bare",
    ]

    (stubs.bin / "fvm").unlink()
    with_dart = tmp_path / "with_dart"
    web_template(with_dart)
    init(with_dart, "--from", TEMPLATE_WEB, kind="web", name="acme_web")
    assert stubs.calls[-1] == "dart format lib @ with_dart"

    (stubs.bin / "dart").unlink()
    without = tmp_path / "without"
    web_template(without)
    result = init(without, "--from", TEMPLATE_WEB, kind="web", name="acme_web")
    assert result.returncode == 0
    assert "dart not found: run `dart format lib test` before `make lint`" in result.stdout

    stubs.add("dart", stderr="syntax error", status=65)
    failing = tmp_path / "failing"
    web_template(failing)
    result = init(failing, "--from", TEMPLATE_WEB, kind="web", name="acme_web")
    assert result.returncode == 1
    assert result.stderr == "productforge-config: `dart format` failed after the rename: syntax error\n"


def test_the_rename_handles_a_name_without_a_separator_one_containing_the_old_one_the_same_name_and_a_taken_directory(
    tmp_path: Path, stubs: Stubs
) -> None:
    stubs.add("uv")
    stubs.add("dart")
    plain = tmp_path / "plain"
    (plain / "lib").mkdir(parents=True)
    (plain / "api" / "app").mkdir(parents=True)
    (plain / "pubspec.yaml").write_text("name: app\ndescription: A web app named app\n")
    (plain / "lib" / "main.dart").write_text("import 'package:app/main.dart';\n// an app\n")
    init(plain, "--from", "app", kind="web", name="acme_web")  # only where it is unambiguous
    assert (plain / "pubspec.yaml").read_text() == "name: acme_web\ndescription: A web app named app\n"
    assert (plain / "lib" / "main.dart").read_text() == "import 'package:acme_web/main.dart';\n// an app\n"
    assert (plain / "api" / "acme_web").is_dir()

    contained = tmp_path / "contained"
    contained.mkdir()
    (contained / "a.txt").write_text("acme_api and acme-api\n")
    init(contained, "--from", "acme_api", name="acme_api_v2")  # renamed once, not again inside the new name
    assert (contained / "a.txt").read_text() == "acme_api_v2 and acme-api-v2\n"

    same = tmp_path / "same"
    api_template(same)
    readme = (same / "README.md").read_text()
    result = init(same, "--from", TEMPLATE_API, name=TEMPLATE_API)
    assert result.returncode == 0 and "0 paths renamed" in result.stdout
    assert (same / "README.md").read_text() == readme and (same / "api" / TEMPLATE_API).is_dir()

    taken = tmp_path / "taken"
    api_template(taken)
    (taken / "api" / "acme_api").mkdir()
    result = init(taken, "--from", TEMPLATE_API)
    assert result.returncode == 1
    assert "can't move api/productforge_api_template/ to api/acme_api/: the latter already exists" in result.stderr
