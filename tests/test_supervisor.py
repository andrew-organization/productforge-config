"""The runner supervisor, run as a person runs it against a fake registration endpoint and a fake container runtime.

The endpoint is a local HTTP server that keeps the registrations the way GitHub does: a runner that passed
its job deregisters itself. The runtime is a `docker` program on PATH that records its calls and plays the
outcome a case scripted for each container: `pass`, `fail` or `hang` (a job that never ends).
"""

import http.server
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

GROUP_ID = 7

STUB_DOCKER = """#!{python}
import json, os, sys, time, urllib.request
from pathlib import Path

root = Path({root!r})
args = sys.argv[1:]
with (root / "calls.log").open("a") as log:
    log.write(" ".join(args) + "\\n")
if args[0] == "info":
    sys.exit(1 if (root / "docker-down").exists() else 0)
elif args[0] == "run" and any("curl" in a for a in args):
    # The metadata check: what the token request and the request without a token answer, as "TOKEN METADATA".
    answer = (root / "metadata").read_text() if (root / "metadata").exists() else "000 000"
    if answer == "cannot-run":
        sys.exit(125)
    print(answer, end="")
    sys.exit(0)
elif args[0] == "ps":
    print((root / "leftover").read_text() if (root / "leftover").exists() else "", end="")
elif args[0] == "rm":
    (root / ("killed-" + args[2])).write_text("")
elif args[0] == "run":
    name = args[args.index("--name") + 1]
    (root / "jitconfig").write_text(os.environ["ACTIONS_RUNNER_INPUT_JITCONFIG"])
    outcomes = (root / "outcomes").read_text().split()
    outcome = outcomes.pop(0)
    (root / "outcomes").write_text(" ".join(outcomes))
    if outcome == "hang":
        while not (root / ("killed-" + name)).exists():
            time.sleep(0.05)
        sys.exit(137)
    if outcome == "fail-then-down":
        (root / "docker-down").write_text("")
        sys.exit(1)
    if outcome == "pass":
        urllib.request.urlopen(urllib.request.Request({url!r} + "/_job_done/" + name, method="POST"))
    sys.exit(0 if outcome == "pass" else 1)
"""


@dataclass
class Endpoint:
    """The fake registration endpoint: its registrations, the calls made to it and its address."""

    url: str = ""
    runners: dict[int, str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    bodies: list[dict[str, Any]] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)
    next_id: int = 100
    busy: bool = False  # what GET of one runner answers for its `busy`
    busy_get_status: int = 200  # what GET of one runner answers with; GitHub failing is not a job's end
    busy_deletes: int = 0  # the next this many DELETEs answer 422, as GitHub does for a runner still busy

    @property
    def names(self) -> list[str]:
        return sorted(self.runners.values())


