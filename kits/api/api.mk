# The rest of an API repository's Makefile: testing, migrations and the local
# Docker Compose project. Included after common.mk, which supplies the ports, names and
# the targets every repository shares.

# Speed up builds.
export COMPOSE_DOCKER_CLI_BUILD=1
export DOCKER_BUILDKIT=1

# The compose file, from the kit, and this repository's own overlay when it has one. Compose
# reads productforge.env for the values common.mk works out, and .env, when present, for a
# developer's own overrides. --project-directory keeps every relative path relative to this
# repository's root, not to the kit's directory.
PF_ENV_FILES = $(strip --env-file productforge.env $(if $(wildcard .env),--env-file .env))
PF_COMPOSE = $(strip docker compose $(PF_ENV_FILES) -f .productforge/compose.yml $(if $(wildcard docker-compose.local.yml),-f docker-compose.local.yml) --project-directory .)
PF_COMPOSE_TEST = $(strip docker compose $(PF_ENV_FILES) -f .productforge/compose.test.yml --project-directory .)

# The integration tests' own database, from compose.test.yml.
PF_TEST_DB_ENV = POSTGRES_HOST=localhost POSTGRES_PORT=$(PF_PORT_TEST_POSTGRES) POSTGRES_DB=$(PF_POSTGRES_DB)_test

.PHONY: all lock test test-integration test-integration-ci check-migrations build up down logs shell clean-api
clean: clean-api

# ─── Default ────────────────────────────────────────────────────────────────

## Rebuild and restart all containers
all: down build up

# ─── Dependencies ───────────────────────────────────────────────────────────

## Regenerate uv.lock after changing pyproject.toml
lock:
	uv lock

# ─── Testing ────────────────────────────────────────────────────────────────

## Run unit tests (no DB). Usage: make test [path=<path>] [k=<keyword>]
## path (optional): repo-relative path to a test file or directory.
## k (optional): pytest -k expression to match test names or keywords.
test:
	uv run pytest -n auto $(if $(path),$(path),api/tests) -v -m "not django_db" $(if $(k),-k "$(k)")

## Run DB integration tests against a disposable Postgres, and remove it after. Usage: make test-integration [path=<path>] [k=<keyword>]
## Uses its own Postgres container on this slot's test port, so it can run alongside the app.
test-integration:
	$(PF_COMPOSE_TEST) up -d --wait --remove-orphans postgres-test
	$(PF_TEST_DB_ENV) uv run pytest -n auto $(if $(path),$(path),api/tests) -m django_db -v $(if $(k),-k "$(k)"); \
		status=$$?; $(PF_COMPOSE_TEST) down --remove-orphans; exit $$status

## Run DB integration tests against the test Postgres, started here and left running for the runner to discard (used in CI).
test-integration-ci:
	$(PF_COMPOSE_TEST) up -d --wait --remove-orphans postgres-test
	$(PF_TEST_DB_ENV) uv run pytest -n auto api/tests -m django_db -v --create-db

## Check every model change has its migration. The git hook and CI both run this target.
check-migrations:
	cd api && $(PF_TEST_DB_ENV) uv run python manage.py makemigrations --check --dry-run --settings=$(PF_DJANGO_PROJECT).settings.test

# ─── Docker ─────────────────────────────────────────────────────────────────

## Build all Docker images
build:
	$(PF_COMPOSE) build

## Start all containers in the background. Usage: make up [mode=mobile]
## mode (optional): "laptop" (default) allows the web app on localhost only. "mobile" also allows this machine's
## LAN IP, so a phone on the same network can reach both the web app and this API: it sets
## DJANGO_ALLOWED_HOSTS, CORS_EXTRA_ORIGINS and FRONTEND_BASE_URL for the run. Start the web app
## with the same mode.
up:
	$(if $(filter mobile,$(mode)),@test -n "$(LAN_IP)" || { echo "up: no LAN IP found for mode=mobile" >&2; exit 1; })
	$(if $(filter mobile,$(mode)),DJANGO_ALLOWED_HOSTS=$(LAN_IP) CORS_EXTRA_ORIGINS=http://$(LAN_IP):$(PF_PORT_WEB) FRONTEND_BASE_URL=http://$(LAN_IP):$(PF_PORT_WEB) )$(PF_COMPOSE) up -d --remove-orphans

## Stop and remove all containers and networks
down:
	$(PF_COMPOSE) down --remove-orphans

## Tail Django container logs
logs:
	$(PF_COMPOSE) logs -f django

## Open a Django shell_plus session inside the Django container
shell:
	$(PF_COMPOSE) run --rm django ./manage.py shell

# ─── Housekeeping ───────────────────────────────────────────────────────────

## Remove compiled Python files and caches
clean-api:
	find api -type f -name "*.pyc" -delete
	find api -type d -name "__pycache__" -exec rm -rf {} +
	find api -type d -name ".mypy_cache" -exec rm -rf {} +
	find api -type d -name ".pytest_cache" -exec rm -rf {} +
