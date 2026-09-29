
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

## Local overrides (`.env`)

A `.env` at the repository's root (gitignored, never committed) is read after
`productforge.env`, for values that are one developer's own. Every one is
optional; without them the API allows `localhost` only.

- `DJANGO_ALLOWED_HOSTS` — extra Django `ALLOWED_HOSTS` entries, for example
  this machine's LAN IP, to reach the API from a phone on the same network.
- `CORS_EXTRA_ORIGINS` — extra allowed CORS origins, comma-separated; the web
  app's own origin on that LAN IP, for the same reason.
- `FRONTEND_BASE_URL` — where a link in a sent email (verification, invite)
  points. Defaults to `http://localhost:<web port>` from the slot.

`make up mode=mobile` sets all three for one run from this machine's LAN IP.
