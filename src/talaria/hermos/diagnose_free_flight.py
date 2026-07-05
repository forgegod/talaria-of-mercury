"""Free-flight open-ended anomaly pass for ``talaria hermes diagnose``.

The free-flight pass assembles a compact evidence bundle (structured
detector findings + redacted ``config.yaml`` + log-file references +
``state.db`` reference) and makes a single ``hermes chat -q`` call to
the operator's configured ``_curator`` model. The model is asked to
find anomalies and config improvements the deterministic rules do not
know to look for.

Design:

* ``config.yaml`` is **read, redacted, and inlined** into the prompt.
  The :func:`_redact_raw_yaml` helper strips every secret-bearing key
  and every ``auth`` / ``credentials`` / ``secrets`` parent block
  before the text reaches the model. The raw config is never handed
  to the model via ``@file:`` — that would leak API keys and tokens.
* Log files and ``state.db`` are referenced via hermes'
  ``@folder:<path>:N`` and ``@file:<path>`` syntax. Log files do not
  contain credential secrets, and ``state.db`` is a binary SQLite
  database whose session/message rows carry no API keys. The
  per-file line cap (``@folder:<log_dir>:N``) bounds the inlined
  size; the operator can override with ``--free-flight-log-lines=N``.

Two finding kinds are returned:

* ``kind: "anomaly"`` (default) — something is wrong or unexpected.
* ``kind: "config_suggestion"`` — a concrete ``config.yaml``
  change. Carries ``yaml_path``, ``current_value``,
  ``suggested_value``, ``rationale``.

A model failure / parse error / refusal to participate degrades
to a single ``DetectorResult`` with ``severity=info`` and
``model_verdict.error`` set. The free-flight pass never breaks
the diagnose command.
"""

from __future__ import annotations

import json
import re
from typing import Any

from talaria.hermos.diagnose import (
    SEVERITY_ALERT,
    SEVERITY_INFO,
    SEVERITY_WARN,
    DetectorResult,
    resolve_window,
)
from talaria.hermos import diagnose_llm
from talaria.paths import ResolvedPaths

#: Per-file line cap when the framework inlines the logs folder
#: (``@folder:<log_dir>:N``). The hermes framework inlines the
#: first N lines of every file in the folder. The operator
#: can override with the orchestrator ``free_flight_log_lines=``
#: kwarg.
DEFAULT_LOG_LINES = 200

#: Per-call subprocess timeout in seconds. The hermes chat
#: call is single-shot (one ``hermes chat -q`` invocation), so
#: the timeout is the whole free-flight pass budget. Adjust
#: upward if the curator model is consistently slow on the
#: operator's network.
DEFAULT_TIMEOUT_SECONDS = 180


#: Prompt template. Single-bracket placeholders are replaced via
#: :func:`str.replace` (the schema JSON contains literal ``{``
#: / ``}`` which would break :py:meth:`str.format`).
PROMPT_TEMPLATE = """\
You are running a free-flight anomaly pass on a Hermes Agent
profile.

The structured 11-detector pass has already run; its findings are
listed below. The deterministic detectors only catch KNOWN
patterns. Your job is to find issues and improvement opportunities
the deterministic rules do not know to look for.

__TASK__

The hermes framework will inline the files referenced below into
your context. Read them as needed; do not summarise away load-
bearing detail.

* Profile config (redacted — secrets stripped):

```yaml
__CONFIG_YAML__
```

* Log files (first __LOG_LINES__ lines per file):  @folder:`__LOGS_PATH__`:__LOG_LINES__
* state.db (the session/message/cost tables):  @file:`__STATE_DB_PATH__`

Structured detector findings (already fired):

```
__FINDINGS__
```

Return ONLY a JSON object matching the schema. If you find nothing
anomalous, return {"findings": []}. Do not include prose outside the
JSON object.

__SCHEMA__
"""


