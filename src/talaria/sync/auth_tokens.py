"""Sync OAuth tokens in ``auth.json`` between profiles.

The Hermes runtime stores OAuth credentials in ``auth.json`` at the
profile root. When the operator runs multiple profiles (e.g.
``hermes-vc`` and ``hermes-legal``) each profile gets its own
``auth.json`` — but the underlying OAuth tokens are shared: there
is one ``nous`` account, one ``openai-codex`` account, etc.

When a token refresh happens in profile A, profile B still holds
the stale token until its own runtime hits the API and refreshes.
This phase closes that gap: it scans every profile's ``auth.json``
for the most recently updated token per provider, then writes that
newest token into the target profile's ``auth.json``.

Recency is determined per provider via a timestamp extracted from
the provider block. The field checked is (in priority order):

* ``last_refresh`` (openai-codex style)
* ``obtained_at`` (nous style)
* ``updated_at`` at the top level of ``auth.json`` (fallback)

If none are present the provider block is treated as having no
timestamp and is never selected as newest (it can still be the
target's existing value that gets preserved).

The merge is **token-only**: the target's non-token fields
(``active_provider``, ``credential_pool`` entries without raw
tokens, etc.) are preserved. Only the ``providers`` dict is
overwritten, and only for providers where the source has a newer
token.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from talaria.sync.paths import SyncProfile, list_profiles
from talaria.sync.result import AuthTokensPhaseResult
from talaria.sync.writer import write_with_backup


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime.

    Returns ``None`` if *value* is falsy or unparseable. Handles both
    ``Z`` suffix and explicit offsets.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _provider_timestamp(provider_block: dict) -> datetime | None:
    """Extract the best available timestamp from a provider block.

    Checks ``last_refresh`` then ``obtained_at`` — the two fields
    Hermes writes when it refreshes a token. Returns ``None`` when
    neither is present or parseable.
    """
    for key in ("last_refresh", "obtained_at"):
        ts = _parse_timestamp(provider_block.get(key))
        if ts is not None:
            return ts
    return None


def _load_auth(path: Path) -> dict:
    """Load and return ``auth.json`` as a dict. Returns ``{}`` on missing/unparseable."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _find_newest_tokens(root: Path) -> dict[str, tuple[datetime, dict, str]]:
    """Scan every profile under *root* for the newest token per provider.

    Returns a mapping ``provider_name -> (timestamp, provider_block,
    source_profile_name)``. Only providers with a parseable timestamp
    are included; providers without timestamps are invisible to the
    newest-token selection.
    """
    # Collect all profile auth.json paths (default + named profiles).
    candidates: list[tuple[str, Path]] = []
    default_auth = root / "auth.json"
    if default_auth.exists():
        candidates.append(("default", default_auth))
    for name in list_profiles(root=root):
        if name == "default":
            continue
        auth = root / "profiles" / name / "auth.json"
        if auth.exists():
            candidates.append((name, auth))

    newest: dict[str, tuple[datetime, dict, str]] = {}
    for profile_name, auth_path in candidates:
        data = _load_auth(auth_path)
        providers = data.get("providers", {})
        if not isinstance(providers, dict):
            continue
        for prov_name, prov_block in providers.items():
            if not isinstance(prov_block, dict):
                continue
            ts = _provider_timestamp(prov_block)
            if ts is None:
                continue
            current = newest.get(prov_name)
            if current is None or ts > current[0]:
                newest[prov_name] = (ts, prov_block, profile_name)
    return newest


def sync_auth_tokens(
    source: SyncProfile,
    target: SyncProfile,
    *,
    apply: bool = True,
    no_backup: bool = False,
    root: Path | None = None,
) -> AuthTokensPhaseResult:
    """Propagate the newest OAuth tokens across all profiles into *target*.

    Scans every profile's ``auth.json`` under the Hermes root (not
    just *source*) to find the most recently refreshed token per
    provider. Writes those tokens into *target*'s ``auth.json``,
    preserving the target's non-provider fields.

    Parameters
    ----------
    source, target:
        Resolved profiles. The *source* is not used directly — the
        phase scans all profiles. It is accepted to match the
        ``sync_*`` signature shared by every phase.
    apply:
        When ``True`` (default), writes the merged ``auth.json`` to
        the target. When ``False``, reports what would change.
    no_backup:
        Skip the ``.bak`` backup before overwriting the target's
        ``auth.json``.
    root:
        Override the Hermes root for scanning. Tests pass a
        ``tmp_path``; production code uses the resolved root.
    """
    result = AuthTokensPhaseResult(
        phase="auth_tokens",
        status="in_sync",
        target_path=target.auth_file,
    )

    # Derive the Hermes root from the target profile so the scan
    # covers every sibling profile. For named profiles the root is
    # two levels up (profiles/<name> -> profiles -> hermes_root);
    # for the default profile target.root IS the hermes_root.
    if root is not None:
        scan_root = root
    elif target.is_default:
        scan_root = target.root
    else:
        scan_root = target.root.parent.parent

    newest = _find_newest_tokens(scan_root)
    if not newest:
        result.status = "skipped"
        result.logs.append("  skip: no auth.json with tokens found in any profile")
        return result

    target_data = _load_auth(target.auth_file)
    target_providers = target_data.get("providers", {})
    if not isinstance(target_providers, dict):
        target_providers = {}

    updated_providers: list[str] = []
    new_providers: list[str] = []

    for prov_name, (ts, prov_block, src_profile) in sorted(newest.items()):
        existing = target_providers.get(prov_name)
        if existing == prov_block:
            continue
        if existing is None:
            new_providers.append(prov_name)
        else:
            updated_providers.append(prov_name)
        result.logs.append(
            f"  {prov_name}: from {src_profile} "
            f"({ts.isoformat()})"
        )

    result.updated_providers = updated_providers
    result.new_providers = new_providers
    result.source_profiles = sorted({src for _, _, src in newest.values()})

    if not updated_providers and not new_providers:
        result.logs.append("  auth.json: all tokens already current")
        return result

    result.status = "updated" if updated_providers else "new"

    if not apply:
        result.logs.append("  (dry run)")
        return result

    # Merge: start from target_data, overwrite providers with newest.
    merged = dict(target_data)
    merged_providers = dict(target_providers)
    for prov_name, (_, prov_block, _) in newest.items():
        merged_providers[prov_name] = prov_block
    merged["providers"] = merged_providers

    payload = json.dumps(merged, indent=2, default=str) + "\n"
    outcome = write_with_backup(target.auth_file, payload, no_backup=no_backup)
    result.write_confirmed = True
    result.backup_path = outcome.backup
    if outcome.backup:
        result.logs.append(f"  backup: {outcome.backup}")
    result.logs.append(f"  written: {outcome.written}")
    return result
