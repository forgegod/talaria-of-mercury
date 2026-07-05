"""Model benchmark — `talaria hermes benchmark`.

Reports per-model health, cost, latency, and capability data for every
model the profile routes through. Two data sources are combined:

1. **state.db (passive).** Recent sessions are aggregated per
   ``(model, provider)`` pair: call count, average input/output/reasoning
   tokens, average cost, first-response latency (from the messages
   table), and the configured reasoning level (from ``model_config``).
2. **Smoke call (active, cached).** When the cache is older than the
   TTL (default 30 min), one ``hermes chat -q`` JSON-smoke call is made
   per discovered model. The smoke result records whether the model
   returned parseable JSON within the timeout, plus the wall-clock
   latency of the fresh call. The result is cached to
   ``$XDG_CACHE_HOME/talaria/benchmark-cache.json`` so repeated
   ``talaria hermes benchmark`` invocations within the TTL window do
   not burn tokens.

Model **capabilities** (reasoning, tool-call, vision/attachment,
context/output limits, per-token cost) are enriched from the Hermes
``models_dev_cache.json`` when available. The provider prefix in the
operator's config (e.g. ``zai-coding/``) may differ from the upstream
models.dev id (e.g. ``z-ai/``); a suffix match on the model slug
handles this.

The report is read-only. No writes to ``state.db``, ``config.yaml``,
or any Hermes artefact.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from talaria.paths import ResolvedPaths

#: Default look-back window for state.db aggregation.
DEFAULT_LOOKBACK_DAYS = 7

#: Default cache TTL in seconds (30 minutes). Smoke calls within this
#: window reuse the cached result; the next call after expiry triggers
#: a fresh smoke call per model.
DEFAULT_TTL_SECONDS = 30 * 60

#: Per-smoke-call timeout (seconds). Individual model checks should be
#: fast because the prompt is small.
SMOKE_TIMEOUT = 90

#: Cache file path under ``$XDG_CACHE_HOME``. Profile-scoped: the
#: profile name is part of the filename so multiple profiles don't
#: collide.
def _default_cache_path(profile: str) -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(xdg) / "talaria" / f"benchmark-cache-{profile}.json"

#: The canonical smoke prompt. Every model gets the same prompt so the
#: pass/fail criterion is uniform: did the model return parseable JSON
#: within the timeout?
_SMOKE_PROMPT = (
    'Return ONLY this JSON object with no prose: {"ok": true, "model": "bench"}'
)


# ---------------- Config helpers (reused from diagnose_llm) ----------------

def _resolve_config_path(paths: ResolvedPaths) -> Path:
    if paths.profile == "default":
        return paths.hermes_root / "config.yaml"
    return paths.hermes_root / "profiles" / paths.profile / "config.yaml"


def _load_config(paths: ResolvedPaths) -> dict[str, Any]:
    config_path = _resolve_config_path(paths)
    try:
        with open(config_path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


# ---------------- Model discovery (same algorithm as the test suite) ----------------

@dataclass(frozen=True)
class ModelTarget:
    """A unique ``(model, provider)`` pair with provenance labels."""

    model: str
    provider: str
    sources: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        raw = f"{self.model}--{self.provider}"
        return raw.replace("/", "_").replace(" ", "_")


def discover_model_targets(cfg: dict[str, Any]) -> list[ModelTarget]:
    """Discover every unique ``(model, provider)`` pair in *cfg*.

    Walks three config sections:

    * ``model.default`` + provider (top-level ``provider`` or
      ``model.provider``)
    * ``model.aliases`` (every entry; provider resolved per-alias)
    * ``auxiliary.<usecase>.model`` + ``auxiliary.<usecase>.provider``
    """
    targets: dict[str, ModelTarget] = {}

    def _register(model: str, provider: str, source: str) -> None:
        if not model or not str(model).strip():
            return
        model = str(model)
        provider = str(provider or "auto")
        key = f"{model}::{provider}"
        if key in targets:
            existing = targets[key]
            if source not in existing.sources:
                targets[key] = ModelTarget(
                    model, provider,
                    existing.sources + (source,),
                )
        else:
            targets[key] = ModelTarget(model, provider, (source,))

    model_block = cfg.get("model") or {}
    # Top-level ``provider`` takes precedence (legacy/override); fall
    # back to ``model.provider`` (the canonical Hermes location) when
    # the top-level key is absent.
    top_provider = cfg.get("provider") or model_block.get("provider") or "auto"

    default_model = model_block.get("default")
    if default_model:
        _register(str(default_model), top_provider, "model.default")

    aliases = (cfg.get("model") or {}).get("aliases") or {}
    auxiliary = cfg.get("auxiliary") or {}

    for alias, model in aliases.items():
        if not model:
            continue
        if alias.startswith("_"):
            usecase = alias[1:]
            aux_block = auxiliary.get(usecase) or {}
            provider = aux_block.get("provider") or top_provider
        else:
            provider = top_provider
        _register(str(model), provider, f"model.aliases.{alias}")

    for usecase, block in auxiliary.items():
        if not isinstance(block, dict):
            continue
        model = block.get("model")
        if model:
            provider = block.get("provider") or top_provider
            _register(str(model), provider, f"auxiliary.{usecase}.model")

    return list(targets.values())


# ---------------- State.db aggregation ----------------

def _open_state_db_readonly(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        uri = f"file:{path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


def _aggregate_state_db(
    con: sqlite3.Connection,
    since_ts: float,
) -> dict[str, dict[str, Any]]:
    """Aggregate per-model stats from the sessions table.

    Returns ``{model_id: {stats}}`` keyed by the model string as
    stored in ``sessions.model``. ``model_id`` is the raw column
    value — provider is not part of the sessions table, so provider
    association happens in the caller via :class:`ModelTarget`.
    """
    rows = con.execute(
        """
        SELECT
            model,
            COUNT(*)                           AS call_count,
            COALESCE(ROUND(AVG(input_tokens)), 0)   AS avg_input_tokens,
            COALESCE(ROUND(AVG(output_tokens)), 0)  AS avg_output_tokens,
            COALESCE(ROUND(AVG(cache_read_tokens)), 0)  AS avg_cache_read,
            COALESCE(ROUND(AVG(cache_write_tokens)), 0) AS avg_cache_write,
            COALESCE(ROUND(AVG(reasoning_tokens)), 0) AS avg_reasoning_tokens,
            COALESCE(ROUND(SUM(actual_cost_usd), 6), 0)  AS total_cost_usd,
            COALESCE(ROUND(MAX(actual_cost_usd), 6), 0)  AS max_cost_usd,
            COALESCE(ROUND(AVG(actual_cost_usd), 6), 0) AS avg_cost_usd
        FROM sessions
        WHERE started_at >= ?
          AND model IS NOT NULL
        GROUP BY model
        """,
        (since_ts,),
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for r in rows:
        result[r["model"]] = {k: r[k] for k in r.keys()}
    return result


def _reasoning_config_for_model(
    con: sqlite3.Connection,
    since_ts: float,
) -> dict[str, dict[str, Any]]:
    """Extract the most recent ``model_config`` reasoning settings per model.

    ``model_config`` is a JSON string stored on the sessions table. It
    carries ``reasoning_config.effort`` (the "reasoning level"),
    ``max_iterations``, and ``max_tokens``.
    """
    rows = con.execute(
        """
        SELECT model, model_config
        FROM sessions
        WHERE started_at >= ?
          AND model IS NOT NULL
          AND model_config IS NOT NULL
        ORDER BY started_at DESC
        """,
        (since_ts,),
    ).fetchall()
    by_model: dict[str, dict[str, Any]] = {}
    for r in rows:
        model = r["model"]
        if model in by_model:
            continue
        try:
            cfg = json.loads(r["model_config"])
        except (json.JSONDecodeError, TypeError):
            continue
        by_model[model] = cfg
    return by_model


def _first_response_latency(
    con: sqlite3.Connection,
    since_ts: float,
) -> dict[str, float]:
    """Average first-response latency per model from the messages table.

    Measures the time from the session's first user message to the
    *next* assistant message (the earliest assistant reply after the
    first user prompt). This is the "time to first token" proxy.
    """
    rows = con.execute(
        """
        WITH first_user AS (
            SELECT session_id, MIN(timestamp) AS ts
            FROM messages
            WHERE role = 'user'
            GROUP BY session_id
        ),
        first_reply AS (
            SELECT m.session_id, MIN(m.timestamp) AS ts
            FROM messages m
            JOIN first_user fu ON fu.session_id = m.session_id
            WHERE m.role = 'assistant'
              AND m.timestamp > fu.ts
            GROUP BY m.session_id
        )
        SELECT s.model, fr.ts - fu.ts AS latency
        FROM sessions s
        JOIN first_user fu ON fu.session_id = s.id
        JOIN first_reply fr ON fr.session_id = s.id
        WHERE s.started_at >= ?
          AND s.model IS NOT NULL
        """,
        (since_ts,),
    ).fetchall()
    latencies: dict[str, list[float]] = {}
    for r in rows:
        latencies.setdefault(r["model"], []).append(r["latency"])
    return {
        model: round(sum(v) / len(v), 2) for model, v in latencies.items() if v
    }


# ---------------- models.dev capability enrichment ----------------

#: Hermes caches models.dev data here.
_MODELS_DEV_CACHE = Path.home() / ".hermes" / "models_dev_cache.json"

#: Provider prefix aliases: the operator's config may use a different
#: prefix than models.dev. We match on the model slug (everything
#: after the first ``/``). Some gateways prepend their own prefix
#: (e.g. kilocode uses ``kilocode/zai-coding/glm-5.2``), so we also
#: try progressively stripping prefixes.
def _slug(model_id: str) -> str:
    """Return the model slug: the last path segment after the last ``/``.

    For ``zai-coding/glm-5.2`` this returns ``glm-5.2``.
    For ``kilocode/zai-coding/glm-5.2`` this also returns ``glm-5.2``.
    """
    return model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id


def _load_models_dev() -> dict[str, dict[str, Any]]:
    """Load models.dev cache. Returns ``{model_slug: {caps}}``.

    The slug is the part after the first ``/`` in the model id. This
    normalises across provider prefix differences (e.g.
    ``zai-coding/glm-5.2`` vs ``z-ai/glm-5.2``).
    """
    if not _MODELS_DEV_CACHE.exists():
        return {}
    try:
        with open(_MODELS_DEV_CACHE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _prov_name, prov_data in data.items():
        if not isinstance(prov_data, dict):
            continue
        models = prov_data.get("models") or {}
        if isinstance(models, dict):
            for m_id, m_info in models.items():
                if not isinstance(m_info, dict):
                    continue
                slug = _slug(m_id)
                # First provider wins; later duplicates are skipped
                # (the slug is the same model, capabilities don't
                # change per provider).
                if slug not in out:
                    out[slug] = m_info
    return out


def _enrich_capabilities(
    model: str,
    models_dev: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Look up capabilities for *model* by slug match."""
    slug = _slug(model)
    info = models_dev.get(slug)
    if not info:
        return {}
    caps: dict[str, Any] = {}
    modalities = info.get("modalities") or {}
    caps["reasoning"] = bool(info.get("reasoning"))
    caps["tool_call"] = bool(info.get("tool_call"))
    caps["vision"] = bool(info.get("attachment")) or "image" in (
        modalities.get("input") or []
    )
    caps["structured_output"] = bool(info.get("structured_output"))
    limit = info.get("limit") or {}
    if limit:
        caps["context_limit"] = limit.get("context")
        caps["output_limit"] = limit.get("output")
    cost = info.get("cost") or {}
    if cost:
        caps["cost_per_mtokens"] = {
            "input": cost.get("input"),
            "output": cost.get("output"),
            "cache_read": cost.get("cache_read"),
        }
    caps["family"] = info.get("family")
    caps["name"] = info.get("name")
    return caps


