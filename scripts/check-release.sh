#!/usr/bin/env bash
# check-release.sh — the release workflow's last step.
#
# Usage: check-release.sh
#
# Fails unless the latest tag on the default branch has a GitHub release.
# The tag is this repository's only version: nothing in the tree carries it.
#
# semantic-release exits successfully when it finds nothing to release,
# including when a failed run left a tag behind without its release. This
# step turns that silent success into a failure.

set -euo pipefail

BRANCH="${GITHUB_REF_NAME:?GITHUB_REF_NAME required}"

git fetch --quiet --tags origin "${BRANCH}"

latest_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*.[0-9]*.[0-9]*' --exclude '*-*' "origin/${BRANCH}" 2>/dev/null || true)"

if [ -z "${latest_tag}" ]; then
  echo "[check-release] no release tag on ${BRANCH} yet"
  exit 0
fi

gh release view "${latest_tag}" > /dev/null || {
  echo "[check-release] ERROR: no GitHub release exists for ${latest_tag}" >&2
  exit 1
}
