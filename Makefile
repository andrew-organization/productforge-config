.PHONY: all install lint test clean setup-hooks

# The update command reads settings/ bundled into its own wheel (see
# pyproject.toml's force-include), exactly as a real `uvx --from git+...`
# install does. An editable install — uv's default for the project it's
# run in, on every `uv sync` and `uv run` alike — skips that build step and
# reads straight from src/ instead, missing settings/ entirely.
export UV_NO_EDITABLE := 1

## Lint and test everything, as CI does
all: lint test

# ─── Dependencies ───────────────────────────────────────────────────────────

## Install the dev dependencies (pre-commit, pytest) with uv, the lint hook environments, and the git hooks
install:
	uv sync --dev
	uv run pre-commit install-hooks --config .pre-commit-lint.yaml
	$(MAKE) setup-hooks

# ─── Code quality ───────────────────────────────────────────────────────────

## Lint every file in this repository with the tools in .pre-commit-lint.yaml — the
## Python in src/ and tests/, markdown, whitespace. The git hook and CI both run this target.
lint:
	uv run pre-commit run --all-files --config .pre-commit-lint.yaml

## Run the update command's own test suite: its file-rewriting logic against
## tests/fixture_repo, and the published hooks against fixture files, entirely
## against throwaway copies. CI runs this target.
test:
	PYTHONDONTWRITEBYTECODE=1 uv run pytest -n auto

# ─── Housekeeping ───────────────────────────────────────────────────────────

## Remove the virtualenv and the test and build caches
clean:
	rm -rf .venv .pytest_cache dist build

## Install the pre-commit git hook into .git/hooks
setup-hooks:
	uv run pre-commit install
