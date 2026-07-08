"""Refresh and reshape gateway model catalogs into a Hermes manifest.

Ports the *transform* half of ``~/.local/bin/refresh-kilocode-catalog.sh``
into the Talaria feature system. The bash script remains the cron-driven
fetch+lock+idempotency orchestrator; Talaria owns the reshape step that
turns the OpenAI-style ``{data: [...]}`` response into the Hermes
manifest schema (``{providers: {<provider>: {models: [...]}}}``) and
writes it atomically to the profile-agnostic cache.

By design this feature is *profile-agnostic* — every Hermes profile points
at the same catalog cache. Do not add ``--profile`` handling here.

Exit semantics (per the project-wide contract):

* ``0`` — refreshed, skipped-because-fresh, or reshape succeeded.
* ``2`` — tool error: missing credential, HTTP failure, parse failure,
  write failure.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from talaria.paths import ResolvedPaths

@dataclass(frozen=True)
class GatewayConfig:
    """Catalog settings for one gateway-backed Hermes provider."""

    gateway: str
    provider_id: str
    display_name: str
    source_url: str
    cache_filename: str
    credential_env: str
    source_label: str
    note: str


GATEWAYS: dict[str, GatewayConfig] = {
    "kilocode": GatewayConfig(
        gateway="kilocode",
        provider_id="kilocode",
        display_name="Kilo Code",
        source_url="https://api.kilo.ai/api/gateway/models",
        cache_filename="kilocode_catalog.json",
        credential_env="KILOCODE_API_KEY",
        source_label="kilocode-gateway-api",
        note="Live catalog from api.kilo.ai. Schema normalized to Hermes manifest shape.",
    ),
}

DEFAULT_GATEWAY = "kilocode"

#: Default catalog endpoint for callers that do not need multi-gateway selection.
SOURCE_URL = GATEWAYS[DEFAULT_GATEWAY].source_url

def default_cache_path(gateway: str = DEFAULT_GATEWAY) -> Path:
    """Return the default manifest cache path for *gateway*."""
    try:
        config = GATEWAYS[gateway]
    except KeyError as exc:
        supported = ", ".join(sorted(GATEWAYS))
        raise ValueError(f"unsupported gateway: {gateway!r} (supported: {supported})") from exc
    return Path(os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")) / config.cache_filename


DEFAULT_CACHE_PATH = default_cache_path(DEFAULT_GATEWAY)

#: Idempotency window. The bash script skips a refetch if the cache is
#: younger than 6 hours; Talaria preserves that behaviour so the two
#: remain drop-in compatible.
MAX_AGE_SECONDS = 6 * 60 * 60  # 6h

#: HTTP timeout for the catalog fetch (seconds).
HTTP_TIMEOUT = 30

#: Manifest schema version. Bump when the reshape changes shape.
MANIFEST_VERSION = 1

#: Provider identifier written into the manifest's ``providers`` map.
PROVIDER_ID = GATEWAYS[DEFAULT_GATEWAY].provider_id

#: Free-model detection cap. Any model whose per-million prompt AND
#: completion prices both round to 0 is reported as free.
FREE_PRICE_TOLERANCE = 0.0


# ---------- Credential discovery ----------
def gateway_config(gateway: str) -> GatewayConfig:
    """Return the configured gateway, or raise ``CatalogError`` for unknown names."""
    try:
        return GATEWAYS[gateway]
    except KeyError as exc:
        supported = ", ".join(sorted(GATEWAYS))
        raise CatalogError(f"unsupported gateway: {gateway!r} (supported: {supported})", kind="config") from exc


def _discover_api_key(gateway: str = DEFAULT_GATEWAY) -> str | None:
    """Return the API key for *gateway* from the environment.

    Mirrors the bash script's fallback: provider-specific env var first,
    then ``~/.hermes/.env`` (read but never returned in error messages).
    """
    config = gateway_config(gateway)
    key = os.environ.get(config.credential_env)
    if key:
        return key
    env_file = Path.home() / ".hermes" / ".env"
    if not env_file.exists():
        return None
    try:
        for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # Match only the exact variable name; do NOT match prefixes
            # like KILOCODE_API_KEY_OTHER which would silently win.
            if line.startswith(f"{config.credential_env}=") or line.startswith(f"export {config.credential_env}="):
                stripped = line.split("=", 1)[1].strip().strip('"').strip("'")
                if stripped:
                    return stripped
    except OSError:
        return None
    return None


# ---------- Reshape ----------
def _reshape_model(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one upstream model dict into the manifest shape.

    Returns ``None`` for entries that have no ``id`` — the upstream API
    occasionally returns blank rows that must be skipped, not failed on.
    """
    mid = (raw.get("id") or "").strip()
    if not mid:
        return None

    pricing = raw.get("pricing") or {}
    try:
        p_in = float(pricing.get("prompt") or 0)
    except (TypeError, ValueError):
        p_in = 0.0
    try:
        p_out = float(pricing.get("completion") or 0)
    except (TypeError, ValueError):
        p_out = 0.0

    arch = raw.get("architecture") or {}
    is_free = bool(raw.get("isFree")) or (
        p_in <= FREE_PRICE_TOLERANCE and p_out <= FREE_PRICE_TOLERANCE
    )

    return {
        "id": mid,
        "description": (raw.get("description") or raw.get("name") or "")[:200],
        "context_length": int(raw.get("context_length") or 0),
        "is_free": is_free,
        "input_modalities": list(arch.get("input_modalities") or []),
        "output_modalities": list(arch.get("output_modalities") or []),
        "pricing": {
            "prompt_per_million": p_in * 1_000_000,
            "completion_per_million": p_out * 1_000_000,
        },
    }


