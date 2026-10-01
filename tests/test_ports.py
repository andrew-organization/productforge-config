"""The port arithmetic, as the command prints it (`productforge-config ports`) and as the kits' make works it out."""

import re
from pathlib import Path

import pytest

from .conftest import Repo, run_config


@pytest.fixture
def restricted(shipped: Path) -> set[int]:
    """The ports the kit's make refuses a slot for: a second statement of the limit, to hold the command to."""
    return {int(port) for port in mk_value(shipped, "PF_RESTRICTED_PORTS").split()}


@pytest.fixture
def last_slot(shipped: Path) -> int:
    return int(mk_value(shipped, "PF_LAST_SLOT"))


def mk_value(shipped: Path, name: str) -> str:
    match = re.search(rf"^{name} := (.+)$", (shipped / "kits" / "product" / "product.mk").read_text(), re.MULTILINE)
    assert match, name
    return match.group(1).strip()


def test_ports_prints_the_ports_of_a_slot_as_a_table_and_as_env_lines() -> None:
    table = run_config("ports", "--slot", 1)
    slot_zero = run_config("ports", "--slot", 0, "--env")
    slot_three = run_config("ports", "--slot", 3, "--env")

    assert (table.returncode, table.stderr) == (0, "")
    assert "6120  the API" in table.stdout and "6126  the web dev server, and a served build" in table.stdout
    assert slot_zero.stdout.splitlines() == [  # slot 0 keeps the ports the template always used
        *("PF_PORT_API=6100", "PF_PORT_POSTGRES=6101", "PF_PORT_REDIS=6102", "PF_PORT_FLOWER=6103"),
        *("PF_PORT_MAILPIT_SMTP=6104", "PF_PORT_MAILPIT_UI=6105", "PF_PORT_WEB=6106", "PF_PORT_TEST_POSTGRES=6111"),
    ]
    assert {"PF_PORT_API=6160", "PF_PORT_WEB=6166", "PF_PORT_TEST_POSTGRES=6171"} <= set(slot_three.stdout.splitlines())


def test_every_slot_is_refused_or_owns_ports_no_browser_refuses_below_the_ephemeral_range(
    restricted: set[int], last_slot: int
) -> None:
    usable = []
    for slot in range(last_slot + 2):
        result = run_config("ports", "--slot", slot, "--env")
        block = range(6100 + 20 * slot, 6100 + 20 * slot + 20)
        if result.returncode == 0:
            ports = [int(line.split("=")[1]) for line in result.stdout.splitlines()]
            assert set(ports) <= set(block) and not restricted & set(block), slot
            usable.append(slot)
        else:
            assert slot > last_slot or restricted & set(block), slot  # refused only for a reason
            assert result.stdout == "" and result.stderr.startswith("productforge-config: "), slot
    assert {0, 1, last_slot} <= set(usable) and not {23, 28, 29, last_slot + 1} & set(usable)
    assert max(6100 + 20 * slot + 19 for slot in usable) < 32768
    assert "6665" in run_config("ports", "--slot", 28).stderr  # and says which port, or why
    assert "the highest slot is 1332" in run_config("ports", "--slot", last_slot + 1).stderr
    assert "whole number" in run_config("ports", "--slot", -1).stderr


def test_make_works_out_the_same_ports_as_the_command_and_refuses_the_same_slots(
    api_repo: Repo, last_slot: int
) -> None:
    api_repo.update()

    for slot in (0, 1, 2, 27, 30, 198, last_slot):
        api_repo.write("productforge.env", f"PF_KITS=python django-api\nPF_SLOT={slot}\nPF_NAME=fixture_api\n")
        made = api_repo.make("ports")

        first = 6100 + 20 * slot
        assert made.returncode == 0, made.stderr
        assert made.stdout.splitlines() == [
            f"slot {slot}: {first}-{first + 19}",
            *run_config("ports", "--slot", slot).stdout.splitlines(),
        ]
    for slot, message in (
        ("28", "Chromium"),
        ("x", "PF_SLOT must be a whole number"),
        (str(last_slot + 1), "too large"),
    ):
        api_repo.write("productforge.env", f"PF_KITS=python django-api\nPF_SLOT={slot}\nPF_NAME=fixture_api\n")
        made = api_repo.make("-n", "test")

        assert made.returncode != 0 and message in made.stderr, slot
        assert run_config("ports", "--slot", slot).returncode != 0


def test_make_exports_the_values_the_command_prints(api_repo: Repo) -> None:
    api_repo.update()
    api_repo.write("env.mk", "include Makefile\nprint-env:\n\t@env\n")

    result = api_repo.make("-f", "env.mk", "print-env")

    exported = dict(line.split("=", 1) for line in result.stdout.splitlines() if line.startswith("PF_"))
    assert result.returncode == 0, result.stderr
    ports = run_config("ports", "--slot", 3, "--env").stdout.splitlines()
    assert sorted(f"{k}={v}" for k, v in exported.items() if k.startswith("PF_PORT_")) == sorted(ports)
    assert exported["PF_NAME"] == exported["PF_DJANGO_PROJECT"] == exported["PF_POSTGRES_DB"] == "fixture_api"
