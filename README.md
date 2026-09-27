# productforge-config

Public; holds nothing secret, so every repository's CI reads it without a
token. What every ProductForge repository has in common: its shared
pre-commit hooks, its CI setup, and its lint and format settings. Released
by tag (`vMAJOR.MINOR.PATCH`, with a moving `vMAJOR` tag); a repository is
brought up to a release by one command, run in it.

## What it holds

- `.pre-commit-hooks.yaml` — the lint hooks every repository shares
  (`trailing-whitespace`, `end-of-file-fixer`, `markdownlint`, `pyupgrade`,
  `isort`, `black`, `flake8`), each pinned to its tool's own version.
- `actions/setup/action.yml` — the CI setup every repository repeats: uv
  with Python 3.14 and its cache, the pre-commit cache keyed on the
  caller's own hook files, and `make install` — with Flutter and Postgres
  as options (`flutter: true`, `postgres: true`).
- `settings/markdownlint.jsonc`, `settings/python.toml` — the markdownlint
  rules and the black, isort and flake8 settings every repository keeps a
  real local copy of, so editors read them.
- `src/productforge_config/` — the `productforge-config update` command
  that brings a repository up to a release.

## Bringing a repository up to a release

Run in the repository, typically through its own `make update-config`:

```sh
uvx --from git+https://github.com/andrew-organization/productforge-config@v1.0.0 \
  productforge-config update --version v1.0.0
```

It moves the productforge-config hook source's `rev` in
`.pre-commit-lint.yaml` and the
`andrew-organization/productforge-config/actions/setup@...` ref in every
`.github/workflows/*.yml` file to that version, and rewrites the shared
keys this release carries into the repository's own local copies
(`.markdownlint-cli2.jsonc`'s `"config"`; `pyproject.toml`'s
`[tool.black]`/`[tool.isort]`; `setup.cfg`'s `[flake8]`) — leaving a
repository's own hooks, ignored paths and excluded paths exactly as they
were. Commit the result and raise it as an ordinary pull request; nothing
here opens that pull request for you.

## A repository's own side

```yaml
# .pre-commit-lint.yaml
repos:
  - repo: https://github.com/andrew-organization/productforge-config
    rev: v1.0.0
    hooks: [{id: trailing-whitespace}, {id: end-of-file-fixer}, {id: markdownlint},
            {id: pyupgrade}, {id: isort}, {id: black}, {id: flake8}]   # the hooks it needs
  # then the repository's own hooks, unchanged
```

```yaml
# .github/workflows/ci.yml, after actions/checkout
- uses: andrew-organization/productforge-config/actions/setup@v1.0.0
  with:
    flutter: "true"   # reads the repository's own .fvmrc
    postgres: "true"  # starts postgres:18-alpine as a background container
```

```makefile
# Makefile
update-config:
	uvx --from git+https://github.com/andrew-organization/productforge-config@$(VERSION) \
	  productforge-config update --version $(VERSION)
```

## Developing this repository

`make install`, `make lint`, `make test` — the same three targets every
ProductForge repository has. `make test` runs the update command's own
test suite against `tests/fixture_repo`, entirely against throwaway
copies, plus a smoke test of the published hooks themselves.