@pytest.fixture
def endpoint() -> Iterator[Endpoint]:
    state = Endpoint()

    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self, status: int, body: Any = None) -> None:
            payload = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def handle_any(self, method: str) -> None:
            path = self.path.split("?")[0]
            state.calls.append(f"{method} {path}")
            if not path.startswith("/_"):
                state.tokens.add(self.headers.get("Authorization", ""))
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            if (method, path) == ("GET", "/orgs/acme/actions/runner-groups"):
                self.reply(200, {"runner_groups": [{"id": GROUP_ID, "name": "pf-group"}, {"id": 1, "name": "Default"}]})
            elif (method, path) == ("GET", f"/orgs/acme/actions/runner-groups/{GROUP_ID}/runners"):
                self.reply(200, {"runners": [{"id": i, "name": n} for i, n in state.runners.items()]})
            elif (method, path) == ("POST", "/orgs/acme/actions/runners/generate-jitconfig"):
                state.bodies.append(body)
                state.next_id += 1
                state.runners[state.next_id] = body["name"]
                self.reply(201, {"runner": {"id": state.next_id, "name": body["name"]}, "encoded_jit_config": "JIT"})
            elif method == "GET" and path.startswith("/orgs/acme/actions/runners/"):
                runner_id = int(path.rsplit("/", 1)[1])
                if state.busy_get_status != 200:
                    self.reply(state.busy_get_status, {"message": "unavailable"})
                else:
                    self.reply(200, {"id": runner_id, "name": state.runners[runner_id], "busy": state.busy})
            elif method == "DELETE" and path.startswith("/orgs/acme/actions/runners/"):
                runner_id = int(path.rsplit("/", 1)[1])
                if state.busy_deletes and runner_id in state.runners:
                    state.busy_deletes -= 1
                    self.reply(422, {"message": "Runner is busy"})
                else:
                    self.reply(204 if state.runners.pop(runner_id, None) else 404)
            elif method == "POST" and path.startswith("/_job_done/"):
                name = path.rsplit("/", 1)[1]
                state.runners = {i: n for i, n in state.runners.items() if n != name}
                self.reply(200)
            else:
                self.reply(404)

        def do_GET(self) -> None:
            self.handle_any("GET")

        def do_POST(self) -> None:
            self.handle_any("POST")

        def do_DELETE(self) -> None:
            self.handle_any("DELETE")

        def log_message(self, *_: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()


@dataclass
class Host:
    """A host the supervisor runs on: the fake runtime's directory and how to start the command."""

    root: Path
    script: Path
    endpoint: Endpoint

    def outcomes(self, *outcomes: str) -> None:
        (self.root / "outcomes").write_text(" ".join(outcomes))

    @property
    def docker_calls(self) -> list[str]:
        log = self.root / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    @property
    def job_runs(self) -> list[str]:
        """The `docker run` calls that start a runner, as opposed to the metadata check's."""
        return [c for c in self.docker_calls if c.startswith("run ") and "--name" in c.split()]

    def start(self, *extra: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(self.script), "run", "--org", "acme", "--group", "pf-group", "--labels", "pf,unique",
             "--api-url", self.endpoint.url, "--docker", str(self.root / "docker"), "--retry-seconds", "0.05",
             "--token-file", str(self.root / "token"), *extra],
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )  # fmt: skip

    def wait_for(self, condition: Any) -> None:
        deadline = time.monotonic() + 20
        while not condition():
            assert time.monotonic() < deadline, "the supervisor never reached the state the case waits for"
            time.sleep(0.05)


@pytest.fixture
def host(tmp_path: Path, shipped: Path, endpoint: Endpoint) -> Host:
    root = tmp_path / "host"
    root.mkdir()
    (root / "token").write_text("registration-token\n")
    docker = root / "docker"
    docker.write_text(STUB_DOCKER.format(python=sys.executable, root=str(root), url=endpoint.url))
    docker.chmod(0o755)
    return Host(root=root, script=shipped / "supervisor" / "runner_supervisor.py", endpoint=endpoint)


def test_two_jobs_run_back_to_back_each_on_a_fresh_runner_and_container(host: Host, endpoint: Endpoint) -> None:
    host.outcomes("pass", "pass")
    process = host.start("--max-jobs", "2")
    out, err = process.communicate(timeout=30)
    assert process.returncode == 0  # exit status
    assert err == ""  # stderr
    assert out.count("registered runner pf-") == 2 and out.count("removed runner and container pf-") == 2  # stdout
    assert endpoint.runners == {}  # nothing stays registered
    registered = [b["name"] for b in endpoint.bodies]
    assert len(set(registered)) == 2  # a new runner for each job
    assert all(
        b["labels"] == ["self-hosted", "pf", "unique"] and b["runner_group_id"] == GROUP_ID for b in endpoint.bodies
    )
    assert endpoint.tokens == {"Bearer registration-token"}
    runs = host.job_runs
    assert [c.split()[c.split().index("--name") + 1] for c in runs] == registered  # a fresh container each
    assert all(c.startswith("run --rm ") for c in runs)
    assert [c for c in host.docker_calls if c.startswith("rm ")] == [f"rm -f {n}" for n in registered]
    assert (host.root / "jitconfig").read_text() == "JIT"  # the JIT config reaches the container by environment
    assert not any("=JIT" in c or " JIT" in c for c in host.docker_calls)  # and never by argument


def test_a_failed_job_removes_its_runner_and_container_then_the_next_job_runs(host: Host, endpoint: Endpoint) -> None:
    host.outcomes("fail", "pass")
    process = host.start("--max-jobs", "2")
    out, err = process.communicate(timeout=30)
    assert process.returncode == 0
    assert "exited with status 1" in out and "exited with status 0" in out
    assert endpoint.runners == {}  # the failed job's runner would otherwise have stayed registered
    registered = [b["name"] for b in endpoint.bodies]
    assert [c for c in host.docker_calls if c.startswith("rm ")] == [f"rm -f {n}" for n in registered]


