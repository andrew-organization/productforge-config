
## API targets

Run these from the repository root; the Docker ones use `docker compose` (v2)
with `.productforge/compose.yml`, plus this repository's own
`docker-compose.local.yml` when it has one, and a `.env` for a developer's own
overrides.

- `make install` — sync dependencies into the local uv venv, install the lint
  hook environments and the git hooks
- `make test [path=… k=…]` — unit tests, no database
- `make test-integration [path=… k=…]` — database tests against a disposable
  Postgres from `.productforge/compose.test.yml`, removed afterwards
- `make test-integration-ci` — the same tests, leaving that Postgres running
  for the CI runner to discard
- `make check-migrations` — fails if a model change has no migration
- `make lint` — every lint and format hook on every file
- `make build`, `up`, `down`, `logs`, `shell` — the local Docker Compose project
- `make lock` — regenerate `uv.lock`

The Django project package is `PF_DJANGO_PROJECT` (`api/<name>/`), and its
settings module `<name>.settings.base`. The Compose project gives Django `WEB_ORIGIN`
(the web app's origin on this machine) and `FRONTEND_BASE_URL`, both from the
slot; settings should allow `WEB_ORIGIN` for CORS. The dev database is
`PF_POSTGRES_DB`, kept in the Compose project's own volume: `docker compose down -v`
removes it.
