"""Render sync reports as human-readable text or structured JSON.

The renderer is the only place that knows about colour codes,
section headers, and the per-phase ordering. Phase modules return
plain :class:`~talaria.sync.result.PhaseResult` dataclasses; this
module turns them into output.

``--verbose`` controls how much detail is shown:

* default — one line per phase summary (``config: updated``,
  ``skills: 3 updated, 1 new``), plus ``written:`` / ``backup:``
  confirmations on apply.
* ``--verbose`` — adds the YAML diff, per-skill detail, per-env
  ``new:``/``preserved:`` lines, source/target banners, and the
  per-phase section headers.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from talaria.sync.result import (
    AuthTokensPhaseResult,
    ConfigPhaseResult,
    FilePhaseResult,
    PhaseResult,
    SkillsPhaseResult,
    SyncReport,
)

# ANSI colour helpers. TTY-detection is intentionally skipped — the
# standalone tool always coloured, and the operator piping through
# ``less -R`` or ``tee`` is happy with raw escape codes. Tests assert
# against the uncoloured text so colour is purely cosmetic.
_RESET = "\033[0m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"

_STATUS_PREFIXES: dict[str, tuple[str, str]] = {
    "in_sync": (f"{_GREEN}ok{_RESET}", "✓"),
    "updated": (f"{_YELLOW}updated{_RESET}", "↻"),
    "new": (f"{_CYAN}new{_RESET}", "+"),
    "skipped": (f"{_CYAN}skipped{_RESET}", "·"),
    "error": (f"{_RED}error{_RESET}", "✗"),
}


def render_human(report: SyncReport, *, verbose: bool = False) -> tuple[int, str]:
    """Render *report* as a human-readable string and return ``(exit_code, text)``.

    Exit code follows talaria's CLI contract: ``0`` on success
    (sync ran without errors), ``2`` on tool error. The ``1`` (alert
    fired) exit code is unused by sync — there is no signal to fire.
    """
    if not report.ok:
        first_error = _first_error(report)
        body = "\n".join([
            f"{_BOLD}talaria sync{_RESET}: {_RED}FAILED{_RESET}",
            f"  source: {report.source}",
            f"  target: {report.target}",
            "",
            first_error or "unknown error",
        ])
        return 2, body

    lines: list[str] = []
    lines.append(f"{_BOLD}talaria sync{_RESET}: {report.source} → {report.target}")
    if not report.apply:
        lines.append(f"  {_YELLOW}(dry run — no changes written){_RESET}")

    for phase_result in _iter_phases(report):
        if phase_result is None:
            continue
        section = _render_phase_section(phase_result, verbose=verbose)
        if section:
            lines.append("")
            lines.append(section)

    summary = _render_summary(report)
    if summary:
        lines.append("")
        lines.append(summary)

    return 0, "\n".join(lines) + "\n"


def render_json(report: SyncReport) -> str:
    """Render *report* as a JSON document.

    Path objects become strings; dataclasses become dicts. Stable
    shape across runs so cron consumers can rely on the keys.
    """
    return json.dumps(_report_to_dict(report), indent=2, default=str) + "\n"


def _report_to_dict(report: SyncReport) -> dict[str, Any]:
    """Convert *report* and every contained phase to a JSON-safe dict.

    Helper for :func:`render_json`. Recursively stringifies
    :class:`~pathlib.Path` values via ``default=str`` in
    :func:`json.dumps`.
    """
    payload: dict[str, Any] = {
        "source": report.source,
        "target": report.target,
        "apply": report.apply,
        "ok": report.ok,
        "any_writes": report.any_writes,
    }
    for phase_name in ("config", "soul", "skills", "env", "context_cache", "auth_tokens", "mcp_serve"):
        phase = getattr(report, phase_name)
        if phase is None:
            payload[phase_name] = None
            continue
        d = asdict(phase)
        d["target_path"] = str(d["target_path"]) if d.get("target_path") else None
        if d.get("backup_path"):
            d["backup_path"] = str(d["backup_path"])
        payload[phase_name] = d
    if report.error:
        payload["error"] = report.error
    return payload


def _iter_phases(report: SyncReport) -> list[PhaseResult | None]:
    """Return phase results in the canonical display order."""
    return [
        report.config,
        report.soul,
        report.skills,
        report.env,
        report.context_cache,
        report.auth_tokens,
        report.mcp_serve,
    ]


def _first_error(report: SyncReport) -> str | None:
    """Return the first error message across all phases."""
    if report.error:
        return report.error
    for phase in _iter_phases(report):
        if phase is not None and phase.status == "error":
            return "\n".join(phase.logs) or "phase reported status=error"
    return None


def _render_phase_section(result: PhaseResult, *, verbose: bool) -> str:
    """Format a single phase result as a labelled section."""
    status_label, glyph = _STATUS_PREFIXES.get(result.status, (result.status, "?"))
    header = f"  {glyph} {result.phase}: {status_label}"
    lines: list[str] = [header]

    if isinstance(result, ConfigPhaseResult):
        mode_bits = []
        if result.mode and result.mode != "identity":
            mode_bits.append(f"mode={result.mode}")
        if result.mcp_serve_name:
            mode_bits.append(f"mcp_serve={result.mcp_serve_name}")
        if mode_bits:
            lines.append(f"    [{', '.join(mode_bits)}]")

    if isinstance(result, SkillsPhaseResult):
        lines.append(
            f"    {result.copied} updated, "
            f"{result.new_count} new, {result.skipped} in sync"
        )
        if verbose and result.skills_detail:
            for detail in result.skills_detail:
                lines.append(f"    {detail}")

    if isinstance(result, AuthTokensPhaseResult):
        parts = []
        if result.updated_providers:
            parts.append(f"{len(result.updated_providers)} updated")
        if result.new_providers:
            parts.append(f"{len(result.new_providers)} new")
        if parts:
            lines.append(f"    {', '.join(parts)} provider(s)")
        if verbose and result.source_profiles:
            lines.append(f"    sources: {', '.join(result.source_profiles)}")

    if isinstance(result, FilePhaseResult) and verbose:
        for key in result.new_vars:
            lines.append(f"    new var: {key}")
        for key in result.preserved_vars:
            lines.append(f"    preserved: {key}")
        for key in result.new_keys:
            lines.append(f"    new key: {key}")
        for key in result.updated_keys:
            lines.append(f"    updated key: {key}")

    if isinstance(result, ConfigPhaseResult) and verbose and result.diff_lines:
        lines.append("    diff:")
        for dline in result.diff_lines:
            colour = _RESET
            if dline.startswith("+++"):
                colour = _GREEN
            elif dline.startswith("---"):
                colour = _RED
            elif dline.startswith("+"):
                colour = _GREEN
            elif dline.startswith("-"):
                colour = _RED
            elif dline.startswith("@@"):
                colour = _CYAN
            lines.append(f"      {colour}{dline}{_RESET}")

    # Write-confirmations always show (the operator opted in with
    # --apply). Detail lines show only in verbose mode.
    for log in result.logs:
        stripped = log.strip()
        if stripped.startswith("written:") or stripped.startswith("backup:"):
            lines.append(f"    {log.strip()}")
        elif verbose:
            lines.append(f"    {log.strip()}")

    return "\n".join(lines)


def _render_summary(report: SyncReport) -> str:
    """One-line summary of what changed."""
    bits = []
    if report.config and report.config.write_confirmed:
        bits.append("config.yaml")
    if report.soul and report.soul.write_confirmed:
        bits.append("SOUL.md")
    if report.skills and report.skills.write_confirmed:
        bits.append("skills/")
    if report.env and report.env.write_confirmed:
        bits.append(".env")
    if report.context_cache and report.context_cache.write_confirmed:
        bits.append("context_length_cache.yaml")
    if report.auth_tokens and report.auth_tokens.write_confirmed:
        bits.append("auth.json")
    if report.mcp_serve and report.mcp_serve.write_confirmed:
        bits.append("mcp_servers entry")

    if not bits:
        return f"  {_CYAN}no changes needed{_RESET}"
    if not report.apply:
        return f"  {_YELLOW}dry run — would write: {', '.join(bits)}{_RESET}"
    return f"  {_GREEN}wrote: {', '.join(bits)}{_RESET}"


def print_error(message: str) -> None:
    """Print an error message to stderr (bypasses the report renderer).

    Used for argument-validation failures and profile-resolution
    errors that happen before the report can be assembled.
    """
    print(f"{_BOLD}talaria sync{_RESET}: {_RED}error{_RESET}: {message}", file=sys.stderr)