"""Tests of the port arithmetic, in Python (`ports`) and in the kit's make (`common.mk`)."""

import re
import subprocess
from pathlib import Path

import pytest

from productforge_config import cli, ports

REPO_ROOT = Path(__file__).parent.parent
COMMON_MK = REPO_ROOT / "kits" / "common" / "common.mk"


def test_slot_zero_reproduces_the_templates_current_ports() -> None:
    assert ports.ports_for(0) == {
        "API": 6100,
        "POSTGRES": 6101,
        "REDIS": 6102,
        "FLOWER": 6103,
        "MAILPIT_SMTP": 6104,
        "MAILPIT_UI": 6105,
        "WEB": 6106,
        "TEST_POSTGRES": 6111,
    }


def test_a_slot_owns_twenty_ports_from_6100_plus_twenty_a_slot() -> None:
    assert ports.ports_for(3)["API"] == 6160
    assert ports.ports_for(3)["WEB"] == 6166
    assert ports.ports_for(3)["TEST_POSTGRES"] == 6171
    assert list(ports.block(3)) == list(range(6160, 6180))


def test_adjacent_slots_never_share_a_port() -> None:
    assert not set(ports.block(4)) & set(ports.block(5))


@pytest.mark.parametrize(
    ("slot", "why"),
    [
        (23, "6566"),  # offset 6, the web dev server
        (28, "6665"),  # offsets 5-9
        (29, "6697"),  # a reserved offset: no later kit may land on it either
        (199, "10080"),
    ],
)
def test_refuses_a_slot_whose_block_holds_a_chromium_restricted_port(slot: int, why: str) -> None:
    with pytest.raises(ports.PortError, match=why):
        ports.block(slot)


@pytest.mark.parametrize("slot", [-1, 1333, 10**6])
def test_refuses_a_slot_out_of_range(slot: int) -> None:
    with pytest.raises(ports.PortError):
        ports.block(slot)


def test_the_highest_slot_stays_below_the_ephemeral_range() -> None:
    assert max(ports.block(ports.MAX_SLOT)) < 32768


def test_no_usable_slot_holds_a_restricted_port() -> None:
    usable = []
    for slot in range(ports.MAX_SLOT + 1):
        try:
            ports.block(slot)
        except ports.PortError:
            continue
        usable.append(slot)
    assert 0 in usable and 1 in usable
    assert all(not ports.CHROMIUM_RESTRICTED_PORTS & set(ports.block(slot)) for slot in usable)


def test_the_ports_command_prints_the_slots_ports(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ports", "--slot", "1"]) == 0
    out = capsys.readouterr().out
    assert "6120  the API" in out
    assert "6126  the web dev server, and a served build" in out


def test_the_ports_command_prints_env_lines(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ports", "--slot", "1", "--env"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "PF_PORT_API=6120"
    assert "PF_PORT_TEST_POSTGRES=6131" in lines


def test_the_ports_command_refuses_a_restricted_slot(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ports", "--slot", "28"]) == 1
    assert "6665" in capsys.readouterr().err


def _mk_value(name: str) -> str:
    match = re.search(rf"^{name} := (.+)$", COMMON_MK.read_text(), re.MULTILINE)
    assert match, name
    return match.group(1).strip()


def test_common_mk_holds_the_same_limits_as_the_python() -> None:
    assert {int(p) for p in _mk_value("PF_RESTRICTED_PORTS").split()} == set(ports.CHROMIUM_RESTRICTED_PORTS)
    assert int(_mk_value("PF_LAST_SLOT")) == ports.MAX_SLOT


def _make_ports(tmp_path: Path, slot: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "productforge.env").write_text(f"PF_KIND=api\nPF_SLOT={slot}\nPF_NAME=demo_app\n")
    (tmp_path / "Makefile").write_text("include productforge.env\ninclude common.mk\n")
    (tmp_path / "common.mk").write_text(COMMON_MK.read_text())
    return subprocess.run(["make", "ports"], cwd=tmp_path, capture_output=True, text=True)


@pytest.mark.parametrize("slot", [0, 1, 2, 27, 30, 198, ports.MAX_SLOT])
def test_make_works_out_the_same_ports_as_the_python(tmp_path: Path, slot: int) -> None:
    result = _make_ports(tmp_path, str(slot))
    assert result.returncode == 0, result.stderr
    expected = ports.table(slot)
    first = ports.block(slot)[0]
    assert result.stdout.splitlines() == [f"slot {slot}: {first}-{first + 19}", *expected]


@pytest.mark.parametrize("slot", [23, 28, 29, 199])
def test_make_refuses_the_same_restricted_slots(tmp_path: Path, slot: int) -> None:
    result = _make_ports(tmp_path, str(slot))
    assert result.returncode != 0
    assert "Chromium" in result.stderr


@pytest.mark.parametrize("slot", ["08", "-1", "x", "1333", "3.5"])
def test_make_refuses_a_slot_that_is_not_a_whole_number_in_range(tmp_path: Path, slot: str) -> None:
    result = _make_ports(tmp_path, slot)
    assert result.returncode != 0
    assert "PF_SLOT" in result.stderr
