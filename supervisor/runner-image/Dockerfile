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

# The `taplo` pre-commit hook asks pip for taplo at the version it pins, which has no Linux arm64 wheel on PyPI and a source
# distribution that does not build. A wheel made from taplo's own arm64 release binary, which pip finds first
# through PIP_FIND_LINKS, satisfies it. TAPLO_VERSION is that version; a test keeps it equal to the hook's.
ARG TAPLO_VERSION=0.9.3
COPY taplo_wheel.py /tmp/taplo_wheel.py
RUN curl -fsSL "https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-aarch64.gz" \
        | gunzip > /tmp/taplo \
    && python3 /tmp/taplo_wheel.py "${TAPLO_VERSION}" /tmp/taplo /opt/wheels \
    && rm /tmp/taplo /tmp/taplo_wheel.py
ENV PIP_FIND_LINKS=/opt/wheels

# Flutter, from its own git repository at the version the repositories' `.fvmrc` pins (settings/fvmrc): flutter-action
# finds no Linux arm64 SDK in Flutter's release manifest, so the image carries it and actions/setup skips the action
# when `flutter` on PATH already reports that version. FLUTTER_VERSION must equal settings/fvmrc's (a test keeps it
# so); when that moves, rebuild the image (the supervisor's README says how). Owned by `runner`, since Flutter writes
# its caches inside its SDK directory. The web artefacts are fetched now, so a job downloads none.
ARG FLUTTER_VERSION=3.44.7
ENV FLUTTER_HOME=/opt/flutter
ENV PATH=${FLUTTER_HOME}/bin:${PATH}
RUN install -d -o runner -g runner "${FLUTTER_HOME}"
USER runner
RUN git clone https://github.com/flutter/flutter.git --branch "${FLUTTER_VERSION}" --depth 1 "${FLUTTER_HOME}" \
    && flutter --version \
    && flutter precache --web
USER root

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# The engine's images and containers live under /var/lib/docker, which Sysbox backs with storage it removes
# with the container, so they go with the job's container.
WORKDIR /home/runner
# Root, so the entrypoint can start the engine; it hands over to the `runner` user before the runner starts.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["./run.sh"]
