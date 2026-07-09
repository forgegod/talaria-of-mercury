"""Resolve sync-relevant paths for a Hermes profile.

Talaria's read-only features consume ``state.db`` and ``logs/``. Sync
needs the rest of the profile — config.yaml, SOUL.md, skills/,
``.env``, context_length_cache.yaml — so a feature group that operates
on profile *artefacts* (not profile state) can read and write them.

The resolver mirrors :mod:`talaria.paths`:

* a profile name (e.g. ``default``, ``hermes-vc``)
* an absolute path to a ``config.yaml`` file

returns a :class:`SyncProfile` whose attributes are absolute paths to
every artefact the sync phases know about. The ``default`` profile uses
``$HERMES_ROOT`` directly; named profiles use
``$HERMES_ROOT/profiles/<name>/``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


def hermes_root() -> Path:
    """Return the canonical Hermes install root, evaluated at call time.

    Resolved as ``$HOME/.hermes`` so subprocess tests can override
    ``HOME`` via ``env=`` and point at a temp directory. Computed
    on each call (not module import) because module-level
    ``Path.home()`` captures the import-time value and would ignore
    later ``HOME`` overrides.
    """
    return Path(os.environ.get("HOME") or str(Path.home())) / ".hermes"


#: Backwards-compatible constant for callers that need a Path.
#: Evaluated at import; tests should call :func:`hermes_root` instead.
HERMES_ROOT: Path = hermes_root()
"""Initial-value snapshot of :func:`hermes_root` for code that imports
the constant. Tests and any code that needs to react to ``HOME``
changes must call :func:`hermes_root` directly."""

DEFAULT_PROFILE_NAME = "default"
""":class:`str` profile name that maps to ``$HERMES_ROOT`` directly."""


@dataclass(frozen=True)
class SyncProfile:
    """Resolved paths for a Hermes profile as seen by sync phases.

    All attributes are absolute paths and may not exist on disk — the
    resolver never asserts existence. Callers decide how to react to
    a missing artefact (most phases log a ``skip`` line and move on).
    """

    name: str
    """:class:`str` profile name (``"default"`` or a named profile)."""

    root: Path
    """:class:`~pathlib.Path` profile directory (``$HERMES_ROOT`` or
    ``$HERMES_ROOT/profiles/<name>``)."""

    config_yaml: Path
    """:class:`~pathlib.Path` to the profile's ``config.yaml``."""

    soul_md: Path
    """:class:`~pathlib.Path` to ``SOUL.md`` (``<root>/SOUL.md``)."""

    skills_dir: Path
    """:class:`~pathlib.Path` to ``skills/`` (``<root>/skills``)."""

    env_file: Path
    """:class:`~pathlib.Path` to ``.env`` (``<root>/.env``)."""

    context_cache: Path
    """:class:`~pathlib.Path` to ``context_length_cache.yaml``."""

    auth_file: Path
    """:class:`~pathlib.Path` to ``auth.json`` (``<root>/auth.json``).
    Contains OAuth tokens for providers (nous, openai-codex, etc.)
    and the credential pool. Synced by the ``auth_tokens`` phase
    using newest-token-wins per provider."""

    @property
    def is_default(self) -> bool:
        """:class:`bool` -- whether this is the default profile."""
        return self.name == DEFAULT_PROFILE_NAME

    @property
    def home(self) -> Path:
        """:class:`~pathlib.Path` alias for :attr:`root`.

        Sync phases accept ``source_home`` / ``target_home`` parameters;
        this alias keeps that nomenclature tidy without renames.
        """
        return self.root