def _build_manifest(payload: dict[str, Any], *, gateway: str = DEFAULT_GATEWAY) -> dict[str, Any]:
    """Convert the upstream ``{data: [...]}`` payload into the Hermes manifest."""
    config = gateway_config(gateway)
    if not isinstance(payload, dict) or "data" not in payload or not isinstance(payload["data"], list):
        raise ValueError("unexpected upstream response shape: expected {data: [...]}")
    models: list[dict[str, Any]] = []
    for raw in payload["data"]:
        if not isinstance(raw, dict):
            continue
        model = _reshape_model(raw)
        if model is not None:
            models.append(model)
    models.sort(key=lambda m: (not m["is_free"], m["pricing"]["prompt_per_million"], m["id"]))
    return {
        "version": MANIFEST_VERSION,
        "source": config.source_label,
        "source_url": config.source_url,
        "providers": {
            config.provider_id: {
                "metadata": {
                    "display_name": config.display_name,
                    "note": config.note,
                },
                "models": models,
            }
        },
    }


def reshape_catalog(src: Path, dst: Path, *, gateway: str = DEFAULT_GATEWAY) -> dict[str, Any]:
    """Read ``src``, reshape, and write the manifest to ``dst`` atomically.

    The write goes through a sibling temp file and is then ``os.replace``-d
    onto ``dst`` so a concurrent reader never sees a half-written file.

    Returns the manifest dict that was written.
    """
    src = Path(src)
    dst = Path(dst)
    with src.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    manifest = _build_manifest(payload, gateway=gateway)

    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=dst.name + ".", suffix=".tmp", dir=dst.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        os.replace(tmp_path, dst)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return manifest


