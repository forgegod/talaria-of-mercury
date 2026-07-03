"""Hermes-specific Talaria features.

Each module under this package implements a single feature that operates
on Hermes' ``state.db`` and ``logs/`` directory. The MoA truncation
check is the first feature.
"""

from __future__ import annotations

from talaria.hermos import moa_truncation

__all__ = ["moa_truncation"]