def resolve_profile(
    spec: str,
    *,
    root: Path | None = None,
) -> SyncProfile:
    """Resolve a profile spec (name or ``config.yaml`` path) to a :class:`SyncProfile`.

    Resolution rules:

    * ``"default"`` / ``"main"`` / ``"base"`` -> ``$HERMES_ROOT``
    * ``"<name>"`` -> ``$HERMES_ROOT/profiles/<name>`` (must exist)
    * an absolute path to a ``config.yaml`` -> its parent directory

    Parameters
    ----------
    spec:
        Profile name or path to ``config.yaml``.
    root:
        Override the Hermes install root. Tests pass a ``tmp_path``
        fixture; production code uses the result of :func:`hermes_root`
        (which honours ``$HOME``).

    Raises
    ------
    FileNotFoundError:
        ``spec`` looks like a profile name but the directory is missing.
    """
    base = root if root is not None else hermes_root()

    # Profile-name shortcuts that all map to ~/.hermes.
    if spec in ("default", "main", "base"):
        return _build(DEFAULT_PROFILE_NAME, base)

    # Named profile
    profile_dir = base / "profiles" / spec
    if profile_dir.is_dir() and (profile_dir / "config.yaml").exists():
        return _build(spec, profile_dir)

    # Literal file path
    p = Path(spec).expanduser()
    if p.is_file() and p.name == "config.yaml":
        return _build(p.parent.name or DEFAULT_PROFILE_NAME, p.parent)

    # Profile-name miss with a path-like or dotfile-relative spec is a
    # likely typo; surface available profiles to help the operator.
    if "/" not in spec and not spec.startswith(".") and not spec.startswith("~"):
        available = list_profiles(root=base)
        raise FileNotFoundError(
            f"Profile '{spec}' not found at {profile_dir}.\n"
            f"Available profiles: {', '.join(available) or '(none)'}"
        )

    raise FileNotFoundError(f"Config file not found: {spec}")


def list_profiles(*, root: Path | None = None) -> list[str]:
    """Return sorted profile names available on disk.

    A profile is ``<root>/profiles/<name>/config.yaml``. The
    ``default`` profile (``<root>/config.yaml``) is included only if
    that file exists.
    """
    base = root if root is not None else hermes_root()
    profiles_dir = base / "profiles"
    found: list[str] = []
    if profiles_dir.is_dir():
        for d in sorted(profiles_dir.iterdir()):
            if d.is_dir() and (d / "config.yaml").exists():
                found.append(d.name)
    if (base / "config.yaml").exists() and DEFAULT_PROFILE_NAME not in found:
        found.insert(0, DEFAULT_PROFILE_NAME)
    return found


def _build(name: str, root: Path) -> SyncProfile:
    """Construct a :class:`SyncProfile` from a resolved ``root`` directory.

    Private helper for :func:`resolve_profile`. All path attributes are
    absolute and computed at construction time so callers never have to
    think about relative paths.
    """
    root = root.resolve()
    return SyncProfile(
        name=name,
        root=root,
        config_yaml=root / "config.yaml",
        soul_md=root / "SOUL.md",
        skills_dir=root / "skills",
        env_file=root / ".env",
        context_cache=root / "context_length_cache.yaml",
        auth_file=root / "auth.json",
    )


def same_profile(a: SyncProfile, b: SyncProfile) -> bool:
    """:class:`bool` -- whether two profiles resolve to the same directory.

    Compares resolved ``config.yaml`` paths so symlinks and ``default``
    vs. ``main`` aliases don't accidentally trigger a self-sync.
    """
    return a.config_yaml.resolve() == b.config_yaml.resolve()


# Default port for the Hermes dashboard/web server (mirrors
# hermes_cli/subcommands/dashboard.py). Exposed here so :mod:`mcp_serve`
# can build its YAML entry without importing Hermes internals.
DEFAULT_MCP_SERVE_PORT = 9119


def mcp_serve_entry(port: int = DEFAULT_MCP_SERVE_PORT, host: str = "localhost") -> dict:
    """:class:`dict` YAML-shaped entry pointing at a Hermes SSE endpoint.

    The Hermes dashboard server exposes ``/sse`` (transport ``sse``).
    A target profile with this entry in its ``mcp_servers`` connects
    to that running server and gains the conversation-bridge tools
    (list/read/send/poll/approve).

    Parameters
    ----------
    port:
        SSE endpoint port. Defaults to :data:`DEFAULT_MCP_SERVE_PORT`.
    host:
        Hostname or IP. Defaults to ``"localhost"``.
    """
    return {
        "url": f"http://{host}:{port}/sse",
        "transport": "sse",
    }