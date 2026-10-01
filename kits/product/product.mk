# What a product's repositories (an API or a web app) share: the values they supply in
# productforge.env, validated, and the ports their slot fixes. Written for the django-api and
# flutter-web kits, whichever is taken.

ifeq ($(strip $(PF_NAME)),)
$(error PF_NAME is not set in productforge.env)
endif
ifeq ($(strip $(PF_SLOT)),)
$(error PF_SLOT is not set in productforge.env)
endif

# A dotenv file's own values may carry stray whitespace and quotes; make keeps both.
PF_NAME := $(strip $(subst ",,$(subst ',,$(PF_NAME))))
PF_SLOT := $(strip $(subst ",,$(subst ',,$(PF_SLOT))))
PF_DJANGO_PROJECT := $(strip $(subst ",,$(subst ',,$(PF_DJANGO_PROJECT))))
PF_POSTGRES_DB := $(strip $(subst ",,$(subst ',,$(PF_POSTGRES_DB))))
PF_DJANGO_PROJECT := $(if $(PF_DJANGO_PROJECT),$(PF_DJANGO_PROJECT),$(PF_NAME))
PF_POSTGRES_DB := $(if $(PF_POSTGRES_DB),$(PF_POSTGRES_DB),$(PF_NAME))

# A name is a valid Compose project name, database, Python package and Dart package at once.
PF_NAME_ERROR := $(shell echo "$(strip $(PF_NAME))" | grep -Eq '^[a-z][a-z0-9_]{0,57}$$' || echo "PF_NAME must be lower-case letters, digits and underscores, starting with a letter, at most 58 characters, not '$(PF_NAME)'")
ifneq ($(PF_NAME_ERROR),)
$(error $(PF_NAME_ERROR))
endif

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

export PF_NAME PF_SLOT PF_DJANGO_PROJECT PF_POSTGRES_DB
export PF_PORT_API PF_PORT_POSTGRES PF_PORT_REDIS PF_PORT_FLOWER
export PF_PORT_MAILPIT_SMTP PF_PORT_MAILPIT_UI PF_PORT_WEB PF_PORT_TEST_POSTGRES

# This machine's own LAN IP, from the interface that carries its default route
# (macOS), or its first address (Linux). Only consulted for mode=mobile.
LAN_IP = $(shell ipconfig getifaddr $$(route -n get default 2>/dev/null | awk '/interface:/{print $$2}') 2>/dev/null || hostname -I 2>/dev/null | awk '{print $$1}')

.PHONY: ports

## Show the ports this product's slot owns
ports:
	@printf '%s\n' "slot $(PF_SLOT): $(PF_BASE_PORT)-$(lastword $(PF_BLOCK_PORTS))" \
		"$(PF_PORT_API)  the API" "$(PF_PORT_POSTGRES)  Postgres" "$(PF_PORT_REDIS)  Redis" \
		"$(PF_PORT_FLOWER)  Flower" "$(PF_PORT_MAILPIT_SMTP)  Mailpit (SMTP)" \
		"$(PF_PORT_MAILPIT_UI)  Mailpit (web UI)" "$(PF_PORT_WEB)  the web dev server, and a served build" \
		"$(PF_PORT_TEST_POSTGRES)  Postgres for the integration tests"