def test_stopping_mid_job_removes_the_container_and_the_registration(host: Host, endpoint: Endpoint) -> None:
    host.outcomes("hang")
    process = host.start()
    host.wait_for(lambda: host.job_runs)
    assert len(endpoint.runners) == 1
    process.send_signal(signal.SIGTERM)
    out, err = process.communicate(timeout=30)
    assert process.returncode == 0
    assert endpoint.runners == {}
    name = endpoint.bodies[0]["name"]
    assert f"rm -f {name}" in host.docker_calls
    assert f"stopped: removing {name}" in out
    assert len(endpoint.bodies) == 1  # no further runner after the stop


def test_start_removes_what_a_previous_run_left_under_its_prefix_only(host: Host, endpoint: Endpoint) -> None:
    endpoint.runners = {1: "pf-leftover", 2: "someone-elses"}
    (host.root / "leftover").write_text("old-container\n")
    host.outcomes()
    process = host.start("--max-jobs", "0")
    host.wait_for(lambda: "rm -f old-container" in host.docker_calls)
    host.wait_for(lambda: "pf-leftover" not in endpoint.names)
    process.send_signal(signal.SIGTERM)
    out, _ = process.communicate(timeout=30)
    assert endpoint.names == ["someone-elses"]
    assert "ps -aq --filter label=productforge.runner-supervisor=pf" in host.docker_calls
    # A leftover container still running a job keeps its runner busy, so it goes before the registration.
    assert out.index("removing the leftover container old-container") < out.index(
        "removing the leftover registration pf-leftover"
    )


def test_a_group_that_does_not_exist_is_refused_before_anything_is_registered(host: Host, endpoint: Endpoint) -> None:
    process = subprocess.run(
        [sys.executable, str(host.script), "run", "--org", "acme", "--group", "missing", "--api-url", endpoint.url,
         "--docker", str(host.root / "docker"), "--token-file", str(host.root / "token")],
        capture_output=True, text=True, cwd=host.root, check=False,
    )  # fmt: skip
    assert process.returncode == 1
    assert process.stderr == "runner-supervisor: organisation acme has no runner group named missing\n"
    assert endpoint.bodies == [] and host.docker_calls == []


def test_a_missing_token_is_refused_with_one_line(host: Host, endpoint: Endpoint) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PF_RUNNER_TOKEN"}
    process = subprocess.run(
        [sys.executable, str(host.script), "run", "--org", "acme", "--group", "pf-group", "--api-url", endpoint.url],
        capture_output=True, text=True, cwd=host.root, env=env, check=False,
    )  # fmt: skip
    assert process.returncode == 1
    assert process.stderr == "runner-supervisor: no token: pass --token-file or set PF_RUNNER_TOKEN\n"
    assert endpoint.calls == []


def test_a_job_container_is_confined(host: Host) -> None:
    host.outcomes("pass")
    host.start("--max-jobs", "1", "--pids-limit", "99", "--memory", "2g").communicate(timeout=30)
    (run,) = host.job_runs
    assert "--cap-drop ALL" in run and "--security-opt no-new-privileges" in run
    assert "--pids-limit 99" in run and "--memory 2g" in run


def test_the_default_limits_and_a_pinned_image_digest(host: Host) -> None:
    host.outcomes("pass")
    host.start("--max-jobs", "1").communicate(timeout=30)
    (run,) = host.job_runs
    assert "--pids-limit 512" in run and "--memory 4g" in run
    assert " ghcr.io/actions/actions-runner@sha256:" in run and ":latest" not in run


def test_without_a_runtime_option_no_runtime_is_passed(host: Host) -> None:
    host.outcomes("pass")
    host.start("--max-jobs", "1").communicate(timeout=30)
    (run,) = host.job_runs
    assert "--runtime" not in run


def test_a_sysbox_runtime_starts_the_job_and_the_check_under_it_without_the_capability_drop(host: Host) -> None:
    host.outcomes("pass")
    host.start("--max-jobs", "1", "--runtime", "sysbox-runc", "--pids-limit", "99", "--memory", "2g").communicate(
        timeout=30
    )
    (run,) = host.job_runs
    check = next(c for c in host.docker_calls if " curl " in c)
    for call in (run, check):
        assert "--runtime sysbox-runc" in call
        assert "--cap-drop" not in call and "no-new-privileges" not in call
        assert "--pids-limit 99" in call and "--memory 2g" in call


