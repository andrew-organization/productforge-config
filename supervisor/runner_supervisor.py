#!/usr/bin/env python3
"""Runs one GitHub Actions job at a time on a fresh, throwaway self-hosted runner.

The supervisor registers a just-in-time runner in one runner group, starts it in a new `docker run --rm`
container, and waits for the container to exit, which a just-in-time runner does after exactly one job.
Then it removes the container and the runner's registration and starts the next. It uses the Python
standard library and the `docker` command, so a host needs nothing else installed.

How it learns a job is queued: it doesn't ask. One runner is always registered and waiting; GitHub hands the
next queued job for the runner's labels to it, and the runner's container ending is the signal to start the
next. That needs only the permission to register runners, where polling the queued jobs would also need
read access to every repository's Actions.

Every runner the supervisor registers is named `<prefix>-<random>`; at start it removes the registrations
and containers a previous run left under its prefix, so one supervisor per prefix and group.

A job can run anything, so its container is confined: every capability dropped, no privilege gained, a
process and a memory limit, and, checked once at start, no route to the EC2 instance metadata service.
"""

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_API_URL = "https://api.github.com"
# The arm64 manifest of ghcr.io/actions/actions-runner:latest on 2026-10-15; the README says how to move it.
DEFAULT_IMAGE = "ghcr.io/actions/actions-runner@sha256:9644537b29b4799cb752a2f23a45a1e603cf74c3c5998241a1533f0e8c3dc649"
DEFAULT_MEMORY = "4g"
DEFAULT_PIDS_LIMIT = 512
METADATA_ADDRESS = "169.254.169.254"
CURL_COULD_NOT_CONNECT = 7
CURL_TIMED_OUT = 28
DELETE_ATTEMPTS = 5
DELETE_BACKOFF_CAP_SECONDS = 15.0
DOCKER_BACKOFF_CAP_SECONDS = 300.0
DEFAULT_PREFIX = "pf"
LABEL = "productforge.runner-supervisor"
PAGE_SIZE = 100
POLL_SECONDS = 1.0


