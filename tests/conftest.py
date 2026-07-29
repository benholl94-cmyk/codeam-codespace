"""Shared pytest fixtures for the rollout-shield test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so ``import rollout_shield`` works
# in dev mode (without invoking scripts/install.sh).
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def scratch_state_root(tmp_path) -> Path:
    """A fresh, isolated state root for the test.

    Used by every test that touches ``State`` so the user's real
    ``~/.rollout-shield/`` is never touched.
    """
    root = tmp_path / "rollout-shield-test"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def scratch_state(scratch_state_root):
    """A fresh ``State`` instance bound to a scratch root."""
    from rollout_shield.state import State
    return State(root=scratch_state_root)


@pytest.fixture
def scratch_prefix(tmp_path) -> Path:
    """A fresh, isolated install prefix for self-test or install-sh tests."""
    prefix = tmp_path / "usr"
    prefix.mkdir(parents=True, exist_ok=True)
    return prefix


@pytest.fixture
def has_cryptography() -> bool:
    """True if the optional ``cryptography`` dep is installed."""
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture
def requires_cryptography(has_cryptography):
    """Skip the test if cryptography is not installed."""
    if not has_cryptography:
        pytest.skip("cryptography not installed")


@pytest.fixture
def has_finetune() -> bool:
    """True if the optional ``peft`` (and friends) deps are installed."""
    try:
        import peft  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture
def requires_finetune(has_finetune):
    """Skip the test if the [finetune] extra is not installed."""
    if not has_finetune:
        pytest.skip("[finetune] extra not installed (no peft)")


@pytest.fixture
def clean_quarantine(scratch_state):
    """Move all quarantined keys back to the active registry."""
    from rollout_shield.space import unquarantine_key
    for k in scratch_state.list_keys():
        if k.get("quarantined"):
            unquarantine_key(scratch_state, k["id"])
    return scratch_state
