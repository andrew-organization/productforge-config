#!/usr/bin/env bash
# Authenticates every later Git read of the repository owner's repositories in this job with $PF_TOKEN, through
# Git configuration supplied by the job's environment (GIT_CONFIG_COUNT, GIT_CONFIG_KEY_n, GIT_CONFIG_VALUE_n,
# exported to the following steps through $GITHUB_ENV). The token is never written to a Git configuration file.
set -euo pipefail

: "${PF_TOKEN:?no token given}"
: "${GITHUB_ENV:?not running in a workflow}"
: "${GITHUB_REPOSITORY_OWNER:?not running in a workflow}"

header="AUTHORIZATION: basic $(printf 'x-access-token:%s' "${PF_TOKEN}" | base64 | tr -d '\n')"
# The header is a different string from the token, so it is masked in its own right.
echo "::add-mask::${PF_TOKEN}"
echo "::add-mask::${header#AUTHORIZATION: basic }"

# Adds to whatever configuration the job's environment already carries, never replaces it.
index="${GIT_CONFIG_COUNT:-0}"
{
  echo "GIT_CONFIG_COUNT=$((index + 1))"
  echo "GIT_CONFIG_KEY_${index}=http.https://github.com/${GITHUB_REPOSITORY_OWNER}/.extraheader"
  echo "GIT_CONFIG_VALUE_${index}=${header}"
} >> "${GITHUB_ENV}"
