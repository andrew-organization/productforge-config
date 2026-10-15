import base64
import hashlib
import hmac
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SECRET = "webhook-secret-value"
INSTANCE = "i-0123456789abcdef0"


class RefusedStart(Exception):
    """What boto3's ClientError looks like to the function: an exception with an error code in `response`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeWaiter:
    def __init__(self, ec2: FakeEc2) -> None:
        self.ec2 = ec2

    def wait(self, *, InstanceIds: list[str], WaiterConfig: dict[str, int]) -> None:
        self.ec2.waited.append((InstanceIds, WaiterConfig))
        if self.ec2.never_stops:
            raise RuntimeError("Max attempts exceeded")
        self.ec2.state = "stopped"


class FakeEc2:
    """Records the calls made to it and answers describe_instances with the state it was given."""

    def __init__(self, state: str = "stopped", *, refuse_start_as: str | None = None, after_refusal: str = "") -> None:
        self.state = state
        self.refuse_start_as = refuse_start_as
        self.after_refusal = after_refusal
        self.never_stops = False
        self.described: list[list[str]] = []
        self.started: list[list[str]] = []
        self.waited: list[tuple[list[str], dict[str, int]]] = []
        self.waiters: list[str] = []

    def describe_instances(self, *, InstanceIds: list[str]) -> dict[str, Any]:
        self.described.append(InstanceIds)
        return {"Reservations": [{"Instances": [{"State": {"Name": self.state}}]}]}

    def get_waiter(self, name: str) -> FakeWaiter:
        self.waiters.append(name)
        return FakeWaiter(self)

    def start_instances(self, *, InstanceIds: list[str]) -> dict[str, Any]:
        if self.refuse_start_as:
            code, self.refuse_start_as = self.refuse_start_as, None
            self.state = self.after_refusal
            raise RefusedStart(code)
        self.started.append(InstanceIds)
        return {}


@pytest.fixture(scope="module")
def starter(shipped: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("starter_handler", shipped / "supervisor" / "starter" / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def delivery(
    body: dict[str, Any] | str, *, kind: str = "workflow_job", secret: str = SECRET, signed_body: str | None = None
) -> dict[str, Any]:
    text = body if isinstance(body, str) else json.dumps(body)
    digest = hmac.new(secret.encode(), (signed_body or text).encode(), hashlib.sha256).hexdigest()
    return {"headers": {"X-Hub-Signature-256": f"sha256={digest}", "X-GitHub-Event": kind}, "body": text}


def job(*, action: str = "queued", labels: tuple[str, ...] = ("self-hosted", "pf")) -> dict[str, Any]:
    return {"action": action, "workflow_job": {"labels": list(labels)}}


ENVIRON = {"WEBHOOK_SECRET": SECRET, "INSTANCE_ID": INSTANCE}


def test_a_signed_queued_job_with_the_runners_labels_starts_a_stopped_instance(starter: ModuleType) -> None:
    ec2 = FakeEc2("stopped")
    response = starter.handle(event=delivery(job()), environ=ENVIRON, ec2=ec2)
    assert response["statusCode"] == 200
    assert ec2.described == [[INSTANCE]]
    assert ec2.started == [[INSTANCE]]


def test_labels_are_compared_as_a_subset_without_regard_to_case_or_extra_labels(starter: ModuleType) -> None:
    ec2 = FakeEc2()
    event = delivery(job(labels=("Self-Hosted", "PF", "linux")))
    assert starter.handle(event=event, environ=ENVIRON, ec2=ec2)["statusCode"] == 200
    assert ec2.started == [[INSTANCE]]


def test_the_labels_come_from_the_environment(starter: ModuleType) -> None:
    ec2 = FakeEc2()
    environ = {**ENVIRON, "RUNNER_LABELS": "self-hosted, pf, big"}
    starter.handle(event=delivery(job()), environ=environ, ec2=ec2)
    assert ec2.started == []
    starter.handle(event=delivery(job(labels=("self-hosted", "pf", "big"))), environ=environ, ec2=ec2)
    assert ec2.started == [[INSTANCE]]


def test_a_bad_signature_is_refused_with_401_and_nothing_is_touched(starter: ModuleType) -> None:
    ec2 = FakeEc2()
    response = starter.handle(event=delivery(job(), secret="another-secret"), environ=ENVIRON, ec2=ec2)
    assert response["statusCode"] == 401
    assert ec2.described == [] and ec2.started == []


def test_a_body_altered_after_signing_is_refused(starter: ModuleType) -> None:
    ec2 = FakeEc2()
    event = delivery(job(labels=("self-hosted", "pf")), signed_body=json.dumps(job(labels=("x",))))
    assert starter.handle(event=event, environ=ENVIRON, ec2=ec2)["statusCode"] == 401
    assert ec2.started == []


def test_a_missing_signature_is_refused(starter: ModuleType) -> None:
    ec2 = FakeEc2()
    event = {"headers": {"x-github-event": "workflow_job"}, "body": json.dumps(job())}
    assert starter.handle(event=event, environ=ENVIRON, ec2=ec2)["statusCode"] == 401
    assert ec2.started == []


def test_a_base64_encoded_body_is_verified_as_the_bytes_github_sent(starter: ModuleType) -> None:
    ec2 = FakeEc2()
    event = delivery(job())
    event["body"] = base64.b64encode(event["body"].encode()).decode()
    event["isBase64Encoded"] = True
    event["headers"] = {key.lower(): value for key, value in event["headers"].items()}
    assert starter.handle(event=event, environ=ENVIRON, ec2=ec2)["statusCode"] == 200
    assert ec2.started == [[INSTANCE]]


@pytest.mark.parametrize(
    "labels", [("ubuntu-latest",), ("self-hosted",), ("pf",), ("self-hosted", "other")], ids="-".join
)
def test_a_job_for_other_labels_is_acknowledged_and_ignored(starter: ModuleType, labels: tuple[str, ...]) -> None:
    ec2 = FakeEc2()
    response = starter.handle(event=delivery(job(labels=labels)), environ=ENVIRON, ec2=ec2)
    assert response["statusCode"] == 200
    assert ec2.described == [] and ec2.started == []


@pytest.mark.parametrize("action", ["in_progress", "completed", "waiting"])
def test_a_workflow_job_that_is_not_queued_is_ignored(starter: ModuleType, action: str) -> None:
    ec2 = FakeEc2()
    assert starter.handle(event=delivery(job(action=action)), environ=ENVIRON, ec2=ec2)["statusCode"] == 200
    assert ec2.described == [] and ec2.started == []


@pytest.mark.parametrize("state", ["running", "pending", "shutting-down"])
def test_an_instance_that_is_not_stopped_is_not_started_again(starter: ModuleType, state: str) -> None:
    ec2 = FakeEc2(state)
    response = starter.handle(event=delivery(job()), environ=ENVIRON, ec2=ec2)
    assert response["statusCode"] == 200
    assert ec2.described == [[INSTANCE]] and ec2.started == []


def test_a_job_queued_while_the_instance_is_stopping_waits_for_it_to_stop_then_starts_it(starter: ModuleType) -> None:
    ec2 = FakeEc2("stopping")
    response = starter.handle(event=delivery(job()), environ=ENVIRON, ec2=ec2)
    assert response["statusCode"] == 200
    assert ec2.waiters == ["instance_stopped"]
    assert ec2.waited == [([INSTANCE], {"Delay": 5, "MaxAttempts": 30})]
    assert ec2.started == [[INSTANCE]]


def test_an_instance_that_never_finishes_stopping_is_a_500_so_the_delivery_can_be_redelivered(
    starter: ModuleType,
) -> None:
    ec2 = FakeEc2("stopping")
    ec2.never_stops = True
    assert starter.handle(event=delivery(job()), environ=ENVIRON, ec2=ec2)["statusCode"] == 500
    assert ec2.started == []


def test_a_start_refused_because_the_instance_began_stopping_waits_and_starts_it_again(starter: ModuleType) -> None:
    ec2 = FakeEc2("stopped", refuse_start_as="IncorrectInstanceState", after_refusal="stopping")
    response = starter.handle(event=delivery(job()), environ=ENVIRON, ec2=ec2)
    assert response["statusCode"] == 200
    assert len(ec2.waited) == 1
    assert ec2.started == [[INSTANCE]]


@pytest.mark.parametrize("now", ["running", "pending"])
def test_a_start_refused_because_another_delivery_already_started_it_is_a_200(starter: ModuleType, now: str) -> None:
    ec2 = FakeEc2("stopped", refuse_start_as="IncorrectInstanceState", after_refusal=now)
    response = starter.handle(event=delivery(job()), environ=ENVIRON, ec2=ec2)
    assert response["statusCode"] == 200 and now in response["body"]
    assert ec2.started == [] and ec2.waited == []


def test_a_start_refused_for_any_other_reason_is_a_500(starter: ModuleType) -> None:
    ec2 = FakeEc2("stopped", refuse_start_as="UnauthorizedOperation")
    assert starter.handle(event=delivery(job()), environ=ENVIRON, ec2=ec2)["statusCode"] == 500


def test_a_ping_is_answered_200_when_signed_and_401_when_not(starter: ModuleType) -> None:
    ec2 = FakeEc2()
    assert starter.handle(event=delivery({"zen": "hi"}, kind="ping"), environ=ENVIRON, ec2=ec2)["statusCode"] == 200
    bad = delivery({"zen": "hi"}, kind="ping", secret="x")
    assert starter.handle(event=bad, environ=ENVIRON, ec2=ec2)["statusCode"] == 401
    assert ec2.described == [] and ec2.started == []


def test_another_event_and_an_unreadable_payload_are_acknowledged_and_ignored(starter: ModuleType) -> None:
    ec2 = FakeEc2()
    assert starter.handle(event=delivery(job(), kind="push"), environ=ENVIRON, ec2=ec2)["statusCode"] == 200
    assert starter.handle(event=delivery("not json"), environ=ENVIRON, ec2=ec2)["statusCode"] == 200
    assert starter.handle(event=delivery({"action": "queued"}), environ=ENVIRON, ec2=ec2)["statusCode"] == 200
    assert ec2.started == []


def test_an_aws_failure_is_a_500_so_the_delivery_shows_as_failed(starter: ModuleType) -> None:
    class Refusing(FakeEc2):
        def start_instances(self, *, InstanceIds: list[str]) -> dict[str, Any]:
            raise RuntimeError("UnauthorizedOperation")

    assert starter.handle(event=delivery(job()), environ=ENVIRON, ec2=Refusing())["statusCode"] == 500


@pytest.mark.parametrize("missing", ["WEBHOOK_SECRET", "INSTANCE_ID"])
def test_a_function_without_its_secret_or_instance_refuses_everything(starter: ModuleType, missing: str) -> None:
    ec2 = FakeEc2()
    environ = {k: v for k, v in ENVIRON.items() if k != missing}
    assert starter.handle(event=delivery(job()), environ=environ, ec2=ec2)["statusCode"] == 500
    assert ec2.started == []


# ─── The role's policies ────────────────────────────────────────────────────


def test_the_role_may_start_one_instance_describe_instances_and_write_its_own_logs(shipped: Path) -> None:
    folder = shipped / "supervisor" / "starter"
    policy = json.loads((folder / "iam-policy.json").read_text())
    by_action = {
        action: (statement["Resource"] if isinstance(statement["Resource"], list) else [statement["Resource"]])
        for statement in policy["Statement"]
        for action in ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])
    }
    assert all(statement["Effect"] == "Allow" for statement in policy["Statement"])
    assert by_action == {
        "ec2:StartInstances": ["arn:aws:ec2:REGION:ACCOUNT_ID:instance/INSTANCE_ID"],
        "ec2:DescribeInstances": ["*"],
        "logs:CreateLogGroup": [
            "arn:aws:logs:REGION:ACCOUNT_ID:log-group:/aws/lambda/FUNCTION_NAME",
            "arn:aws:logs:REGION:ACCOUNT_ID:log-group:/aws/lambda/FUNCTION_NAME:*",
        ],
        "logs:CreateLogStream": by_action["logs:CreateLogGroup"],
        "logs:PutLogEvents": by_action["logs:CreateLogGroup"],
    }
    trust = json.loads((folder / "trust-policy.json").read_text())
    assert trust["Statement"] == [
        {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}
    ]


# ─── The owner's setup script, against a fake aws ───────────────────────────

STUB_AWS = """#!{python}
import sys
from pathlib import Path

