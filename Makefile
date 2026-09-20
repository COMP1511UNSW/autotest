# Development targets for autotest.
#
# The tools are found through two overridable variables so that the same
# targets work from a plain virtualenv and under pipenv:
#   make VENV_BIN=/path/to/venv/bin lint      # tools in that bin directory
#   pipenv run make lint                       # tools already on PATH
#   make PYTHON=python3.11 test                # a particular interpreter
# Their configuration (rules, per-file ignores, exclusions) is in
# pyproject.toml.

VENV_BIN ?=
BIN := $(if $(VENV_BIN),$(VENV_BIN)/,)
PYTHON ?= $(if $(VENV_BIN),$(VENV_BIN)/python,python3)
PYTEST := $(PYTHON) -m pytest
RUFF := $(BIN)ruff
BLACK := $(BIN)black
MYPY := $(BIN)mypy
BANDIT := $(BIN)bandit
VULTURE := $(BIN)vulture

# The coverage gate is the fail_under value under [tool.coverage.report] in
# pyproject.toml, so the number lives in one place.  A missing or unreadable
# value is an error rather than 0: silently printing 0 would turn the gate
# off and the coverage target would still look like it had run.
COV_FAIL_UNDER = $(shell $(PYTHON) -c 'import re, sys; m = re.search(r"^fail_under\s*=\s*([0-9.]+)", open("pyproject.toml").read(), re.M); sys.exit("no fail_under in pyproject.toml") if not m else print(m.group(1))')

.PHONY: lint format test coverage check

# every checker must pass on the whole tree; see pyproject.toml for the rules
lint:
	$(RUFF) check .
	$(BLACK) --check .
	$(MYPY) .
	$(BANDIT) -c pyproject.toml -q -r .
	$(VULTURE)

# rewrite the tree: black first, then ruff's safe fixes (import order, ...)
format:
	$(BLACK) .
	$(RUFF) check --fix .

test:
	$(PYTEST)

# Branch coverage of the whole suite, subprocesses included.  The
# autotest.py subprocesses are measured through patch = ["subprocess"] in
# [tool.coverage.run] (pytest-cov 7 no longer measures subprocesses
# itself) - do not remove it: without it autotest.py drops to 37% and
# run_tests.py to 8%.  Fails below the gate in pyproject.toml.
#
# The gate assumes a host which can build the sandbox: where unprivileged
# user namespaces are unavailable the sandbox tests skip and the total
# falls by about six points.  Run this on a sandbox-capable host.
coverage:
	@test -n "$(COV_FAIL_UNDER)" || { echo 'make coverage: no fail_under in pyproject.toml' >&2; exit 1; }
	$(PYTEST) --cov=. --cov-branch --cov-report=term-missing --cov-fail-under=$(COV_FAIL_UNDER)

check: lint test

README.md: README.template.md parameter_descriptions.py examples/wrapper.sh examples/simple_C/tests.txt Makefile
	perl -pe '/^#execute *(\s.*)/ and do { $$_ = `$$1`; $$? == 0 or die "$$1 failed\n" }' $< >$@ || { rm -f $@; exit 1; }
