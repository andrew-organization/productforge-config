import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

NOW = 1_000_000.0
MINUTE = 60


@pytest.fixture(scope="module")
def idle_stop(shipped: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("idle_stop", shipped / "supervisor" / "idle_stop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "activity"


@pytest.fixture
def shutdowns(tmp_path: Path) -> list[str]:
    return [sys.executable, "-c", f"open({str(tmp_path / 'shutdown.log')!r}, 'a').write('stopped\\n')"]


def stopped(tmp_path: Path) -> bool:
    return (tmp_path / "shutdown.log").exists()


def run(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], *, minutes: float = 15, now: float = NOW
) -> bool:
    return idle_stop.check(
        idle_stop_minutes=minutes, now=now, shutdown=shutdowns, state_glob=str(state_file), booted=NOW - 3 * 3600
    )


def test_a_job_finishing_then_the_quiet_period_passing_stops_the_host(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], tmp_path: Path
) -> None:
    state_file.write_text(f"idle {int(NOW - 15 * MINUTE)}\n")
    assert run(idle_stop, state_file, shutdowns) is True
    assert stopped(tmp_path)


def test_a_host_idle_for_less_than_the_quiet_period_stays_up(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], tmp_path: Path
) -> None:
    state_file.write_text(f"idle {int(NOW - 14 * MINUTE)}\n")
    assert run(idle_stop, state_file, shutdowns) is False
    assert not stopped(tmp_path)


def test_a_job_arriving_within_the_quiet_period_keeps_the_host_up(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], tmp_path: Path
) -> None:
    state_file.write_text(f"busy {int(NOW - 10)}\n")  # refreshed by the supervisor a few seconds ago
    assert run(idle_stop, state_file, shutdowns) is False
    assert not stopped(tmp_path)


def test_a_job_that_ends_restarts_the_quiet_period(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], tmp_path: Path
) -> None:
    state_file.write_text(f"idle {int(NOW - 2 * MINUTE)}\n")  # written when the job ended, after a long busy spell
    assert run(idle_stop, state_file, shutdowns) is False
    assert not stopped(tmp_path)


def test_a_busy_record_a_supervisor_stopped_refreshing_counts_as_idle_from_its_last_refresh(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], tmp_path: Path
) -> None:
    state_file.write_text(f"busy {int(NOW - 20 * MINUTE)}\n")
    assert run(idle_stop, state_file, shutdowns) is True
    assert stopped(tmp_path)


@pytest.mark.parametrize("age_minutes", [0, 15, 24 * 60])
def test_zero_minutes_never_stops_the_host(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], tmp_path: Path, age_minutes: int
) -> None:
    state_file.write_text(f"idle {int(NOW - age_minutes * MINUTE)}\n")
    assert run(idle_stop, state_file, shutdowns, minutes=0) is False
    assert not stopped(tmp_path)


@pytest.mark.parametrize("content", [None, "", "idle", "idle soon", "running 5"])
def test_without_a_readable_state_the_boot_time_stands_in_for_the_last_activity(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], tmp_path: Path, content: str | None
) -> None:
    if content is not None:
        state_file.write_text(content)
    assert run(idle_stop, state_file, shutdowns) is True  # booted three hours before `now`
    assert idle_stop.idle_seconds(state=None, now=NOW, booted=NOW - 5 * MINUTE) == 5 * MINUTE


def test_a_state_file_left_by_a_previous_run_of_the_host_counts_from_the_boot(
    idle_stop: ModuleType, state_file: Path, shutdowns: list[str], tmp_path: Path
) -> None:
    state_file.write_text(f"idle {int(NOW - 3 * 3600)}\n")  # written hours ago, before the host last booted
    booted = NOW - 5 * MINUTE
    assert (
        idle_stop.check(idle_stop_minutes=15, now=NOW, shutdown=shutdowns, state_glob=str(state_file), booted=booted)
        is False
    )
    assert not stopped(tmp_path)
    assert idle_stop.idle_seconds(state=("idle", NOW - 3 * 3600), now=NOW, booted=booted) == 5 * MINUTE
    assert idle_stop.idle_seconds(state=("busy", NOW - 3 * 3600), now=NOW, booted=booted) == 5 * MINUTE
    later = NOW + 11 * MINUTE  # the quiet period then passes from the boot
    assert idle_stop.check(
        idle_stop_minutes=15, now=later, shutdown=shutdowns, state_glob=str(state_file), booted=booted
    )
    assert stopped(tmp_path)