def test_another_runtime_keeps_the_capability_drop_and_no_new_privileges(host: Host) -> None:
    host.outcomes("pass")
    host.start("--max-jobs", "1", "--runtime", "runsc").communicate(timeout=30)
    (run,) = host.job_runs
    assert "--runtime runsc" in run
    assert "--cap-drop ALL" in run and "--security-opt no-new-privileges" in run


@pytest.mark.parametrize("answer", ["200 401", "000 200"])
def test_start_still_refuses_under_sysbox_when_metadata_is_readable(
    host: Host, endpoint: Endpoint, answer: str
) -> None:
    (host.root / "metadata").write_text(answer)
    process = host.start("--max-jobs", "1", "--runtime", "sysbox-runc")
    _, err = process.communicate(timeout=30)
    assert process.returncode == 1 and "instance metadata service" in err
    assert endpoint.bodies == [] and host.job_runs == []


def test_a_sysbox_start_passes_when_a_token_request_fails_and_a_request_without_one_answers_401(
    host: Host, endpoint: Endpoint
) -> None:
    (host.root / "metadata").write_text("000 401")
    host.outcomes("pass")
    process = host.start("--max-jobs", "1", "--runtime", "sysbox-runc")
    process.communicate(timeout=30)
    assert process.returncode == 0 and len(host.job_runs) == 1


RUNNER_IMAGE = Path("supervisor") / "runner-image"


def test_the_image_is_built_from_the_digest_the_supervisor_runs_by_default(shipped: Path) -> None:
    supervisor = (shipped / "supervisor" / "runner_supervisor.py").read_text()
    default = next(line for line in supervisor.splitlines() if line.startswith("DEFAULT_IMAGE = ")).split('"')[1]
    dockerfile = (shipped / RUNNER_IMAGE / "Dockerfile").read_text()
    assert f"ARG BASE_IMAGE={default}" in dockerfile
    assert "FROM ${BASE_IMAGE}" in dockerfile


@pytest.mark.parametrize(
    "tool",
    ["make", "python3", "python3-venv", "build-essential", "git", "curl", "unzip", "xz-utils", "zip", "jq", "gh"]
    + ["docker-ce", "docker-compose-plugin"],
)
def test_the_image_installs_the_tools_the_kits_use(shipped: Path, tool: str) -> None:
    dockerfile = (shipped / RUNNER_IMAGE / "Dockerfile").read_text()
    installed = {
        word
        for block in re.findall(r"apt-get install\b(.*?)(?:&&|$)", dockerfile.replace("\\\n", " "), flags=re.S)
        for word in block.split()
        if not word.startswith("-")
    }
    assert tool in installed


def test_the_entrypoint_starts_the_engine_then_hands_the_runner_to_the_runner_user(shipped: Path) -> None:
    entrypoint = shipped / RUNNER_IMAGE / "entrypoint.sh"
    text = entrypoint.read_text()
    assert os.access(entrypoint, os.X_OK)
    commands = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert commands[-1] == 'HOME=/home/runner exec runuser --user runner -- "$@"'
    (engine,) = (line for line in commands if "dockerd" in line and "--host" in line)
    assert engine.startswith("env -u ACTIONS_RUNNER_INPUT_JITCONFIG dockerd ")
    assert text.index("dockerd") < text.index("runuser --user runner")
    assert "usermod --append --groups docker runner" in (shipped / RUNNER_IMAGE / "Dockerfile").read_text()
    assert 'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]' in (shipped / RUNNER_IMAGE / "Dockerfile").read_text()


