"""`productforge-config clashes`: what several checkouts would take from one machine at once."""

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from .conftest import Repo, run_config

PRODUCT_ENV = "PF_KITS={kits}\nPF_SLOT={slot}\nPF_NAME={name}\n"
API_PORTS = ("API", "POSTGRES", "REDIS", "FLOWER", "MAILPIT_SMTP", "MAILPIT_UI", "TEST_POSTGRES")


@pytest.fixture
def compose() -> None:
    """Skips a case when `docker compose` is not installed where the case's own Docker configuration looks."""
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("docker compose isn't available")


@pytest.fixture
def checkout(shipped: Path, tmp_path: Path) -> Callable[..., Repo]:
    """Makes a checkout of a fixture kind under its own directory, brought up to a release unless told not to."""

    def make(directory: str, *, kind: str, name: str, slot: int, updated: bool = True) -> Repo:
        repo = Repo(tmp_path / directory)
        shutil.copytree(shipped / "tests" / f"fixture_{kind}", repo.root)
        kits = "python django-api" if kind == "api" else "flutter-web"
        repo.write("productforge.env", PRODUCT_ENV.format(kits=kits, slot=slot, name=name))
        if updated:
            assert repo.update().returncode == 0
        return repo

    return make


def port_lines(slot: int, *, names: tuple[str, ...], checkouts: tuple[Repo, ...]) -> list[str]:
    block = 6100 + 20 * slot
    offsets = {"API": 0, "POSTGRES": 1, "REDIS": 2, "FLOWER": 3, "MAILPIT_SMTP": 4, "MAILPIT_UI": 5, "WEB": 6}
    offsets["TEST_POSTGRES"] = 11
    return [f"host port {block + offsets[n]}: {', '.join(str(c.root) for c in checkouts)}" for n in names]


@pytest.mark.usefixtures("compose")
def test_clashes_reports_each_value_two_checkouts_would_take_and_compares_ports_alone_without_docker(
    checkout: Callable[..., Repo], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    one = checkout("one", kind="api", name="alpha", slot=1)
    two = checkout("two", kind="api", name="beta", slot=1)  # one's slot
    three = checkout("three", kind="api", name="alpha", slot=2)  # one's name
    hand_set = checkout("hand_set", kind="api", name="delta", slot=4)
    web = checkout("web", kind="web", name="web_one", slot=1)  # one's slot, which an API and its web app share
    hand_set.write(
        "docker-compose.local.yml", "services:\n  django:\n    ports:\n      - '6126:8000'\n"  # the web app's port
    )
    everyone = (one, two, three, hand_set, web)
    named = lambda *repos: ", ".join(str(r.root) for r in repos)  # noqa: E731

    result = run_config("clashes", *(r.root for r in everyone))

    assert result.returncode == 1 and result.stderr == ""
    assert result.stdout.splitlines()[:-1] == [
        *port_lines(1, names=API_PORTS[:-1], checkouts=(one, two)),
        f"host port 6126: {named(hand_set, web)}",  # between the API's own, which the web app's port sits among
        *port_lines(1, names=API_PORTS[-1:], checkouts=(one, two)),
        f"Compose project alpha: {named(one, three)}",
        f"Compose project alpha-test: {named(one, three)}",
        *(
            f"image alpha_{service}: {named(one, three)}"
            for service in ("celerybeat", "celeryworker", "django", "flower")
        ),
        f"volume alpha_local_postgres_data: {named(one, three)}",
        f"volume alpha_uv-cache: {named(one, three)}",
    ]
    compared = result.stdout.splitlines()[-1]
    assert compared.startswith("compared host ports, Compose projects, images and volumes across 5 checkouts: ")
    assert f"{web.root} (6126)" in compared and f"{one.root} (6120 6121 6122 6123 6124 6125 6131)" in compared

    as_list = run_config("clashes", *(r.root for r in everyone), "--json")
    assert as_list.returncode == 1
    assert [
        f"{c['kind']} {c['value']}: {', '.join(c['checkouts'])}" for c in json.loads(as_list.stdout)
    ] == result.stdout.splitlines()[:-1]

    nothing_on_path = tmp_path / "nothing_on_path"
    nothing_on_path.mkdir()
    monkeypatch.setenv("PATH", str(nothing_on_path))
    alone = run_config("clashes", *(r.root for r in everyone))

    assert alone.returncode == 1
    assert alone.stdout.splitlines()[:-1] == port_lines(1, names=API_PORTS, checkouts=(one, two))
    assert "host ports only" in alone.stdout.splitlines()[-1] and "not resolved" in alone.stdout.splitlines()[-1]


@pytest.mark.usefixtures("compose")
def test_clashes_exits_zero_for_a_clean_set_and_says_what_it_compared(checkout: Callable[..., Repo]) -> None:
    repos = [
        checkout("api_zero", kind="api", name="api_zero", slot=0),
        checkout("web_zero", kind="web", name="web_zero", slot=0),
        checkout("api_one", kind="api", name="api_one", slot=1),
        checkout("web_one", kind="web", name="web_one", slot=1),
    ]

    result = run_config("clashes", *(r.root for r in repos))

    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout.splitlines() == [
        "compared host ports, Compose projects, images and volumes across 4 checkouts: "
        f"{repos[0].root} (6100 6101 6102 6103 6104 6105 6111), {repos[1].root} (6106), "
        f"{repos[2].root} (6120 6121 6122 6123 6124 6125 6131), {repos[3].root} (6126)"
    ]
    assert json.loads(run_config("clashes", *(r.root for r in repos), "--json").stdout) == []


@pytest.mark.usefixtures("compose")
def test_clashes_refuses_a_checkout_it_cannot_read_and_prints_nothing_else(
    checkout: Callable[..., Repo], tmp_path: Path
) -> None:
    good = checkout("good", kind="api", name="good_api", slot=1)
    bare = tmp_path / "bare"
    bare.mkdir()
    invalid = checkout("invalid", kind="web", name="invalid_web", slot=2)
    invalid.write("productforge.env", "PF_KITS=django-api flutter-web\nPF_SLOT=2\nPF_NAME=invalid_web\n")
    unresolved = checkout("unresolved", kind="api", name="unresolved_api", slot=3, updated=False)  # no compose files

    missing = tmp_path / "missing"
    for refused, reason in (
        (bare, "has no productforge.env"),
        (missing, "is not a directory"),
        (invalid.root, "both"),
        (unresolved.root, "resolve"),
    ):
        before = sorted(refused.rglob("*"))

        result = run_config("clashes", good.root, refused)

        assert result.returncode == 2 and result.stdout == "", reason
        assert result.stderr.startswith(f"productforge-config: {refused}: ") and reason in result.stderr, reason
        assert sorted(refused.rglob("*")) == before, reason
    assert "two or more" in run_config("clashes", good.root).stderr
    repeated = run_config("clashes", good.root, f"{good.root}/")
    assert repeated.returncode == 2 and repeated.stdout == "" and "given twice" in repeated.stderr


@pytest.mark.usefixtures("compose")
def test_clashes_reads_the_checkouts_dotenv_as_make_up_does(checkout: Callable[..., Repo]) -> None:
    one = checkout("one", kind="api", name="alpha", slot=1)
    two = checkout("two", kind="api", name="beta", slot=2)
    two.write(".env", "COMPOSE_PROJECT_NAME=alpha\n")  # a project name the file sets, which is one's

    result = run_config("clashes", one.root, two.root)

    assert result.returncode == 1 and f"Compose project alpha: {one.root}, {two.root}" in result.stdout