def test_a_job_that_becomes_busy_just_before_the_shutdown_keeps_the_host_up(
    idle_stop: ModuleType,
    state_file: Path,
    shutdowns: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file.write_text(f"idle {int(NOW - 20 * MINUTE)}\n")
    reads = iter([("idle", NOW - 20 * MINUTE), ("busy", NOW - 1)])  # the second read is the one before the shutdown
    monkeypatch.setattr(idle_stop, "read_state", lambda *, path: next(reads))
    assert run(idle_stop, state_file, shutdowns) is False
    assert not stopped(tmp_path)


@pytest.mark.skipif(not Path("/proc/uptime").exists(), reason="the idle stop reads the boot time from /proc/uptime")
def test_the_command_runs_the_shutdown_command_it_is_given_once_the_host_is_idle(
    shipped: Path, state_file: Path, tmp_path: Path
) -> None:
    script = shipped / "supervisor" / "idle_stop.py"
    command = [
        sys.executable,
        str(script),
        "--state-glob",
        str(state_file),
        "--shutdown-command",
        f"touch {tmp_path / 'down'}",
    ]
    state_file.write_text(f"idle {int(time.time()) - 60}\n")
    subprocess.run([*command, "--idle-stop-minutes", "0"], check=True)
    assert not (tmp_path / "down").exists()
    result = subprocess.run([*command, "--idle-stop-minutes", "0.001"], capture_output=True, text=True, check=True)
    assert (tmp_path / "down").exists()
    assert re.search(r"idle-stop: idle for .* minutes", result.stdout)


def test_the_idle_stop_is_a_root_timer_beside_a_supervisor_that_runs_as_the_runner_user(shipped: Path) -> None:
    supervisor = (shipped / "supervisor" / "productforge-runner-supervisor@.service").read_text()
    stop = (shipped / "supervisor" / "productforge-runner-idle-stop.service").read_text()
    timer = (shipped / "supervisor" / "productforge-runner-idle-stop.timer").read_text()
    assert "User=pf-runner" in supervisor and "--state-file /var/lib/productforge-runner/activity-%i" in supervisor
    assert "User=" not in stop and "--idle-stop-minutes 15" in stop
    assert "OnUnitActiveSec=1min" in timer


def several(idle_stop: ModuleType, tmp_path: Path, shutdowns: list[str], **files: str) -> bool:
    for name, content in files.items():
        (tmp_path / f"activity-{name}").write_text(content)
    return idle_stop.check(
        idle_stop_minutes=15,
        now=NOW,
        shutdown=shutdowns,
        state_glob=str(tmp_path / "activity*"),
        booted=NOW - 3 * 3600,
    )


def test_one_busy_runner_among_idle_ones_keeps_the_host_up(
    idle_stop: ModuleType, tmp_path: Path, shutdowns: list[str]
) -> None:
    old = f"idle {int(NOW - 60 * MINUTE)}\n"
    assert several(idle_stop, tmp_path, shutdowns, **{"1": old, "2": f"busy {int(NOW - 5)}\n", "3": old}) is False
    assert not stopped(tmp_path)


def test_every_runner_idle_for_the_quiet_period_stops_the_host(
    idle_stop: ModuleType, tmp_path: Path, shutdowns: list[str]
) -> None:
    old = f"idle {int(NOW - 20 * MINUTE)}\n"
    assert several(idle_stop, tmp_path, shutdowns, **{"1": old, "2": old, "3": old}) is True
    assert stopped(tmp_path)


def test_each_files_quiet_period_is_counted_on_its_own(
    idle_stop: ModuleType, tmp_path: Path, shutdowns: list[str]
) -> None:
    stale = f"idle {int(NOW - 2 * 3600)}\n"  # idle for two hours
    fresh = f"idle {int(NOW - 3 * MINUTE)}\n"  # a job ended three minutes ago
    assert several(idle_stop, tmp_path, shutdowns, **{"1": stale, "2": fresh}) is False
    assert not stopped(tmp_path)
    glob = str(tmp_path / "activity*")
    assert idle_stop.host_idle_seconds(pattern=glob, now=NOW, booted=NOW - 3 * 3600) == 3 * MINUTE
    assert idle_stop.host_idle_seconds(pattern=glob, now=NOW + 12 * MINUTE, booted=NOW - 3 * 3600) == 15 * MINUTE


def test_a_busy_record_that_stopped_refreshing_does_not_hold_the_host_up_beside_idle_runners(
    idle_stop: ModuleType, tmp_path: Path, shutdowns: list[str]
) -> None:
    dead = f"busy {int(NOW - 30 * MINUTE)}\n"
    assert several(idle_stop, tmp_path, shutdowns, **{"1": dead, "2": f"idle {int(NOW - 40 * MINUTE)}\n"}) is True


def test_a_runner_that_became_busy_just_before_the_shutdown_keeps_the_host_up(
    idle_stop: ModuleType, tmp_path: Path, shutdowns: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "activity-1").write_text(f"idle {int(NOW - 20 * MINUTE)}\n")
    (tmp_path / "activity-2").write_text(f"idle {int(NOW - 20 * MINUTE)}\n")
    reads = iter([("idle", NOW - 20 * MINUTE)] * 2 + [("idle", NOW - 20 * MINUTE), ("busy", NOW - 1)])
    monkeypatch.setattr(idle_stop, "read_state", lambda *, path: next(reads))  # the second pair is the re-read
    assert (
        idle_stop.check(
            idle_stop_minutes=15,
            now=NOW,
            shutdown=shutdowns,
            state_glob=str(tmp_path / "activity*"),
            booted=NOW - 3 * 3600,
        )
        is False
    )
    assert not stopped(tmp_path)


def test_the_supervisors_half_written_tmp_files_are_not_state_files(idle_stop: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "activity-1").write_text("idle 1\n")
    (tmp_path / "activity-1.tmp").write_text("busy 1\n")
    assert idle_stop.state_files(pattern=str(tmp_path / "activity*")) == [tmp_path / "activity-1"]
