# productforge-config

Public; holds nothing secret, so every repository's CI reads it without a
token. What every ProductForge repository has in common: its shared
pre-commit hooks, its CI setup, its release workflow, its lint and format
settings, and its build, run, test and CI configuration, which a repository
takes as a kit and supplies only its own values to. Released by tag
(`vMAJOR.MINOR.PATCH`), automatically, on every releasing merge to main; a
repository is brought up to a release by one command, run in it.

## What it holds

- `.pre-commit-hooks.yaml` — the lint hooks every repository shares, each
  pinned to its tool's own version: `trailing-whitespace`,
  `end-of-file-fixer`, `check-json`, `markdownlint`, `yamllint`,
  `shellcheck`, `taplo-format`, `taplo-lint` and `checkmake`; `pyupgrade`, `isort`, `black` and `flake8` for Python;
  `django-mypy` for a Django API (a system hook, run through the project's
  own uv environment, so mypy's Django plugin can import every runtime
  dependency; it reports without blocking a commit); and
  `dart-format` and `flutter-analyze` for a Flutter app, run through its own
  Flutter. A repository takes the ones its kits carry.
  `end-of-file-fixer` holds every text file to a single final newline,
  however it got there: inside a commit it stages its own fix and lets the
  commit through; in CI, or run by hand, it fails as any fixer does.
- `actions/setup/action.yml` — the CI setup every repository repeats, written
  once: uv with the Python `settings/python.toml` states and its cache, the
  pre-commit cache keyed on the caller's hook files, and `make install` — with
  Flutter as an option (`flutter: true`).
- `.github/workflows/ci-api.yml`, `.github/workflows/ci-web.yml` — the
  reusable CI workflows for an API and a web repository (see "Reusable CI
  workflows"), and `.github/workflows/release.yml`, the reusable release.
- `kits/` — what each kit writes into a repository (see "Kits"): `common`,
  `python`, `django-api`, `flutter-web`, `product` (shared by the two product
  kits) and `kits/init/`, the thin files `init` writes.
- `settings/markdownlint.jsonc`, `settings/yamllint.yaml`,
  `settings/python.toml`, `settings/common.toml` — the markdownlint and yamllint
  rules, the Python version and the black, isort, flake8 and mypy settings, and
  the `pre-commit` floor. The Python version is stated once, in
  `settings/python.toml`: `update` derives black's target, mypy's version,
  `requires-python` and the `django-api` image's Python from it, and
  `actions/setup` installs it. The line length is stated there once too, and
  written as black's, isort's and flake8's.
  `update` writes them into each repository, as a real local copy, so editors
  read the same rules; a change here reaches every repository on its next
  update.
- `settings/editorconfig` — written whole as every repository's `.editorconfig`,
  so editors that read EditorConfig save files the way the whitespace hooks
  leave them.
- `settings/pre-commit-config-django-api.yaml`,
  `settings/pre-commit-config-flutter-web.yaml`, `settings/fvmrc` and
  `settings/dart.toml` — the git-hook config of each product kit, and the
  Flutter version with the Dart SDK constraint that goes with it, written to
  fixed paths in a repository that takes the kit (see "Kits").
- `go.mod` — only so pre-commit can install the golang `checkmake` hook.
- `settings/github.json` — the GitHub repository settings every
  ProductForge repository shares, checked and applied by
  `productforge-config github` rather than kept as a local copy (see
  below).
- `src/productforge_config/` — the `productforge-config update` command
  that brings a repository up to a release, `init` and `ports` for the
  kits, and the `productforge-config github` command that checks or applies
  `settings/github.json`.

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
suffix) or a full 40-character commit SHA, which pins the hooks `rev:` and
the workflow `uses:` refs to that commit so a repository can adopt an
unreleased config, before anything is written, and refused with a clear message
otherwise.

