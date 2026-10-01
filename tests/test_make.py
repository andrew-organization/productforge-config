"""The kits as a repository runs them: `make -n` against the fixture's thin Makefile, Docker Compose
interpolation of the compose files, and the web kit's identity scripts.
"""

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time

import pytest

from .conftest import Repo, Stubs, git_tags

# The fixtures are at slot 3: 6160-6179.
API, POSTGRES, REDIS, FLOWER, SMTP, UI, WEB, TEST_POSTGRES = 6160, 6161, 6162, 6163, 6164, 6165, 6166, 6171
FLUTTER = r"(fvm )?flutter"
SOURCE = "git+https://github.com/andrew-organization/productforge-config"


@pytest.fixture
def api(api_repo: Repo) -> Repo:
    assert api_repo.update().returncode == 0
    return api_repo


@pytest.fixture
def web(web_repo: Repo) -> Repo:
    assert web_repo.update().returncode == 0
    return web_repo


# ─── The API kit ──────────────────────────────────────────────────────────


def test_the_api_targets_run_the_commands_the_slot_and_the_names_give(api: Repo) -> None:
    for target in (
        *("install", "lint", "setup-hooks", "clean", "test", "test-integration", "test-integration-ci"),
        *("check-migrations", "build", "up", "down", "logs", "shell", "lock", "all", "ports"),
    ):
        api.dry(target)
    assert 'uv run pytest -n auto api/tests -v -m "not django_db"' in api.dry("test")
    assert 'uv run pytest -n auto api/x.py -v -m "not django_db" -k "y"' in api.dry("test", "path=api/x.py", "k=y")
    integration = api.dry("test-integration")
    assert (
        "-f .productforge/compose.test.yml --project-directory . up -d --wait --remove-orphans postgres-test"
        in integration
    )
    assert (
        f"POSTGRES_HOST=localhost POSTGRES_PORT={TEST_POSTGRES} POSTGRES_DB=fixture_api_test uv run pytest -n auto"
        in integration
    )
    assert (
        "down --remove-orphans" in integration and "exit $status" in integration
    )  # the database goes, whatever the tests did
    ci = api.dry("test-integration-ci")
    assert (
        "up -d --wait --remove-orphans postgres-test" in ci
        and "pytest -n auto api/tests -m django_db -v --create-db" in ci
    )
    assert "down" not in ci
    assert "--settings=fixture_api.settings.test" in api.dry("check-migrations")

    prefix = "docker compose --env-file productforge.env -f .productforge/compose.yml --project-directory ."
    assert f"{prefix} build" in api.dry("build")
    assert f"{prefix} up -d --remove-orphans" in api.dry("up")
    assert f"{prefix} down --remove-orphans" in api.dry("down")
    assert f"{prefix} logs -f django" in api.dry("logs")
    assert f"{prefix} run --rm django ./manage.py shell" in api.dry("shell")
    assert "docker-compose" not in "".join(api.dry(t) for t in ("build", "up", "down", "logs", "shell"))
    everything = api.dry("all")
    assert everything.index(" down ") < everything.index(" build") < everything.index(" up ")
    assert api.dry() == everything and "uv sync" not in api.dry()  # a bare make is `all`

    api.write("docker-compose.local.yml", "services: {}\n")
    api.write(".env", "DJANGO_ALLOWED_HOSTS=example\n")
    assert (
        "docker compose --env-file productforge.env --env-file .env -f .productforge/compose.yml "
        "-f docker-compose.local.yml --project-directory . build"
    ) in api.dry(
        "build"
    )  # a local overlay and a dotenv join the command when present

    api.write("productforge.env", api.read("productforge.env") + "PF_DJANGO_PROJECT=core\nPF_POSTGRES_DB=shop\n")
    assert "--settings=core.settings.test" in api.dry("check-migrations")
    assert "POSTGRES_DB=shop_test" in api.dry("test-integration")

    api.write("productforge.env", "PF_KITS=python django-api\nPF_SLOT=3\nPF_NAME=Not-Snake\n")
    refused = api.make("-n", "test")
    assert refused.returncode != 0 and "PF_NAME must be lower-case" in refused.stderr