# ---------------- Smoke call + cache ----------------

def _smoke_call(
    model: str,
    provider: str,
    *,
    timeout: int = SMOKE_TIMEOUT,
    runner: Callable[..., tuple[int, str, str]] | None = None,
) -> dict[str, Any]:
    """Make one ``hermes chat -q`` smoke call. Returns the result dict.

    The ``latency_s`` is the full subprocess round-trip: Python
    interpreter startup, Hermes agent initialisation (config load,
    MCP/tool/memory plugin setup), the API call, and teardown. It
    is a health-check latency, not a model-latency benchmark — use
    ``avg_first_response_latency_s`` (from state.db) for pure
    model+gateway latency.
    """
    from talaria.hermos.diagnose_llm import hermes_chat

    if runner is None:
        if shutil.which("hermes") is None:
            return {"ok": False, "error": "hermes CLI not on PATH", "latency_s": 0.0}
        runner = hermes_chat
    t0 = time.time()
    try:
        rc, stdout, stderr = runner(
            _SMOKE_PROMPT,
            model=model,
            provider=provider,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_s": round(time.time() - t0, 2),
        }
    dt = time.time() - t0
    out = stdout.strip()
    body = out.split("session_id:", 1)[-1] if "session_id:" in out else out
    ok = '"' in body and (
        body.rstrip().endswith("}") or body.rstrip().endswith("]")
    )
    return {
        "ok": ok,
        "latency_s": round(dt, 2),
        "returncode": rc,
    }