root = Path({root!r})
args = sys.argv[1:]
with (root / "aws.log").open("a") as log:
    log.write(" ".join(args) + "\\n")
command = args[5]
if command == "get-caller-identity":
    print("123456789012")
elif command in ("get-role", "get-function", "get-function-url-config"):
    if not ((root / "exists").exists() or (root / ("created-" + command)).exists()):
        sys.exit(254)
    if command == "get-function-url-config":
        print("https://abc.lambda-url.eu-west-1.on.aws/")
elif command.startswith("create-"):
    (root / ("created-get-" + command.removeprefix("create-"))).write_text("")
if "--environment" in args:
    environment = Path(args[args.index("--environment") + 1].removeprefix("file://"))
    (root / "environment.json").write_text(environment.read_text())
"""


@pytest.fixture
def fake_aws(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(STUB_AWS.format(python=sys.executable, root=str(tmp_path)))
    aws.chmod(0o755)
    return bin_dir


def create(shipped: Path, tmp_path: Path, fake_aws: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    secret = tmp_path / "secret"
    secret.write_text(SECRET + "\n")
    env = {**os.environ, "PATH": f"{fake_aws}{os.pathsep}{os.environ['PATH']}", "PF_STARTER_ROLE_WAIT": "0"}
    return subprocess.run(
        [str(shipped / "supervisor" / "starter" / "create-starter.sh"), "--instance-id", INSTANCE,
         "--region", "eu-west-1", "--secret-file", str(secret), "--profile", "pf-admin", *extra],
        capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip


def test_the_script_creates_the_role_function_and_url_and_prints_the_url_without_the_secret(
    shipped: Path, tmp_path: Path, fake_aws: Path
) -> None:
    result = create(shipped, tmp_path, fake_aws)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("https://abc.lambda-url.eu-west-1.on.aws/")
    assert SECRET not in result.stdout + result.stderr
    calls = (tmp_path / "aws.log").read_text()
    assert SECRET not in calls
    assert all(line.startswith("--profile pf-admin --region eu-west-1 ") for line in calls.splitlines())
    commands = [line.split()[5] for line in calls.splitlines()]
    for expected in ("create-role", "put-role-policy", "create-function", "create-function-url-config"):
        assert expected in commands
    assert "--auth-type NONE" in calls
    assert "--runtime python3.12" in calls
    create_call = next(line for line in calls.splitlines() if line.split()[5] == "create-function")
    assert "--timeout 180" in create_call
    assert "put-function-concurrency --function-name pf-runner-starter --reserved-concurrent-executions 2" in calls
    assert json.loads((tmp_path / "environment.json").read_text()) == {
        "Variables": {"WEBHOOK_SECRET": SECRET, "INSTANCE_ID": INSTANCE, "RUNNER_LABELS": "self-hosted,pf"}
    }
    assert "free" in result.stdout.lower()


def test_running_the_script_again_updates_what_exists_instead_of_creating_it_twice(
    shipped: Path, tmp_path: Path, fake_aws: Path
) -> None:
    (tmp_path / "exists").write_text("")
    result = create(shipped, tmp_path, fake_aws)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "aws.log").read_text()
    commands = [line.split()[5] for line in calls.splitlines()]
    assert "create-role" not in commands and "create-function" not in commands
    assert "create-function-url-config" not in commands
    assert "update-function-code" in commands and "update-function-configuration" in commands
    update_call = next(line for line in calls.splitlines() if line.split()[5] == "update-function-configuration")
    assert "--timeout 180" in update_call
    assert "put-function-concurrency" in commands


def test_the_script_refuses_an_empty_secret_file_and_missing_arguments(
    shipped: Path, tmp_path: Path, fake_aws: Path
) -> None:
    (tmp_path / "secret").write_text("")
    script = str(shipped / "supervisor" / "starter" / "create-starter.sh")
    env = {**os.environ, "PATH": f"{fake_aws}{os.pathsep}{os.environ['PATH']}", "PF_STARTER_ROLE_WAIT": "0"}
    empty = subprocess.run(
        [script, "--instance-id", INSTANCE, "--region", "r", "--secret-file", str(tmp_path / "secret"),
         "--profile", "p"],
        capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip
    assert empty.returncode == 1 and "missing or empty" in empty.stderr
    missing = subprocess.run([script, "--region", "r"], capture_output=True, text=True, env=env, check=False)
    assert missing.returncode == 2 and "Usage" in missing.stderr
    assert not (tmp_path / "aws.log").exists()
    assert stat.S_IMODE(os.stat(script).st_mode) & 0o111  # executable
