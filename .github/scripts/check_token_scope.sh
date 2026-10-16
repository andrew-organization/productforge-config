#!/usr/bin/env bash
# Proves, in the job it runs in, that the token $PF_TOKEN is held by Git only through the job's environment: shown
# from the environment by `git config --list --show-origin`, and in no Git configuration file. With no token set,
# the check says so and passes: a pull request from a fork has no secrets.
set -euo pipefail

if [ -z "${PF_TOKEN:-}" ]; then
  echo "::notice::no token set, so the check of where it is held was skipped"
  exit 0
fi

header="$(printf 'x-access-token:%s' "${PF_TOKEN}" | base64 | tr -d '\n')"
listing="$(git config --list --show-origin)"

# Found, and only from the environment ("command line:" is how git names the origin of GIT_CONFIG_* entries).
holders="$(grep -F "${header}" <<< "${listing}" | cut -f1 | sort -u || true)"
if [ "${holders}" != "command line:" ]; then
  echo "::error::the token's header is held by '${holders:-nothing}', not only by the environment"
  exit 1
fi
if grep -qF "${PF_TOKEN}" <<< "${listing}"; then
  echo "::error::the raw token is in Git's configuration"
  exit 1
fi

# And in no file Git reads or this checkout holds.
files=("${HOME}/.gitconfig" "${XDG_CONFIG_HOME:-${HOME}/.config}/git/config" /etc/gitconfig .git/config)
for file in "${files[@]}"; do
  if [ -f "${file}" ] && grep -qF -e "${PF_TOKEN}" -e "${header}" "${file}"; then
    echo "::error::${file} holds the token"
    exit 1
  fi
done
echo "the token is held by the environment alone"