def test_the_python_kit_keeps_bytecode_out_cleans_up_and_runs_pre_commit_through_uv(api: Repo) -> None:
    api.write("env.mk", "include Makefile\nprint-env:\n\t@env\n")
    exported = dict(
        line.split("=", 1) for line in api.make("-f", "env.mk", "print-env").stdout.splitlines() if "=" in line
    )

    clean = api.dry("clean")

    assert exported["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "rm -rf .venv" in clean and "find . -type d -name __pycache__" in clean
    assert "rm -rf .pytest_cache .mypy_cache" in clean  # and what every repository cleans
    lint = api.dry("lint")
    assert "uv run pre-commit run --all-files --config .productforge/pre-commit.yaml" in lint
    assert "uv run pre-commit run --all-files --config .pre-commit-lint.yaml" in lint  # the fixture has its own
    assert "uv sync --dev" in api.dry("install") and "uv run pre-commit install" in api.dry("setup-hooks")


def test_up_in_mobile_mode_wires_the_lan_ip_through_the_api_and_the_web_app_and_needs_one(api: Repo, web: Repo) -> None:
    api_up = api.dry("up", "mode=mobile", "LAN_IP=10.1.2.3")
    web_up = web.dry("up", "mode=mobile", "LAN_IP=10.1.2.3")

    assert f"DJANGO_ALLOWED_HOSTS=10.1.2.3 CORS_EXTRA_ORIGINS=http://10.1.2.3:{WEB} " in api_up
    assert f"FRONTEND_BASE_URL=http://10.1.2.3:{WEB} docker compose" in api_up
    assert "10.1.2.3" not in api.dry("up")
    assert f"--web-port={WEB}" in web_up and f"GRAPHQL_ENDPOINT=http://10.1.2.3:{API}/graphql/" in web_up
    assert "--web-hostname=0.0.0.0" in web_up
    for repo, target in ((api, "up"), (web, "up"), (web, "build")):
        result = repo.make(target, "mode=mobile", "LAN_IP=")

        assert result.returncode != 0 and "no LAN IP" in result.stderr, (repo, target)


# ─── The web kit ──────────────────────────────────────────────────────────


def test_the_web_targets_run_the_commands_the_slot_gives(web: Repo) -> None:
    for target in (
        *("install", "lint", "setup-hooks", "clean", "test", "l10n", "generate", "identity"),
        *(
            "check-identity-regeneration",
            "check-generated",
            "build",
            "up",
            "debug",
            "down",
            "serve-build",
            "all",
            "ports",
        ),
    ):
        web.dry(target)
    endpoint = f"--dart-define=GRAPHQL_ENDPOINT=http://localhost:{API}/graphql/"
    assert re.search(rf"{FLUTTER} run -d web-server --web-port={WEB} {endpoint}\s*$", web.dry("up"))
    assert f"run -d chrome --web-port={WEB} {endpoint}" in web.dry("debug")
    assert f"build web {endpoint}" in web.dry("build")
    assert f'pkill -f "flutter_tools.*--web-port={WEB}"' in web.dry("down")
    assert "GRAPHQL_ENDPOINT=https://api.example.test/graphql/" in web.dry(
        "build", "GRAPHQL_ENDPOINT=https://api.example.test/graphql/"
    )
    assert f"python3 .productforge/serve_web_build.py {WEB}" in web.dry("serve-build")
    assert "python3 .productforge/generate_identity.py" in web.dry("identity")
    assert "python3 .productforge/check_identity_regeneration.py" in web.dry("check-identity-regeneration")
    generated = web.dry("check-generated")
    assert (
        generated.index("build_runner")
        < generated.index("gen-l10n")
        < generated.index("generate_identity.py")
        < generated.index("git diff")
    )
    install = web.dry("install")
    assert (
        install.index("pub get")
        < install.index("uv sync --dev")
        < install.index("install-hooks")
        < install.index("install\n")
    )
    assert web.dry() == web.dry("all") and "uv sync" not in web.dry()
    refused = web.make("-n", "logs")
    assert refused.returncode != 0 and "No rule to make target" in refused.stderr  # no docker targets


# ─── update-config and check-config ───────────────────────────────────────


def test_update_config_takes_a_tag_or_a_commit_else_the_newest_release_and_check_config_checks_the_recorded_one(
    api: Repo, stubs: Stubs
) -> None:
    for version in ("v1.0.0", "0123456789abcdef0123456789abcdef01234567"):
        out = api.dry("update-config", f"VERSION={version}")

        assert f"uvx --from {SOURCE}@{version}" in out
        assert f"productforge-config update --version {version}" in out
    for tags, expected in (
        (["v1.9.0", "v1.10.0", "v1.11.0-rc.1"], "v1.10.0"),  # stable wins over a newer -rc.N
        (["v1.0.0-rc.2", "v1.0.0-rc.10", "v0.9.0-rc.11", "nonsense"], "v1.0.0-rc.10"),  # no stable: the newest -rc.N
    ):
        stubs.add("git", stdout=git_tags(tags))

        assert f"productforge-config update --version {expected}" in api.dry("update-config")
    stubs.add("git", stdout=git_tags(["not-a-release"]))
    result = api.make("update-config")
    assert result.returncode != 0 and "no productforge-config release tag found" in result.stderr
    checking = api.dry("check-config")
    assert "update --check --version $release" in checking and ".productforge/release" in checking


# ─── Docker Compose ───────────────────────────────────────────────────────


@pytest.fixture
def compose() -> None:
    """Skips a case when `docker compose` is not installed where the case's own Docker configuration looks."""
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("docker compose isn't available")


def exported_env(repo: Repo) -> dict[str, str]:
    """What make hands a recipe: every PF_ value the Makefile works out, from a real make run."""
    repo.write("env.mk", "include Makefile\nprint-env:\n\t@env\n")
    result = repo.make("-f", "env.mk", "print-env")
    assert result.returncode == 0, result.stderr
    (repo.root / "env.mk").unlink()
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if line.startswith("PF_"))