def _load_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    try:
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache_path: Path, data: dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def _cache_is_fresh(entry: dict[str, Any], ttl: int) -> bool:
    ts = entry.get("smoke_ts")
    if ts is None:
        return False
    return (time.time() - float(ts)) < ttl


# ---------------- Vision benchmark ----------------

#: Per-vision-call timeout (seconds). Vision prompts are heavier
#: than JSON smoke calls; the extra headroom covers image upload
#: and multi-token reasoning.
VISION_TIMEOUT = 120

#: Default fixture directory. Resolved relative to the repository
#: root (the parent of ``src/``), so an editable install sees the
#: checked-in images without a package-data step.
def _default_vision_dir() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "assets" / "benchmark" / "vision"
    )


#: Each entry: (image_relative_path, question, ground_truth, label).
#:
#: ``ground_truth`` is a list of entries. Each entry is either a
#: required substring (case-insensitive) or a ``|``-separated set of
#: acceptable alternatives (any one match satisfies the entry — for
#: visually-ambiguous fixtures like a stylised wing glyph that may
#: read as "wings", "winged", "sandal", or "butterfly" depending on
#: the model).
VISION_FIXTURES: list[tuple[str, str, list[str], str]] = [
    (
        "count_grid.png",
        "Look at the image. How many circles are red? How many total "
        "circles are there? Answer with ONLY: red=N total=M",
        ["red=4", "total=10"],
        "count + colour discrimination",
    ),
    (
        "error_card.png",
        "Read the error card in the image. What is the error code and "
        "module? Answer with ONLY: code=ERR_XXXX module=name",
        ["err_4042", "agent.compression"],
        "OCR of structured error text",
    ),
    (
        "spatial_arrow.png",
        "Which box (A, B, C, or D) does the red arrow point to? "
        "Answer with ONLY: box=X",
        ["box=b"],
        "spatial reasoning + arrow direction",
    ),
    (
        "logo/logo-512.png",
        "Look at this logo image. What is the word written in it? What "
        "object or symbol is depicted on the left side? What is the "
        "dominant colour? Answer with ONLY: word=X icon=Y colour=Z",
        ["word=talaria", "wings|winged|sandal|butterfly", "gold"],
        "brand logo recognition (Talaria wordmark + winged-sandal glyph)",
    ),
]


