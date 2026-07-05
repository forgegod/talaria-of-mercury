"""Curator-model subprocess runner for ``talaria hermes diagnose``.

This module is the *only* place the diagnose feature talks to a
language model. The free-flight pass in
:mod:`talaria.hermos.diagnose_free_flight` resolves the curator
model + provider from the active profile's ``config.yaml`` at
runtime (see :func:`resolve_curator_config`) and invokes
:func:`hermes_chat` to call ``hermes chat -q`` with whatever model
and provider the operator configured. **Nothing is hardcoded** —
the module does not assume ``_curator`` or ``kilo``.

Resolution precedence (first non-empty wins):

1. ``auxiliary.curator.model`` + ``auxiliary.curator.provider``
   (the dedicated block the operator configures for this exact
   purpose).
2. ``model.aliases._curator`` (the short-alias form; provider is
   taken from the matching ``providers:`` block or defaults to
   ``auto``).
3. ``model.default`` + ``provider`` (the profile's main model).

Hardening contract:

* The subprocess inherits the same env as the orchestrator so the
  operator's profile selection, model aliases, and gateway config
  are honored.
* ``hermes`` must be on ``$PATH``; otherwise the runner raises
  :class:`AdjudicationUnavailable`. The orchestrator catches this
  and degrades to a ``free_flight:unavailable`` result — the
  diagnose command never breaks because the model was missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from talaria.paths import ResolvedPaths


#: Default per-call timeout (seconds) for the curator-model subprocess.
#: The orchestrator may override via ``free_flight_timeout``.
ADJUDICATE_TIMEOUT_SECONDS = 90


class AdjudicationUnavailable(RuntimeError):
    """Raised when the curator model cannot be reached.

    Never propagated by the orchestrator — the diagnose command
    catches this and degrades to a no-op detector result.
    """


def _resolve_config_path(paths: ResolvedPaths) -> Path:
    """Return the config.yaml path for the resolved profile."""
    if paths.profile == "default":
        return paths.hermes_root / "config.yaml"
    return paths.hermes_root / "profiles" / paths.profile / "config.yaml"


def resolve_curator_config(paths: ResolvedPaths) -> tuple[str, str]:
    """Resolve the curator model + provider from the profile config.

    Returns ``(model, provider)``. Never raises on a missing or
    incomplete config — falls back to sensible defaults so the
    diagnose command degrades gracefully rather than crashing.

    Resolution order (first non-empty model wins):

    1. ``auxiliary.curator.model`` — the dedicated block. Provider
       comes from the same block's ``provider`` field.
    2. ``model.aliases._curator`` — the short-alias form. Provider
       defaults to ``auto`` (hermes resolves it from the alias
       prefix or the configured providers).
    3. ``model.default`` — the profile's main model. Provider comes
       from the top-level ``provider`` key.
    """
    config_path = _resolve_config_path(paths)
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg: dict[str, Any] = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return "_curator", "auto"

    auxiliary = cfg.get("auxiliary") or {}
    curator_block = auxiliary.get("curator") or {}
    model = curator_block.get("model")
    provider = curator_block.get("provider")
    if model:
        return str(model), str(provider or "auto")

    aliases = (cfg.get("model") or {}).get("aliases") or {}
    model = aliases.get("_curator")
    if model:
        return str(model), str(provider or "auto")

    model = (cfg.get("model") or {}).get("default")
    provider = cfg.get("provider")
    if model:
        return str(model), str(provider or "auto")

    return "_curator", "auto"


def resolve_auxiliary_model(
    paths: ResolvedPaths, usecase: str,
) -> tuple[str, str] | None:
    """Resolve a model + provider for an auxiliary usecase.

    Returns ``(model, provider)`` or ``None`` if the usecase block
    is absent or has no ``model`` field. Used by the benchmark
    tests to discover the live configured model without hardcoding.
    """
    config_path = _resolve_config_path(paths)
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg: dict[str, Any] = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None

    block = (cfg.get("auxiliary") or {}).get(usecase) or {}
    model = block.get("model")
    provider = block.get("provider")
    if not model:
        # Fall back to the _<usecase> alias if present.
        aliases = (cfg.get("model") or {}).get("aliases") or {}
        model = aliases.get(f"_{usecase}")
    if not model:
        return None
    return str(model), str(provider or "auto")


def hermes_chat(
    prompt: str,
    *,
    model: str = "_curator",
    provider: str = "auto",
    timeout: int = ADJUDICATE_TIMEOUT_SECONDS,
    image: str | None = None,
) -> tuple[int, str, str]:
    """Run ``hermes chat -q`` with the given model + provider.

    Returns ``(returncode, stdout, stderr)``.

    The ``model`` and ``provider`` are keyword-only; test stubs
    must accept ``prompt`` positionally and ``model``, ``provider``,
    ``timeout`` as keywords. An optional ``image`` path attaches a
    vision input (used by the benchmark tests).

    Raises :class:`AdjudicationUnavailable` if the ``hermes`` binary
    is missing or the subprocess cannot be launched. The
    orchestrator catches this and degrades to a no-op.
    """
    binary = shutil.which("hermes")
    if binary is None:
        raise AdjudicationUnavailable("`hermes` CLI not on PATH")
    cmd = [
        binary, "chat",
        "-q", prompt,
        "-m", model,
        "--provider", provider,
        "-Q",  # quiet: suppress banner/spinner so we get the response only
    ]
    if image:
        cmd.extend(["--image", image])
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, check=False, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdjudicationUnavailable(
            f"model {model} timed out after {timeout}s"
        ) from exc
    except OSError as exc:
        raise AdjudicationUnavailable(f"subprocess launch failed: {exc}") from exc
    return proc.returncode, proc.stdout, proc.stderr
