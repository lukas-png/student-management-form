ifeq ($(OS),Windows_NT)
    VENV_BIN := .venv/Scripts
else
    VENV_BIN := .venv/bin
endif

PYTHON := $(VENV_BIN)/python
PIP    := $(VENV_BIN)/pip

.PHONY: venv install check lint typecheck test dev format

venv:
	python -m venv .venv

install: venv
	$(PIP) install -e ".[dev]"

check: lint typecheck test

lint:
	$(VENV_BIN)/ruff check src tests
	$(VENV_BIN)/ruff format --check src tests

typecheck:
	$(VENV_BIN)/mypy src/planer/domain

test:
	$(VENV_BIN)/pytest --cov=src/planer --cov-report=term-missing

dev:
	$(VENV_BIN)/uvicorn planer.web.app:app --reload --host 127.0.0.1 --port 8000 --no-access-log

format:
	$(VENV_BIN)/ruff format src tests
	$(VENV_BIN)/ruff check --fix src tests