def test_the_image_carries_an_arm64_taplo_wheel_for_the_hook_that_pins_it(shipped: Path, tmp_path: Path) -> None:
    dockerfile = (shipped / RUNNER_IMAGE / "Dockerfile").read_text()
    assert "ENV PIP_FIND_LINKS=/opt/wheels" in dockerfile and "taplo-linux-aarch64.gz" in dockerfile
    version = next(line for line in dockerfile.splitlines() if line.startswith("ARG TAPLO_VERSION=")).split("=")[1]
    assert f'additional_dependencies: ["taplo=={version}"]' in (shipped / ".pre-commit-hooks.yaml").read_text()
    binary = tmp_path / "taplo"
    binary.write_bytes(b"binary")
    script = shipped / RUNNER_IMAGE / "taplo_wheel.py"
    done = subprocess.run(
        [sys.executable, str(script), version, str(binary), str(tmp_path / "wheels")],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    (wheel,) = (tmp_path / "wheels").glob("*.whl")
    assert wheel.name == f"taplo-{version}-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read(f"taplo-{version}.data/scripts/taplo") == b"binary"
        assert (archive.getinfo(f"taplo-{version}.data/scripts/taplo").external_attr >> 16) & 0o111


def test_the_build_command_tags_the_image_with_the_release(host: Host, shipped: Path) -> None:
    build = shipped / RUNNER_IMAGE / "build.sh"
    done = subprocess.run(
        [str(build), "v9.9.9"], env={**os.environ, "PATH": f"{host.root}:{os.environ['PATH']}"},
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    (call,) = (c for c in host.docker_calls if c.startswith("build "))
    assert "--tag productforge-runner:v9.9.9" in call and call.endswith(str(build.parent))


def test_the_build_command_refuses_without_a_version(shipped: Path) -> None:
    done = subprocess.run([str(shipped / RUNNER_IMAGE / "build.sh")], capture_output=True, text=True, check=False)
    assert done.returncode == 2 and "usage" in done.stderr


def test_start_refuses_when_a_container_can_get_a_metadata_token(host: Host, endpoint: Endpoint) -> None:
    (host.root / "metadata").write_text("200 401")
    process = host.start("--max-jobs", "1")
    out, err = process.communicate(timeout=30)
    assert process.returncode == 1
    assert "can read a session token from the instance metadata service at 169.254.169.254" in err
    assert "IMDSv2 with hop limit 1" in err
    assert endpoint.bodies == []  # nothing registered
    assert host.job_runs == []
    check = next(c for c in host.docker_calls if " curl " in c)
    assert "--cap-drop ALL" in check and "http://169.254.169.254/latest/api/token" in check
    assert check.startswith("run --rm ")


def test_start_refuses_when_a_container_can_read_metadata_without_a_token(host: Host, endpoint: Endpoint) -> None:
    (host.root / "metadata").write_text("000 200")
    process = host.start("--max-jobs", "1")
    _, err = process.communicate(timeout=30)
    assert process.returncode == 1 and "can read metadata without a token" in err and endpoint.bodies == []


def test_imdsv2_at_hop_limit_1_passes_though_a_request_without_a_token_answers_401(
    host: Host, endpoint: Endpoint
) -> None:
    # What a container on an EC2 instance with IMDSv2 required at hop limit 1 sees: the token's answer
    # never arrives (000), and a request without a token is refused (401). Nothing can be read.
    (host.root / "metadata").write_text("000 401")
    host.outcomes("pass")
    process = host.start("--max-jobs", "1")
    _, err = process.communicate(timeout=30)
    assert process.returncode == 0 and err == "" and len(host.job_runs) == 1


@pytest.mark.parametrize("output", ["", " ", "000", "000 abc", "200 401 000"])
def test_a_metadata_check_whose_answer_cannot_be_read_is_a_refusal(host: Host, endpoint: Endpoint, output: str) -> None:
    # Curl missing from the image prints only the separator; anything not two status codes is unreadable.
    (host.root / "metadata").write_text(output)
    process = host.start("--max-jobs", "1")
    _, err = process.communicate(timeout=30)
    assert process.returncode == 1 and "could not run the check" in err and endpoint.bodies == []


def test_a_metadata_check_that_cannot_run_is_a_refusal(host: Host, endpoint: Endpoint) -> None:
    (host.root / "metadata").write_text("cannot-run")
    process = host.start("--max-jobs", "1")
    _, err = process.communicate(timeout=30)
    assert process.returncode == 1 and "could not run the check" in err and endpoint.bodies == []


def test_the_flag_skips_the_metadata_check_for_a_host_that_is_not_ec2(host: Host, endpoint: Endpoint) -> None:
    (host.root / "metadata").write_text("200 200")
    host.outcomes("pass")
    process = host.start("--max-jobs", "1", "--allow-metadata-reachable")
    out, err = process.communicate(timeout=30)
    assert process.returncode == 0 and err == ""
    assert not any(" curl " in c for c in host.docker_calls)
    assert "--allow-metadata-reachable" in out and len(host.job_runs) == 1


def test_a_registration_still_busy_is_retried_then_deleted(host: Host, endpoint: Endpoint) -> None:
    endpoint.busy_deletes = 2  # 422, 422, then 204
    host.outcomes("fail")  # a failed job leaves its registration to be deleted
    process = host.start("--max-jobs", "1")
    out, err = process.communicate(timeout=30)
    assert process.returncode == 0 and err == ""
    assert endpoint.runners == {}
    assert [c for c in endpoint.calls if c.startswith("DELETE")].count(
        f"DELETE /orgs/acme/actions/runners/{endpoint.next_id}"
    ) == 3
    assert "removed runner and container" in out


def test_a_registration_that_stays_busy_is_left_and_logged_not_fatal(host: Host, endpoint: Endpoint) -> None:
    endpoint.busy_deletes = 1000
    host.outcomes("fail", "fail")
    process = host.start("--max-jobs", "2")
    out, err = process.communicate(timeout=60)
    assert process.returncode == 0 and err == ""
    assert "still busy after 5 attempts to remove it; leaving it" in out
    assert len(endpoint.bodies) == 2  # the next job still ran


def test_a_leftover_registration_that_is_busy_at_start_is_retried_then_left_without_exit_1(
    host: Host, endpoint: Endpoint
) -> None:
    endpoint.runners = {1: "pf-leftover", 2: "pf-other"}
    endpoint.busy_deletes = 1  # the first DELETE answers 422, the retry 204
    host.outcomes("pass")
    process = host.start("--max-jobs", "1")
    out, err = process.communicate(timeout=30)
    assert process.returncode == 0 and err == ""
    assert endpoint.runners == {} and "removing the leftover registration pf-leftover" in out

    endpoint.runners = {1: "pf-leftover"}
    endpoint.busy_deletes = 1000
    host.outcomes("pass")
    process = host.start("--max-jobs", "1")
    out, err = process.communicate(timeout=60)
    assert process.returncode == 0 and err == ""
    assert "left the registration pf-leftover, which GitHub still holds as busy" in out
    assert len(endpoint.bodies) == 2  # and it went on to run its job


def test_no_runner_is_registered_while_the_docker_daemon_is_down(host: Host, endpoint: Endpoint) -> None:
    (host.root / "docker-down").write_text("")
    host.outcomes("pass")
    process = host.start("--max-jobs", "1")
    host.wait_for(lambda: host.docker_calls.count("info") >= 3)  # it kept trying, backing off
    assert endpoint.bodies == [] and host.job_runs == []
    assert not any(c.startswith("POST") for c in endpoint.calls)
    (host.root / "docker-down").unlink()
    out, err = process.communicate(timeout=30)
    assert process.returncode == 0 and len(endpoint.bodies) == 1
    assert "is not available; checking again" in out


def test_no_runner_is_registered_while_the_docker_binary_is_missing(host: Host, endpoint: Endpoint) -> None:
    process = host.start("--max-jobs", "1", "--docker", str(host.root / "no-such-docker"))
    time.sleep(1.0)
    process.send_signal(signal.SIGTERM)
    out, err = process.communicate(timeout=30)
    assert process.returncode == 0
    assert endpoint.bodies == [] and not any(c.startswith("POST") for c in endpoint.calls)
    assert out.count("is not available") >= 2


def test_docker_going_down_after_a_job_stops_new_registrations(host: Host, endpoint: Endpoint) -> None:
    host.outcomes("fail-then-down", "pass")
    process = host.start("--max-jobs", "2")
    host.wait_for(lambda: len(endpoint.bodies) == 1 and (host.root / "docker-down").exists())
    time.sleep(1.0)
    assert len(endpoint.bodies) == 1  # no registration per retry while it is down
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)


def test_the_image_carries_the_flutter_the_repositories_pin(shipped: Path) -> None:
    dockerfile = (shipped / RUNNER_IMAGE / "Dockerfile").read_text()
    version = next(line for line in dockerfile.splitlines() if line.startswith("ARG FLUTTER_VERSION=")).split("=")[1]
    assert version == json.loads((shipped / "settings" / "fvmrc").read_text())["flutter"]
    assert "git clone https://github.com/flutter/flutter.git --branch" in dockerfile and "precache --web" in dockerfile


def test_the_image_verifies_what_it_downloads_with_pinned_build_args(shipped: Path) -> None:
    dockerfile = (shipped / RUNNER_IMAGE / "Dockerfile").read_text()
    args = dict(re.findall(r'^ARG (\w+)="?([^"\n]*)"?$', dockerfile, flags=re.M))
    assert re.fullmatch(r"[0-9a-f]{64}", args["TAPLO_SHA256"])
    assert re.fullmatch(r"[0-9a-f]{40}", args["FLUTTER_COMMIT"])
    assert re.fullmatch(r"[0-9A-F]{40}", args["DOCKER_KEY_FINGERPRINT"])
    assert all(re.fullmatch(r"[0-9A-F]{40}", f) for f in args["GITHUB_CLI_KEY_FINGERPRINTS"].split())
    assert 'echo "${TAPLO_SHA256}  /tmp/taplo.gz" | sha256sum --check -' in dockerfile
    assert '[ "$(git -C "${FLUTTER_HOME}" rev-parse HEAD)" = "${FLUTTER_COMMIT}" ]' in dockerfile
    assert "${DOCKER_KEY_FINGERPRINT}" in dockerfile and "${GITHUB_CLI_KEY_FINGERPRINTS}" in dockerfile


def test_the_state_file_reads_busy_while_a_job_runs_and_idle_once_it_ends(host: Host, endpoint: Endpoint) -> None:
    state_file = host.root / "activity"
    endpoint.busy = True
    host.outcomes("hang")
    process = host.start("--state-file", str(state_file), "--activity-poll-seconds", "0.05")
    host.wait_for(lambda: state_file.exists() and state_file.read_text().startswith("busy "))
    assert re.fullmatch(r"busy \d+\n", state_file.read_text())
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)
    assert re.fullmatch(r"idle \d+\n", state_file.read_text())


