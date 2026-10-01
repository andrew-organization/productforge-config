# What every ProductForge repository's Makefile shares: installing, linting, the git hook, and
# bringing the repository up to a productforge-config release. A repository's Makefile includes
# productforge.env, then every .productforge/*.mk (the kits it takes each write their own).

# The kits the repository takes, from productforge.env: quotes a dotenv file allows are dropped.
PF_KITS := $(strip $(subst ",,$(subst ',,$(PF_KITS))))

# A bare `make` runs the repository's own `all`.
.DEFAULT_GOAL := all

# pre-commit runs through uv, taken from pyproject.toml's dev dependencies.
PRE_COMMIT := uv run pre-commit

# The hooks the kits taken carry, and the repository's own hooks when it has any.
PF_LINT_CONFIG := .productforge/pre-commit.yaml
PF_OWN_LINT_CONFIG := .pre-commit-lint.yaml

PF_CONFIG_URL := https://github.com/andrew-organization/productforge-config

.PHONY: install lint setup-hooks clean update-config check-config

# ─── Dependencies ───────────────────────────────────────────────────────────

## Install every dependency, the lint hook environments, and the git hooks
install:
	uv sync --dev
	$(PRE_COMMIT) install-hooks --config $(PF_LINT_CONFIG)
	$(if $(wildcard $(PF_OWN_LINT_CONFIG)),$(PRE_COMMIT) install-hooks --config $(PF_OWN_LINT_CONFIG))
	$(MAKE) setup-hooks

# ─── Code quality ───────────────────────────────────────────────────────────

## Lint and format every file in the repository, auto-fixing where it can: the hooks of the kits
## taken (.productforge/pre-commit.yaml), then the repository's own (.pre-commit-lint.yaml, when
## there is one). Both always run, and it fails when either does. The git hook and CI both run this target.
lint:
	@status=0; \
	$(PRE_COMMIT) run --all-files --config $(PF_LINT_CONFIG) || status=1; \
	$(if $(wildcard $(PF_OWN_LINT_CONFIG)),$(PRE_COMMIT) run --all-files --config $(PF_OWN_LINT_CONFIG) || status=1;) \
	exit $$status

# ─── Housekeeping ───────────────────────────────────────────────────────────

## Install the pre-commit git hook into .git/hooks
setup-hooks:
	$(PRE_COMMIT) install

## Remove caches and build artefacts
clean:
	rm -rf .pytest_cache .mypy_cache

# ─── productforge-config ────────────────────────────────────────────────────

# The newest stable release tag, or the newest -rc.N when no stable release exists yet; looked up only when a recipe reads it.
VERSION ?= $(shell tags=$$(git ls-remote --tags --refs $(PF_CONFIG_URL) 2>/dev/null | sed 's,.*refs/tags/,,'); v=$$(echo "$$tags" | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$$' | sort -V | tail -n1); [ -n "$$v" ] || v=$$(echo "$$tags" | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+$$' | sort -V | tail -n1); echo "$$v")

## Bring this repository up to a productforge-config release: its kits, hooks, CI and shared settings.
## Usage: make update-config [VERSION=<tag or commit SHA>]
update-config:
	@test -n "$(VERSION)" || { echo "update-config: no productforge-config release tag found" >&2; exit 1; }
	uvx --from git+$(PF_CONFIG_URL)@$(VERSION) productforge-config update --version $(VERSION)

## Fail when a file productforge-config writes has changed by hand, or a release left one unwritten,
## at the release this repository was last brought to (.productforge/release).
check-config:
	@release=$$(sed -n '1p' .productforge/release); test -n "$$release" || { echo "check-config: no release recorded in .productforge/release" >&2; exit 1; }; \
	uvx --from git+$(PF_CONFIG_URL)@$$release productforge-config update --check --version $$release
