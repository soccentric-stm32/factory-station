# HWVAL Factory — developer Makefile.
#
# Unix/macOS: use GNU make directly.
# Windows:     use MSYS2/`make`, or the packaging/build-windows.ps1 script.
#
# Run `make` or `make help` for the target list.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV ?= .venv
DATA_DIR ?= station-data
RUN_ARGS ?=

ifeq ($(OS),Windows_NT)
	VENV_BIN := $(VENV)/Scripts
	PY := $(VENV_BIN)/python.exe
	PATHSEP := ;
else
	VENV_BIN := $(VENV)/bin
	PY := $(VENV_BIN)/python
	PATHSEP := :
endif

PIP := $(PY) -m pip
INSTALL_STAMP := $(VENV)/.hwval-installed
DEV_STAMP := $(VENV)/.hwval-dev-installed
PACKAGING_STAMP := $(VENV)/.hwval-packaging-installed

# Source-tree runs work without an editable install.
export PYTHONPATH := src$(if $(PYTHONPATH),$(PATHSEP)$(PYTHONPATH))

.PHONY: help venv install dev packaging-deps run demo smoke test \
	lint format typecheck check build package-linux package-windows \
	hostkit version clean clean-data clean-all

help: ## Show this help
	@echo "HWVAL Factory — common targets"
	@echo
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Variables: PYTHON=$(PYTHON) VENV=$(VENV) DATA_DIR=$(DATA_DIR)"
	@echo "Example:   make demo"

venv: $(PY) ## Create the virtual environment
	@echo "virtual environment: $(VENV)"

$(PY):
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip

$(INSTALL_STAMP): pyproject.toml requirements.txt | $(PY)
	$(PIP) install -e .
	@touch $(INSTALL_STAMP)

install: $(INSTALL_STAMP) ## Editable install into the virtualenv

$(DEV_STAMP): pyproject.toml requirements.txt | $(PY)
	$(PIP) install -e ".[dev]"
	@touch $(DEV_STAMP)

dev: $(DEV_STAMP) ## Install development tools (ruff, mypy, pytest extras)

$(PACKAGING_STAMP): pyproject.toml requirements.txt | $(PY)
	$(PIP) install -e ".[packaging]"
	@touch $(PACKAGING_STAMP)

packaging-deps: $(PACKAGING_STAMP) ## Install PyInstaller

run: install ## Launch the bench UI
	$(PY) -m hwval_factory --data-dir $(DATA_DIR) $(RUN_ARGS)

demo: install ## Launch the bench UI with a simulated board
	$(PY) -m hwval_factory --data-dir $(DATA_DIR) --simulate

smoke: install ## Print the version and build the bench window offscreen
	$(PY) -m hwval_factory --version
	QT_QPA_PLATFORM=offscreen $(PY) -m pytest -q tests/test_ui_smoke.py
	@echo "smoke: PASS"

hostkit: ## Vendor the canonical HWLINK bundle and regenerate the schema
	$(PYTHON) scripts/update_hostkit.py

test: dev ## Run the pytest contract and simulator suite
	$(PY) -m pytest

lint: dev ## Run ruff
	$(PY) -m ruff check src scripts/update_hostkit.py packaging/launcher.py

format: dev ## Format Python sources with ruff
	$(PY) -m ruff format src scripts/update_hostkit.py packaging/launcher.py

typecheck: dev ## Run mypy in strict mode
	$(PY) -m mypy --strict src

check: lint typecheck ## Lint and type-check

build: packaging-deps ## Build the PyInstaller bundle
	$(PY) -m PyInstaller --clean --noconfirm packaging/hwval-factory.spec

package-linux: dev ## Build the Linux .deb/AppImage via packaging/build-linux.sh
	@if [ "$(OS)" = "Windows_NT" ]; then \
		echo "package-linux: run this target on Linux or macOS"; \
	else \
		PATH="$(CURDIR)/$(VENV_BIN):$$PATH" bash packaging/build-linux.sh; \
	fi

package-windows: dev ## Build the Windows MSI on Windows
	@if [ "$(OS)" != "Windows_NT" ]; then \
		echo "package-windows: run this target on Windows"; \
	else \
		powershell -ExecutionPolicy Bypass -File packaging/build-windows.ps1; \
	fi

version: ## Print the application version
	$(PYTHON) -c "import sys; sys.path.insert(0, 'src'); from hwval_factory import __version__; print(__version__)"

clean: ## Remove build/test caches (keeps runtime data)
	rm -rf build dist .ruff_cache .mypy_cache .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

clean-data: ## Remove local bench data
	rm -rf $(DATA_DIR)

clean-all: clean clean-data ## Remove caches, runtime data and the virtualenv
	rm -rf $(VENV)
