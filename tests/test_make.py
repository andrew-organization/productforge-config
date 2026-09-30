"""Tests of the kits as a repository runs them: `make -n` against the fixture's thin Makefile,
Docker Compose interpolation of the compose files, and the web kit's identity scripts.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from productforge_config import cli, ports

from .conftest import VERSION, run_make

# The fixtures are at slot 3: 6160-6179.
API, POSTGRES, REDIS, FLOWER, SMTP, UI, WEB, TEST_POSTGRES = 6160, 6161, 6162, 6163, 6164, 6165, 6166, 6171

needs_docker = pytest.mark.skipif(
    shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode,
    reason="docker compose isn't available",
)


@pytest.fixture
def api(api_repo: Path) -> Path:
    cli.update(api_repo, VERSION)
    return api_repo


@pytest.fixture
def web(web_repo: Path) -> Path:
    cli.update(web_repo, VERSION)
    return web_repo


def _make(repo: Path, *args: str) -> str:
    result = run_make(repo, "-n", *args)
    assert result.returncode == 0, result.stderr
    return result.stdout


# ─── api.mk ───────────────────────────────────────────────────────────────


def test_make_targets_the_api_kit_names_all_run(api: Path) -> None:
    for target in (
        "install",
        "lint",
        "setup-hooks",
        "clean",
        "test",
        "test-integration",
        "test-integration-ci",
        "check-migrations",
        "build",
        "up",
        "down",
        "logs",
        "shell",
        "lock",
        "all",
        "ports",
    ):
        _make(api, target)


def test_test_runs_the_unit_tests_and_takes_a_path_and_a_keyword(api: Path) -> None:
    assert 'uv run pytest -n auto api/tests -v -m "not django_db"' in _make(api, "test")
    assert 'uv run pytest -n auto api/x.py -v -m "not django_db" -k "y"' in _make(api, "test", "path=api/x.py", "k=y")


def test_integration_tests_use_the_slots_test_postgres_and_the_projects_test_database(api: Path) -> None:
    out = _make(api, "test-integration")
    assert "-f .productforge/compose.test.yml --project-directory . up -d --wait --remove-orphans postgres-test" in out
    assert (
        f"POSTGRES_HOST=localhost POSTGRES_PORT={TEST_POSTGRES} POSTGRES_DB=fixture_api_test uv run pytest -n auto"
        in out
    )
    assert "down --remove-orphans" in out  # and it removes the database after, whatever the tests did
    assert "exit $status" in out


def test_the_ci_integration_target_starts_the_test_database_and_leaves_it(api: Path) -> None:
    out = _make(api, "test-integration-ci")
    assert "up -d --wait --remove-orphans postgres-test" in out
    assert "pytest api/tests -m django_db -v --create-db" in out
    assert "down" not in out


def test_check_migrations_uses_the_django_project_from_the_env(api: Path) -> None:
    out = _make(api, "check-migrations")
    assert "--settings=fixture_api.settings.test" in out
    (api / "productforge.env").write_text(
        (api / "productforge.env").read_text() + "PF_DJANGO_PROJECT=core\nPF_POSTGRES_DB=shop\n"
    )
    assert "--settings=core.settings.test" in _make(api, "check-migrations")
    assert "POSTGRES_DB=shop_test" in _make(api, "test-integration")


def test_the_docker_targets_run_docker_compose_v2_with_the_kits_file(api: Path) -> None:
    prefix = "docker compose --env-file productforge.env -f .productforge/compose.yml --project-directory ."
    assert f"{prefix} build" in _make(api, "build")
    assert f"{prefix} up -d --remove-orphans" in _make(api, "up")
    assert f"{prefix} down --remove-orphans" in _make(api, "down")
    assert f"{prefix} logs -f django" in _make(api, "logs")
    assert f"{prefix} run --rm django ./manage.py shell" in _make(api, "shell")
    assert "docker-compose" not in "".join(_make(api, t) for t in ("build", "up", "down", "logs", "shell"))


def test_the_compose_command_takes_a_local_overlay_and_a_dotenv_when_present(api: Path) -> None:
    (api / "docker-compose.local.yml").write_text("services: {}\n")
    (api / ".env").write_text("DJANGO_ALLOWED_HOSTS=example\n")
    assert (
        "docker compose --env-file productforge.env --env-file .env -f .productforge/compose.yml "
        "-f docker-compose.local.yml --project-directory . build"
    ) in _make(api, "build")


def test_all_rebuilds_and_restarts(api: Path) -> None:
    out = _make(api, "all")
    assert out.index(" down ") < out.index(" build") < out.index(" up ")


def test_up_in_mobile_mode_wires_the_lan_ip_to_the_web_port_of_the_slot(api: Path) -> None:
    out = _make(api, "up", "mode=mobile", "LAN_IP=10.1.2.3")
    assert f"DJANGO_ALLOWED_HOSTS=10.1.2.3 CORS_EXTRA_ORIGINS=http://10.1.2.3:{WEB} " in out
    assert f"FRONTEND_BASE_URL=http://10.1.2.3:{WEB} docker compose" in out
    assert "10.1.2.3" not in _make(api, "up")


def test_up_in_mobile_mode_needs_a_lan_ip(api: Path) -> None:
    result = run_make(api, "up", "mode=mobile", "LAN_IP=")
    assert result.returncode != 0
    assert "no LAN IP" in result.stderr


def test_clean_removes_python_caches(api: Path) -> None:
    assert '-name "__pycache__"' in _make(api, "clean")


# ─── web.mk ───────────────────────────────────────────────────────────────

FLUTTER = r"(fvm )?flutter"


def test_make_targets_the_web_kit_names_all_run(web: Path) -> None:
    for target in (
        "install",
        "lint",
        "setup-hooks",
        "clean",
        "test",
        "l10n",
        "generate",
        "identity",
        "check-identity-regeneration",
        "check-generated",
        "build",
        "up",
        "debug",
        "down",
        "serve-build",
        "all",
        "ports",
    ):
        _make(web, target)


def test_up_serves_on_the_slots_web_port_against_the_slots_api(web: Path) -> None:
    out = _make(web, "up")
    assert re.search(
        rf"{FLUTTER} run -d web-server --web-port={WEB} "
        rf"--dart-define=GRAPHQL_ENDPOINT=http://localhost:{API}/graphql/\s*$",
        out,
    )


def test_up_in_mobile_mode_binds_every_interface_and_targets_the_lan_ip(web: Path) -> None:
    out = _make(web, "up", "mode=mobile", "LAN_IP=10.1.2.3")
    assert f"--web-port={WEB}" in out
    assert f"GRAPHQL_ENDPOINT=http://10.1.2.3:{API}/graphql/" in out
    assert "--web-hostname=0.0.0.0" in out


def test_debug_build_and_down_use_the_slots_ports(web: Path) -> None:
    assert f"run -d chrome --web-port={WEB} --dart-define=GRAPHQL_ENDPOINT=http://localhost:{API}/graphql/" in _make(
        web, "debug"
    )
    assert f"build web --dart-define=GRAPHQL_ENDPOINT=http://localhost:{API}/graphql/" in _make(web, "build")
    assert f'pkill -f "flutter_tools.*--web-port={WEB}"' in _make(web, "down")


def test_the_endpoint_can_be_overridden_for_one_run(web: Path) -> None:
    assert "GRAPHQL_ENDPOINT=https://api.example.test/graphql/" in _make(
        web, "build", "GRAPHQL_ENDPOINT=https://api.example.test/graphql/"
    )


def test_serve_build_serves_the_kits_script_on_the_web_port(web: Path) -> None:
    assert f"python3 .productforge/serve_web_build.py {WEB}" in _make(web, "serve-build")


def test_the_identity_targets_run_the_kits_scripts(web: Path) -> None:
    assert "python3 .productforge/generate_identity.py" in _make(web, "identity")
    assert "python3 .productforge/check_identity_regeneration.py" in _make(web, "check-identity-regeneration")


def test_check_generated_regenerates_everything_then_diffs(web: Path) -> None:
    out = _make(web, "check-generated")
    assert out.index("build_runner") < out.index("gen-l10n") < out.index("generate_identity.py") < out.index("git diff")


def test_a_web_repository_gets_no_docker_targets(web: Path) -> None:
    result = run_make(web, "-n", "logs")
    assert result.returncode != 0
    assert "No rule to make target" in result.stderr


def test_install_fetches_pub_packages_before_the_shared_steps(web: Path) -> None:
    out = _make(web, "install")
    assert out.index("pub get") < out.index("uv sync --dev") < out.index("install-hooks") < out.index("install\n")


# ─── common.mk ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("fixture", ["api", "web"])
def test_make_ports_prints_the_slots_ports(fixture: str, request: pytest.FixtureRequest) -> None:
    repo: Path = request.getfixturevalue(fixture)
    result = run_make(repo, "ports")
    assert result.stdout.splitlines() == ["slot 3: 6160-6179", *ports.table(3)]


@pytest.mark.parametrize("fixture", ["api", "web"])
def test_make_refuses_a_restricted_slot_in_the_repository(fixture: str, request: pytest.FixtureRequest) -> None:
    repo: Path = request.getfixturevalue(fixture)
    env = repo / "productforge.env"
    env.write_text(env.read_text().replace("PF_SLOT=3", "PF_SLOT=28"))
    result = run_make(repo, "-n", "test")
    assert result.returncode != 0
    assert "Chromium" in result.stderr


def test_lint_and_install_run_pre_commit_through_uv(api: Path) -> None:
    assert "uv run pre-commit run --all-files --config .pre-commit-lint.yaml" in _make(api, "lint")
    assert "uv sync --dev" in _make(api, "install")
    assert "uv run pre-commit install" in _make(api, "setup-hooks")


def test_the_makefile_keeps_its_own_update_config(api: Path) -> None:
    out = _make(api, "update-config", "VERSION=v1.0.0")
    assert "uvx --from git+https://github.com/andrew-organization/productforge-config@$version" in out


# ─── Docker Compose ───────────────────────────────────────────────────────


def _exported_env(repo: Path) -> dict[str, str]:
    """What make hands a recipe: every PF_ value the Makefile works out, from a real make run."""
    (repo / "env.mk").write_text("include Makefile\nprint-env:\n\t@env\n")
    result = run_make(repo, "-f", "env.mk", "print-env")
    assert result.returncode == 0, result.stderr
    (repo / "env.mk").unlink()
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if line.startswith("PF_"))


def _compose_config(repo: Path, compose_file: str, env: dict[str, str]) -> dict:
    result = subprocess.run(
        [
            *["docker", "compose", "--env-file", "productforge.env", "-f", compose_file],
            *["--project-directory", ".", "config", "--format", "json"],
        ],
        cwd=repo,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_make_exports_the_values_the_ports_command_prints(api: Path) -> None:
    exported = _exported_env(api)
    assert sorted(f"{k}={v}" for k, v in exported.items() if k.startswith("PF_PORT_")) == sorted(ports.env_lines(3))
    assert exported["PF_NAME"] == exported["PF_DJANGO_PROJECT"] == exported["PF_POSTGRES_DB"] == "fixture_api"


@needs_docker
def test_the_dev_stack_interpolates_from_the_slot_and_the_names(api: Path) -> None:
    config = _compose_config(api, ".productforge/compose.yml", _exported_env(api))
    services = config["services"]
    assert config["name"] == "fixture_api"
    assert set(services) == {"django", "postgres", "redis", "mailpit", "celeryworker", "celerybeat", "flower"}

    def published(service: str) -> list[str]:
        return [str(p["published"]) for p in services[service]["ports"]]

    assert published("django") == [str(API)]
    assert published("postgres") == [str(POSTGRES)]
    assert published("redis") == [str(REDIS)]
    assert published("flower") == [str(FLOWER)]
    assert published("mailpit") == [str(SMTP), str(UI)]
    assert "ports" not in services["celeryworker"] or not services["celeryworker"]["ports"]

    django = services["django"]["environment"]
    assert django["DJANGO_SETTINGS_MODULE"] == "fixture_api.settings.base"
    assert django["POSTGRES_DB"] == services["postgres"]["environment"]["POSTGRES_DB"] == "fixture_api"
    assert django["WEB_ORIGIN"] == f"http://localhost:{WEB}"
    assert django["FRONTEND_BASE_URL"] == f"http://localhost:{WEB}"
    assert services["django"]["image"] == "fixture_api_django"
    assert services["celeryworker"]["image"] == "fixture_api_celeryworker"
    assert services["celeryworker"]["command"][:4] == ["uv", "run", "celery", "-A"]
    assert services["celeryworker"]["command"][4] == "fixture_api"
    assert services["celerybeat"]["command"][4] == "fixture_api"
    assert services["flower"]["command"][4] == "fixture_api"
    assert services["flower"]["environment"]["DJANGO_SETTINGS_MODULE"] == "fixture_api.settings.base"
    assert "fixture_api" in services["postgres"]["healthcheck"]["test"][1]
    assert services["django"]["build"]["dockerfile"] == "./.productforge/Dockerfile"
    assert services["django"]["build"]["context"] == str(api.resolve())


@needs_docker
def test_the_dev_stack_takes_a_web_origin_override_and_an_overlay(api: Path) -> None:
    (api / ".env").write_text("FRONTEND_BASE_URL=http://10.0.0.9:6166\n")
    (api / "docker-compose.local.yml").write_text("services:\n  django:\n    environment:\n      EXTRA: yes\n")
    result = subprocess.run(
        [
            *["docker", "compose", "--env-file", "productforge.env", "--env-file", ".env"],
            *["-f", ".productforge/compose.yml", "-f", "docker-compose.local.yml", "--project-directory", "."],
            *["config", "--format", "json"],
        ],
        cwd=api,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **_exported_env(api)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    django = json.loads(result.stdout)["services"]["django"]["environment"]
    assert django["FRONTEND_BASE_URL"] == "http://10.0.0.9:6166"
    assert django["EXTRA"] == "yes" and django["WEB_ORIGIN"] == f"http://localhost:{WEB}"


@needs_docker
def test_the_test_database_interpolates_from_the_slot_and_the_names(api: Path) -> None:
    config = _compose_config(api, ".productforge/compose.test.yml", _exported_env(api))
    postgres = config["services"]["postgres-test"]
    assert config["name"] == "fixture_api-test"
    assert [str(p["published"]) for p in postgres["ports"]] == [str(TEST_POSTGRES)]
    assert postgres["environment"]["POSTGRES_DB"] == "fixture_api_test"
    assert "fixture_api_test" in postgres["healthcheck"]["test"][1]


@needs_docker
@pytest.mark.parametrize("missing", ["PF_NAME", "PF_DJANGO_PROJECT", "PF_POSTGRES_DB", "PF_PORT_API", "PF_PORT_WEB"])
def test_compose_refuses_to_run_without_a_value_it_needs(api: Path, missing: str) -> None:
    env = _exported_env(api)
    del env[missing]
    result = subprocess.run(
        ["docker", "compose", "-f", ".productforge/compose.yml", "--project-directory", ".", "config"],
        cwd=api,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert missing in result.stderr


@needs_docker
def test_another_slot_moves_every_port_and_only_the_ports(api: Path) -> None:
    (api / "productforge.env").write_text(
        (api / "productforge.env").read_text().replace("PF_SLOT=3", "PF_SLOT=40").replace("fixture_api", "other_api")
    )
    config = _compose_config(api, ".productforge/compose.yml", _exported_env(api))
    assert config["name"] == "other_api"
    assert [str(p["published"]) for p in config["services"]["django"]["ports"]] == ["6900"]
    assert config["services"]["django"]["environment"]["WEB_ORIGIN"] == "http://localhost:6906"


# ─── The web kit's identity scripts ───────────────────────────────────────


def _run(repo: Path, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, f".productforge/{script}"], cwd=repo, capture_output=True, text=True)


def _git_init(repo: Path) -> None:
    for command in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *command], cwd=repo, check=True, capture_output=True)


def test_generate_identity_writes_the_three_files_from_product_yaml(web: Path) -> None:
    (web / "product.yaml").write_text(
        "name: Acme's Shop\nshort_name: Acme\nemail_from: hi@acme.test\ndomain: acme.test\n"
    )
    assert _run(web, "generate_identity.py").returncode == 0
    assert "static const String name = 'Acme\\'s Shop';" in (web / "lib/config/product.dart").read_text()
    assert "<title>Acme&#x27;s Shop</title>" in (web / "web/index.html").read_text()
    assert json.loads((web / "web/manifest.json").read_text())["short_name"] == "Acme"
    assert ".productforge/generate_identity.py" in (web / "lib/config/product.dart").read_text()


@pytest.mark.parametrize(
    "product_yaml",
    [
        "name: Fixture Web\nshort_name: Fixture\nemail_from: a@b.test\ndomain: b.test\n",
        "# leading comment\nname: 'Quoted ''Name'''\nshort_name: \"Dq\"  # trailing\n"
        "email_from: a@b.test\ndomain: b.test\n",
        "domain: b.test\nemail_from: a@b.test\nshort_name: Zed\nname: Zed Zero\n",
        "name: The ProductForge template\nshort_name: ProductForge\nemail_from: a@b.test\ndomain: b.test\n",
    ],
)
def test_the_identity_regeneration_check_passes_in_any_product(web: Path, product_yaml: str) -> None:
    (web / "product.yaml").write_text(product_yaml)
    assert _run(web, "generate_identity.py").returncode == 0
    before = {
        p: (web / p).read_text()
        for p in ("product.yaml", "lib/config/product.dart", "web/index.html", "web/manifest.json")
    }
    result = _run(web, "check_identity_regeneration.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
    assert before == {p: (web / p).read_text() for p in before}  # restored


def test_the_identity_regeneration_check_needs_no_git_and_leaves_uncommitted_work_alone(web: Path) -> None:
    _run(web, "generate_identity.py")
    (web / "web/index.html").write_text(
        (web / "web/index.html").read_text().replace("</body>", "<!-- wip -->\n</body>")
    )
    wip = (web / "web/index.html").read_text()
    assert not (web / ".git").exists()
    result = _run(web, "check_identity_regeneration.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (web / "web/index.html").read_text() == wip


def test_the_identity_regeneration_check_fails_when_the_generator_ignores_the_name(web: Path) -> None:
    _run(web, "generate_identity.py")
    generator = web / ".productforge" / "generate_identity.py"
    generator.write_text(generator.read_text().replace('html.escape(product["name"])', "'Hard-coded'"))
    (web / "web" / "index.html").write_text(
        (web / "web" / "index.html").read_text().replace("Fixture Web", "Hard-coded")
    )
    result = _run(web, "check_identity_regeneration.py")
    assert result.returncode != 0
    assert "web/index.html" in result.stderr
    assert (web / "product.yaml").read_text().startswith("# A fixture product's identity.\nname: Fixture Web")


def test_the_identity_regeneration_check_fails_without_a_name_line(web: Path) -> None:
    (web / "product.yaml").write_text("short_name: Only\nemail_from: a@b.test\ndomain: b.test\n")
    result = _run(web, "check_identity_regeneration.py")
    assert result.returncode != 0
    assert "`name:`" in result.stderr


def test_serve_web_build_falls_back_to_the_index_for_an_unknown_path(web: Path) -> None:
    import http.client
    import socket
    import time

    (web / "build" / "web").mkdir(parents=True)
    (web / "build" / "web" / "index.html").write_text("<p>the app</p>")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, ".productforge/serve_web_build.py", str(port)], cwd=web)
    try:
        for _ in range(50):
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                connection.request("GET", "/verify-email")
                response = connection.getresponse()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the server never came up")
        assert response.status == 200
        assert response.read() == b"<p>the app</p>"
    finally:
        server.terminate()
        server.wait()


@pytest.mark.parametrize("fixture", ["api", "web"])
def test_a_bare_make_runs_the_kinds_all(fixture: str, request: pytest.FixtureRequest) -> None:
    repo: Path = request.getfixturevalue(fixture)
    assert _make(repo) == _make(repo, "all")
    assert "uv sync" not in _make(repo)


def test_a_bare_make_in_an_api_repository_rebuilds_and_restarts(api: Path) -> None:
    out = _make(api)
    assert out.index(" down ") < out.index(" build") < out.index(" up ")


@pytest.mark.parametrize("target", ["up", "build"])
def test_web_mobile_mode_needs_a_lan_ip(web: Path, target: str) -> None:
    result = run_make(web, target, "mode=mobile", "LAN_IP=")
    assert result.returncode != 0
    assert f"{target}: no LAN IP found for mode=mobile" in result.stderr


def test_make_refuses_a_name_that_is_not_snake_case(api: Path) -> None:
    (api / "productforge.env").write_text("PF_KIND=api\nPF_SLOT=3\nPF_NAME=Not-Snake\n")
    result = run_make(api, "-n", "test")
    assert result.returncode != 0
    assert "PF_NAME must be lower-case" in result.stderr
