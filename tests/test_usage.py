"""Tests of `productforge-config usage` against recorded billing summaries,
served by tests/fixtures/fake_gh.py installed onto PATH, never a real network call.
"""

import json
import os
import stat
from pathlib import Path

import pytest

from .conftest import run_config

ORG = "andrew-organization"
TWO_ITEMS = [
    {"product": "Actions", "sku": "actions_linux", "grossQuantity": 600.0, "unitType": "minutes"},
    {"product": "Actions", "sku": "actions_macos", "grossQuantity": 150.0, "unitType": "minutes"},
]
OTHER_ITEMS = [
    {"product": "Copilot", "sku": "copilot_premium_request", "grossQuantity": 900.0, "unitType": "requests"},
    {"product": "Actions", "sku": "actions_storage", "grossQuantity": 5.0, "unitType": "gigabyte-hours"},
]


def _summary(items: list[dict]) -> dict:
    return {"timePeriod": {"year": 2026, "month": 9}, "organization": ORG, "usageItems": items}


@pytest.fixture
def billing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shipped: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text((shipped / "tests" / "fixtures" / "fake_gh.py").read_text())
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_STATE", str(state_path))
    monkeypatch.setenv("FAKE_GH_CALLS", str(tmp_path / "calls.jsonl"))

    def seed(items: list[dict]) -> None:
        state_path.write_text(json.dumps({"billing_summary": _summary(items)}))

    return seed


def test_a_quarter_of_the_allowance_a_quarter_through_the_month_exits_0(billing) -> None:
    billing(TWO_ITEMS)  # 750 minutes on day 8 of September's 30
    result = run_config("usage", "--org", ORG, "--today", "2026-09-08")
    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        "Actions: 750 of 3000 minutes used, day 8 of 30, projected 2812\n",
        "",
    )


def test_a_month_on_course_to_pass_the_allowance_exits_1(billing) -> None:
    billing(TWO_ITEMS)  # 750 minutes on day 5 projects to 4500
    result = run_config("usage", "--org", ORG, "--today", "2026-09-05")
    assert (result.returncode, result.stdout) == (1, "Actions: 750 of 3000 minutes used, day 5 of 30, projected 4500\n")


def test_the_second_day_of_a_month_exits_0_whatever_the_usage(billing) -> None:
    billing([{"product": "Actions", "sku": "actions_linux", "grossQuantity": 2900.0, "unitType": "minutes"}])
    result = run_config("usage", "--org", ORG, "--today", "2026-09-02")
    assert (result.returncode, result.stdout) == (
        0,
        "Actions: 2900 of 3000 minutes used, day 2 of 30, projected 43500\n",
    )


def test_from_day_moves_the_day_the_projection_starts_to_warn(billing) -> None:
    billing(TWO_ITEMS)
    assert run_config("usage", "--org", ORG, "--today", "2026-09-05", "--from-day", "6").returncode == 0
    assert run_config("usage", "--org", ORG, "--today", "2026-09-06", "--from-day", "6").returncode == 1


def test_allowance_is_taken_from_the_option(billing) -> None:
    billing(TWO_ITEMS)
    result = run_config("usage", "--org", ORG, "--today", "2026-09-05", "--allowance", "5000")
    assert (result.returncode, result.stdout) == (0, "Actions: 750 of 5000 minutes used, day 5 of 30, projected 4500\n")


@pytest.mark.parametrize("items", [[], OTHER_ITEMS])
def test_a_month_with_no_actions_minutes_prints_no_usage_yet_and_exits_0(billing, items: list[dict]) -> None:
    billing(items)
    for day in ("2026-09-01", "2026-09-20"):
        result = run_config("usage", "--org", ORG, "--today", day)
        assert (result.returncode, result.stdout, result.stderr) == (0, "no usage yet\n", "")


def test_the_summary_is_read_from_the_organisations_billing_endpoint(billing, tmp_path: Path) -> None:
    billing(TWO_ITEMS)
    run_config("usage", "--org", ORG, "--today", "2026-09-08")
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [(c["method"], c["endpoint"]) for c in calls] == [
        ("GET", f"organizations/{ORG}/settings/billing/usage/summary")
    ]


def test_a_failing_gh_exits_2_with_its_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho 'gh: Not Found (HTTP 404)' >&2\nexit 1\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = run_config("usage", "--org", ORG, "--today", "2026-09-08")
    assert (result.returncode, result.stdout) == (2, "")
    assert "Not Found" in result.stderr
