VENV := .venv

ifeq ($(OS),Windows_NT)
PYTHON ?= python
PY := $(VENV)/Scripts/python.exe
else
PYTHON ?= python3
PY := $(VENV)/bin/python
endif

PIP := $(PY) -m pip

.PHONY: venv deps lint lintfix test run screenshots

venv:
	@command -v $(PYTHON) >/dev/null 2>&1 || \
		{ echo "ERROR: $(PYTHON) not found. Install Python 3."; exit 1; }
	$(PYTHON) -m venv $(VENV)

deps: venv
	$(PIP) install -r requirements-dev.txt

lint:
	$(PY) -m ruff check src/ scripts/ tests/ tools/screenshots/
	$(PY) -m ruff format --check src/ scripts/ tests/ tools/screenshots/
	$(PY) -m mypy src/thunderwatch/ scripts/

lintfix:
	$(PY) -m ruff check --fix src/ scripts/ tests/ tools/screenshots/
	$(PY) -m ruff format src/ scripts/ tests/ tools/screenshots/

test:
	QT_QPA_PLATFORM=offscreen $(PY) -m pytest tests/ --cov=src/thunderwatch --cov=scripts --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml

run:
	$(PY) -m thunderwatch

screenshots: deps
	QT_QPA_PLATFORM=offscreen $(PY) tools/screenshots/generate_readme_screenshots.py
	QT_QPA_PLATFORM=offscreen $(PY) tools/screenshots/generate_wizard_step_screenshots.py
