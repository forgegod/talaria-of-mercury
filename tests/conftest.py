"""Shared pytest fixtures for Talaria tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_hermes_root(tmp_path: Path) -> Path:
    """A temporary directory laid out like ``~/.hermes/``."""
    root = tmp_path / ".hermes"
    root.mkdir()
    return root


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no Hermes-related env vars leak from the host environment."""
    for var in ("HERMES_PROFILE", "HERMES_HOME"):
        monkeypatch.delenv(var, raising=False)