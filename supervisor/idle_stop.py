#!/usr/bin/env python3
"""Stops this host when the runner supervisor has recorded no job for a quiet period.

Run every minute by a root systemd timer. It reads every state file the supervisors write, one per runner
(`busy <epoch>` while a job runs, `idle <epoch>` from the moment the last one ended or the supervisor started),
and runs `shutdown -h now` once all of them have been idle for `--idle-stop-minutes`: one busy, fresh file keeps
the host up, and each file's quiet period is counted on its own. On an EC2 instance whose shutdown
behaviour is `stop`, that stops it. It never stops a host a job is running on: `busy` is refreshed by the
supervisor every few seconds, and only a `busy` record older than `BUSY_STALE_SECONDS` (a supervisor that died)
counts as idle, from the time it was last refreshed. With no state file at all, or an unreadable one, the host's
boot time stands in for the last activity.

It is a separate root process so the supervisor, which runs as `pf-runner` in the `docker` group, is never given
the right to stop the host: the only things it can influence are the two words and the number in its file.
"""

import argparse
import glob
import shlex
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_IDLE_STOP_MINUTES = 15.0
DEFAULT_SHUTDOWN_COMMAND = "shutdown -h now"
DEFAULT_STATE_GLOB = "/var/lib/productforge-runner/activity*"
BUSY_STALE_SECONDS = 300.0


def log(*, message: str) -> None:
    """Writes one line to stdout, which systemd keeps in the journal."""
    print(f"idle-stop: {message}", flush=True)


def read_state(*, path: Path) -> tuple[str, float] | None:
    """The state file's `busy` or `idle` and its epoch, or None when it is missing or not in that form."""
    try:
        state, epoch = path.read_text().split()
        return (state, float(epoch)) if state in ("busy", "idle") else None
    except (OSError, ValueError):  # fmt: skip
        return None


def boot_time(*, now: float) -> float:
    """When the host booted, from /proc/uptime; now when that cannot be read."""
    try:
        return now - float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):  # fmt: skip
        return now


def idle_seconds(*, state: tuple[str, float] | None, now: float, booted: float) -> float:
    """How long the host has been idle: 0 while a job runs, else the time since the later of the last activity and
    the boot, so a state file left by a previous run of the host adds nothing.
    """
    if state is None:
        return now - booted
    kind, epoch = state
    epoch = min(epoch, now)
    if kind == "busy" and now - epoch < BUSY_STALE_SECONDS:
        return 0.0
    return now - max(epoch, booted)


def state_files(*, pattern: str) -> list[Path]:
    """The state files matching the glob, leaving out the supervisor's half-written `.tmp` files."""
    return sorted(Path(m) for m in glob.glob(pattern) if not m.endswith(".tmp"))


def host_idle_seconds(*, pattern: str, now: float, booted: float) -> float:
    """How long the whole host has been idle: the least of every state file's idle time, so one busy runner keeps
    it at 0. With no state file the host's boot time stands in for the last activity.
    """
    paths = state_files(pattern=pattern)
    if not paths:
        return now - booted
    return min(idle_seconds(state=read_state(path=path), now=now, booted=booted) for path in paths)


def check(*, idle_stop_minutes: float, now: float, shutdown: list[str], state_glob: str, booted: float) -> bool:
    """Runs the shutdown command when the host has been idle long enough; True when it did."""
    if idle_stop_minutes <= 0:
        return False
    idle = host_idle_seconds(pattern=state_glob, now=now, booted=booted)
    if idle < idle_stop_minutes * 60:
        return False
    # Re-read every file just before stopping: a job that started since the first read keeps the host up.
    idle = host_idle_seconds(pattern=state_glob, now=now, booted=booted)
    if idle < idle_stop_minutes * 60:
        log(message="a job started while the host was about to stop: not stopping it")
        return False
    log(message=f"idle for {idle / 60:.1f} minutes, at or over {idle_stop_minutes:g}: stopping the host")
    subprocess.run(shutdown, check=True)
    return True


def main(argv: list[str] | None = None) -> int:
    """The command's entry: exit status 0 whether or not it stopped the host, 1 when the shutdown command fails."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--idle-stop-minutes",
        type=float,
        default=DEFAULT_IDLE_STOP_MINUTES,
        help="stop the host after this many idle minutes; 0 never stops it (default: %(default)s)",
    )
    parser.add_argument("--shutdown-command", default=DEFAULT_SHUTDOWN_COMMAND, help="(default: %(default)s)")
    parser.add_argument(
        "--state-glob",
        default=DEFAULT_STATE_GLOB,
        help="the supervisors' state files, one per runner (default: %(default)s)",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    now = time.time()
    try:
        check(
            idle_stop_minutes=args.idle_stop_minutes,
            now=now,
            shutdown=shlex.split(args.shutdown_command),
            state_glob=args.state_glob,
            booted=boot_time(now=now),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"idle-stop: the shutdown command failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
