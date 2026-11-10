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
STATION ?= examples/station.simulator.yaml
PROFILE ?= examples/profile-sc767-demo
FIXTURE ?= F1
WORK_ORDER ?=
RUN_ARGS ?=
SMOKE_SERIAL ?= PCB-MAKE-$(shell $(PYTHON) -c "import uuid; print(uuid.uuid4().hex[:12])")

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

.PHONY: help venv install dev packaging-deps run demo smoke self-test test \
	lint format typecheck check build package-linux package-windows \
	hostkit version clean clean-data clean-all

help: ## Show this help
	@echo "HWVAL Factory — common targets"
	@echo
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "Variables: PYTHON=$(PYTHON) VENV=$(VENV) STATION=$(STATION)"
	@echo "           PROFILE=$(PROFILE) FIXTURE=$(FIXTURE) WORK_ORDER=$(WORK_ORDER)"
	@echo "Example:   make demo STATION=examples/station.simulator.yaml"

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

run: install ## Launch the station UI (simulator by default)
	$(PY) -m hwval_factory.app --station $(STATION) $(RUN_ARGS)

demo: install ## Run one simulated unit headless
	$(PY) -m hwval_factory --cli \
		--station $(STATION) \
		--profile $(PROFILE) \
		--fixture $(FIXTURE) \
		--pcb-serial "$(SMOKE_SERIAL)" \
		$(if $(WORK_ORDER),--work-order "$(WORK_ORDER)")

smoke: install ## Version, fixture list, self-test and one simulated unit
	$(PY) -m hwval_factory --version
	$(PY) -m hwval_factory --list-fixtures --station $(STATION)
	$(PY) -m hwval_factory --self-test --station $(STATION) \
		--profile $(PROFILE) --fixture $(FIXTURE)
	$(PY) -m hwval_factory --cli \
		--station $(STATION) \
		--profile $(PROFILE) \
		--fixture $(FIXTURE) \
		--pcb-serial "$(SMOKE_SERIAL)" \
		$(if $(WORK_ORDER),--work-order "$(WORK_ORDER)") \
		--json > /dev/null
	@echo "smoke: PASS"

self-test: install ## Run the fixture golden-board self-test
	$(PY) -m hwval_factory --self-test --station $(STATION) \
		--profile $(PROFILE) --fixture $(FIXTURE)

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

clean-data: ## Remove simulator runtime data, labels and local databases
	rm -rf station-data
	rm -f station-data.sqlite station-data.sqlite-wal station-data.sqlite-shm
	rm -f hwval-factory.sqlite hwval-factory.sqlite-wal hwval-factory.sqlite-shm

clean-all: clean clean-data ## Remove caches, runtime data and the virtualenv
	rm -rf $(VENV)
