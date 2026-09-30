include productforge.env
include $(sort $(wildcard .productforge/*.mk))

.PHONY: all clean clean-config test

# The update command reads settings/ bundled into its own wheel (see
# pyproject.toml's force-include), exactly as a real `uvx --from git+...`
# install does. An editable install — uv's default for the project it's
# run in, on every `uv sync` and `uv run` alike — skips that build step and
# reads straight from src/ instead, missing settings/ entirely.
export UV_NO_EDITABLE := 1

# The kits' makefiles give `test` its recipe (parallel pytest); it is named here for checkmake, which reads this file alone.
test:

## Lint and test everything, as CI does
all: lint test

# ─── Housekeeping ───────────────────────────────────────────────────────────

clean: clean-config

## Remove the build outputs (the Python kit's clean removes the virtualenv)
clean-config:
	rm -rf dist build
