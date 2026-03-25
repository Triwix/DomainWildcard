PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python
VENV_PIP := $(VENV_DIR)/bin/pip

.PHONY: setup preflight run run-reload test test-js batch-help

setup:
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_PIP) install -r requirements.txt

preflight:
	$(PYTHON) scripts/preflight.py

run:
	$(PYTHON) -m app.launcher --open

run-reload:
	$(PYTHON) -m app.launcher --open --reload

test:
	pytest -q

test-js:
	node --test tests/test_wordlist_utils.js

batch-help:
	$(PYTHON) scripts/domain_batch_run.py --help
