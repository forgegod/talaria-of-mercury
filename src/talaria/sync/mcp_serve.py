"""Add an ``mcp_servers`` entry to a target profile's ``config.yaml``.

This phase is the standalone ``--add-mcp-serve`` flag from the
original tool: it edits the target's ``mcp_servers`` block to
connect to a running Hermes dashboard server's SSE endpoint. The
phase does **not** require a source profile (use ``"-"`` or any
non-existent name as a placeholder source) and does not depend on
any other phase having run.

It is split from :mod:`config` so the CLI can expose it without
needing a ``--config`` mode. Use it together with ``talaria sync``
when wiring one profile to a shared hub running on a known port.
"""

from __future__ import annotations

from talaria.sync.config import sync_config
from talaria.sync.paths import SyncProfile
from talaria.sync.result import ConfigPhaseResult, PhaseResult


def sync_mcp_serve(
    target: SyncProfile,
    *,
    name: str = "hermes",
    port: int = 9119,
    host: str = "localhost",
    apply: bool = True,
    no_backup: bool = False,
) -> ConfigPhaseResult:
    """Add or update an ``mcp_servers.<name>`` entry on *target*.

    Delegates to :func:`talaria.sync.config.sync_config` so the
    config-merge logic stays in one place. ``source`` is the same
    *target* — the phase reads the existing config, injects the
    SSE entry, and writes it back. This matches the standalone
    tool's ``--add-mcp-serve`` flag (no source required).
    """
    return sync_config(
        source=target,
        target=target,
        add_mcp_serve=True,
        mcp_serve_name=name,
        mcp_serve_port=port,
        mcp_serve_host=host,
        apply=apply,
        no_backup=no_backup,
    )