def _vision_cache_key(model_id: str, fixture_label: str) -> str:
    """Stable cache key for one ``(model, fixture)`` vision result."""
    return f"{model_id}::vision::{fixture_label}"


def _match_vision_response(body: str, expected: list[str]) -> list[str]:
    """Return the list of *missing* expected entries (empty = all matched).

    Each entry in ``expected`` is either a required substring or a
    ``|``-separated set of acceptable alternatives. Matching is
    case-insensitive against the full response body.
    """
    low = body.lower()
    missing: list[str] = []
    for entry in expected:
        options = entry.split("|")
        if not any(opt.strip().lower() in low for opt in options):
            missing.append(entry)
    return missing


def _vision_call(
    model: str,
    provider: str,
    image_path: Path,
    question: str,
    expected: list[str],
    *,
    timeout: int = VISION_TIMEOUT,
    runner: Callable[..., tuple[int, str, str]] | None = None,
) -> dict[str, Any]:
    """Make one ``hermes chat --image ...`` vision call.

    Returns the result dict with ``ok``, ``latency_s``, ``returncode``,
    and ``missing`` (the expected substrings not found).
    """
    from talaria.hermos.diagnose_llm import hermes_chat

    if runner is None:
        if shutil.which("hermes") is None:
            return {"ok": False, "error": "hermes CLI not on PATH", "latency_s": 0.0}
        runner = hermes_chat
    t0 = time.time()
    try:
        rc, stdout, stderr = runner(
            question,
            model=model,
            provider=provider,
            timeout=timeout,
            image=str(image_path),
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_s": round(time.time() - t0, 2),
        }
    dt = time.time() - t0
    body = stdout.strip()
    body = body.split("session_id:", 1)[-1] if "session_id:" in body else body
    missing = _match_vision_response(body, expected)
    return {
        "ok": not missing,
        "latency_s": round(dt, 2),
        "returncode": rc,
        "missing": missing,
        "response_tail": body[-300:],
    }


