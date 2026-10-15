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

### The runner switch

The jobs of `ci-api.yml`, `ci-web.yml` and `release.yml` take their runner from
the organisation variable `PF_RUNS_ON`:

```yaml
runs-on: ${{ fromJSON(vars.PF_RUNS_ON || '"ubuntu-latest"') }}
```

| `PF_RUNS_ON` | The job runs on |
| --- | --- |
| unset or empty | a GitHub-hosted runner (`ubuntu-latest`), counted against the Actions allowance |
| `"ubuntu-latest"` | the same |
| `["self-hosted","pf"]` | the runner carrying those labels, which uses no hosted minutes |
| anything else | nothing: the run fails at its start |

The value is JSON, so a label list keeps its brackets and quotes. Deleting the
variable returns every repository to hosted runners at once, with no change to
any caller. This repository's own `ci.yml` keeps `ubuntu-latest` because the
repository is public, and a public repository's hosted minutes are free.

When the runner is down, jobs wait for it rather than fail. A queued job has
already resolved its labels, so changing the variable does not move it: unset
`PF_RUNS_ON` (or set it to `"ubuntu-latest"`), cancel the waiting runs, then
re-run them, and the new runs resolve to hosted. Set the variable back once the
runner is up.

A public repository that calls these workflows with the switch set to the
self-hosted runner queues forever: the runner group excludes public
repositories, so no runner ever takes the job. Public repositories stay on
`ubuntu-latest`, whose minutes are free.

An invalid value fails the run at its start as a workflow file issue, with no job
and no minutes used, and GitHub's message doesn't name the variable. When a run
fails that way and the workflow file is unchanged, check `PF_RUNS_ON` first.

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
- **Rulesets**, on a public or a private repository alike: a variant
  names which of the file's `rulesets` it applies (see Variants below).

### Variants

`--variant` picks the protection a repository gets; the default is
`standard`.

