# The Python kit's makefile: the one pytest invocation, parallel by default, and the `test` target
# a repository with Python and no API kit needs. A repository whose tests need more writes its own
# `test` after the includes, running $(PYTEST).

PYTEST := uv run pytest -n auto

.PHONY: test

ifeq ($(filter django-api,$(PF_KITS)),)
## Run the tests, across every core. Usage: make test [path=<path>] [k=<keyword>]
test:
	$(PYTEST) $(path) $(if $(k),-k "$(k)")
endif
