# Makefile for codeam-codespace / rollout-shield
#
# Convenience targets wrapping the production-grade tooling. Run
# `make help` to see what's available.

SHELL := /usr/bin/env bash
PYTHON ?= python3
CLI := rollout-shield

# The installed CLI lives at ~/usr/bin/rollout-shield after `make install`.
# In dev mode (targeting the repo), we invoke the package directly.
REPO_ROOT := $(shell pwd)
PYTHONPATH := $(REPO_ROOT)

.DEFAULT_GOAL := help

.PHONY: help
help: ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------- install ----------

.PHONY: install
install: ## hard-build into ~/usr/ (scripts/install.sh)
	@bash scripts/install.sh

.PHONY: verify-install
verify-install: ## verify the install is healthy (scripts/verify-install.sh)
	@bash scripts/verify-install.sh

.PHONY: uninstall
uninstall: ## remove the ~/usr/ install (does NOT touch state)
	@bash scripts/uninstall.sh

# ---------- runtime ----------

.PHONY: install-state
install-state: ## initialize ~/.rollout-shield/ + default key
	@$(PYTHON) -m rollout_shield install

.PHONY: status
status: ## print system summary
	@$(PYTHON) -m rollout_shield status

.PHONY: self-check
self-check: ## diagnose the environment
	@$(PYTHON) -m rollout_shield self-check

.PHONY: self-heal
self-heal: ## diagnose + auto-repair (--dry-run by default)
	@$(PYTHON) -m rollout_shield self-heal --dry-run

.PHONY: self-heal-auto
self-heal-auto: ## diagnose + auto-repair (writes to state)
	@$(PYTHON) -m rollout_shield self-heal

.PHONY: self-test
self-test: ## end-to-end smoke test against a scratch state
	@$(PYTHON) -m rollout_shield self-test

.PHONY: monitor-once
monitor-once: ## run a single monitor cycle
	@$(PYTHON) -m rollout_shield monitor --once

.PHONY: monitor-daemon
monitor-daemon: ## start the monitor as a long-lived foreground daemon
	@$(PYTHON) -m rollout_shield monitor --daemon --foreground

.PHONY: dashboard
dashboard: ## serve the web dashboard on http://127.0.0.1:8765
	@$(PYTHON) -m rollout_shield dashboard --port 8765

.PHONY: ai-route
ai-route: ## route a prompt through the AI portfolio (prompt=...)
	@$(PYTHON) -m rollout_shield ai route "$(PROMPT)" --strategy concat

.PHONY: ai-leaderboard
ai-leaderboard: ## show the AI benchmark leaderboard
	@$(PYTHON) -m rollout_shield ai leaderboard

.PHONY: ai-cycle
ai-cycle: ## run one AI self-cycle
	@$(PYTHON) -m rollout_shield ai cycle --count 1

.PHONY: space-show
space-show: ## show the current controller policy
	@$(PYTHON) -m rollout_shield space show

# ---------- quality ----------

.PHONY: test
test: ## run the pytest suite (smoke + integration)
	@$(PYTHON) -m pytest tests/ -m "smoke or integration" -v

.PHONY: test-smoke
test-smoke: ## run only the smoke tests (fast)
	@$(PYTHON) -m pytest tests/ -m smoke -v

.PHONY: test-all
test-all: ## run the full pytest suite (smoke + slow + integration)
	@$(PYTHON) -m pytest tests/ -v

.PHONY: lint
lint: ## run ruff lint
	@$(PYTHON) -m ruff check rollout_shield/ tests/ benchmarks/ 2>/dev/null || echo "(ruff not installed; pip install ruff)"

.PHONY: mypy
mypy: ## run mypy type check
	@$(PYTHON) -m mypy rollout_shield/ --ignore-missing-imports 2>/dev/null || echo "(mypy not installed; pip install mypy)"

.PHONY: format
format: ## run ruff format
	@$(PYTHON) -m ruff format rollout_shield/ tests/ benchmarks/ 2>/dev/null || echo "(ruff not installed; pip install ruff)"

.PHONY: bench
bench: ## run the benchmark suite
	@$(PYTHON) -m benchmarks

.PHONY: bench-micro
bench-micro: ## run only the micro benchmarks
	@$(PYTHON) -m benchmarks --kind micro

.PHONY: bench-ai
bench-ai: ## run only the AI benchmarks
	@$(PYTHON) -m benchmarks --kind ai

# ---------- housekeeping ----------

.PHONY: clean
clean: ## remove Python caches + temp state
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .mypy_cache build/ dist/ 2>/dev/null || true
	@echo "cleaned"

.PHONY: clean-state
clean-state: ## remove transient state at ~/.rollout-shield/ (USE WITH CARE)
	@echo "WARNING: this deletes ~/.rollout-shield/ but NOT ~/.rollout-shield/keys_material/"
	@rm -rf ~/.rollout-shield/claims ~/.rollout-shield/alerts \
	        ~/.rollout-shield/health ~/.rollout-shield/ai \
	        ~/.rollout-shield/daemon.json ~/.rollout-shield/monitor.* \
	        ~/.rollout-shield/reputation.json
	@echo "transient state cleared"

# ---------- composite ----------

.PHONY: ci
ci: lint mypy test-smoke self-test ## full CI pipeline (lint + type + smoke + self-test)

.PHONY: dev
dev: install install-state self-test ## fresh dev setup on a new host