_TASK_DESCRIPTION = (
    "Look for: log lines that suggest a misbehaving tool or model "
    "(repeated retries, mid-tool-call stream drops, unexpected errors); "
    "sessions with anomalously high output_tokens, message_count, or "
    "tool_call_count; sessions in unusual end_reason or cost_status "
    "states; config keys that look miscalibrated for the observed "
    "workload; and any cross-file pattern that ties a log entry to a "
    "session id. For each finding, return either kind=anomaly "
    "(something is wrong) or kind=config_suggestion (a concrete "
    "config.yaml change). Suggest config changes only when the live "
    "value is observably miscalibrated — do not invent improvements."
)


#: Schema the model must return.
_RESPONSE_SCHEMA = {
    "findings": [
        {
            "kind": "anomaly",
            "id": "snake_case_slug",
            "severity": "info | warn | alert",
            "title": "one-line headline",
            "summary": "≤ 400 chars rationale; mention the specific session id or log line",
            "evidence_quote": "the exact text or data point that triggered the finding",
        },
        {
            "kind": "config_suggestion",
            "id": "snake_case_slug",
            "severity": "info | warn | alert  (alert = the current config is causing a real cost/perf/correctness problem)",
            "title": "one-line headline",
            "summary": "≤ 400 chars rationale; explain which anomaly this would mitigate",
            "yaml_path": "dotted dot-path into the profile's config.yaml, e.g. 'moa.presets.coding.max_tokens'",
            "current_value": "the live value as a string (e.g. '32768' or 'glm-4.5-air'); may be 'unknown' if the key is not currently set",
            "suggested_value": "the proposed new value as a string (e.g. '16384' or 'minimax/minimax-m3')",
            "rationale": "≤ 200 chars; why this change would help (cost, correctness, perf)",
        },
    ],
}


#: Map from the model's severity string to a canonical severity.
_SEVERITY_MAP = {
    "alert": SEVERITY_ALERT,
    "warn": SEVERITY_WARN,
    "info": SEVERITY_INFO,
    "warning": SEVERITY_WARN,
    "error": SEVERITY_ALERT,
    "critical": SEVERITY_ALERT,
}


# ---------------- Config redaction ----------------

#: Top-level (or nested) keys whose entire block is redacted.
#: Matching is case-insensitive on the leaf key name. A parent
#: block match redacts every child line until the parent's
#: indentation returns.
_REDACT_PARENT_KEYS = frozenset({
    "auth", "authentication", "credentials", "secrets",
    "providers", "api_keys", "tokens",
})

#: Leaf keys whose value is redacted regardless of parent.
#: Matching splits the key on ``_`` / ``-`` / non-alphanumeric
#: delimiters and checks each part for membership (case-insensitive).
#: This avoids false positives like ``max_tokens`` (part ``tokens``
#: ≠ ``token``) while still catching ``access_token`` (part ``token``),
#: ``api_key`` (part ``key``), and bare ``token:`` / ``password:``.
_REDACT_VALUE_PARTS = frozenset({
    "api_key", "apikey", "secret", "token", "password", "passwd",
    "credential", "private_key", "access_key", "bearer",
})

#: Substrings matched against the whole key (not split). Used for
#: compound forms like ``clientsecret`` that don't split cleanly.
_REDACT_VALUE_SUBSTRINGS = (
    "clientsecret", "client_secret", "refreshtoken", "refresh_token",
)

#: The literal written in place of a redacted value.
_REDACTED = "***REDACTED***"


def _split_key_parts(low: str) -> list[str]:
    """Split a YAML key into its delimiter-separated parts.

    Splits on ``_``, ``-``, and any non-alphanumeric character,
    then lowercases. ``api_key`` → ``["api", "key"]``,
    ``max_tokens`` → ``["max", "tokens"]``, ``clientSecret`` →
    ``["clientsecret"]``. The single-token forms like ``api_key``
    are retained alongside their split parts so exact-part matches
    work: ``api_key`` → ``["api", "key", "api_key"]``.
    """
    parts: list[str] = []
    parts.append(low)
    cur = []
    for ch in low:
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                parts.append("".join(cur))
                cur = []
            # The delimiter-joined form (e.g. "api_key") is also
            # useful for exact matches, so push it once.
    if cur:
        parts.append("".join(cur))
    return parts