def compose_config(
    repo: Repo, files: list[str], env: dict[str, str], dotenv: bool = False
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--env-file", "productforge.env", *(["--env-file", ".env"] if dotenv else [])]
    for file in files:
        command += ["-f", file]
    return subprocess.run(
        [*command, "--project-directory", ".", "config", "--format", "json"],
        cwd=repo.root,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


def published(service: dict) -> list[str]:
    return [str(p["published"]) for p in service["ports"]]


@pytest.mark.usefixtures("compose")
def test_the_dev_stack_interpolates_from_the_slot_and_the_names(api: Repo) -> None:
    result = compose_config(api, [".productforge/compose.yml"], exported_env(api))
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    services = config["services"]
    assert config["name"] == "fixture_api"
    assert set(services) == {"django", "postgres", "redis", "mailpit", "celeryworker", "celerybeat", "flower"}
    assert published(services["django"]) == [str(API)] and published(services["postgres"]) == [str(POSTGRES)]
    assert published(services["redis"]) == [str(REDIS)] and published(services["flower"]) == [str(FLOWER)]
    assert published(services["mailpit"]) == [str(SMTP), str(UI)]
    assert not services["celeryworker"].get("ports")
    django = services["django"]["environment"]
    assert django["DJANGO_SETTINGS_MODULE"] == "fixture_api.settings.base"
    assert django["POSTGRES_DB"] == services["postgres"]["environment"]["POSTGRES_DB"] == "fixture_api"
    assert django["WEB_ORIGIN"] == django["FRONTEND_BASE_URL"] == f"http://localhost:{WEB}"
    assert (
        services["django"]["image"] == "fixture_api_django"
        and services["celeryworker"]["image"] == "fixture_api_celeryworker"
    )
    for service in ("celeryworker", "celerybeat", "flower"):
        assert services[service]["command"][:4] == ["uv", "run", "celery", "-A"]
        assert services[service]["command"][4] == "fixture_api"
    assert services["flower"]["environment"]["DJANGO_SETTINGS_MODULE"] == "fixture_api.settings.base"
    assert "fixture_api" in services["postgres"]["healthcheck"]["test"][1]
    assert services["django"]["build"]["dockerfile"] == "./.productforge/Dockerfile"
    assert services["django"]["build"]["context"] == str(api.root.resolve())

    api.write(".env", "FRONTEND_BASE_URL=http://10.0.0.9:6166\n")
    api.write("docker-compose.local.yml", "services:\n  django:\n    environment:\n      EXTRA: yes\n")
    overlaid = compose_config(
        api, [".productforge/compose.yml", "docker-compose.local.yml"], exported_env(api), dotenv=True
    )
    assert overlaid.returncode == 0, overlaid.stderr
    django = json.loads(overlaid.stdout)["services"]["django"]["environment"]
    assert django["FRONTEND_BASE_URL"] == "http://10.0.0.9:6166" and django["EXTRA"] == "yes"
    assert django["WEB_ORIGIN"] == f"http://localhost:{WEB}"

    api.write(
        "productforge.env",
        api.read("productforge.env").replace("PF_SLOT=3", "PF_SLOT=40").replace("fixture_api", "other_api"),
    )
    moved = json.loads(compose_config(api, [".productforge/compose.yml"], exported_env(api)).stdout)
    assert moved["name"] == "other_api" and published(moved["services"]["django"]) == ["6900"]
    assert moved["services"]["django"]["environment"]["WEB_ORIGIN"] == "http://localhost:6906"


@pytest.mark.usefixtures("compose")
def test_the_test_database_interpolates_from_the_slot_and_the_names(api: Repo) -> None:
    result = compose_config(api, [".productforge/compose.test.yml"], exported_env(api))

    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    postgres = config["services"]["postgres-test"]
    assert config["name"] == "fixture_api-test"
    assert published(postgres) == [str(TEST_POSTGRES)]
    assert postgres["environment"]["POSTGRES_DB"] == "fixture_api_test"
    assert "fixture_api_test" in postgres["healthcheck"]["test"][1]


@pytest.mark.usefixtures("compose")
def test_compose_refuses_to_run_without_a_value_it_needs(api: Repo) -> None:
    for missing in ("PF_NAME", "PF_DJANGO_PROJECT", "PF_POSTGRES_DB", "PF_PORT_API", "PF_PORT_WEB"):
        env = exported_env(api)
        del env[missing]
        result = subprocess.run(
            ["docker", "compose", "-f", ".productforge/compose.yml", "--project-directory", ".", "config"],
            cwd=api.root,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0, missing
        assert missing in result.stderr, missing


# ─── The web kit's identity scripts ───────────────────────────────────────


def run_script(repo: Repo, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, f".productforge/{script}"], cwd=repo.root, capture_output=True, text=True)


def test_generate_identity_writes_the_three_files_from_product_yaml(web: Repo) -> None:
    web.write("product.yaml", "name: Acme's Shop\nshort_name: Acme\nemail_from: hi@acme.test\ndomain: acme.test\n")

    result = run_script(web, "generate_identity.py")

    assert result.returncode == 0, result.stderr
    assert "static const String name = 'Acme\\'s Shop';" in web.read("lib/config/product.dart")
    assert "<title>Acme&#x27;s Shop</title>" in web.read("web/index.html")
    assert json.loads(web.read("web/manifest.json"))["short_name"] == "Acme"
    assert ".productforge/generate_identity.py" in web.read("lib/config/product.dart")

    web.edit("web/index.html", "</body>", "<!-- wip -->\n</body>")  # uncommitted work, in a repository with no git
    wip = web.read("web/index.html")
    checked = run_script(web, "check_identity_regeneration.py")
    assert not web.exists(".git")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert web.read("web/index.html") == wip


def test_the_identity_regeneration_check_passes_in_any_product(web: Repo) -> None:
    web.write(  # quoted values, a comment, and the keys in another order
        "product.yaml",
        '# leading comment\ndomain: b.test\nemail_from: a@b.test\nshort_name: "Dq"  # trailing\n'
        "name: 'Quoted ''Name'''\n",
    )
    assert run_script(web, "generate_identity.py").returncode == 0
    files = ("product.yaml", "lib/config/product.dart", "web/index.html", "web/manifest.json")
    before = {rel: web.read(rel) for rel in files}

    result = run_script(web, "check_identity_regeneration.py")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
    assert {rel: web.read(rel) for rel in files} == before  # restored


def test_the_identity_regeneration_check_fails_when_the_generator_ignores_the_name(web: Repo) -> None:
    run_script(web, "generate_identity.py")
    web.edit(".productforge/generate_identity.py", 'html.escape(product["name"])', "'Hard-coded'")
    web.edit("web/index.html", "Fixture Web", "Hard-coded")

    result = run_script(web, "check_identity_regeneration.py")

    assert result.returncode != 0
    assert "web/index.html" in result.stderr
    assert web.read("product.yaml").startswith("# A fixture product's identity.\nname: Fixture Web")


def test_the_identity_regeneration_check_fails_without_a_name_line(web: Repo) -> None:
    web.write("product.yaml", "short_name: Only\nemail_from: a@b.test\ndomain: b.test\n")

    result = run_script(web, "check_identity_regeneration.py")

    assert result.returncode != 0
    assert "`name:`" in result.stderr


def test_serve_web_build_falls_back_to_the_index_for_an_unknown_path(web: Repo) -> None:
    web.write("build/web/index.html", "<p>the app</p>")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, ".productforge/serve_web_build.py", str(port)], cwd=web.root)
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