It writes the files of every kit the repository's `productforge.env` declares
(see "Kits"), moves every
`andrew-organization/productforge-config/actions/...@ref` and
`andrew-organization/productforge-config/.github/workflows/...@ref` in every
`.github/workflows/*.yml` file (the setup action, the release workflow, the
reusable CI workflows) to that version, and merges the shared keys this release
carries into the repository's own files: `.markdownlint-cli2.jsonc`'s
`"config"`, and, for the `python` kit, `pyproject.toml`'s
`[tool.black]`/`[tool.isort]` and `setup.cfg`'s `[flake8]`, and for `django-api`
`[tool.mypy]`'s strictness. Everything else in those files stays as it was.
Commit the result and raise it as an ordinary pull request; nothing here opens
that pull request for you. A repository with no `productforge.env` is refused.

A repository from before the kits split (declared with `PF_KIND`, its hooks
listed from this repository in `.pre-commit-lint.yaml`, its Makefile including
`.productforge/common.mk` and `api.mk` or `web.mk`) is moved in the same run:
`PF_KIND` becomes `PF_KITS`, the block of this repository's hooks is removed from
`.pre-commit-lint.yaml` (the file is deleted when nothing else is left in it),
and the Makefile's includes and its own `update-config` recipe give way to
`.productforge/*.mk`. A `.pre-commit-lint.yaml` that names this repository in
flow style is refused, since its block cannot be located.

`update --check` changes nothing: it lists what an update would change, and
exits 1 when there is anything to change, 0 when there is not, and 2 when it
cannot tell (an invalid version, a declaration it can't use, or no release
recorded). Without `--version` it checks at the release recorded in
`.productforge/release`, so a newer release existing is not drift; `--version
latest` reports a repository behind the newest release. It finds a written file
edited by hand, a stale file the update would remove, and a workflow ref behind
the release. `make check-config` runs it at the recorded release.

## Kits

A repository says which kits it takes in one line, and everything it shares with
the others follows from that line and the release it is on. It commits a
`productforge.env` at its root (dotenv):

```sh
# Space-separated, any order; `common` is always taken and never named.
# Kits: python, shell, django-api, flutter-web, release.
PF_KITS=python django-api

# Required with django-api or flutter-web, refused without one.
PF_SLOT=0              # the product's port slot, shared by its API and web app
PF_NAME=acme_api       # the Compose project name, image prefix, database name, and the
                       # Django project package or the Dart package
# PF_DJANGO_PROJECT=   # optional, defaults to PF_NAME (django-api)
# PF_POSTGRES_DB=      # optional, defaults to PF_NAME (django-api)
# PF_LINT_EXCLUDE=     # optional regular expression of paths the shared hooks skip
```

Names are lower-case letters, digits and underscores, starting with a letter.
`update` refuses, before writing anything, an unknown kit, `common` named, a kit
named twice, `PF_KIND` with `PF_KITS`, both product kits, a product kit without a
valid name and slot, and a slot, name or override with no product kit.

`update` writes each kit's files, each with a header saying it is generated
where its syntax allows one:

