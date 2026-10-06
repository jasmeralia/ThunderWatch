PYTHON ?= python3

.PHONY: deps lint lintfix test run screenshots

deps:
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	$(PYTHON) -m ruff check src/ scripts/ tests/ tools/screenshots/
	$(PYTHON) -m ruff format --check src/ scripts/ tests/ tools/screenshots/
	$(PYTHON) -m mypy src/thunderwatch/ scripts/

lintfix:
	$(PYTHON) -m ruff check --fix src/ scripts/ tests/ tools/screenshots/
	$(PYTHON) -m ruff format src/ scripts/ tests/ tools/screenshots/

test:
	QT_QPA_PLATFORM=offscreen $(PYTHON) -m pytest tests/ --cov=src/thunderwatch --cov=scripts --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml

run:
	$(PYTHON) -m thunderwatch

screenshots:
	QT_QPA_PLATFORM=offscreen $(PYTHON) tools/screenshots/generate_readme_screenshots.py
	QT_QPA_PLATFORM=offscreen $(PYTHON) tools/screenshots/generate_wizard_step_screenshots.py