class SupervisorError(Exception):
    """A refusal the supervisor reports as one line and exit status 1.

    `status` is GitHub's HTTP status when the refusal came from there.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Settings:
    """What one supervisor run is configured with."""

    api_url: str
    docker: str
    group: str
    image: str
    labels: tuple[str, ...]
    max_jobs: int
    memory: str
    metadata_reachable_allowed: bool
    org: str
    pids_limit: int
    prefix: str
    retry_seconds: float
    token_file: str | None


class Stop:
    """Set by SIGTERM or SIGINT; the loop checks it between waits."""

    def __init__(self) -> None:
        self.requested = False

    def request(self, *_: Any) -> None:
        self.requested = True


def log(*, message: str) -> None:
    """Writes one timestamped line to stdout, which systemd keeps in the journal."""
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}", flush=True)


def read_token(*, token_file: str | None) -> str:
    """Reads the token fresh each time, so a rotated token file is picked up without a restart."""
    if token_file:
        try:
            token = Path(token_file).read_text().strip()
        except OSError as error:
            raise SupervisorError(f"cannot read the token file {token_file}: {error.strerror}") from error
    else:
        token = os.environ.get("PF_RUNNER_TOKEN", "").strip()
    if not token:
        raise SupervisorError("no token: pass --token-file or set PF_RUNNER_TOKEN")
    return token


def api(*, method: str, path: str, settings: Settings, body: dict[str, Any] | None = None) -> Any:
    """Calls GitHub's REST API as the runner-registration token and returns the decoded JSON, or None."""
    request = urllib.request.Request(
        f"{settings.api_url}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {read_token(token_file=settings.token_file)}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        raise SupervisorError(f"GitHub answered {error.code} to {method} {path}", status=error.code) from error
    except urllib.error.URLError as error:
        raise SupervisorError(f"cannot reach GitHub for {method} {path}: {error.reason}") from error
    return json.loads(payload) if payload else None


def pages(*, key: str, path: str, settings: Settings) -> list[dict[str, Any]]:
    """Every item of a paginated list endpoint."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        joiner = "&" if "?" in path else "?"
        data = api(method="GET", path=f"{path}{joiner}per_page={PAGE_SIZE}&page={page}", settings=settings)
        items.extend(data[key])
        if len(data[key]) < PAGE_SIZE:
            return items
        page += 1


def find_group(*, settings: Settings) -> int:
    """The id of the runner group the supervisor's name is given for."""
    groups = pages(key="runner_groups", path=f"/orgs/{settings.org}/actions/runner-groups", settings=settings)
    for group in groups:
        if group["name"] == settings.group:
            return group["id"]
    raise SupervisorError(f"organisation {settings.org} has no runner group named {settings.group}")


def group_runners(*, group_id: int, settings: Settings) -> list[dict[str, Any]]:
    """The runners registered in the group."""
    path = f"/orgs/{settings.org}/actions/runner-groups/{group_id}/runners"
    return pages(key="runners", path=path, settings=settings)


def pause(*, seconds: float, stop: Stop) -> None:
    """Sleeps for the time given, or until a stop is requested."""
    deadline = time.monotonic() + seconds
    while not stop.requested and (remaining := deadline - time.monotonic()) > 0:
        time.sleep(min(remaining, POLL_SECONDS))


def delete_runner(*, runner_id: int, settings: Settings) -> bool:
    """Removes a registration and returns True, or False when GitHub still holds it as busy.

    One already gone (a runner deregisters itself after its job) is fine. A 422 means the runner is
    still busy, which it is for a moment after its container is removed; the delete is retried with a
    bounded backoff and then left, to be swept at the next start.
    """
    for attempt in range(DELETE_ATTEMPTS):
        try:
            api(method="DELETE", path=f"/orgs/{settings.org}/actions/runners/{runner_id}", settings=settings)
            return True
        except SupervisorError as error:
            if error.status == 404:
                return True
            if error.status != 422:
                raise
        if attempt < DELETE_ATTEMPTS - 1:
            time.sleep(min(settings.retry_seconds * 2**attempt, DELETE_BACKOFF_CAP_SECONDS))
    log(message=f"runner {runner_id} is still busy after {DELETE_ATTEMPTS} attempts to remove it; leaving it")
    return False


def docker(*, args: list[str], settings: Settings) -> subprocess.CompletedProcess[str]:
    """Runs the container runtime to completion and captures its output."""
    return subprocess.run([settings.docker, *args], capture_output=True, text=True, check=False)


def docker_available(*, settings: Settings) -> bool:
    """Whether the container runtime's command exists and its daemon answers."""
    try:
        return docker(args=["info"], settings=settings).returncode == 0
    except OSError:
        return False


def wait_for_docker(*, settings: Settings, stop: Stop) -> bool:
    """Waits, backing off, until the runtime answers; False when a stop came first.

    Nothing is registered while it is down: a registration without a container to take the job would
    sit idle and offline.
    """
    delay = settings.retry_seconds
    cap = max(DOCKER_BACKOFF_CAP_SECONDS, settings.retry_seconds)
    while not stop.requested:
        if docker_available(settings=settings):
            return True
        log(message=f"{settings.docker} is not available; checking again in {delay:g}s")
        pause(seconds=delay, stop=stop)
        delay = min(delay * 2, cap)
    return False


def hardening(*, settings: Settings) -> list[str]:
    """The `docker run` options that confine a container."""
    return [
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(settings.pids_limit),
        "--memory",
        settings.memory,
    ]


def check_metadata_unreachable(*, settings: Settings) -> None:
    """Refuses to start when a container can reach the EC2 instance metadata service.

    Runs a short container, confined as a job's is, that asks 169.254.169.254 for anything at all; any
    HTTP answer means a job could read the instance's role credentials. Only a refused or timed-out
    connection passes. A check that cannot run is also a refusal.
    """
    if settings.metadata_reachable_allowed:
        log(message=f"not checking that containers cannot reach {METADATA_ADDRESS}: --allow-metadata-reachable")
        return
    result = docker(
        args=[
            "run",
            "--rm",
            *hardening(settings=settings),
            settings.image,
            "curl",
            "--silent",
            "--output",
            "/dev/null",
            "--max-time",
            "5",
            "--write-out",
            "%{http_code}",
            f"http://{METADATA_ADDRESS}/latest/meta-data/",
        ],
        settings=settings,
    )
    answer = result.stdout.strip()
    if result.returncode == 0 or answer not in ("", "000"):
        raise SupervisorError(
            f"a container can reach the instance metadata service at {METADATA_ADDRESS} (HTTP {answer or 'answer'}); "
            "require IMDSv2 with hop limit 1 on the instance, or pass --allow-metadata-reachable on a host that "
            "is not an EC2 instance"
        )
    if result.returncode not in (CURL_COULD_NOT_CONNECT, CURL_TIMED_OUT):
        raise SupervisorError(
            f"could not run the check that containers cannot reach {METADATA_ADDRESS} "
            f"(exit {result.returncode}): {result.stderr.strip()[:200]}"
        )


def remove_container(*, name: str, settings: Settings) -> None:
    """Force-removes a container, stopping its job if it is still running."""
    docker(args=["rm", "-f", name], settings=settings)


def sweep(*, group_id: int, settings: Settings) -> None:
    """Removes the registrations and containers a previous run left under this prefix."""
    stale = [
        r for r in group_runners(group_id=group_id, settings=settings) if r["name"].startswith(f"{settings.prefix}-")
    ]
    for runner in stale:
        log(message=f"removing the leftover registration {runner['name']}")
        if not delete_runner(runner_id=runner["id"], settings=settings):
            log(message=f"left the registration {runner['name']}, which GitHub still holds as busy")
    listed = docker(args=["ps", "-aq", "--filter", f"label={LABEL}={settings.prefix}"], settings=settings)
    for container in listed.stdout.split():
        log(message=f"removing the leftover container {container}")
        remove_container(name=container, settings=settings)


def run_one(*, group_id: int, settings: Settings, stop: Stop) -> bool:
    """Registers one runner, runs it in a fresh container until it exits, and removes both.

    Returns True when the container ended on its own with status 0, the one job done.
    """
    name = f"{settings.prefix}-{secrets.token_hex(4)}"
    labels = list(dict.fromkeys(["self-hosted", *settings.labels]))
    registered = api(
        method="POST",
        path=f"/orgs/{settings.org}/actions/runners/generate-jitconfig",
        settings=settings,
        body={"labels": labels, "name": name, "runner_group_id": group_id, "work_folder": "_work"},
    )
    runner_id = registered["runner"]["id"]
    log(message=f"registered runner {name}; waiting for a job")
    container = None
    status = None
    try:
        command = [
            settings.docker,
            "run",
            "--rm",
            "--name",
            name,
            *hardening(settings=settings),
            "--label",
            f"{LABEL}={settings.prefix}",
            "--env",
            "ACTIONS_RUNNER_INPUT_JITCONFIG",
            settings.image,
            "./run.sh",
        ]
        try:
            container = subprocess.Popen(
                command,
                env={**os.environ, "ACTIONS_RUNNER_INPUT_JITCONFIG": registered["encoded_jit_config"]},
                stdin=subprocess.DEVNULL,
            )
        except OSError as error:
            raise SupervisorError(f"cannot start {settings.docker}: {error.strerror}") from error
        while status is None and not stop.requested:
            try:
                status = container.wait(timeout=POLL_SECONDS)
            except subprocess.TimeoutExpired:
                continue
        if stop.requested:
            log(message=f"stopped: removing {name}")
        else:
            log(message=f"{name} exited with status {status}")
    finally:
        remove_container(name=name, settings=settings)
        if container is not None:
            container.wait()
        removed = delete_runner(runner_id=runner_id, settings=settings)
        log(message=f"removed {'runner and ' if removed else ''}container {name}")
    return status == 0


def supervise(*, settings: Settings, stop: Stop) -> None:
    """Runs jobs one after another until stopped, or until `max_jobs` runners have run when that is set."""
    group_id = find_group(settings=settings)
    if not wait_for_docker(settings=settings, stop=stop):
        return
    check_metadata_unreachable(settings=settings)
    sweep(group_id=group_id, settings=settings)
    runs = 0
    while not stop.requested and (settings.max_jobs == 0 or runs < settings.max_jobs):
        if not wait_for_docker(settings=settings, stop=stop):
            return
        try:
            succeeded = run_one(group_id=group_id, settings=settings, stop=stop)
        except SupervisorError as error:
            log(message=f"{error}; trying again in {settings.retry_seconds:g}s")
            succeeded = False
        runs += 1
        if not succeeded:
            pause(seconds=settings.retry_seconds, stop=stop)


def status(*, settings: Settings) -> None:
    """Prints the runners registered under the prefix and the containers running."""
    group_id = find_group(settings=settings)
    for runner in group_runners(group_id=group_id, settings=settings):
        if runner["name"].startswith(f"{settings.prefix}-"):
            state = "busy" if runner.get("busy") else runner.get("status", "unknown")
            print(f"runner {runner['name']} {state}")
    listed = docker(
        args=["ps", "--filter", f"label={LABEL}={settings.prefix}", "--format", "{{.Names}} {{.Status}}"],
        settings=settings,
    )
    for line in listed.stdout.splitlines():
        print(f"container {line}")


def parse(*, argv: list[str]) -> tuple[str, Settings]:
    """Reads the command line into the command and its settings."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("run", "status"), help="run the supervisor, or print its state")
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", DEFAULT_API_URL))
    parser.add_argument("--docker", default="docker", help="the container runtime's command")
    parser.add_argument("--group", required=True, help="name of the runner group to register in")
    parser.add_argument(
        "--allow-metadata-reachable",
        action="store_true",
        help="skip the start-up check that containers cannot reach the EC2 metadata service (a local test only)",
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="the runner image, by digest (default: %(default)s)")
    parser.add_argument("--labels", default="", help="comma-separated labels besides self-hosted")
    parser.add_argument("--max-jobs", type=int, default=0, help="stop after this many runners (0 is never)")
    parser.add_argument("--memory", default=DEFAULT_MEMORY, help="each container's memory limit (default: %(default)s)")
    parser.add_argument("--org", required=True, help="the GitHub organisation")
    parser.add_argument(
        "--pids-limit",
        type=int,
        default=DEFAULT_PIDS_LIMIT,
        help="each container's process limit (default: %(default)s)",
    )
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="runner names start with this and a dash")
    parser.add_argument("--retry-seconds", type=float, default=5.0, help="wait after a failed job or call")
    parser.add_argument("--token-file", help="file holding the token (default: $PF_RUNNER_TOKEN)")
    args = parser.parse_args(argv)
    labels = tuple(label for label in args.labels.split(",") if label)
    return args.command, Settings(
        api_url=args.api_url.rstrip("/"),
        docker=args.docker,
        group=args.group,
        image=args.image,
        labels=labels,
        max_jobs=args.max_jobs,
        memory=args.memory,
        metadata_reachable_allowed=args.allow_metadata_reachable,
        org=urllib.parse.quote(args.org),
        pids_limit=args.pids_limit,
        prefix=args.prefix,
        retry_seconds=args.retry_seconds,
        token_file=args.token_file,
    )


def main(argv: list[str] | None = None) -> int:
    """The command's entry: exit status 0 when it ends as asked, 1 with one line on stderr when it refuses."""
    command, settings = parse(argv=sys.argv[1:] if argv is None else argv)
    stop = Stop()
    signal.signal(signal.SIGTERM, stop.request)
    signal.signal(signal.SIGINT, stop.request)
    try:
        if command == "status":
            status(settings=settings)
        else:
            supervise(settings=settings, stop=stop)
    except SupervisorError as error:
        print(f"runner-supervisor: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