| Kit | Files |
| --- | --- |
| `common` (always) | `common.mk` (`install`, `lint`, `setup-hooks`, `clean`, `update-config`, `check-config`), `pre-commit.yaml` (the hooks of the kits taken, at the release), `release` (the release record), and at the root `.editorconfig`, `.yamllint` and `.python-version` (the Python `settings/python.toml` states, so uv picks it in a repository with no `requires-python`); the `"config"` of `.markdownlint-cli2.jsonc` |
| `python` | `python.mk` (`PYTEST`, parallel by default, `test` where `django-api` is not taken, `PYTHONDONTWRITEBYTECODE`, and `clean` of `.venv` and every `__pycache__`); the black and isort keys of `pyproject.toml` and the flake8 keys of `setup.cfg` |
| `shell` | the `shellcheck` hook |
| `django-api` | `django-api.mk` (`test`, `test-integration`, `test-integration-ci`, `check-migrations`, `build`, `up`, `down`, `logs`, `shell`, `lock`, `all`), `Dockerfile`, `entrypoint`, `compose.yml`, `compose.test.yml`, a root `.dockerignore` and `.pre-commit-config.yaml`; the `django-mypy` hook and `[tool.mypy]` |
| `flutter-web` | `flutter-web.mk` (`l10n`, `generate`, `identity`, `check-identity-regeneration`, `check-generated`, `test`, `build`, `up`, `debug`, `down`, `serve-build`, `all`), `generate_identity.py`, `check_identity_regeneration.py`, `serve_web_build.py`, `analysis_options.yaml`, and at the root `.pre-commit-config.yaml` and `.fvmrc`; the pubspec's `environment.sdk` |
| product kits | `product.mk` (the `PF_` validation, the slot's ports, `ports`) and `CLAUDE.md` |
| `release` | nothing written yet; the repository calls the shared release workflow |

A repository declares `pre-commit` as a plain dev dependency: `update` (and so
`update --check`) fails on a `pyproject.toml` that gives it a version, because the floor is
stated once, in `settings/common.toml`.

`make lint` runs `.productforge/pre-commit.yaml` and then the repository's own
`.pre-commit-lint.yaml` when it has one, both always, failing when either does;
a repository lists only the hooks its kits carry, so it never builds the
environment of a tool it has no files for. `.pre-commit-lint.yaml` is the
repository's own hooks only.

Every whole file `update` wrote is listed in `.productforge/manifest`, so a
declaration that drops a kit has that kit's files removed, and any other file
is left alone.

The repository's own `Makefile` is thin: it includes `productforge.env` and
`$(sort $(wildcard .productforge/*.mk))`, and keeps its own targets. A product
repository's `CLAUDE.md` imports the fragment describing how to run, ports and CI
with `@.productforge/CLAUDE.md`, and a web app's own `analysis_options.yaml` is
`include: .productforge/analysis_options.yaml` plus what is its own.

### Docker Compose

The API's Compose project is run as `docker compose --env-file productforge.env -f
.productforge/compose.yml [-f docker-compose.local.yml, if present]
--project-directory .` (Compose v2), which the Makefile does; `.env`, when
present, is read after `productforge.env` for a developer's own overrides.
Compose's `include:` doesn't carry a file's `name:`, hence the `-f`. Both compose
files require their values with `${PF_...:?}`, so a missing one is an error
naming it: `name:`, image names, ports, the database, `DJANGO_SETTINGS_MODULE`
(`${PF_DJANGO_PROJECT}.settings.base`) and `celery -A ${PF_DJANGO_PROJECT}`. The
Compose project also gives Django `WEB_ORIGIN` and `FRONTEND_BASE_URL`, the web app's
origin on this machine, worked out from the slot. `make up mode=mobile` (on
the API and on the web app) adds this machine's LAN IP to the allowed hosts and
CORS origins, and points the web app at the API on that IP, so a phone on the
same network reaches both.

This repository takes its own kits, `python shell release`, through its own
`update`, at the release it last recorded: its hooks, `.editorconfig`,
`.yamllint`, `.markdownlint-cli2.jsonc` and `setup.cfg` are what `update` writes,
and its CI checks them with `make check-config`. Its own lint declares only what
is its own: `PF_LINT_EXCLUDE` in `productforge.env` skips `tests/fixture_*` and the
kits' makefile fragments.

## Ports

A slot owns 20 local ports, from 6100 + 20 x slot. The API and the web app of a
product share a slot, so each derives the other's port: the web app's API
endpoint, and the API's CORS origin and `FRONTEND_BASE_URL`.

| Offset | Service | `make` variable |
| --- | --- | --- |
| +0 | API | `PF_PORT_API` |
| +1 | Postgres | `PF_PORT_POSTGRES` |
| +2 | Redis | `PF_PORT_REDIS` |
| +3 | Flower | `PF_PORT_FLOWER` |
| +4 | Mailpit (SMTP) | `PF_PORT_MAILPIT_SMTP` |
| +5 | Mailpit (web UI) | `PF_PORT_MAILPIT_UI` |
| +6 | Web dev server, and a served build | `PF_PORT_WEB` |
| +11 | Postgres for the integration tests | `PF_PORT_TEST_POSTGRES` |

Offsets +7 to +10 and +12 to +19 are reserved. Slot 0 is 6100-6119, the ports
the templates used before slots. A slot is refused when its block, reserved
offsets included, holds a port Chromium refuses to connect to (6566,
6665-6669, 6679, 6697, 10080), or when it is above 1332, whose ports would
reach the range operating systems hand out to outgoing connections.

```sh
productforge-config ports --slot 3          # 6160  the API, 6161  Postgres, ...
productforge-config ports --slot 3 --env    # PF_PORT_API=6160, PF_PORT_POSTGRES=6161, ...
make ports                                  # the same, for this repository's own slot
```

## Starting a repository: `init`

```sh
uvx --from git+https://github.com/andrew-organization/productforge-config@v1.0.0 \
  productforge-config init --kind api --name acme_api --slot 4 \
  [--from productforge_api_template] [--version v1.0.0]
```

Validates the values first, then writes `productforge.env`, the thin
`Makefile`, and the thin `.github/workflows/ci.yml` (on every pull request and every
push to `main`, calling `ci-<kind>.yml@<tag>`), replacing a full `Makefile` or `ci.yml` already there,
and then runs `update`, so the kit is in place. Running it again with the same
values changes nothing.

With `--from <old-name>`, the repository is renamed first: every form of the
old name becomes the new one across the repository: snake_case and kebab-case
(given in either), the Django project directory `api/<old>/` to `api/<new>/`,
Dart `package:<old>/` imports and the pubspec's name, and then `uv lock` for an
API. It leaves `.git`, virtual environments, build output and binary files
alone. A name without a separator (`app`, `api`) is too likely to be an ordinary
word to replace everywhere, so it gets only the directory, the imports and the
pubspec's name; module paths such as `api.settings` need the caller's care.
Renaming from `productforge_api_template` or `productforge_web_template`, the
real use, is safe: those names are unique, so every form of them is replaced.

## Reusable CI workflows

`.github/workflows/ci-api.yml` and `ci-web.yml` are `workflow_call` workflows;
a repository's `ci.yml` is thin:

```yaml
on:
  pull_request:
  push:
    branches:
      - main

concurrency:
  group: ci-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  ci:
    uses: andrew-organization/productforge-config/.github/workflows/ci-api.yml@v1.0.0
    permissions:
      contents: read
```

The push trigger runs the workflow on `main` after every merge, so the caches it
saves belong to main's scope, which every pull request restores; the concurrency
group cancels a superseded pull-request run and never a run on `main`, since a
cancelled run saves no cache.

Each runs the make targets a developer runs: the API's `make lint`, `make
check-migrations`, `make test` and `make test-integration-ci` (which starts the
test Postgres from `compose.test.yml`) in parallel; the web's `make
check-generated` and `make check-identity-regeneration`, then `make lint` and
`make test` in parallel; and both a `Config unchanged` step, `make check-config`,
so a written file changed by hand, or left behind by a release, fails the pull
request. The setup is not written out in either: a reusable workflow can't
reference an action of this repository at its own tag with `uses:`, so each checks
this repository out at the commit it runs from (`job.workflow_repository` at
`job.workflow_sha`, into an untracked `.productforge-config/`) and calls
`actions/setup` from there. `parallel:` steps run inside a reusable workflow as
they do in a caller's own job.

## A repository's own side

```yaml
# .pre-commit-lint.yaml: optional, and only the repository's own hooks; the hooks of
# the kits taken are in .productforge/pre-commit.yaml, which `update` writes.
repos:
  - repo: local
    hooks:
      - id: vault
        name: vault properties contract
        entry: python3 scripts/vault.py check
        language: system
```

```yaml
# .github/workflows/ci.yml, after actions/checkout
- uses: andrew-organization/productforge-config/actions/setup@v1.0.0
  with:
    flutter: "true"        # reads the repository's own .fvmrc
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

## Checking and applying a repository's GitHub settings

`settings/github.json` holds the GitHub repository settings every
ProductForge repository shares — not a repository's own local copy, but
the live GitHub-side configuration itself. Each value is proposed for
review before it's ever applied to a real repository:

- **Default branch**: `main` (`default_branch`), the branch every workflow,
  release and product copy is built around; `check` reports any repository
  on another.
- **Merge methods**: squash only (`allow_squash_merge`, with
  `allow_merge_commit` and `allow_rebase_merge` both false) — one commit
  per pull request on the target branch, so history reads as a list of
  changes rather than a list of individual commits plus their merges.
  `squash_merge_commit_title: PR_TITLE` and
  `squash_merge_commit_message: PR_BODY` make that squash commit's
  message the pull request's own title and body, rather than GitHub's
  default concatenation of every commit on the branch.
- **`delete_branch_on_merge: true`** — a merged branch is done; nothing
  needs it left behind.
- **`allow_update_branch: true`** — offers the one-click "Update branch"
  button on a pull request behind its target, without requiring it.
- **`allow_auto_merge: false`** — a merge is a deliberate action, not
  something queued to happen unattended.
- **`has_issues`, `has_projects`, `has_wiki`, `has_discussions`: all
  false** — none of these are where ProductForge tracks work or
  discussion; leaving them on invites drift to a second, unmaintained
  home for both.
- **`web_commit_signoff_required: false`** — this org doesn't require
  sign-off on GitHub's own web-based commits.
- **Actions workflow permissions default to read**
  (`default_workflow_permissions: read`,
  `can_approve_pull_request_reviews: false`) — a workflow's `GITHUB_TOKEN`
  can read a repository by default but not write to it or approve pull
  requests; a workflow that genuinely needs to write asks for that
  permission explicitly in its own YAML instead of relying on a broad
  repository default.
- **For public repositories only**, a ruleset requiring a pull request to
  change `main` (no direct pushes) — skipped for a private repository,
  because branch rules aren't available on this organisation's GitHub
  Free plan.

### Checking

```sh
uvx --from git+https://github.com/andrew-organization/productforge-config@v1.0.0 \
  productforge-config github check --repo andrew-organization/some-repo
```

Prints one line for each setting whose live value differs from
`settings/github.json` (or nothing, when the repository already matches).
Exits `1` if anything differs, `0` if nothing does. Entirely read-only —
`check` never calls a `gh api` write.

### Applying

```sh
uvx --from git+https://github.com/andrew-organization/productforge-config@v1.0.0 \
  productforge-config github apply --repo andrew-organization/some-repo
```

Applies `settings/github.json` to the named repository: `gh api -X PATCH
repos/<repo>` for the repository fields, `gh api -X PUT
repos/<repo>/actions/permissions/workflow` for the Actions default
workflow permissions, and — only when the repository is public — the
branch ruleset, created if it doesn't exist yet or updated in place by
name if it does. Idempotent: every call sends the file's full values, so
running it again once a repository already matches changes nothing
further.

**Applying needs repository admin rights** on the target — the same
rights `gh` itself needs to change these settings — and changes a real
repository's configuration immediately, with no further confirmation.
Review the proposed values above (and `settings/github.json` itself)
before running it against any repository.

Both commands need `gh` installed and authenticated (`gh auth status`)
with access to the target repository; neither talks to the GitHub API
directly.

## Developing this repository

`make install`, `make lint`, `make test` — the same three targets every
ProductForge repository has. `make test` runs the update command's own
test suite against `tests/fixture_repo` (a repository with no kit) and
`tests/fixture_api` and `tests/fixture_web` (one of each kind), entirely
against throwaway copies: install, idempotence, stale files, `--check`,
`init` and the rename, the port arithmetic in Python and in `make`, `make
-n` against each fixture's thin Makefile, and Docker Compose interpolation
(skipped when `docker compose` is absent), plus a smoke test of the
published hooks themselves. Run it with `TMPDIR=$(mktemp -d)` so it doesn't
share a temp root with another pytest run.