def test_a_runner_that_waits_without_a_job_is_idle_and_a_finished_job_leaves_it_idle(
    host: Host, endpoint: Endpoint
) -> None:
    state_file = host.root / "activity"
    host.outcomes("pass")
    process = host.start("--max-jobs", "1", "--state-file", str(state_file))
    process.communicate(timeout=30)
    assert process.returncode == 0
    assert re.fullmatch(r"idle \d+\n", state_file.read_text())


def test_a_failing_busy_check_counts_as_busy(host: Host, endpoint: Endpoint) -> None:
    state_file = host.root / "activity"
    endpoint.busy_get_status = 500
    host.outcomes("hang")
    process = host.start("--state-file", str(state_file), "--activity-poll-seconds", "0.05")
    host.wait_for(lambda: state_file.exists() and state_file.read_text().startswith("busy "))
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)


def test_without_a_state_file_nothing_is_recorded_or_polled(host: Host, endpoint: Endpoint) -> None:
    host.outcomes("pass")
    host.start("--max-jobs", "1").communicate(timeout=30)
    assert not [c for c in endpoint.calls if c.startswith("GET /orgs/acme/actions/runners/")]
    assert not (host.root / "activity").exists()


def test_the_idle_time_is_not_refreshed_while_the_runner_waits_for_a_job(host: Host, endpoint: Endpoint) -> None:
    state_file = host.root / "activity"
    host.outcomes("hang")
    process = host.start("--state-file", str(state_file), "--activity-poll-seconds", "0.05")
    host.wait_for(lambda: state_file.exists() and state_file.read_text().startswith("idle "))
    first = state_file.read_text()
    polls = len([c for c in endpoint.calls if c.startswith("GET /orgs/acme/actions/runners/")])
    time.sleep(2.2)  # the epoch has whole-second resolution, so a rewrite would show within this
    assert len([c for c in endpoint.calls if c.startswith("GET /orgs/acme/actions/runners/")]) > polls + 1
    assert state_file.read_text() == first
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)


def test_the_idle_time_moves_once_when_a_busy_runner_becomes_idle(host: Host, endpoint: Endpoint) -> None:
    state_file = host.root / "activity"
    host.outcomes("hang")
    process = host.start("--state-file", str(state_file), "--activity-poll-seconds", "0.05")
    host.wait_for(lambda: state_file.exists() and state_file.read_text().startswith("idle "))
    started_idle = int(state_file.read_text().split()[1])
    time.sleep(1.1)
    endpoint.busy = True
    host.wait_for(lambda: state_file.read_text().startswith("busy "))
    time.sleep(1.1)
    endpoint.busy = False
    host.wait_for(lambda: state_file.read_text().startswith("idle "))
    became_idle = int(state_file.read_text().split()[1])
    assert became_idle >= started_idle + 2
    time.sleep(2.2)
    assert state_file.read_text() == f"idle {became_idle}\n"
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)
