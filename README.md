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
  `shellcheck`, `taplo-format`, `taplo-lint` and `checkmake` for any
  repository; `pyupgrade`, `isort`, `black` and `flake8` for Python;
  `django-mypy` for a Django API (a system hook, run through the project's
  own uv environment, so mypy's Django plugin can import every runtime
  dependency; it reports without blocking a commit); and
  `dart-format` and `flutter-analyze` for a Flutter app, run through its own
  Flutter. A repository takes the ones for the files it has.
  `end-of-file-fixer` holds every text file to a single final newline,
  however it got there: inside a commit it stages its own fix and lets the
  commit through; in CI, or run by hand, it fails as any fixer does.
- `actions/setup/action.yml` — the CI setup every repository repeats: uv
  with Python 3.14 and its cache, the pre-commit cache keyed on the
  caller's own hook files, and `make install` — with Flutter and Postgres
  as options (`flutter: true`, `postgres: true`).
- `.github/workflows/ci-api.yml`, `.github/workflows/ci-web.yml` — the
  reusable CI workflows for an API and a web repository (see "Reusable CI
  workflows"), and `.github/workflows/release.yml`, the reusable release.
- `kits/common/`, `kits/api/`, `kits/web/` — the build, run, test and CI
  kits (see "Kits"), and `kits/init/`, the two thin files `init` writes.
- `settings/markdownlint.jsonc`, `settings/yamllint.yaml`,
  `settings/python.toml` — the markdownlint and yamllint rules and the
  black, isort, flake8 and mypy settings. `update` writes them into each
  repository that takes the matching hook, as a real local copy, so editors
  read the same rules; a change here reaches every repository on its next
  update.
- `settings/editorconfig` — written whole as the `.editorconfig` of each
  repository that takes `end-of-file-fixer` or `trailing-whitespace`, so
  editors that read EditorConfig save files the way those hooks leave them.
- `settings/pre-commit-config-api.yaml`, `settings/pre-commit-config-web.yaml`,
  `settings/fvmrc` and `settings/dart.toml` — the git-hook config of each
  kind, and the Flutter version with the Dart SDK constraint that goes with
  it, written to fixed paths in a repository that takes a kit (see "Kits").
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

It moves the productforge-config hook source's `rev` in
`.pre-commit-lint.yaml`, and the
`andrew-organization/productforge-config/actions/setup@...` and
`andrew-organization/productforge-config/.github/workflows/release.yml@...`
refs in every `.github/workflows/*.yml` file, to that version, and rewrites the shared
keys this release carries into the repository's own local copies
(`.markdownlint-cli2.jsonc`'s `"config"`; and, only for a hook the
repository actually takes from this repository's own block in
`.pre-commit-lint.yaml`, `.yamllint` written whole, `pyproject.toml`'s
`[tool.black]`/`[tool.isort]` and `setup.cfg`'s `[flake8]`) — leaving a repository's own hooks, ignored
paths and excluded paths exactly as they were. Commit the result and raise
it as an ordinary pull request; nothing here opens that pull request for
you.

## Kits

A repository takes its build, run, test and CI configuration from this
repository, and supplies only its own values. It commits a
`productforge.env` at its root (dotenv):

```sh
PF_KIND=api            # api or web: which kit it takes
PF_SLOT=0              # its port slot, shared with the other repository of the product
PF_NAME=acme_api       # the Compose project name, image prefix, database name, and the
                       # Django project package or the Dart package
# PF_DJANGO_PROJECT=   # optional, defaults to PF_NAME (api)
# PF_POSTGRES_DB=      # optional, defaults to PF_NAME (api)
```

Names are lower-case letters, digits and underscores, starting with a letter.
`update` installs the kit `PF_KIND` names, written whole into `.productforge/`,
each file with a header saying it is generated and to change it here:

| Kit | Files |
| --- | --- |
| `common` (both) | `common.mk` (computes and exports `PF_PORT_*` from the slot; `install`, `lint`, `setup-hooks`, `clean`, `ports`), `CLAUDE.md` |
| `api` | `api.mk` (`test`, `test-integration`, `test-integration-ci`, `check-migrations`, `build`, `up`, `down`, `logs`, `shell`, `lock`, `all`), `Dockerfile`, `entrypoint`, `compose.yml`, `compose.test.yml`, and a root `.dockerignore` |
| `web` | `web.mk` (`l10n`, `generate`, `identity`, `check-identity-regeneration`, `check-generated`, `test`, `build`, `up`, `debug`, `down`, `serve-build`, `all`), `generate_identity.py`, `check_identity_regeneration.py`, `serve_web_build.py`, `analysis_options.yaml` |

Every file the kit wrote is listed in `.productforge/manifest`, so an update
removes a file an earlier kit installed that this one no longer carries, and
leaves any other file alone. Beside the kit, `update` writes the settings that
go at fixed paths: `.pre-commit-config.yaml` for the kind, and for a web app
`.fvmrc` and the pubspec's `environment.sdk`; and it keeps
`.pre-commit-lint.yaml`'s top-level `exclude` covering `^\.productforge/`,
adding it or widening the pattern already there, because generated files are
linted where they are written.

The repository's own `Makefile` is thin: it includes `productforge.env`,
`.productforge/common.mk` and the kit's own `.mk`, and keeps its
`update-config` target. Its `CLAUDE.md` imports the fragment describing how to
run, ports and CI with `@.productforge/CLAUDE.md`, and a web app's own
`analysis_options.yaml` is `include: .productforge/analysis_options.yaml` plus
what is its own.

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
`Makefile`, and the thin `.github/workflows/ci.yml` (`on: pull_request`, calling
`ci-<kind>.yml@<tag>`), replacing a full `Makefile` or `ci.yml` already there,
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

jobs:
  ci:
    uses: andrew-organization/productforge-config/.github/workflows/ci-api.yml@v1.0.0
    permissions:
      contents: read
```

Each reads the `PF_*` lines of `productforge.env` into the job's environment
and runs the make targets a developer runs: the API's `make lint`, `make
check-migrations`, `make test` and `make test-integration-ci` (which starts the
test Postgres from `compose.test.yml`) in parallel after `make install`; the
web's `make check-generated` and `make check-identity-regeneration`, then `make
lint` and `make test` in parallel. The setup is written out in each, because a
reusable workflow can't reference an action of this repository at its own tag;
`actions/setup` remains for repositories with their own workflow. `parallel:`
steps run inside a reusable workflow as they do in a caller's own job.

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

## Checking and applying a repository's GitHub settings

`settings/github.json` holds the GitHub repository settings every
ProductForge repository shares — not a repository's own local copy, but
the live GitHub-side configuration itself. Each value is proposed for
review before it's ever applied to a real repository:

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