# ---------- Fetch ----------
def fetch_catalog(
    src_url: str = SOURCE_URL,
    *,
    timeout: int = HTTP_TIMEOUT,
    gateway: str = DEFAULT_GATEWAY,
) -> tuple[int, Path]:
    """Fetch the catalog into a temp file. Returns ``(http_code, tmp_path)``.

    The caller owns the temp file and is responsible for unlinking it
    after the reshape succeeds. A non-200 ``http_code`` is reported but
    the function still returns the path so callers can inspect any error
    body the server sent.
    """
    config = gateway_config(gateway)
    api_key = _discover_api_key(gateway)
    if not api_key:
        raise CatalogError("missing credential", kind="auth")

    fd, tmp_name = tempfile.mkstemp(prefix=f"{config.provider_id}_catalog.", suffix=".json")
    tmp_path = Path(tmp_name)

    req = urllib.request.Request(
        src_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=timeout) as resp:
            http_code = getattr(resp, "status", None) or resp.getcode()
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
            return http_code, tmp_path
    except urllib.error.HTTPError as exc:
        # Drain the error body into the temp file for diagnostics, but
        # report the actual HTTP status to the caller.
        try:
            tmp_path.write_bytes(exc.read() or b"")
        except OSError:
            pass
        return exc.code, tmp_path
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise CatalogError(f"network error: {exc}", kind="network") from exc