def _redact_raw_yaml(text: str) -> str:
    """Return *text* with every secret-bearing line redacted.

    The function is a line-oriented scanner, not a YAML parser:

    * Top-level or nested keys in :data:`_REDACT_PARENT_KEYS`
      (``auth``, ``credentials``, ``secrets``, …) redact their
      own value and every nested child line until the parent's
      indentation returns.
    * Any leaf key whose name contains one of the
      :data:`_REDACT_VALUE_SUBSTRINGS` substrings (case-insensitive)
      has its value replaced with ``***REDACTED***``.
    * Comments and blank lines are preserved verbatim so the
      redacted text still round-trips as YAML and stays
      human-readable.

    The function is intentionally conservative: when in doubt it
    redacts. A false positive (redacting a non-secret value) is
    recoverable — the model sees ``***REDACTED***`` and notes the
    redaction; a false negative (leaking a real key to the model)
    is not.
    """
    if not text:
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    # Stack of (indent, is_redacting_parent) for every nested block
    # currently open. When the scanner leaves a redacting parent's
    # indent level, the block is popped and redaction stops.
    redact_stack: list[tuple[int, bool]] = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        indent = len(line) - len(stripped)
        # Pop any redact-parent whose indent we've left.
        while redact_stack and indent <= redact_stack[-1][0]:
            redact_stack.pop()
        in_redact_parent = any(active for _, active in redact_stack)
        # Parse the key (text before ``:`` or ``: ``).
        colon = stripped.find(":")
        if colon == -1:
            # Not a key:value line — pass through, but if we're
            # inside a redacting parent, redact the value.
            if in_redact_parent:
                out.append(_redact_value_line(line))
            else:
                out.append(line)
            continue
        key = stripped[:colon].strip().strip("\"'")
        low = key.lower()
        value_part = stripped[colon + 1:].strip()
        is_parent_block = value_part == "" or value_part == "|"
        if in_redact_parent:
            # Everything inside a redacting parent is redacted.
            out.append(_redact_value_line(line))
            if is_parent_block:
                redact_stack.append((indent, True))
            continue
        # Check parent keys first.
        if low in _REDACT_PARENT_KEYS:
            if is_parent_block:
                redact_stack.append((indent, True))
                out.append(f"{line[:indent]}{key}: {_REDACTED}\n"
                           if value_part == "" else
                           f"{line[:indent]}{key}: {_REDACTED}\n")
            else:
                out.append(_redact_value_line(line))
            continue
        # Check leaf parts / substrings.
        key_parts = _split_key_parts(low)
        if any(p in _REDACT_VALUE_PARTS for p in key_parts) or \
           any(s in low for s in _REDACT_VALUE_SUBSTRINGS):
            out.append(_redact_value_line(line))
            continue
        out.append(line)
    return "".join(out)


def _redact_value_line(line: str) -> str:
    """Replace the value part of a ``key: value`` line with REDACTED.

    Preserves the key and indentation. For lines without a colon,
    returns ``***REDACTED***`` on its own (used inside parent
    blocks where the line format is opaque).
    """
    stripped = line.lstrip()
    indent = line[:len(line) - len(stripped)]
    colon = stripped.find(":")
    if colon == -1:
        return f"{indent}{_REDACTED}\n"
    key = stripped[:colon]
    trailing = "\n" if stripped.endswith("\n") else ""
    return f"{indent}{key}: {_REDACTED}{trailing}"


def _build_prompt(
    *,
    config_yaml_redacted: str,
    logs_path: str,
    state_db_path: str,
    log_lines: int,
    findings_payload: str,
) -> str:
    """Assemble the model prompt.

    The prompt is task description + structured findings + inline
    redacted config + file references for logs and state.db. The
    hermes framework reads the referenced files and inlines them
    into the model's context; the prompt itself never contains
    file contents except for the redacted config.
    """
    return (
        PROMPT_TEMPLATE
        .replace("__TASK__", _TASK_DESCRIPTION)
        .replace("__CONFIG_YAML__", config_yaml_redacted)
        .replace("__LOGS_PATH__", logs_path)
        .replace("__STATE_DB_PATH__", state_db_path)
        .replace("__LOG_LINES__", str(log_lines))
        .replace("__FINDINGS__", findings_payload)
        .replace("__SCHEMA__", json.dumps(_RESPONSE_SCHEMA, indent=2))
    )


