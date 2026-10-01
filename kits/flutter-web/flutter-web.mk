# The rest of a web repository's Makefile: localisation, code generation, the
# product's identity, testing and the local dev server. Included with
# common.mk and product.mk, which supply the ports, names and the targets every
# repository shares.

# Use fvm's pinned Flutter/Dart SDK when fvm is available (local dev); fall
# back to a bare `flutter`/`dart` otherwise (CI, where flutter-action already
# installs the exact version pinned in .fvmrc directly onto PATH).
FLUTTER := $(shell command -v fvm >/dev/null 2>&1 && echo "fvm flutter" || echo "flutter")
DART := $(shell command -v fvm >/dev/null 2>&1 && echo "fvm dart" || echo "dart")

# The API this app talks to: this machine's own port for the API of the same slot, or, for
# mode=mobile, this machine's LAN IP, since "localhost" in a page opened on a phone is the phone.
# Pass GRAPHQL_ENDPOINT=<url> to build or run against another one.
GRAPHQL_ENDPOINT ?= http://$(if $(filter mobile,$(mode)),$(LAN_IP),localhost):$(PF_PORT_API)/graphql/
DART_DEFINES = --dart-define=GRAPHQL_ENDPOINT=$(GRAPHQL_ENDPOINT)

# Git's hook-time variables, cleared before a Flutter run: fvm's flutter wrapper runs git to find
# its own SDK, and under the git hook those variables would point it at this repository.
NO_GIT_HOOK_ENV := env -u GIT_INDEX_FILE -u GIT_DIR -u GIT_WORK_TREE -u GIT_PREFIX

.PHONY: all pub-get l10n generate identity check-identity-regeneration check-generated test build up debug down serve-build clean-web
install: pub-get
clean: clean-web

## Run everything CI runs: the generated-code check, the identity-regeneration proof, lint, and tests.
all: check-generated check-identity-regeneration lint test

# ─── Dependencies ───────────────────────────────────────────────────────────

## Fetch the pub packages
pub-get:
	$(FLUTTER) pub get

# ─── Localization ───────────────────────────────────────────────────────────

## Regenerate AppLocalizations (lib/l10n/) from assets/l10n/*.arb per l10n.yaml.
## `flutter pub get`/`flutter run` already regenerate these automatically, so this
## is only needed to pick up .arb changes without a full pub get.
l10n:
	$(FLUTTER) gen-l10n

# ─── Code generation ────────────────────────────────────────────────────────

## Regenerate every model's own fromJson/toJson (each model's sibling *.g.dart, from its
## @JsonSerializable() annotation). Run this after adding a field to, or adding, a model —
## build_runner doesn't watch for changes on its own.
generate:
	$(DART) run build_runner build --delete-conflicting-outputs

## Regenerate this product's own identity — lib/config/product.dart, web/index.html's title and
## apple-mobile-web-app-title, and web/manifest.json's name and short_name — from product.yaml,
## at the repository's root. The one file a product sets its identity in.
identity:
	python3 .productforge/generate_identity.py

## Proves "identity" regenerates from product.yaml, and only from it: changes its name and short
## name to sentinel values, reruns "identity", checks that only the three files above changed and
## that each now carries the sentinels, then restores product.yaml and those three files either way.
## Run by CI, not the git hook.
check-identity-regeneration:
	python3 .productforge/check_identity_regeneration.py

## Check every generated file is current: regenerates every model's fromJson/toJson,
## AppLocalizations, and this product's own identity, then fails if any of them changed a committed
## file. To bring a stale one up to date, run `make generate`, `make l10n` and/or `make identity`
## and commit the result. The git hook and CI both run this target.
check-generated:
	$(NO_GIT_HOOK_ENV) $(MAKE) generate
	$(NO_GIT_HOOK_ENV) $(MAKE) l10n
	$(NO_GIT_HOOK_ENV) $(MAKE) identity
	@git diff --exit-code -- lib/data/models/ lib/l10n/ lib/config/product.dart web/index.html web/manifest.json || \
		(echo "Generated output is out of date — run 'make generate', 'make l10n' and/or 'make identity' and commit the result." && exit 1)

# ─── Testing ────────────────────────────────────────────────────────────────

## Run unit/widget tests. Usage: make test [path=<path>] [name=<name>]
## path (optional): repo-relative path to a test file or directory.
## name (optional): flutter test --name pattern to match test names.
test:
	$(FLUTTER) test $(if $(path),$(path),test) $(if $(name),--name "$(name)")

# ─── Build & run ────────────────────────────────────────────────────────────

## Build the production web bundle (build/web), talking to the API of this slot. Usage: make build [mode=mobile]
build:
	$(if $(filter mobile,$(mode)),@test -n "$(LAN_IP)" || { echo "build: no LAN IP found for mode=mobile" >&2; exit 1; })
	$(FLUTTER) build web $(DART_DEFINES)

## Start the local dev server, hot-reloadable, without launching a browser itself: open the URL in
## whatever browser tab you already have. Hot reload (`r`/`R`) works interactively when run
## directly in a terminal; it won't work if backgrounded. Usage: make up [mode=mobile]
## mode (optional): "laptop" (default) binds to localhost only. "mobile" binds every network
## interface and compiles the app to talk to the API at this machine's LAN IP, so a phone on the
## same network can load the page at http://<lan-ip>:<web port>. The API must be up with the same
## mode, so it allows that origin. Note that the dev server's own debug service only binds to
## 127.0.0.1: a phone loading it gets a blank page. Testing from another device needs `make build
## mode=mobile` and `make serve-build` instead.
up:
	$(if $(filter mobile,$(mode)),@test -n "$(LAN_IP)" || { echo "up: no LAN IP found for mode=mobile" >&2; exit 1; })
	$(FLUTTER) run -d web-server --web-port=$(PF_PORT_WEB) $(DART_DEFINES) $(if $(filter mobile,$(mode)),--web-hostname=0.0.0.0)

## Same as "up", but launches Flutter's "chrome" web device instead — whichever Chromium-based
## browser Flutter finds on this machine — for DevTools/debugger integration (breakpoints, widget
## inspector) without the separate Dart Debug Chrome extension the web-server device requires.
debug:
	$(FLUTTER) run -d chrome --web-port=$(PF_PORT_WEB) $(DART_DEFINES)

## Stop any running Flutter dev process for this product, matched on its own web port rather than a
## package name, which is too generic to tell one product's dev server from another's.
down:
	-pkill -f "flutter_tools.*--web-port=$(PF_PORT_WEB)" 2>/dev/null || true

## Serve build/web on this slot's web port, falling back to index.html for any path that isn't a
## file (the path-based router's own links). Run `make build` first.
serve-build:
	python3 .productforge/serve_web_build.py $(PF_PORT_WEB)

# ─── Housekeeping ───────────────────────────────────────────────────────────

## Remove build artefacts and caches
clean-web:
	$(FLUTTER) clean
