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
DEFAULT_IMAGE = "ghcr.io/actions/actions-runner:latest"
DEFAULT_PREFIX = "pf"
LABEL = "productforge.runner-supervisor"
PAGE_SIZE = 100
POLL_SECONDS = 1.0


class SupervisorError(Exception):
    """A refusal the supervisor reports as one line and exit status 1."""


@dataclass(frozen=True)
class Settings:
    """What one supervisor run is configured with."""

    api_url: str
    docker: str
    group: str
    image: str
    labels: tuple[str, ...]
    max_jobs: int
    org: str
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
        raise SupervisorError(f"GitHub answered {error.code} to {method} {path}") from error
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


def delete_runner(*, runner_id: int, settings: Settings) -> None:
    """Removes a registration; one already gone (a runner deregisters itself after its job) is fine."""
    try:
        api(method="DELETE", path=f"/orgs/{settings.org}/actions/runners/{runner_id}", settings=settings)
    except SupervisorError as error:
        if " 404 " not in f" {error} ":
            raise


def docker(*, args: list[str], settings: Settings) -> subprocess.CompletedProcess[str]:
    """Runs the container runtime to completion and captures its output."""
    return subprocess.run([settings.docker, *args], capture_output=True, text=True, check=False)


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
        delete_runner(runner_id=runner["id"], settings=settings)
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
            "--label",
            f"{LABEL}={settings.prefix}",
            "--env",
            "ACTIONS_RUNNER_INPUT_JITCONFIG",
            settings.image,
            "./run.sh",
        ]
        container = subprocess.Popen(
            command,
            env={**os.environ, "ACTIONS_RUNNER_INPUT_JITCONFIG": registered["encoded_jit_config"]},
            stdin=subprocess.DEVNULL,
        )
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
        delete_runner(runner_id=runner_id, settings=settings)
        log(message=f"removed runner and container {name}")
    return status == 0


def supervise(*, settings: Settings, stop: Stop) -> None:
    """Runs jobs one after another until stopped, or until `max_jobs` runners have run when that is set."""
    group_id = find_group(settings=settings)
    sweep(group_id=group_id, settings=settings)
    runs = 0
    while not stop.requested and (settings.max_jobs == 0 or runs < settings.max_jobs):
        try:
            succeeded = run_one(group_id=group_id, settings=settings, stop=stop)
        except SupervisorError as error:
            log(message=f"{error}; trying again in {settings.retry_seconds:g}s")
            succeeded = False
        runs += 1
        if not succeeded and not stop.requested:
            time.sleep(settings.retry_seconds)


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
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--labels", default="", help="comma-separated labels besides self-hosted")
    parser.add_argument("--max-jobs", type=int, default=0, help="stop after this many runners (0 is never)")
    parser.add_argument("--org", required=True, help="the GitHub organisation")
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
        org=urllib.parse.quote(args.org),
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
