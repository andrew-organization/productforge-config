#!/bin/bash
# Starts the Docker engine inside this container, then runs the command (the runner) as the `runner` user.
# The container starts as root; under Sysbox that root is an unprivileged user on the host. The runner's
# JIT configuration arrives in the environment (ACTIONS_RUNNER_INPUT_JITCONFIG), which the hand-over keeps.
set -euo pipefail

timeout_seconds=60
dockerd --host unix:///var/run/docker.sock >/var/log/dockerd.log 2>&1 &
dockerd_pid=$!

for _ in $(seq "${timeout_seconds}"); do
    if docker info >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "${dockerd_pid}" 2>/dev/null; then
        echo "entrypoint: dockerd exited; its log follows" >&2
        cat /var/log/dockerd.log >&2
        exit 1
    fi
    sleep 1
done
if ! docker info >/dev/null 2>&1; then
    echo "entrypoint: dockerd did not answer within ${timeout_seconds}s; its log follows" >&2
    cat /var/log/dockerd.log >&2
    exit 1
fi

# runuser keeps the environment; HOME is the runner's.
HOME=/home/runner exec runuser --user runner -- "$@"
