#!/bin/bash
# Builds the runner image from this directory and tags it productforge-runner:VERSION.
#
# usage: build.sh VERSION
#
# VERSION is the productforge-config release this directory came from (for example v2.8.0); it becomes the
# image's tag and a label. Run it on the runner host from the release's supervisor/runner-image/ directory,
# as root or as a user in the docker group. The supervisor's README says when to run it.
set -euo pipefail

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
    echo "usage: build.sh VERSION   (the productforge-config release this directory is from)" >&2
    exit 2
fi
version="$1"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker build \
    --label "org.opencontainers.image.source=https://github.com/andrew-organization/productforge-config" \
    --label "org.opencontainers.image.version=${version}" \
    --tag "productforge-runner:${version}" \
    "${here}"
echo "built productforge-runner:${version}"