# ---------- Cache freshness ----------
def cache_age_seconds(path: Path) -> int | None:
    """Return the cache file's age in seconds, or ``None`` if it can't be read.

    Any stat failure (missing file, parent path blocked by a regular
    file, permission denied) is reported as ``None`` so callers can
    treat the cache as absent and fall through to a real refresh.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return int(max(0, time.time() - mtime))


def is_cache_fresh(path: Path, *, max_age_seconds: int = MAX_AGE_SECONDS) -> bool:
    """True iff the cache exists and is younger than ``max_age_seconds``."""
    age = cache_age_seconds(path)
    return age is not None and age < max_age_seconds


# ---------- Orchestration ----------
class CatalogError(RuntimeError):
    """Raised on missing credentials, network failure, parse failure, or write failure.

    ``kind`` is one of ``"auth" | "network" | "parse" | "write"`` so the
    renderer can show a one-line, actionable hint without leaking internals.
    """

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def run(
    paths: ResolvedPaths,
    *,
    dst: Path | None = None,
    src_url: str | None = None,
    max_age_seconds: int = MAX_AGE_SECONDS,
    force: bool = False,
    gateway: str = DEFAULT_GATEWAY,
) -> dict[str, Any]:
    """Fetch (when needed) and reshape the catalog.

    ``paths`` is accepted for symmetry with :mod:`talaria.hermos.doctor`
    but is unused — this feature is profile-agnostic by design.

    Returned report::

        {
          "ok": bool,
          "skipped": bool,            # True when cache was fresh
          "reason": str | None,       # "fresh" | "auth" | "network" | "parse" | "write"
          "http_code": int | None,
          "cache_path": str,
          "source_url": str,
          "model_count": int,
          "manifest": dict | None,
        }
    """
    try:
        config = gateway_config(gateway)
    except CatalogError as exc:
        return {
            "ok": False,
            "skipped": False,
            "reason": exc.kind,
            "http_code": None,
            "gateway": gateway,
            "provider_id": None,
            "credential_env": None,
            "cache_path": str(dst) if dst is not None else None,
            "source_url": src_url,
            "model_count": 0,
            "manifest": None,
        }
    dst = Path(dst) if dst is not None else default_cache_path(gateway)
    src_url = src_url or config.source_url
    report: dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "reason": None,
        "http_code": None,
        "gateway": config.gateway,
        "provider_id": config.provider_id,
        "credential_env": config.credential_env,
        "cache_path": str(dst),
        "source_url": src_url,
        "model_count": 0,
        "manifest": None,
    }

    if not force and is_cache_fresh(dst, max_age_seconds=max_age_seconds):
        report["ok"] = True
        report["skipped"] = True
        report["reason"] = "fresh"
        try:
            with dst.open("r", encoding="utf-8") as fh:
                report["manifest"] = json.load(fh)
                report["model_count"] = len(
                    report["manifest"].get("providers", {})
                    .get(config.provider_id, {})
                    .get("models", [])
                )
        except (OSError, ValueError):
            # Stale or unreadable cache — fall through to a real refresh
            # rather than reporting a misleading success.
            report["skipped"] = False
            report["reason"] = None
            report["manifest"] = None
            report["model_count"] = 0
        if report["skipped"]:
            return report

    try:
        http_code, tmp_path = fetch_catalog(src_url, gateway=gateway)
    except CatalogError as exc:
        report["reason"] = exc.kind
        return report

    report["http_code"] = http_code
    if http_code != 200:
        report["reason"] = "network"
        tmp_path.unlink(missing_ok=True)
        return report

    try:
        manifest = reshape_catalog(tmp_path, dst, gateway=gateway)
    except ValueError as exc:
        report["reason"] = "parse"
        return report
    except OSError as exc:
        report["reason"] = "write"
        return report
    finally:
        tmp_path.unlink(missing_ok=True)

    report["ok"] = True
    report["reason"] = "refreshed"
    report["manifest"] = manifest
    report["model_count"] = len(manifest["providers"][config.provider_id]["models"])
    return report


# ---------- Human renderer ----------
def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format *report* for the terminal. Returns ``(exit_code, text)``.

    Exit codes follow the project-wide contract: ``0`` for success/clean,
    ``2`` for tool errors. The "fired" exit code (1) is reserved for
    features that emit alerts and does not apply here.
    """
    lines: list[str] = []
    provider = report.get("provider_id") or report.get("gateway") or "unknown"
    lines.append(f"{provider} catalog refresh")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"gateway:  {report.get('gateway')}")
    lines.append(f"provider: {report.get('provider_id')}")
    lines.append(f"cache:    {report['cache_path']}")
    lines.append(f"source:   {report['source_url']}")
    lines.append(f"http:     {report['http_code']}")
    lines.append("")

    if report["ok"] and report["skipped"]:
        lines.append(f"Cache is fresh — skipped refetch ({report['model_count']} models cached).")
        lines.append("Use --force to refetch anyway.")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: clean — cache within freshness window.")
        return 0, "\n".join(lines)

    if not report["ok"]:
        lines.append(f"ERROR: refresh failed ({report['reason']}).")
        hint = {
            "auth": f"Set {report.get('credential_env') or 'KILOCODE_API_KEY'} in the environment or ~/.hermes/.env.",
            "config": "Choose a supported --gateway value.",
            "network": "Check gateway connectivity and retry.",
            "parse": "Upstream response shape changed; update _build_manifest.",
            "write": "Check write permissions on the cache directory.",
        }.get(report["reason"] or "", "")
        if hint:
            lines.append(f"  hint: {hint}")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: tool error — refresh did not complete.")
        return 2, "\n".join(lines)

    lines.append(f"Refreshed: {report['model_count']} models in cache.")
    lines.append("")
    lines.append("=" * 60)
    lines.append("VERDICT: clean — catalog refreshed successfully.")
    return 0, "\n".join(lines)


# ---------- Resolution-descriptor helper ----------
def show_resolution(
    paths: ResolvedPaths,
    *,
    dst: Path | None = None,
    gateway: str = DEFAULT_GATEWAY,
    src_url: str | None = None,
) -> str:
    """Pretty-print the selected gateway and cache location, for debugging."""
    import json as _json
    config = gateway_config(gateway)
    dst = Path(dst) if dst is not None else default_cache_path(gateway)
    return _json.dumps(
        {
            "profile": paths.profile,  # unused but kept for symmetry with doctor
            "gateway": config.gateway,
            "provider_id": config.provider_id,
            "credential_env": config.credential_env,
            "cache_path": str(dst),
            "source_url": src_url or config.source_url,
            "max_age_seconds": MAX_AGE_SECONDS,
            "supported_gateways": sorted(GATEWAYS),
        },
        indent=2,
    )
