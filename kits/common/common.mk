# What every ProductForge repository's Makefile shares: its own values from
# productforge.env, the ports they fix, and the targets that don't depend on
# what kind of repository it is. A repository's Makefile includes
# productforge.env, then this file, then its kind's own (api.mk or web.mk).

ifeq ($(strip $(PF_KIND)),)
$(error PF_KIND is not set: include productforge.env before .productforge/common.mk)
endif
ifeq ($(strip $(PF_NAME)),)
$(error PF_NAME is not set in productforge.env)
endif
ifeq ($(strip $(PF_SLOT)),)
$(error PF_SLOT is not set in productforge.env)
endif

# A name is a valid Compose project name, database, Python package and Dart package at once.
PF_NAME_ERROR := $(shell echo "$(strip $(PF_NAME))" | grep -Eq '^[a-z][a-z0-9_]*$$' || echo "PF_NAME must be lower-case letters, digits and underscores, starting with a letter, not '$(PF_NAME)'")
ifneq ($(PF_NAME_ERROR),)
$(error $(PF_NAME_ERROR))
endif

# A bare `make` rebuilds and restarts (api), or runs everything CI runs (web): the kind's own `all`.
.DEFAULT_GOAL := all

# A dotenv file's own values may carry stray whitespace; make keeps it.
PF_KIND := $(strip $(PF_KIND))
PF_NAME := $(strip $(PF_NAME))
PF_SLOT := $(strip $(PF_SLOT))
PF_DJANGO_PROJECT := $(if $(strip $(PF_DJANGO_PROJECT)),$(strip $(PF_DJANGO_PROJECT)),$(PF_NAME))
PF_POSTGRES_DB := $(if $(strip $(PF_POSTGRES_DB)),$(strip $(PF_POSTGRES_DB)),$(PF_NAME))

# ─── Ports ──────────────────────────────────────────────────────────────────
# A slot owns the 20 ports from 6100 + 20 x slot. The offsets, and the slots
# refused, are the same as `productforge-config ports` works out.

PF_LAST_SLOT := 1332
PF_RESTRICTED_PORTS := 6566 6665 6666 6667 6668 6669 6679 6697 10080

PF_SLOT_ERROR := $(shell echo "$(PF_SLOT)" | grep -Eq '^(0|[1-9][0-9]*)$$' \
	&& { [ "$(PF_SLOT)" -le $(PF_LAST_SLOT) ] || echo "PF_SLOT $(PF_SLOT) is too large: the highest slot is $(PF_LAST_SLOT)"; } \
	|| echo "PF_SLOT must be a whole number, without leading zeros, not $(PF_SLOT)")
ifneq ($(PF_SLOT_ERROR),)
$(error $(PF_SLOT_ERROR))
endif

PF_BASE_PORT := $(shell echo $$((6100 + 20 * $(PF_SLOT))))
PF_BLOCK_PORTS := $(shell seq $(PF_BASE_PORT) $$(($(PF_BASE_PORT) + 19)))
ifneq ($(filter $(PF_RESTRICTED_PORTS),$(PF_BLOCK_PORTS)),)
$(error PF_SLOT $(PF_SLOT) owns a port Chromium refuses to connect to (the block starts at $(PF_BASE_PORT)): pick another slot)
endif

PF_PORT_API := $(PF_BASE_PORT)
PF_PORT_POSTGRES := $(word 2,$(PF_BLOCK_PORTS))
PF_PORT_REDIS := $(word 3,$(PF_BLOCK_PORTS))
PF_PORT_FLOWER := $(word 4,$(PF_BLOCK_PORTS))
PF_PORT_MAILPIT_SMTP := $(word 5,$(PF_BLOCK_PORTS))
PF_PORT_MAILPIT_UI := $(word 6,$(PF_BLOCK_PORTS))
PF_PORT_WEB := $(word 7,$(PF_BLOCK_PORTS))
PF_PORT_TEST_POSTGRES := $(word 12,$(PF_BLOCK_PORTS))

export PF_KIND PF_NAME PF_SLOT PF_DJANGO_PROJECT PF_POSTGRES_DB
export PF_PORT_API PF_PORT_POSTGRES PF_PORT_REDIS PF_PORT_FLOWER
export PF_PORT_MAILPIT_SMTP PF_PORT_MAILPIT_UI PF_PORT_WEB PF_PORT_TEST_POSTGRES

# This machine's own LAN IP, from the interface that carries its default route
# (macOS), or its first address (Linux). Only consulted for mode=mobile.
LAN_IP = $(shell ipconfig getifaddr $$(route -n get default 2>/dev/null | awk '/interface:/{print $$2}') 2>/dev/null || hostname -I 2>/dev/null | awk '{print $$1}')

# pre-commit runs through uv, taken from pyproject.toml's dev dependencies.
PRE_COMMIT := uv run pre-commit

.PHONY: install lint setup-hooks clean ports

# ─── Dependencies ───────────────────────────────────────────────────────────

## Install every dependency, the lint hook environments, and the git hooks
install:
	uv sync --dev
	$(PRE_COMMIT) install-hooks --config .pre-commit-lint.yaml
	$(MAKE) setup-hooks

# ─── Code quality ───────────────────────────────────────────────────────────

## Lint and format every file in the repository with the tools in .pre-commit-lint.yaml,
## auto-fixing where it can. The git hook and CI both run this target.
lint:
	$(PRE_COMMIT) run --all-files --config .pre-commit-lint.yaml

# ─── Housekeeping ───────────────────────────────────────────────────────────

## Install the pre-commit git hook into .git/hooks
setup-hooks:
	$(PRE_COMMIT) install

## Remove caches and build artefacts
clean:
	rm -rf .pytest_cache .mypy_cache

## Show the ports this product's slot owns
ports:
	@printf '%s\n' "slot $(PF_SLOT): $(PF_BASE_PORT)-$(lastword $(PF_BLOCK_PORTS))" \
		"$(PF_PORT_API)  the API" "$(PF_PORT_POSTGRES)  Postgres" "$(PF_PORT_REDIS)  Redis" \
		"$(PF_PORT_FLOWER)  Flower" "$(PF_PORT_MAILPIT_SMTP)  Mailpit (SMTP)" \
		"$(PF_PORT_MAILPIT_UI)  Mailpit (web UI)" "$(PF_PORT_WEB)  the web dev server, and a served build" \
		"$(PF_PORT_TEST_POSTGRES)  Postgres for the integration tests"