# ---------------- Orchestrator ----------------

def run(
    paths: ResolvedPaths,
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
    ttl: int = DEFAULT_TTL_SECONDS,
    smoke: bool = True,
    smoke_runner: Callable[..., tuple[int, str, str]] | None = None,
    config_path: Path | None = None,
    cache_path: Path | None = None,
    vision: bool = True,
    vision_runner: Callable[..., tuple[int, str, str]] | None = None,
    vision_fixtures_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the benchmark and assemble a report.

    Parameters:
        paths: the resolved profile (state.db, config).
        days: look-back window for state.db aggregation (default 7).
        ttl: cache TTL in seconds for smoke/vision results (default
            1800 = 30 min). Calls within the TTL window reuse the
            cached result.
        smoke: when True (default), make fresh JSON smoke calls for
            models whose cached result is stale. ``--no-smoke``
            skips all smoke calls.
        smoke_runner: override the smoke-call runner for tests.
        config_path: explicit path to ``config.yaml`` (overrides
            the resolved-profile path).
        cache_path: explicit path to the benchmark cache file.
        vision: when True (default), run vision checks against every
            discovered model whose capabilities include vision
            (per ``models_dev_cache.json``). ``--no-vision`` skips
            all vision calls.
        vision_runner: override the vision-call runner for tests.
        vision_fixtures_dir: override the fixture-image directory.
            Defaults to ``assets/benchmark/vision/`` resolved from
            the repository root.
    """
    cfg = _load_config(paths) if config_path is None else (
        yaml.safe_load(config_path.read_text()) or {}
    )
    targets = discover_model_targets(cfg)
    models_dev = _load_models_dev()

    # State.db aggregation
    con = _open_state_db_readonly(paths.state_db)
    state_stats: dict[str, dict[str, Any]] = {}
    reasoning_cfg: dict[str, dict[str, Any]] = {}
    latencies: dict[str, float] = {}
    db_exists = con is not None
    if con is not None:
        since_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
        state_stats = _aggregate_state_db(con, since_ts)
        reasoning_cfg = _reasoning_config_for_model(con, since_ts)
        latencies = _first_response_latency(con, since_ts)
        con.close()

    # Smoke calls with cache
    c_path = cache_path or _default_cache_path(paths.profile)
    cache = _load_cache(c_path)
    now = time.time()
    smoke_results: dict[str, Any] = {}
    smoke_made = 0
    smoke_cached = 0
    smoke_skipped = 0

    for t in targets:
        entry = cache.get(t.id)
        if smoke and (entry is None or not _cache_is_fresh(entry, ttl)):
            result = _smoke_call(
                t.model, t.provider,
                runner=smoke_runner,
            )
            result["smoke_ts"] = now
            cache[t.id] = result
            smoke_results[t.id] = result
            smoke_made += 1
        elif smoke and entry is not None:
            smoke_results[t.id] = entry
            smoke_cached += 1
        else:
            smoke_skipped += 1

    if smoke_made > 0:
        _save_cache(c_path, cache)

    # Vision calls with cache (only for vision-capable models)
    v_dir = vision_fixtures_dir or _default_vision_dir()
    vision_results: dict[str, list[dict[str, Any]]] = {}
    vision_made = 0
    vision_cached = 0
    vision_models = 0
    vision_dir_found = v_dir.is_dir()

    for t in targets:
        caps = _enrich_capabilities(t.model, models_dev)
        if not caps.get("vision"):
            continue
        vision_models += 1
        model_vision: list[dict[str, Any]] = []
        for img_rel, question, expected, label in VISION_FIXTURES:
            img_path = v_dir / img_rel
            if not vision_dir_found or not img_path.exists():
                model_vision.append({
                    "fixture": label,
                    "ok": False,
                    "error": f"fixture missing: {img_path}",
                    "skipped": True,
                })
                continue
            ck = _vision_cache_key(t.id, label)
            entry = cache.get(ck)
            if vision and (entry is None or not _cache_is_fresh(entry, ttl)):
                result = _vision_call(
                    t.model, t.provider, img_path, question, expected,
                    runner=vision_runner,
                )
                result["smoke_ts"] = now
                cache[ck] = result
                model_vision.append({"fixture": label, **result})
                vision_made += 1
            elif vision and entry is not None:
                model_vision.append({"fixture": label, **entry})
                vision_cached += 1
            else:
                model_vision.append({"fixture": label, "skipped": True})
        vision_results[t.id] = model_vision

    if vision_made > 0:
        _save_cache(c_path, cache)

    # Assemble per-model report
    per_model: list[dict[str, Any]] = []
    for t in sorted(targets, key=lambda x: (x.model, x.provider)):
        stats = state_stats.get(t.model, {})
        r_cfg = reasoning_cfg.get(t.model, {})
        caps = _enrich_capabilities(t.model, models_dev)
        reasoning_level = None
        rc = r_cfg.get("reasoning_config") if isinstance(r_cfg, dict) else None
        if isinstance(rc, dict):
            reasoning_level = rc.get("effort") or (
                "on" if rc.get("enabled") else "off"
            )
        model_entry: dict[str, Any] = {
            "model": t.model,
            "provider": t.provider,
            "id": t.id,
            "sources": list(t.sources),
            "reasoning_level": reasoning_level,
            "capabilities": caps,
            "state_db": stats if stats else None,
            "avg_first_response_latency_s": latencies.get(t.model),
            "smoke": smoke_results.get(t.id),
            "vision": vision_results.get(t.id),
        }
        per_model.append(model_entry)

    return {
        "profile": paths.profile,
        "state_db": str(paths.state_db),
        "window_days": days,
        "ttl_seconds": ttl,
        "smoke_enabled": smoke,
        "smoke_calls_made": smoke_made,
        "smoke_calls_cached": smoke_cached,
        "smoke_calls_skipped": smoke_skipped,
        "vision_enabled": vision,
        "vision_models": vision_models,
        "vision_calls_made": vision_made,
        "vision_calls_cached": vision_cached,
        "vision_dir": str(v_dir),
        "vision_dir_found": vision_dir_found,
        "state_db_found": db_exists,
        "models_dev_loaded": len(models_dev) > 0,
        "per_model": per_model,
        "ok": True,
    }


# ---------------- Renderer ----------------

def _fmt_tokens(n: Any) -> str:
    if n is None or n == 0:
        return "-"
    try:
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(n)


def _fmt_cost(n: Any) -> str:
    if n is None or n == 0:
        return "-"
    try:
        return f"${float(n):.4f}"
    except (ValueError, TypeError):
        return str(n)


def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format *report* for the terminal. Returns ``(exit_code, text)``."""
    lines: list[str] = []
    lines.append("Model benchmark")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Profile:  {report['profile']}")
    lines.append(f"state.db: {report['state_db']}")
    lines.append(f"window:   last {report['window_days']} day(s)")
    lines.append(f"cache:    TTL {report['ttl_seconds'] // 60} min")
    smoke_status = (
        f"{report['smoke_calls_made']} fresh, "
        f"{report['smoke_calls_cached']} cached"
        if report["smoke_enabled"]
        else "disabled (--no-smoke)"
    )
    lines.append(f"smoke:    {smoke_status}")
    vision_status = (
        f"{report.get('vision_models', 0)} vision-capable models, "
        f"{report.get('vision_calls_made', 0)} fresh, "
        f"{report.get('vision_calls_cached', 0)} cached"
        if report.get("vision_enabled") else "disabled (--no-vision)"
    )
    lines.append(f"vision:   {vision_status}")
    if report.get("vision_enabled") and not report.get("vision_dir_found"):
        lines.append(f"vision:   fixtures dir not found: {report.get('vision_dir')}")
    if not report.get("models_dev_loaded"):
        lines.append("caps:     models.dev cache not found — capability data unavailable")
    if not report.get("state_db_found"):
        lines.append("state.db: not found — no session stats available")
    lines.append("")

    per_model = report.get("per_model", [])
    if not per_model:
        lines.append("(no models discovered in config.yaml)")
        lines.append("")
        lines.append("=" * 70)
        return 0, "\n".join(lines)

    for m in per_model:
        model = m["model"]
        provider = m["provider"]
        sources = ", ".join(m.get("sources") or [])
        lines.append(f"  {model}  [{provider}]")
        lines.append(f"    sources: {sources}")

        # Reasoning level
        rl = m.get("reasoning_level")
        if rl:
            lines.append(f"    reasoning: {rl}")

        # Capabilities
        caps = m.get("capabilities") or {}
        if caps:
            flags = []
            if caps.get("reasoning"):
                flags.append("reasoning")
            if caps.get("tool_call"):
                flags.append("tool-call")
            if caps.get("vision"):
                flags.append("vision")
            if caps.get("structured_output"):
                flags.append("structured-output")
            if flags:
                lines.append(f"    caps:      {', '.join(flags)}")
            ctx = caps.get("context_limit")
            out_lim = caps.get("output_limit")
            if ctx or out_lim:
                lines.append(
                    f"    limits:    context={_fmt_tokens(ctx)}  output={_fmt_tokens(out_lim)}"
                )
            cost = caps.get("cost_per_mtokens") or {}
            if any(cost.values()):
                lines.append(
                    f"    cost/Mtok: in=${cost.get('input')}  "
                    f"out=${cost.get('output')}  "
                    f"cache=${cost.get('cache_read')}"
                )

        # State.db stats
        stats = m.get("state_db")
        if stats:
            lines.append(
                f"    sessions:  {stats.get('call_count', 0)} calls  "
                f"avg_in={_fmt_tokens(stats.get('avg_input_tokens'))}  "
                f"avg_out={_fmt_tokens(stats.get('avg_output_tokens'))}  "
                f"avg_reason={_fmt_tokens(stats.get('avg_reasoning_tokens'))}"
            )
            tc = stats.get("total_cost_usd", 0)
            ac = stats.get("avg_cost_usd", 0)
            if tc or ac:
                lines.append(
                    f"    cost:      total={_fmt_cost(tc)}  "
                    f"avg/session={_fmt_cost(ac)}  "
                    f"max={_fmt_cost(stats.get('max_cost_usd'))}"
                )

        # First-response latency (pure model+gateway, from state.db)
        lat = m.get("avg_first_response_latency_s")
        if lat is not None:
            lines.append(f"    ttfr:      {lat:.2f}s (avg model latency, excl. harness startup)")

        # Smoke result
        sm = m.get("smoke")
        if sm:
            ok = sm.get("ok")
            icon = "✓" if ok else "✗"
            sl = sm.get("latency_s", 0)
            if ok:
                lines.append(f"    smoke:     {icon} JSON ok in {sl:.1f}s (round-trip incl. harness startup)")
            else:
                err = sm.get("error", "non-JSON response")
                lines.append(f"    smoke:     {icon} FAIL ({sl:.1f}s) — {err}")

        # Vision results (only for vision-capable models)
        vis = m.get("vision")
        if vis:
            for vf in vis:
                if vf.get("skipped"):
                    continue
                v_ok = vf.get("ok")
                v_icon = "✓" if v_ok else "✗"
                v_label = vf.get("fixture", "?")
                v_lat = vf.get("latency_s", 0)
                if v_ok:
                    lines.append(f"    vision:    {v_icon} {v_label} ({v_lat:.1f}s)")
                else:
                    v_err = vf.get("error") or ", ".join(vf.get("missing") or [])
                    lines.append(f"    vision:    {v_icon} FAIL {v_label} ({v_lat:.1f}s) — {v_err}")

        lines.append("")

    lines.append("=" * 70)
    any_fail = any(
        (m.get("smoke") or {}).get("ok") is False
        or any(
            (vf.get("ok") is False and not vf.get("skipped"))
            for vf in (m.get("vision") or [])
        )
        for m in per_model
    )
    if any_fail:
        lines.append("VERDICT: at least one model failed the smoke test — review above.")
        return 1, "\n".join(lines)
    lines.append("VERDICT: all models healthy.")
    return 0, "\n".join(lines)


# ---------------- show_resolution ----------------

def show_resolution(
    paths: ResolvedPaths,
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
    ttl: int = DEFAULT_TTL_SECONDS,
    cache_path: Path | None = None,
    config_path: Path | None = None,
    vision_fixtures_dir: Path | None = None,
) -> str:
    """Pretty-print the resolved config + discovery preview."""
    if config_path is not None:
        cfg = yaml.safe_load(config_path.read_text()) or {}
    else:
        cfg = _load_config(paths)
    targets = discover_model_targets(cfg)
    c_path = cache_path or _default_cache_path(paths.profile)
    v_dir = vision_fixtures_dir or _default_vision_dir()
    lines = [
        f"profile:     {paths.profile}",
        f"state_db:    {paths.state_db}",
        f"config:      {_resolve_config_path(paths)}",
        f"cache:       {c_path}",
        f"window_days: {days}",
        f"ttl_seconds: {ttl} ({ttl // 60} min)",
        f"models_dev:  {_MODELS_DEV_CACHE}",
        f"vision_dir:  {v_dir}",
        "",
        f"discovered models ({len(targets)}):",
    ]
    for t in sorted(targets, key=lambda x: (x.model, x.provider)):
        lines.append(f"  {t.model}  [{t.provider}]")
        lines.append(f"    sources: {', '.join(t.sources)}")
    return "\n".join(lines)