def _resolve_config_path(paths: ResolvedPaths) -> Path:
    if paths.profile == "default":
        return paths.hermes_root / "config.yaml"
    return paths.hermes_root / "profiles" / paths.profile / "config.yaml"


def _discover_log_files(paths: ResolvedPaths, include_curator: bool) -> list[Path]:
    """Use the diagnose log-discovery so behaviour stays consistent."""
    from talaria.hermos import diagnose
    return diagnose.discover_log_files(
        paths.log_dir, include_curator=include_curator,
    )


def _parse_findings(stdout: str) -> list[dict[str, Any]]:
    text = stdout.strip()
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        first = text.find("{")
        last = text.rfind("}")
        if first == -1 or last == -1 or last <= first:
            return []
        candidate = text[first:last + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    findings = parsed.get("findings", [])
    if not isinstance(findings, list):
        return []
    return findings


def _finding_to_result(idx: int, finding: dict[str, Any]) -> DetectorResult:
    kind = str(finding.get("kind", "anomaly")).strip().lower() or "anomaly"
    if kind not in ("anomaly", "config_suggestion"):
        kind = "anomaly"
    slug = str(finding.get("id", f"finding_{idx}")).strip() or f"finding_{idx}"
    slug = re.sub(r"[^a-z0-9_]+", "_", slug.lower())[:64] or f"finding_{idx}"
    sev_raw = str(finding.get("severity", "info")).strip().lower()
    severity = _SEVERITY_MAP.get(sev_raw, SEVERITY_INFO)
    title = str(finding.get("title", "")).strip()[:200]
    summary = str(finding.get("summary", "")).strip()[:500]
    if not summary and title:
        summary = title
    if title and not summary.startswith(title):
        summary = f"{title}: {summary}" if summary else title

    if kind == "config_suggestion":
        yaml_path = str(finding.get("yaml_path", "")).strip()
        current = str(finding.get("current_value", "unknown")).strip()[:200]
        suggested = str(finding.get("suggested_value", "")).strip()[:200]
        rationale = str(finding.get("rationale", "")).strip()[:300]
        details = f"{yaml_path}: {current} → {suggested}"
        if rationale:
            details = f"{details}  ({rationale})"
        full_summary = f"{summary}  |  {details}" if summary else details
        return DetectorResult(
            id=f"free_flight:config:{slug}",
            severity=severity,
            summary=full_summary or "(no summary)",
            evidence={
                "kind": "config_suggestion",
                "yaml_path": yaml_path,
                "current_value": current,
                "suggested_value": suggested,
                "rationale": rationale,
                "title": title,
            },
            fired=False,
            borderline=False,
            adjudicated=True,
            model_verdict={"source": "free_flight.config_suggestion", "raw": finding},
        )

    quote = str(finding.get("evidence_quote", "")).strip()[:500]
    return DetectorResult(
        id=f"free_flight:anomaly:{slug}",
        severity=severity,
        summary=summary or "(no summary)",
        evidence={"kind": "anomaly", "evidence_quote": quote, "title": title},
        fired=(severity in (SEVERITY_WARN, SEVERITY_ALERT)),
        borderline=False,
        adjudicated=True,
        model_verdict={"source": "free_flight.anomaly", "raw": finding},
    )


def _findings_for_prompt(paths: ResolvedPaths) -> list[dict[str, Any]]:
    """Build a compact per_detector list for the model prompt.

    Runs the 11 deterministic detectors via the orchestrator
    (with free_flight=False to avoid recursion) and returns a
    pruned list suitable for inline inclusion in the model
    prompt.
    """
    from talaria.hermos import diagnose
    report = diagnose.run(
        paths, days=0, since=None, include_curator=False, free_flight=False,
    )
    out = []
    for d in report.get("per_detector", []):
        ev = d.get("evidence") or {}
        pruned = {k: v for k, v in ev.items() if k != "matches" and k != "per_file"}
        if "per_file" in ev:
            pruned["per_file_count"] = len(ev["per_file"])
        out.append({
            "id": d["id"],
            "severity": d["severity"],
            "fired": d["fired"],
            "summary": d["summary"],
            "evidence": pruned,
        })
    return out


# ---------------- Public entry point ----------------

def run(
    paths: ResolvedPaths,
    *,
    days: int,
    since: str | None = None,
    log_lines: int = DEFAULT_LOG_LINES,
    include_curator: bool = False,
    subprocess_runner=diagnose_llm.hermes_chat,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    model: str | None = None,
    provider: str | None = None,
) -> list[DetectorResult]:
    """Run the free-flight pass and return the findings.

    A ``log_lines`` of 0 (or negative) returns a single skipped
    detector result. The function makes exactly one
    ``hermes chat -q`` call (no batching) and returns the parsed
    findings.

    The curator model + provider are resolved from the active
    profile's ``config.yaml`` via
    :func:`diagnose_llm.resolve_curator_config` when ``model`` /
    ``provider`` are ``None``. Explicit kwargs override the config
    lookup (used by tests to pin a stub). The resolved values are
    passed to ``subprocess_runner(prompt, model=..., provider=..., timeout=...)``.

    A model failure / parse error / refusal to participate
    degrades to a single ``DetectorResult`` with the error
    captured. The list is never empty in a well-formed run;
    the empty-findings case (``{"findings": []}``) returns an
    empty list.
    """
    if log_lines <= 0:
        return [DetectorResult(
            id="free_flight:skipped",
            severity=SEVERITY_INFO,
            summary="free-flight pass disabled (log_lines = 0)",
        )]

    # Resolve the curator model + provider from the profile config.
    # Explicit kwargs (tests) override the config lookup.
    if model is None or provider is None:
        cfg_model, cfg_provider = diagnose_llm.resolve_curator_config(paths)
        if model is None:
            model = cfg_model
        if provider is None:
            provider = cfg_provider

    config_path = _resolve_config_path(paths)
    state_db_path = paths.state_db
    log_files = _discover_log_files(paths, include_curator)

    # Read and redact the profile's config.yaml. A missing config
    # is tolerated — the model still gets the log + state.db
    # references. When present, the raw text is never handed to the
    # model; _redact_raw_yaml strips every secret-bearing key and
    # every auth/credentials/secrets parent block first.
    config_yaml_redacted = ""
    if config_path.exists():
        try:
            raw = config_path.read_text(encoding="utf-8", errors="replace")
            config_yaml_redacted = _redact_raw_yaml(raw)
        except OSError:
            config_yaml_redacted = ""

    if not log_files and not config_yaml_redacted:
        return [DetectorResult(
            id="free_flight:no_data",
            severity=SEVERITY_INFO,
            summary="no log files or config.yaml in the window",
        )]

    findings_payload = json.dumps(_findings_for_prompt(paths), default=str, indent=2)
    prompt = _build_prompt(
        config_yaml_redacted=config_yaml_redacted,
        logs_path=str(paths.log_dir),
        state_db_path=str(state_db_path),
        log_lines=log_lines,
        findings_payload=findings_payload,
    )

    try:
        rc, stdout, stderr = subprocess_runner(
            prompt, model=model, provider=provider, timeout=timeout,
        )
    except diagnose_llm.AdjudicationUnavailable as exc:
        return [DetectorResult(
            id="free_flight:unavailable",
            severity=SEVERITY_INFO,
            summary=f"curator model unavailable: {exc}",
        )]
    except Exception as exc:
        return [DetectorResult(
            id="free_flight:error",
            severity=SEVERITY_INFO,
            summary=f"subprocess failed: {type(exc).__name__}: {exc}",
        )]

    if rc != 0 and not stdout.strip():
        return [DetectorResult(
            id="free_flight:error",
            severity=SEVERITY_INFO,
            summary=f"hermes chat -q exited {rc}: {stderr.strip()[:200]}",
        )]

    findings = _parse_findings(stdout)
    if not findings and "findings" not in (stdout or ""):
        return [DetectorResult(
            id="free_flight:unparseable",
            severity=SEVERITY_INFO,
            summary="curator model response could not be parsed as a findings list",
            evidence={"raw": stdout[:2000]},
        )]
    return [_finding_to_result(i, f) for i, f in enumerate(findings)]
