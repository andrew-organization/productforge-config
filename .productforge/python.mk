# Installed by productforge-config's `update`. Generated: change it in productforge-config
# (andrew-organization/productforge-config), not here, or the next update writes it back.
# The Python kit's makefile: the one pytest invocation, parallel by default, the `test` target
# a repository with Python and no API kit needs, and what a Python repository keeps out of its tree.
# A repository whose tests need more writes its own `test` after the includes, running $(PYTEST).

# No bytecode beside the sources.
export PYTHONDONTWRITEBYTECODE := 1

PYTEST := uv run pytest -n auto

.PHONY: test clean-python
clean: clean-python

ifeq ($(filter django-api,$(PF_KITS)),)
## Run the tests, across every core. Usage: make test [path=<path>] [k=<keyword>]
test:
	$(PYTEST) $(path) $(if $(k),-k "$(k)")
endif

## Remove the virtualenv and every __pycache__
clean-python:
	rm -rf .venv
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
