VENV ?= ../.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install test lint infra-up infra-down scan doctor clean

install:
	$(PIP) install -e ".[dev]"

test:
	$(PY) -m pytest

infra-up:
	docker compose up -d

infra-down:
	docker compose down

doctor:
	$(PY) -m code_memory.cli.main doctor

scan:
	$(PY) -m code_memory.cli.main scan

clean:
	rm -rf data/*.db logs/*.log .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
