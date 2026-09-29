
## Web targets

Run these from the repository root. Every target runs through `fvm flutter`
when `fvm` is installed, and a bare `flutter` otherwise (CI installs the
version `.fvmrc` pins directly).

- `make install` — fetch pub packages, install the lint hook environments and
  the git hooks
- `make test [path=… name=…]` — unit and widget tests
- `make generate` — regenerate every model's `fromJson`/`toJson`
- `make l10n` — regenerate `AppLocalizations` from the `.arb` files
- `make identity` — regenerate `lib/config/product.dart`, and `web/index.html`
  and `web/manifest.json`'s name and short name, from `product.yaml`
- `make check-generated` — regenerate all of the above and fail on an
  uncommitted diff; the git hook and CI run it
- `make check-identity-regeneration` — prove `make identity` regenerates from
  `product.yaml`, and only from it; CI runs it
- `make lint` — every lint and format hook on every file, and `flutter analyze`
  with infos fatal
- `make build [mode=mobile]` — the production bundle in `build/web`
- `make up [mode=mobile]`, `debug`, `down` — the dev server on this slot's web
  port, talking to the API of the same slot
- `make serve-build` — serve `build/web` with a fallback to `index.html`, for
  testing a release build, including from a phone

`analysis_options.yaml` includes `.productforge/analysis_options.yaml` and adds
only what is this project's own. `GRAPHQL_ENDPOINT` (a make variable) points
the app at another API for one run.
