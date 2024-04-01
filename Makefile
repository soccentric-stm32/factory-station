# HWVAL Factory — developer Makefile.
#
# Unix/macOS: use GNU make directly.
# Windows:     use MSYS2/`make`, or the packaging/build-windows.ps1 script.
#
# Run `make` or `make help` for the target list.

SHELL := /bin/bash

# Windows: run every recipe under Git's POSIX bash, also from cmd.exe or
# PowerShell with only Git\cmd on PATH. Otherwise native make falls back to
# cmd.exe, which cannot run the recipes (quotes, pipes, find, rm -rf), and
# `sh scripts/...` is not found. Git's usr\bin goes first on PATH so find,
# sort and friends are the POSIX tools, not the Windows ones of the same name.
ifeq ($(OS),Windows_NT)
ifneq ($(origin SHELL),command line)
GIT_USR_BIN := $(firstword \
  $(if $(wildcard C:/Program?Files/Git/usr/bin/bash.exe),C:/Program?Files/Git/usr/bin) \
  $(if $(wildcard $(subst \,/,$(LOCALAPPDATA))/Programs/Git/usr/bin/bash.exe),$(subst \,/,$(LOCALAPPDATA))/Programs/Git/usr/bin) \
  $(if $(wildcard C:/msys64/usr/bin/bash.exe),C:/msys64/usr/bin))
ifneq ($(GIT_USR_BIN),)
SHELL := $(subst ?, ,$(GIT_USR_BIN))/bash.exe
export PATH := $(subst /,\,$(subst ?, ,$(GIT_USR_BIN)));$(PATH)
endif
endif
endif
.DEFAULT_GOAL := help

# Windows has no `python3` launcher (Git Bash resolves it to a Microsoft Store
# stub that exits non-zero), so the interpreter name differs per platform.
PYTHON ?= $(if $(filter Windows_NT,$(OS)),python,python3)
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
	lint format typecheck check build package-linux package-macos \
	package-windows hostkit version clean clean-data clean-all

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
	@if [ "$(OS)" = "Windows_NT" ] || [ "$$(uname -s)" = "Darwin" ]; then \
		echo "package-linux: run this target on Linux"; \
	else \
		PATH="$(CURDIR)/$(VENV_BIN):$$PATH" bash packaging/build-linux.sh; \
	fi

package-macos: dev ## Build the macOS .app/.dmg via packaging/build-macos.sh
	@if [ "$$(uname -s)" != "Darwin" ]; then \
		echo "package-macos: run this target on macOS"; \
	else \
		bash packaging/build-macos.sh; \
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
	$(PYTHON) -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__') if '.venv' not in p.parts and '.git' not in p.parts]"

clean-data: ## Remove local bench data
	rm -rf $(DATA_DIR)

clean-all: clean clean-data ## Remove caches, runtime data and the virtualenv
	rm -rf $(VENV)
