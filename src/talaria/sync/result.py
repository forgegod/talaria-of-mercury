"""Result dataclasses for sync phases.

Each phase returns a structured result the renderer turns into
human-readable output or JSON. Keeping the shape stable makes
JSON-mode output consumable by cron jobs and dashboards without
string scraping.

Every phase result exposes:

* ``phase`` — short identifier (``"config"``, ``"soul"``, ...)
* ``status`` — one of ``"in_sync"``, ``"updated"``, ``"new"``,
  ``"skipped"``, ``"error"``. The renderer maps each to a coloured
  prefix and the JSON dump maps each to a stable string.
* ``logs`` — phase-specific log lines the operator should see.
* ``write_confirmed`` — ``True`` only when bytes actually hit disk.
  Dry runs always return ``False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Status = Literal["in_sync", "updated", "new", "skipped", "error"]


@dataclass
class PhaseResult:
    """Base shape for every sync phase result.

    Subclasses add phase-specific fields (e.g. ``backup_path`` for
    the config phase). The base fields are common across all phases
    so the renderer and JSON serialiser can rely on them.
    """

    phase: str
    status: Status
    logs: list[str] = field(default_factory=list)
    write_confirmed: bool = False
    target_path: Path | None = None


@dataclass
class ConfigPhaseResult(PhaseResult):
    """Result of the ``config.yaml`` sync phase.

    ``mode`` is one of ``"exclude"``, ``"only"``, ``"identity"`` or
    ``"mcp_serve"``. The renderer uses it to choose a one-line summary
    that matches the operator's flag choice.
    """

    mode: str = "identity"
    exclude_paths: list[str] = field(default_factory=list)
    only_paths: list[str] = field(default_factory=list)
    mcp_serve_name: str | None = None
    mcp_serve_url: str | None = None
    diff_lines: list[str] = field(default_factory=list)
    backup_path: Path | None = None


@dataclass
class FilePhaseResult(PhaseResult):
    """Generic single-file copy result.

    Used by ``SOUL.md`` and ``.env`` and ``context_length_cache.yaml``
    — all three are single-file artefacts with a similar
    update/in-sync/new status shape. ``backup_path`` is set when the
    target file was backed up before being overwritten.
    """

    backup_path: Path | None = None
    new_vars: list[str] = field(default_factory=list)
    preserved_vars: list[str] = field(default_factory=list)
    new_keys: list[str] = field(default_factory=list)
    updated_keys: list[str] = field(default_factory=list)


@dataclass
class AuthTokensPhaseResult(PhaseResult):
    """Result of the ``auth.json`` OAuth token sync phase.

    The phase scans every profile's ``auth.json`` for the newest
    token per provider, then writes those into the target.
    ``updated_providers`` / ``new_providers`` decompose the changes
    by provider name; ``source_profiles`` lists which profiles
    contributed a newest token.
    """

    backup_path: Path | None = None
    updated_providers: list[str] = field(default_factory=list)
    new_providers: list[str] = field(default_factory=list)
    source_profiles: list[str] = field(default_factory=list)


@dataclass
class SkillsPhaseResult(PhaseResult):
    """Result of the ``skills/`` directory-tree sync phase.

    ``copied`` / ``new_count`` / ``skipped`` decompose the tree into
    three counts so the JSON consumer can branch without scanning
    ``logs``. ``skills_detail`` carries per-skill lines for the
    verbose renderer.
    """

    filters: list[str] = field(default_factory=list)
    copied: int = 0
    new_count: int = 0
    skipped: int = 0
    skills_detail: list[str] = field(default_factory=list)


@dataclass
class SyncReport:
    """Top-level report aggregating every phase result.

    ``ok`` is ``True`` when no phase reported ``status="error"``.
    ``any_writes`` is ``True`` when at least one phase actually wrote
    bytes — useful for the ``talaria sync`` exit code semantics.
    """

    source: str
    target: str
    apply: bool
    config: ConfigPhaseResult | None = None
    soul: PhaseResult | None = None
    skills: SkillsPhaseResult | None = None
    env: FilePhaseResult | None = None
    context_cache: FilePhaseResult | None = None
    auth_tokens: AuthTokensPhaseResult | None = None
    mcp_serve: PhaseResult | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """:class:`bool` -- no phase reported an error."""
        if self.error:
            return False
        for phase in self._all_phases():
            if phase is not None and phase.status == "error":
                return False
        return True

    @property
    def any_writes(self) -> bool:
        """:class:`bool` -- at least one phase wrote bytes."""
        return any(
            p is not None and p.write_confirmed for p in self._all_phases()
        )

    def _all_phases(self) -> list[PhaseResult | None]:
        return [
            self.config,
            self.soul,
            self.skills,
            self.env,
            self.context_cache,
            self.auth_tokens,
            self.mcp_serve,
        ]