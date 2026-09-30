# Build, run and CI

This repository takes its build, run, test and CI configuration from
productforge-config, installed into `.productforge/` by `make update-config`,
and supplies only its own values, in `productforge.env` at its root:

- `PF_KITS` — the kits it takes: `python django-api` for an API, `flutter-web`
  for a web app.
- `PF_SLOT` — its port slot, shared with the other repository of the product.
- `PF_NAME` — its name: the Compose project name, image prefix, database name,
  and the Django project package or the Dart package.
- `PF_DJANGO_PROJECT` and `PF_POSTGRES_DB` — optional, each defaulting to
  `PF_NAME`.

Change nothing in `.productforge/`: `make update-config` writes it whole, so a
change there is lost on the next update. To change what every repository
shares, change productforge-config. The Makefile is thin: it includes
`productforge.env` and every `.productforge/*.mk`, and keeps its own targets.
Each target's options are documented above it in `.productforge/*.mk`.

## Ports

A slot owns 20 local ports from 6100 + 20 × slot. The API and the web app of a
product share a slot, so each derives the other's port. Find this
repository's own ports with `make ports`, never from a number written down
elsewhere; the offsets below are what stays fixed.

| Offset | Service |
| --- | --- |
| +0 | API |
| +1 | Postgres |
| +2 | Redis |
| +3 | Flower |
| +4 | Mailpit (SMTP) |
| +5 | Mailpit (web UI) |
| +6 | Web dev server, and a served build |
| +11 | Postgres for the integration tests |

Offsets +7 to +10 and +12 to +19 are reserved. A slot whose block holds a port
Chromium refuses to connect to (6665-6669, for one) is refused.

`make up mode=mobile` (on both repositories) lets a phone on the same network
reach the product: the web app is built for this machine's LAN IP, and the API
allows that origin.

## CI

`.github/workflows/ci.yml` calls productforge-config's reusable workflow for
this kind (`ci-api.yml` or `ci-web.yml`) on every pull request. It reads
`productforge.env` and runs the same make targets a developer runs, so a
failure in CI reproduces locally with the same command.
