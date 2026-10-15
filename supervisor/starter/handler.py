"""Starts the runner's EC2 instance when GitHub reports a queued job for the runner's labels.

An AWS Lambda function behind a function URL, for a GitHub organisation webhook that sends `workflow_job`
events. It uses the standard library and boto3, which Lambda provides.

Configuration, from the function's environment:
  INSTANCE_ID     the instance to start (required)
  WEBHOOK_SECRET  the webhook's secret, which GitHub signs each delivery with (required)
  RUNNER_LABELS   comma-separated labels a job must all ask for (default `self-hosted,pf`)

Every delivery GitHub sends is answered with a 2xx, so GitHub never marks the webhook failing, unless its
signature is wrong (401) or the function itself is misconfigured or AWS refuses it (500).
"""

import base64
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Mapping
from typing import Any

DEFAULT_LABELS = "self-hosted,pf"

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def respond(*, status: int, message: str) -> dict[str, Any]:
    """A function URL response with a plain-text body."""
    return {"statusCode": status, "headers": {"Content-Type": "text/plain"}, "body": message}


def raw_body(*, event: Mapping[str, Any]) -> bytes:
    """The request body as GitHub sent it, which is what the signature covers."""
    body = event.get("body") or ""
    return base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()


def signature_valid(*, body: bytes, header: str, secret: str) -> bool:
    """Whether the `X-Hub-Signature-256` header is the body's HMAC-SHA256, compared in constant time."""
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.encode())


def wanted_labels(*, environ: Mapping[str, str]) -> set[str]:
    """The labels a job must ask for, lower-cased as GitHub compares them."""
    return {label.strip().lower() for label in environ.get("RUNNER_LABELS", DEFAULT_LABELS).split(",") if label.strip()}


def handle(*, event: Mapping[str, Any], environ: Mapping[str, str], ec2: Any) -> dict[str, Any]:
    """Answers one webhook delivery, starting the instance when a queued job for the runner's labels arrives."""
    secret = environ.get("WEBHOOK_SECRET", "")
    instance_id = environ.get("INSTANCE_ID", "")
    if not secret or not instance_id:
        logger.error("WEBHOOK_SECRET and INSTANCE_ID must both be set")
        return respond(status=500, message="not configured")
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    body = raw_body(event=event)
    if not signature_valid(body=body, header=headers.get("x-hub-signature-256", ""), secret=secret):
        logger.warning("refused a delivery with a missing or wrong signature")
        return respond(status=401, message="bad signature")
    kind = headers.get("x-github-event", "")
    if kind == "ping":
        return respond(status=200, message="pong")
    if kind != "workflow_job":
        return respond(status=200, message=f"ignored event {kind}")
    try:
        payload = json.loads(body)
        action = payload["action"]
        labels = {str(label).lower() for label in payload["workflow_job"]["labels"]}
    except ValueError, KeyError, TypeError:
        return respond(status=200, message="ignored: not a workflow_job payload")
    if action != "queued":
        return respond(status=200, message=f"ignored action {action}")
    if not wanted_labels(environ=environ) <= labels:
        return respond(status=200, message="ignored: the job is not for this runner")
    try:
        reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
        state = reservations[0]["Instances"][0]["State"]["Name"]
        if state != "stopped":
            logger.info("instance %s is %s: not starting it", instance_id, state)
            return respond(status=200, message=f"instance is {state}")
        ec2.start_instances(InstanceIds=[instance_id])
    except Exception:
        logger.exception("could not describe or start instance %s", instance_id)
        return respond(status=500, message="could not start the instance")
    logger.info("started instance %s for a queued job", instance_id)
    return respond(status=200, message="starting the instance")


def lambda_handler(event: Mapping[str, Any], context: Any) -> dict[str, Any]:
    """The Lambda entry point: `handler.lambda_handler`."""
    import boto3  # imported here so tests, and hosts without boto3, can load this module

    return handle(event=event, environ=os.environ, ec2=boto3.client("ec2"))
