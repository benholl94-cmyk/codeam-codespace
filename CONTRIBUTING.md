# Contributing

Thanks for taking the time to contribute to codeam-codespace /
rollout-shield. This document covers how to set up a dev environment,
how to run the test pyramid, and the conventions this repo follows.

## code of conduct

By participating you agree to keep the discussion respectful, on-topic,
and constructive. The maintainers reserve the right to lock or
moderate threads that violate the spirit of the project.

## quick start

```bash
git clone <repo>
cd codeam-codespace

# install the runtime + dev extras
bash scripts/install.sh
pip install -e ".[dev]"

# install pre-commit hooks
pip install pre-commit
pre-commit install

# verify everything is green
make verify-install
make self-test
make test-smoke
make bench
```

## project structure

```
rollout_shield/          core runtime (CLI + daemon + AI + state)
dashboard/   docs/   examples/   benchmarks/   tests/    sub-workspaces
scripts/                  install / verify / uninstall / export
.github/workflows/        CI
.beads/                   issue tracker (Dolt DB)
```

Each sub-workspace has its own README. See `WORKSPACES.md` for the
full scope map.

## commit conventions

- Imperative subject line, ≤ 72 chars, no trailing period
  (`feat: add smart-routing binding`, not `Added...`).
- Body explains **why**, not what. Wrap at 72.
- Reference the bd issue id in the footer (`Refs: <id>`).
- `[skip ci]` is reserved for prebuild-only refreshes of the
  devcontainer image. **Do not** use it for source changes.

Branches: `feature/<bd-id>-short-name` or `fix/<bd-id>-short-name`.
PRs target `main`.

## test pyramid

```bash
make self-test         # end-to-end scratch-state smoke
make test-smoke        # pytest -m "not slow"
make bench             # performance benchmarks
```

For new features, write tests **first**:

1. Unit test (no I/O) — `tests/test_unit_*.py`
2. Integration test (scratch state) — `tests/test_integration_*.py`
3. Property-based test (state machine invariants) — `tests/test_property_based.py`

Property-based tests use [hypothesis](https://hypothesis.readthedocs.io/).
Keep examples ≤ 50 and use `@settings(max_examples=...)` for heavier
generators.

## style

- Python 3.11+ stdlib preferred; third-party deps only when there's
  no stdlib equivalent.
- `ruff` for lint + format (run via `pre-commit`).
- `mypy --ignore-missing-imports --no-incremental` non-strict in CI.
- Docstrings on every public function. Format: 1-line summary
  followed by a longer description when needed.
- Module-level docstring naming every public symbol.

## AI layer etiquette

The AI layer is **the IP** of this project. When touching
`rollout_shield/ai/`:

- New strategies in `router.py` must add a test in `test_unit_ai.py`.
- New own models in `own_models.py` must register a `family="own"`
  entry in `models.py` and add a benchmark in `ai/benchmarks.py`.
- New prompts in `generator.py` should not duplicate an existing
  kind.

## filing issues

Use `.github/ISSUE_TEMPLATE/task.yml`. The triage bot will label
your issue with `needs-triage` + `agent-routable` and post a
checklist for converting it into a `bd` issue.

## release process

1. Update `CHANGELOG.md` (move `[unreleased]` → `[<version>] - <date>`).
2. Bump `__version__` in `rollout_shield/__init__.py`.
3. Tag the commit: `git tag -s v<version> -m 'release: <version>'`.
4. CI builds the artifact and publishes it (`.github/workflows/release.yml`).

## license

By contributing, you agree that your contributions are licensed under
the project's `LICENSE`.