| Variant | What it applies |
| --- | --- |
| `standard` | `require-pull-request-to-change-main` (a pull request, squash merge only, no force-push, no deletion, and the status check `ci`) and `protect-release-tags` (`v*` tags can't be moved, deleted or updated) |
| `product` | `standard`, requiring `ci / ci` — the name GitHub reports for a job that calls a reusable workflow, as a repository copied from a template has — and the `production` environment, which only `main` can deploy to |
| `publishes` | `standard` with the organisation owner (`OrganizationAdmin`) as an always-bypass actor on the `main` ruleset, for a repository whose release commits to `main`; with `--bypass-deploy-keys`, its deploy keys too |
| `block-only` | `block-main-rewrites`: only rewriting and deleting `main` is blocked; no check is required |

`--bypass-deploy-keys` adds the repository's deploy keys as a second always-bypass
actor (`DeployKey`) on the `main` ruleset, next to the owner. Only `publishes`
takes it; any other variant refuses it before any `gh` call. It is for a
repository whose release pushes its version commit with a deploy key (see
"A release that pushes with a deploy key" below). GitHub reads the actor back
with `actor_id: null`, so `check` compares it, like the owner, on its type and
mode, in whatever order GitHub lists the actors.

`--required-check NAME` (repeatable) replaces the variant's default required
check, named as GitHub reports it. It is refused with `block-only`, as is an
unknown `--variant`, before any `gh` call.

`--product NAME` sets the repository's value of the organisation's custom
property `product` (`apply` fails, naming the property, when the
organisation has none). Without it, `apply` leaves the value as it is and
`check` reports only a missing one. Given it, `check` reports a missing or
different value.

A private repository on a plan that offers no rules answers the rulesets
with a 403; `apply` and `check` then report `ruleset: skipped (<GitHub's
reason>)` and carry on (`apply` exits `0`, and a skipped ruleset is not a
difference for `check`). The `production` environment's calls are answered the
same way on such a plan and are reported the same way,
`environment 'production': skipped (<GitHub's reason>)`, noted once by `check`.

### The production environment

`--variant product` gives the repository a `production` environment. GitHub
creates an environment with custom branch policies starting empty, so
`apply` PUTs the environment with `custom_branch_policies` on, only when it is
missing or those flags differ (an existing matching environment is reported
`unchanged` and never PUT again, since a full-replace PUT would clear its
reviewers and wait timers), then adds the
branch policy `main` and removes every other, a `*` added by hand included,
so that only a run on `main` can read the environment's secrets. `check`
reports an environment that is missing, or whose deployment-branch policy or
branch policies differ from `settings/github.json`'s. `standard`,
`publishes` and `block-only` create no environment. A repository connected to
production sets its production key as a secret on this environment, and the
job that uses it declares `environment: production`.

### A release that pushes with a deploy key

GitHub refuses `github-actions[bot]`, the identity a release job pushes with,
as a ruleset bypass actor, so on a `main` that requires a pull request a
release can't push its own version commit with the job's token. The shared
`release.yml` instead takes an optional input `environment` and an optional
secret `PF_RELEASE_DEPLOY_KEY`. A caller with both:

```yaml
jobs:
  release:
    uses: andrew-organization/productforge-config/.github/workflows/release.yml@vX.Y.Z
    permissions: {contents: write, issues: write, pull-requests: write}
    with:
      environment: release
    secrets:
      PF_RELEASE_DEPLOY_KEY: ${{ secrets.PF_RELEASE_DEPLOY_KEY }}
```

has its job declare that environment and its checkout use the key, so every
push of the release goes over SSH. The caller passes the secret itself: the
called workflow doesn't read it otherwise. A caller with neither releases
exactly as before, with the job's token.

The repository holds a write deploy key, the key's private half as the secret
`PF_RELEASE_DEPLOY_KEY` of an environment whose only deployment branch is
`main` (so only a run on `main` reads it), and `productforge-config github
apply --variant publishes --bypass-deploy-keys` names the deploy keys as a
bypass actor. A push with the key then lands on `main` while the job's token is
still refused. A push with a deploy key, unlike one with the job's token,
starts workflows, so the version commit keeps its `chore(release):` type and
`[skip ci]`.

### What the organisation holds, and what a repository holds

The organisation holds, at its own level:

- the secret `PF_READ_TOKEN`, with visibility `all`, so every repository,
  including one created later, reads it;
- the variable `PF_RUNS_ON`, once the runner exists;
- the custom property `product`, which groups the repositories: `productforge`
  for ProductForge's own, `workspace` for the workspace's shared projects and
  the deployment's host repositories, a product's name (such as `the-stack`)
  for that product's repositories, and `personal` for the owner's own. `apply`
  has no guard against a `personal` repository, which keeps no rule: the
  callers never apply one, and its value is set once, by hand.

A repository holds only its `production` environment's secrets, because
no repository holds a secret named like an organisation secret: it would
shadow the organisation's, and a rotated value would stay stale there.

### Checking

```sh
uvx --from git+https://github.com/andrew-organization/productforge-config@v1.0.0 \
  productforge-config github check --repo andrew-organization/some-repo \
  [--variant NAME] [--product NAME] [--required-check NAME]...
```

Prints one line for each setting whose live value differs from
`settings/github.json`'s variant (or nothing, when the repository already matches).
Exits `1` if anything differs, `0` if nothing does. Entirely read-only —
`check` never calls a `gh api` write.

### Applying

```sh
uvx --from git+https://github.com/andrew-organization/productforge-config@v1.0.0 \
  productforge-config github apply --repo andrew-organization/some-repo \
  [--variant NAME] [--product NAME] [--required-check NAME]...
```

Applies `settings/github.json`'s variant to the named repository: `gh api -X PATCH
repos/<repo>` for the repository fields, `gh api -X PUT
repos/<repo>/actions/permissions/workflow` for the Actions default
workflow permissions, `gh api -X PATCH repos/<repo>/properties/values`
for `--product`, and the variant's rulesets, each created if no ruleset of
that name exists yet or updated in place by name if one does, on a
repository of any visibility, and the variant's environments (`gh api -X PUT
repos/<repo>/environments/<name>` only when it is missing or its deployment-branch
policy flags differ, then its deployment-branch policies added or deleted to
equal the file's). Idempotent: each part is read first and written only when it
differs, and reported `unchanged` otherwise, so running it again once a
repository already matches sends no write at all.

**Applying needs repository admin rights** on the target — the same
rights `gh` itself needs to change these settings — and changes a real
repository's configuration immediately, with no further confirmation.
Review the proposed values above (and `settings/github.json` itself)
before running it against any repository.

Both commands need `gh` installed and authenticated (`gh auth status`)
with access to the target repository; neither talks to the GitHub API
directly.

## Checking the month's Actions usage

`productforge-config usage --org ORG [--allowance MINUTES] [--from-day DAY]`
reads `organizations/ORG/settings/billing/usage/summary` through `gh api` (the
caller's own `gh` login, which needs the organisation's billing read access),
adds up the current month's Actions items measured in minutes (the month is
named explicitly in the request, in UTC), projects the month evenly from the
time elapsed since it began (fractional days, so part-way through a day is not
counted as a whole one) and prints one line:

```text
Actions: 2161 of 3000 minutes used, day 14 of 31, projected 4785
```

It exits 1 when the projected total reaches the allowance (`--allowance`,
default 3000) and the day of the month is at or after `--from-day` (default
3, since the first days of a month project too wildly to trust); otherwise 0.
A month with no Actions minutes yet prints `no usage yet` and exits 0. A
failing `gh` call exits 2. GitHub's budget stops Actions at the allowance and
warns nobody before it, so this is the early signal; run it daily to be told
in time.

## The supervisor for an ephemeral self-hosted runner

`supervisor/runner_supervisor.py` runs a GitHub Actions job on a runner that
exists for that one job. It is not part of the package and no kit installs it:
it is copied to a host by hand, as below. It needs Python 3.10 or later and
Docker, and nothing else.

It registers one just-in-time (JIT) runner in a runner group
(`POST orgs/ORG/actions/runners/generate-jitconfig`), starts it in a fresh
`docker run --rm` container of the official runner image, and waits for the
container to exit, which a JIT runner does after exactly one job and which
deregisters it. It then removes the container and the registration (a runner
that never got a job stays registered, offline, so it deletes it) and starts
the next. A failed job, a registration call that fails and a stop mid-job each
end the same way: container removed, registration deleted (see "State and
stopping" for a registration GitHub still holds as busy). A job's files live
in the container and go with it, so the next job finds nothing the last one
wrote.

**How it learns a job is queued: it doesn't ask.** One runner is always
registered and idle; GitHub gives the next queued job for its labels to it,
and its container ending is the signal to register the next. Polling for
queued jobs instead would need read access to every repository's Actions on
top of the right to register runners; this needs only the second, and a
queued job waits until a runner exists, so there is never a queued job without
one. An idle runner holds no job and no data.

### What the host needs

- A container runtime: `docker`, with its user able to run containers. While
  `docker` is missing or its daemon is down the supervisor registers nothing: it
  checks `docker info` before every registration and, failing, retries with a
  backoff (doubling from `--retry-seconds`, to five minutes) rather than minting
  a registration per attempt.
- **Containers must not read the EC2 instance metadata service**
  (`169.254.169.254`), whose role credentials a job could otherwise read. On an
  EC2 host set IMDSv2 required and the hop limit to 1: every read then needs a
  session token, and the token's answer never reaches a container on the docker
  bridge, one hop further than the host, so a container can ask but read nothing
  (a request without a token answers 401):

  ```sh
  aws ec2 modify-instance-metadata-options --instance-id i-0123456789abcdef0 \
    --http-tokens required --http-put-response-hop-limit 1 --http-endpoint enabled
  ```

  and attach no instance role the host does not need (ideally none), because
  whatever the role grants is one misconfiguration from a job. At start the
  supervisor proves it: it runs a short check container, confined as a job's is,
  that asks `169.254.169.254` for a session token and for metadata without one,
  and refuses to start (exit 1, one line) if either answers 200, or if it cannot
  run the check. Pass
  `--allow-metadata-reachable` to skip the check, only for a local test on a
  machine that is not an EC2 instance.
- The runner image, pinned by digest: the default `--image` is
  `ghcr.io/actions/actions-runner@sha256:9644537b29b4799cb752a2f23a45a1e603cf74c3c5998241a1533f0e8c3dc649`
  (the base of the runner image below), the arm64 build of `:latest` on 2026-10-15, pulled on first use. A digest does
  not move under the host, so a changed runner is a decision. To move it: read
  the new digest with
  `docker buildx imagetools inspect ghcr.io/actions/actions-runner:latest`
  (use the manifest for `linux/arm64`, or the index digest at the top for any
  architecture), add `--image ghcr.io/actions/actions-runner@sha256:NEW` to the
  unit's `ExecStart`, and `systemctl daemon-reload && systemctl restart
  productforge-runner-supervisor`. GitHub stops accepting a runner version
  some weeks after a newer one ships, so move it at least that often.
- Outbound HTTPS to `api.github.com`, `github.com`, `ghcr.io` and the
  endpoints the runner itself uses. No inbound port.
- A token that can register runners and nothing else: a fine-grained personal
  access token whose resource owner is the organisation, with **no
  repository access** and one organisation permission, **Self-hosted
  runners: Read and write** (`organization_self_hosted_runners`). It reads no
  repository, workflow, secret or setting, and the supervisor never gets more
  than that. A personal access token is created in Settings, Developer
  settings, Fine-grained personal access tokens; no API call creates one. The
  same permission as a GitHub App's token is the call
  `POST /app/installations/INSTALLATION_ID/access_tokens` with
  `{"permissions": {"organization_self_hosted_runners": "write"}}`, an hour's
  token that something on the host has to renew; the supervisor re-reads its
  token file on every call, so a renewed file takes effect without a restart.
  The runner group is created once by an owner, with `visibility` `selected`
  and `allows_public_repositories` false, and its repositories set. After
  creating or changing it, run a job and check it dispatches: a group's first
  runners have sat idle until its repositories were set again.
- **The host is never the production server.** A job builds an agent's code
  and so can run anything; the host holds no production credential, and the
  only secret on it is the registration token.

### How a job container is confined

Each job container starts with `--cap-drop ALL` (no Linux capability),
`--security-opt no-new-privileges` (no setuid escalation), `--pids-limit`
(`--pids-limit`, default 512) and `--memory` (`--memory`, default `4g`). The
official image's runner is a non-root user and needs none of the capabilities,
so a job runs as it did, and the start-up check runs under the same options.
What the options take away from a job is `sudo`: a step that runs `sudo` (for
example `sudo apt-get install`) fails, so install tools without it or bake them
into a job's own container image. A job that needs more memory or processes
raises the two limits in the unit's `ExecStart`.

With `--runtime sysbox-runc` (the runner image below) the container starts under
[Sysbox](https://github.com/nestybox/sysbox) instead, and `--cap-drop ALL` and
`no-new-privileges` are not passed. Sysbox runs the container in a Linux user
namespace, so its root and its capabilities are an unprivileged user's on the
host, which is what confines it: the Docker engine inside needs those
namespaced capabilities to start, and a job's `sudo` works inside the container
and nowhere else. The process and memory limits stay, and so does the metadata
check, which runs under the same runtime and still refuses to start on a 200.
Any other `--runtime` keeps the capability drop and `no-new-privileges`.

### The runner image and Sysbox

`supervisor/runner-image/` builds the image a job runs in: the official runner
image, pinned by the digest above, plus the tools the kits' CI uses (`make`,
Python 3 with `venv` and `pip`, `build-essential`, `git`, `curl`, `unzip`,
`xz-utils`, `zip`, `jq`, `gh`, and Docker's engine, Buildx and Compose plugin).
Its entrypoint starts `dockerd` inside the container, as root, then runs the
runner as the `runner` user, who is in the `docker` group. Under Sysbox that
engine is the job's own: its images and containers live in the job's container
and go with it, and it cannot reach the host's Docker. Moving the base digest means
changing it in `DEFAULT_IMAGE` and in the Dockerfile's `BASE_IMAGE`; a test keeps
the two equal.

The image also carries Flutter, cloned from Flutter's git repository at
`FLUTTER_VERSION` (a Dockerfile build argument) with its web artefacts
fetched, because `flutter-action` finds no Linux arm64 SDK in Flutter's release
manifest. **The image's Flutter must equal the version in the repositories'
`.fvmrc`**, which is `settings/fvmrc` in this repository; a test keeps the
Dockerfile's `FLUTTER_VERSION` equal to it. `actions/setup` with `flutter: true`
skips `flutter-action` when the `flutter` on `PATH` already reports the version
`.fvmrc` pins, and otherwise sets Flutter up as before, so hosted runners are
unchanged. When `settings/fvmrc` moves, change `FLUTTER_VERSION` in the same
release and rebuild the image as below; until it is rebuilt, a job on the runner
finds a different Flutter, falls back to `flutter-action`, and fails on arm64.

**What a rebuild reproduces.** The same pinned base image (`BASE_IMAGE`), the same
Flutter commit (`FLUTTER_COMMIT`, checked against the clone), the same taplo
binary (`TAPLO_SHA256`) and the same Docker and GitHub CLI apt signing keys
(checked against their published fingerprints); the build fails if any differs.
The apt packages are not pinned: a rebuild installs their versions at the time
it runs. Changing `FLUTTER_VERSION` or `TAPLO_VERSION` means changing
`FLUTTER_COMMIT` or `TAPLO_SHA256` with it.

On the host, install Sysbox CE from its release (Ubuntu 24.04, arm64 shown;
check the
[releases](https://github.com/nestybox/sysbox/releases) for the newest):

```sh
curl -fsSLO https://github.com/nestybox/sysbox/releases/download/v0.7.1/sysbox-ce_0.7.1.linux_arm64.deb
systemctl stop productforge-runner-supervisor   # the installer restarts Docker
apt-get install -y ./sysbox-ce_0.7.1.linux_arm64.deb
docker info | grep -i runtimes                  # lists sysbox-runc
```

**Build and update the image.** Run it from the release you are moving to,
on the host, whenever the runner version GitHub accepts moves (a new digest
in `DEFAULT_IMAGE`, see above) or a tool needs updating (the build installs
the packages' current versions):

```sh
VERSION=v2.8.0   # a productforge-config release tag
curl -fsSL https://codeload.github.com/andrew-organization/productforge-config/tar.gz/refs/tags/$VERSION |
  tar -xz --strip-components=3 --one-top-level=/tmp/runner-image-$VERSION \
    productforge-config-${VERSION#v}/supervisor/runner-image
/tmp/runner-image-$VERSION/build.sh $VERSION    # tags productforge-runner:$VERSION
```

Then point the unit at it and restart, keeping its other options:

```sh
# in ExecStart: --image productforge-runner:v2.8.0 --runtime sysbox-runc
systemctl daemon-reload && systemctl restart productforge-runner-supervisor
journalctl -u productforge-runner-supervisor -n 20   # the metadata check passed; a runner registered
```

The image is built on the host, never pulled: a `--image` with no registry is a
local tag. Remove the previous tag when the new one runs
(`docker image rm productforge-runner:OLD`). Keep the server's CPU credits on
`standard`, so it never bills for bursting.

### Install as a service

On the arm64 Linux host (systemd), as root:

```sh
useradd --system --create-home --groups docker pf-runner
install -d /opt/productforge-runner /etc/productforge-runner
install -m 0644 supervisor/runner_supervisor.py /opt/productforge-runner/
install -m 0644 supervisor/productforge-runner-supervisor.service /etc/systemd/system/
install -m 0640 -o root -g pf-runner /dev/stdin /etc/productforge-runner/token   # paste the token, then Ctrl-D
# edit --org, --group and --labels in the unit, then:
systemctl daemon-reload && systemctl enable --now productforge-runner-supervisor
```

Membership of the `docker` group is root on the host, which is why the host
runs nothing else. The labels you pass are what a job's `runs-on` names, next
to `self-hosted`.

### State and stopping

```sh
journalctl -u productforge-runner-supervisor -f       # what it is doing
python3 /opt/productforge-runner/runner_supervisor.py status \
  --org YOUR-ORG --group YOUR-RUNNER-GROUP --token-file /etc/productforge-runner/token
systemctl stop productforge-runner-supervisor         # removes the container, then the registration
```

Stopping removes the running container, then deletes its registration. GitHub
can still hold a runner as busy for a moment after its container is gone and
answers 422 to the delete; the supervisor retries that with a backoff (five
attempts, about 45 seconds, never fatal) and, if the runner is still busy, logs
that it left it and carries on. A registration already gone (404) is fine. The
next start removes any it left. The unit allows two minutes to stop.

`status` lists the runners registered under the supervisor's name prefix
(`pf-`, `--prefix`) and its containers. Stop it before switching jobs back to
hosted runners on purpose; if it is killed rather than stopped, the next start
removes the registrations and containers it left. Run one supervisor per
prefix and group.

## Starting the runner's server on demand, and stopping it when idle

The runner's server is an EC2 instance that is stopped unless CI needs it. Two parts keep it that way, both in
`supervisor/`.

### The starter: a Lambda function behind a webhook

`supervisor/starter/handler.py` is an AWS Lambda function (Python 3.12, standard library plus the boto3 Lambda
provides) behind a function URL. A GitHub organisation webhook for `workflow_job` events calls it. It:

- checks GitHub's `X-Hub-Signature-256` HMAC against the webhook's secret, comparing in constant time, and
  answers `401` when it is wrong or missing, doing nothing else;
- answers `200` to a `ping`, and to every other event, action or job it does not act on;
- for a `queued` job whose labels include every label in `RUNNER_LABELS` (default `self-hosted,pf`), asks EC2
  for the instance's state and calls `ec2:StartInstances` only when it is `stopped`. A running or pending
  instance is left alone, so a burst of queued jobs starts it once;
- answers `500` when AWS refuses or fails, so GitHub shows the delivery as failed and it can be redelivered.

A job that queues while the instance is `stopping` (the minute after an idle stop) finds nothing to start, and
waits until the next job queues or its delivery is redelivered from GitHub's webhook page.

`supervisor/starter/iam-policy.json` is the function's role policy: `ec2:StartInstances` on that one instance's
ARN, `ec2:DescribeInstances` (EC2 cannot scope it to a resource) and writing logs to the function's own log
group. `trust-policy.json` lets `lambda.amazonaws.com` assume the role. A call to start any other instance with
that role is refused.

**Set it up** (the account's owner, with an administrator profile):

```sh
umask 077 && openssl rand -hex 32 >webhook-secret   # keep it; the webhook needs the same value
supervisor/starter/create-starter.sh --instance-id i-... --region eu-west-1 \
  --secret-file webhook-secret --profile YOUR-ADMIN-PROFILE
```

It creates, saying what and its cost as it goes: the role `pf-runner-starter-role` with the policy above; the
log group `/aws/lambda/pf-runner-starter`, kept 14 days; the function `pf-runner-starter` (arm64, 128 MB, 10
seconds) with the secret, instance id and labels as environment variables; and a function URL with auth type
`NONE`, because GitHub cannot sign AWS requests and the function verifies GitHub's signature instead. It prints
the URL. Running it again updates the function and its settings instead of duplicating anything; `--function-name`
and `--labels` change the defaults. The secret is read from the file and sent to AWS from a private temporary
file; it is never printed.

**Cost**: Lambda's free tier covers 1 million calls and 400,000 GB-seconds a month, and this function runs for a
fraction of a second a few times a day, so it is $0. The role, the function URL and the log group (5 GB of
ingestion free) cost nothing at this volume. What costs money is the instance while it runs and its disk while it
is stopped.

**The webhook**, in GitHub under the organisation's Settings, Webhooks, Add webhook:

- Payload URL: the URL the script printed.
- Content type: `application/json`.
- Secret: the contents of the secret file.
- Events: *Let me select individual events*, then only *Workflow jobs*.
- Active: on. GitHub sends a `ping`, which shows as a green tick under *Recent deliveries* when the signature and
  URL are right.

**Turning the starting off**: untick *Active* on the webhook (or delete it). No job then starts the instance; one
that is stopped stays stopped, and CI jobs for the runner's labels queue until it is started by hand (`aws ec2
start-instances`). Turning it back on starts it with the next queued job.

### The idle stop: a root timer on the server

The instance's shutdown behaviour is `stop`, so `shutdown -h now` on it stops the instance. The supervisor runs as
`pf-runner`, in the `docker` group, and is not given the right to stop the host. Instead it records its activity
in `/var/lib/productforge-runner/activity` (`--state-file`): `busy <epoch>`, refreshed every 10 seconds
(`--activity-poll-seconds`) while GitHub reports its runner running a job, and `idle <epoch>` from the moment its
last job ended or it started. A job the supervisor cannot ask GitHub about counts as busy.

`supervisor/idle_stop.py`, run every minute by the root timer `productforge-runner-idle-stop.timer`, reads that
file and runs `shutdown -h now` once the host has been idle for `--idle-stop-minutes` (default 15). It never stops
the host while the file says `busy` and is fresh, so it never stops a host a job container is running on, and a
job that ends restarts the quiet period. A `busy` record older than 5 minutes (a supervisor that died) counts as
idle from its last refresh; with no readable file the host's boot time stands in for the last activity. `0`
disables the stop. The only things `pf-runner` can influence are that file's two words and its number, not
anything the root timer runs.

Install it with the supervisor, as root:

```sh
install -m 0644 supervisor/idle_stop.py /opt/productforge-runner/
install -m 0644 supervisor/productforge-runner-idle-stop.service supervisor/productforge-runner-idle-stop.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now productforge-runner-idle-stop.timer
```

Change the quiet period by editing `--idle-stop-minutes` in `productforge-runner-idle-stop.service` and running
`systemctl daemon-reload`; to turn the idle stop off, set it to `0`, or `systemctl disable --now
productforge-runner-idle-stop.timer`. The supervisor's unit already enables itself at boot, so it starts with
the instance. The first job after a stop waits one to two minutes for the start; one while it is up starts within
seconds. The journal (`journalctl -u productforge-runner-idle-stop`) says when it stopped the host and after how
long.

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
