# The runner image: the official GitHub Actions runner plus the tools the kits' CI uses, and a Docker
# engine of its own for each job. It is meant to run under Sysbox (`docker run --runtime sysbox-runc`),
# which makes root inside the container an unprivileged user on the host, so the engine started here
# cannot reach the host's. `build.sh` builds it; the supervisor's README says when.

# The arm64 manifest of ghcr.io/actions/actions-runner:latest on 2026-10-15, the supervisor's default image.
# A test keeps the two in step.
ARG BASE_IMAGE=ghcr.io/actions/actions-runner@sha256:9644537b29b4799cb752a2f23a45a1e603cf74c3c5998241a1533f0e8c3dc649
FROM ${BASE_IMAGE}

USER root
ENV DEBIAN_FRONTEND=noninteractive

# Docker's engine and Compose plugin from Docker's own apt repository, and GitHub's CLI from GitHub's.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git gnupg iptables jq make pigz python3 python3-pip python3-venv \
        unzip xz-utils zip \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli.gpg \
    && . /etc/os-release \
    && arch="$(dpkg --print-architecture)" \
    && echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list \
    && echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        containerd.io docker-buildx-plugin docker-ce docker-ce-cli docker-compose-plugin gh \
    && rm -rf /var/lib/apt/lists/* \
    && usermod --append --groups docker runner

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# The engine's images and containers live under /var/lib/docker, which Sysbox backs with storage it removes
# with the container, so they go with the job's container.
WORKDIR /home/runner
# Root, so the entrypoint can start the engine; it hands over to the `runner` user before the runner starts.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["./run.sh"]
