# productforge-config

Public; holds nothing secret, so every repository's CI reads it without a
token. What every ProductForge repository has in common: its shared
pre-commit hooks, its CI setup, its release workflow, and its lint and format
settings. Released by tag (`vMAJOR.MINOR.PATCH`), automatically, on every
releasing merge to main; a repository is brought up to a release by one
command, run in it.

## What it holds

- `.pre-commit-hooks.yaml` — the lint hooks every repository shares, each
  pinned to its tool's own version: `trailing-whitespace`,
  `end-of-file-fixer`, `check-json`, `markdownlint`, `yamllint`,
  `shellcheck`, `taplo-format`, `taplo-lint` and `checkmake` for any
  repository; `pyupgrade`, `isort`, `black` and `flake8` for Python; and
  `dart-format` and `flutter-analyze` for a Flutter app, run through its own
  Flutter. A repository takes the ones for the files it has.
- `actions/setup/action.yml` — the CI setup every repository repeats: uv
  with Python 3.14 and its cache, the pre-commit cache keyed on the
  caller's own hook files, and `make install` — with Flutter and Postgres
  as options (`flutter: true`, `postgres: true`).
- `settings/markdownlint.jsonc`, `settings/yamllint.yaml`,
  `settings/python.toml` — the markdownlint and yamllint rules and the
  black, isort and flake8 settings. `update` writes them into each
  repository that takes the matching hook, as a real local copy, so editors
  read the same rules; a change here reaches every repository on its next
  update.
- `go.mod` — only so pre-commit can install the golang `checkmake` hook.
- `src/productforge_config/` — the `productforge-config update` command
  that brings a repository up to a release.

## Bringing a repository up to a release

Run in the repository, typically through its own `make update-config`
(`VERSION` optional — see below):

```sh
uvx --from git+https://github.com/andrew-organization/productforge-config@v1.0.0 \
  productforge-config update --version v1.0.0
```

`--version` is optional: left unset, or given as `latest`, it resolves to
the newest release tag of this repository — a stable `vX.Y.Z`, or the
newest `-rc.N` when no stable release exists yet. An explicit version is
validated as a release tag (`vMAJOR.MINOR.PATCH`, optionally with a `-rc.N`
suffix) before anything is written, and refused with a clear message
otherwise.

It moves the productforge-config hook source's `rev` in
`.pre-commit-lint.yaml` and the
`andrew-organization/productforge-config/actions/setup@...` ref in every
`.github/workflows/*.yml` file to that version, and rewrites the shared
keys this release carries into the repository's own local copies
(`.markdownlint-cli2.jsonc`'s `"config"`; and, only for a hook the
repository actually takes from this repository's own block in
`.pre-commit-lint.yaml`, `pyproject.toml`'s `[tool.black]`/`[tool.isort]`
and `setup.cfg`'s `[flake8]`) — leaving a repository's own hooks, ignored
paths and excluded paths exactly as they were. Commit the result and raise
it as an ordinary pull request; nothing here opens that pull request for
you.

## A repository's own side

```yaml
# .pre-commit-lint.yaml
repos:
  - repo: https://github.com/andrew-organization/productforge-config
    rev: v1.0.0
    hooks: [{id: trailing-whitespace}, {id: end-of-file-fixer}, {id: markdownlint},
            {id: yamllint}, {id: checkmake},
            {id: pyupgrade}, {id: isort}, {id: black}, {id: flake8}]   # the hooks it needs
  # then the repository's own hooks, unchanged
```

```yaml
# .github/workflows/ci.yml, after actions/checkout
- uses: andrew-organization/productforge-config/actions/setup@v1.0.0
  with:
    flutter: "true"        # reads the repository's own .fvmrc
    postgres: "true"       # starts postgres:18-alpine as a background container
    postgres-db: "my_app"  # required when postgres is "true" — no shared default
```

```yaml
# .github/workflows/release.yml, for a repository that releases
name: Release
on:
  push:
    branches: [main]
jobs:
  release:
    uses: andrew-organization/productforge-config/.github/workflows/release.yml@v1.0.0
    permissions: {contents: write, issues: write, pull-requests: write}
    # with:
    #   prepare-cmd: ./scripts/write-version.sh ${nextRelease.version}   # only if a file carries the version
    #   check-cmd: ./scripts/check-release.sh                             # only for a check of its own
```

A merge to main releases by its pull request's title: `feat` a minor,
`fix`, `perf`, `refactor` and `build(deps)` a patch, a `!` or
`BREAKING CHANGE` footer a major, anything else nothing. The release is a
git tag and its GitHub release.

```makefile
# Makefile
VERSION ?= latest

update-config:
	@version="$(VERSION)"; \
	if [ "$$version" = "latest" ]; then version=$$(git ls-remote --tags https://github.com/andrew-organization/productforge-config 2>/dev/null | sed 's#.*refs/tags/##' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$$' | sort -V | tail -n1); [ -n "$$version" ] || version=$$(git ls-remote --tags https://github.com/andrew-organization/productforge-config 2>/dev/null | sed 's#.*refs/tags/##' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+$$' | sort -V | tail -n1); [ -n "$$version" ] || { echo "update-config: couldn't resolve the latest productforge-config release tag" >&2; exit 1; }; fi; \
	uvx --from git+https://github.com/andrew-organization/productforge-config@$$version productforge-config update --version $$version
```

`VERSION` defaults to `latest`; the recipe resolves it to a real tag
before it ever reaches `uvx` — `git+...@latest` isn't a ref `uvx` can
fetch, so an empty or unresolved ref is refused rather than passed
through. Pass an explicit tag to pin one: `make update-config
VERSION=v1.0.0`.

## Developing this repository

`make install`, `make lint`, `make test` — the same three targets every
ProductForge repository has. `make test` runs the update command's own
test suite against `tests/fixture_repo`, entirely against throwaway
copies, plus a smoke test of the published hooks themselves.
