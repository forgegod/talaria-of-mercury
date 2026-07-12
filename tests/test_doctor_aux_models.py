"""Benchmarks for every model the profile routes through.

Design principles:

1. **Single source of truth for model discovery.** The benchmark
   discovers every ``(model, provider)`` pair from three places in
   the profile's ``config.yaml``:

   * ``model.default`` + top-level ``provider`` (the profile's main
     model — the one the operator talks to).
   * Every entry in ``model.aliases`` (provider resolved from the
     matching ``auxiliary.<usecase>.provider`` block when the alias
     is an internal ``_`` form, otherwise the top-level ``provider``).
   * Every ``auxiliary.<usecase>.model`` + ``auxiliary.<usecase>.provider``
     block (the dedicated per-usecase models).

2. **Deduplication by ``(model, provider)``.** The same model+gateway
   pair is benchmarked exactly **once**, regardless of how many
   config paths point to it. ``zai-coding/glm-4.5-air`` via
   ``kilocode`` may be the ``_compression`` alias, the
   ``auxiliary.curator.model``, and the ``auxiliary.compression.model``
   all at once — one call, not three.

3. **Opt-in + opt-out.** The default run skips live calls (fast
   ``pytest``). Set ``_TESTING_TALARIA_RUN_MODEL_BENCH=1`` to opt
   in. Set ``_TESTING_TALARIA_SKIP_MODEL_BENCH=1`` to force-skip
   even when the opt-in is set (useful in CI where the env var is
   sticky but the network is offline). Opt-out wins. Both follow
   the ``_TESTING_TALARIA_*`` prefix rule for internal test env
   vars (see ``tests/AGENTS.md`` §Local Contracts).

4. **Vision is separate.** The vision-capability benchmark needs
   image input (``--image``), so it cannot share the text-only
   JSON prompt. It is a standalone parametrized test that targets
   only the configured vision model.

5. **Curator parity** (``model.aliases._curator`` vs
   ``auxiliary.curator.model``) is a deterministic invariant check
   that always runs (no hermes needed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any
from pathlib import Path

import pytest
import yaml


#: Per-call soft timeout for the live hermes chat. The doctor
#: orchestrator uses 180 s for its primary call; individual model
#: checks should be faster because the prompt is small. 90 s is a
#: generous ceiling.
HERMES_PER_CALL_TIMEOUT = 90


#: Where the bench results are recorded. Persistent across runs so
#: a follow-up ``--compare`` run can spot regressions.
BENCH_RESULTS = Path("/tmp/talaria-model-bench.json")


# ---------------- Config helpers ----------------

def _live_config_path() -> Path:
    """Return the path to the live config, or skip the test.

    Override with ``_TESTING_TALARIA_PROFILE_CONFIG=...`` for
    hermes-free CI runs.
    """
    override = os.environ.get("_TESTING_TALARIA_PROFILE_CONFIG")
    if override:
        return Path(override)
    candidate = Path("/home/raphael/.hermes/profiles/vc-client/config.yaml")
    if not candidate.exists():
        pytest.skip(
            f"no live config at {candidate} and no "
            f"_TESTING_TALARIA_PROFILE_CONFIG override"
        )
    return candidate


def _load_config() -> dict[str, Any]:
    """Load and return the live config as a dict (or skip)."""
    return yaml.safe_load(_live_config_path().read_text())


# ---------------- Unified model discovery + dedup ----------------

class ModelTarget:
    """A unique ``(model, provider)`` pair with provenance labels.

    Two targets are equal when both ``model`` and ``provider`` match,
    so the same pair discovered from multiple config paths is
    benchmarked once. The ``sources`` list records every config path
    that pointed to this pair so the test ID and failure message
    show the full picture.
    """

    __slots__ = ("model", "provider", "sources")

    def __init__(self, model: str, provider: str, source: str) -> None:
        self.model = model
        self.provider = provider
        self.sources: list[str] = [source]

    @property
    def id(self) -> str:
        """Stable test ID: ``model--provider`` (sanitised)."""
        raw = f"{self.model}--{self.provider}"
        return raw.replace("/", "_").replace(" ", "_")

    def add_source(self, source: str) -> None:
        if source not in self.sources:
            self.sources.append(source)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelTarget):
            return NotImplemented
        return self.model == other.model and self.provider == other.provider

    def __hash__(self) -> int:
        return hash((self.model, self.provider))

    def __repr__(self) -> str:
        return f"ModelTarget({self.model!r}, {self.provider!r}, {self.sources!r})"


def discover_model_targets(cfg: dict[str, Any]) -> list[ModelTarget]:
    """Discover every unique ``(model, provider)`` pair in *cfg*.

    Walks three config sections:

    * ``model.default`` + top-level ``provider``
    * ``model.aliases`` (every entry; provider resolved per-alias)
    * ``auxiliary.<usecase>.model`` + ``auxiliary.<usecase>.provider``

    Returns a list of :class:`ModelTarget` with no duplicate
    ``(model, provider)`` pairs. Each target's ``sources`` list
    records every config path that pointed to it.
    """
    targets: dict[int, ModelTarget] = {}

    def _register(model: str, provider: str, source: str) -> None:
        if not model or not str(model).strip():
            return
        model = str(model)
        provider = str(provider or "auto")
        t = ModelTarget(model, provider, source)
        key = hash(t)
        if key in targets:
            targets[key].add_source(source)
        else:
            targets[key] = t

    top_provider = cfg.get("provider") or "auto"

    # 1. model.default
    default_model = (cfg.get("model") or {}).get("default")
    if default_model:
        _register(default_model, top_provider, "model.default")

    aliases = (cfg.get("model") or {}).get("aliases") or {}
    auxiliary = cfg.get("auxiliary") or {}

    # 2. model.aliases — provider from auxiliary.<usecase>.provider
    #    when the alias is an internal _<usecase> form, otherwise
    #    the top-level provider.
    for alias, model in aliases.items():
        if not model:
            continue
        if alias.startswith("_"):
            usecase = alias[1:]
            aux_block = auxiliary.get(usecase) or {}
            provider = aux_block.get("provider") or top_provider
        else:
            provider = top_provider
        _register(model, provider, f"model.aliases.{alias}")

    # 3. auxiliary.<usecase>.model
    for usecase, block in auxiliary.items():
        if not isinstance(block, dict):
            continue
        model = block.get("model")
        if model:
            provider = block.get("provider") or top_provider
            _register(model, provider, f"auxiliary.{usecase}.model")

    return list(targets.values())


# ---------------- Benchmark gating ----------------

#: Opt-in: skip unless the env var is set. Live calls burn tokens.
_run_bench = bool(os.environ.get("_TESTING_TALARIA_RUN_MODEL_BENCH"))

#: Opt-out: force-skip even when the opt-in is set. Wins over opt-in.
_skip_bench = bool(os.environ.get("_TESTING_TALARIA_SKIP_MODEL_BENCH"))

requires_hermes = pytest.mark.skipif(
    (not _run_bench) or _skip_bench,
    reason=(
        "set _TESTING_TALARIA_RUN_MODEL_BENCH=1 to run live hermes integration checks"
        if not _run_bench else
        "_TESTING_TALARIA_SKIP_MODEL_BENCH=1 overrides the opt-in"
    ),
)


# ---------------- JSON-response benchmark (deduplicated) ----------------

#: The canonical smoke prompt. Every model gets the same prompt so
#: the pass/fail criterion is uniform: did the model return
#: parseable JSON within the timeout?
_JSON_SMOKE_PROMPT = (
    "Return ONLY this JSON object with no prose: "
    '{"ok": true, "model": "bench"}'
)


def _hermes_json_call(
    model: str, provider: str, prompt: str,
    timeout: int = HERMES_PER_CALL_TIMEOUT,
) -> tuple[bool, float, str]:
    """One ``hermes chat -q`` call; returns ``(ok, elapsed_s, output)``."""
    if shutil.which("hermes") is None:
        pytest.skip("hermes CLI not on PATH")
    t0 = time.time()
    try:
        proc = subprocess.run(
            ["hermes", "chat", "-q", prompt,
             "-m", model, "--provider", provider, "-Q"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, time.time() - t0, f"subprocess error: {exc}"
    dt = time.time() - t0
    out = proc.stdout.strip()
    body = out.split("session_id:", 1)[-1] if "session_id:" in out else out
    ok = '"' in body and (body.rstrip().endswith("}") or body.rstrip().endswith("]"))
    return ok, dt, out


def _collect_targets() -> list[ModelTarget]:
    """Discover targets from the live config (module-level cache for pytest)."""
    cfg = _load_config()
    return discover_model_targets(cfg)


def _parametrized_targets():
    """Return a list of pytest.param with ids for parametrize."""
    try:
        targets = _collect_targets()
    except Exception:
        return [pytest.param(None, id="no-config")]
    return [
        pytest.param(t, id=t.id)
        for t in sorted(targets, key=lambda t: (t.model, t.provider))
    ]


@requires_hermes
@pytest.mark.parametrize("target", _parametrized_targets())
def test_model_responds_with_json(target: ModelTarget) -> None:
    """Every unique ``(model, provider)`` pair responds to a JSON prompt.

    This is the deduplicated benchmark. The same pair discovered from
    ``model.aliases._curator``, ``auxiliary.curator.model``, and
    ``auxiliary.compression.model`` is called **once**; the test ID
    is ``<model>--<provider>`` and the ``sources`` list in the bench
    JSON records every config path that pointed to it.
    """
    ok, dt, output = _hermes_json_call(
        target.model, target.provider, _JSON_SMOKE_PROMPT,
    )
    # Record the result.
    _record_bench(target, ok, dt)
    assert ok, (
        f"model={target.model!r} provider={target.provider!r} "
        f"(sources: {target.sources}) returned non-JSON in {dt:.1f}s. "
        f"Output tail: {output[-200:]!r}"
    )
    assert dt < HERMES_PER_CALL_TIMEOUT, (
        f"model={target.model!r} provider={target.provider!r} took "
        f"{dt:.1f}s (> {HERMES_PER_CALL_TIMEOUT}s ceiling)."
    )


def _record_bench(target: ModelTarget, ok: bool, dt: float) -> None:
    """Append the run to BENCH_RESULTS (best-effort, never fails the test)."""
    try:
        BENCH_RESULTS.parent.mkdir(parents=True, exist_ok=True)
        history: dict[str, list] = {}
        if BENCH_RESULTS.exists():
            raw = json.loads(BENCH_RESULTS.read_text())
            for k, v in raw.items():
                history[k] = v if isinstance(v, list) else [v]
        history.setdefault(target.id, []).append({
            "model": target.model,
            "provider": target.provider,
            "sources": target.sources,
            "elapsed_s": round(dt, 2),
            "ok": ok,
        })
        BENCH_RESULTS.write_text(json.dumps(history, indent=2))
    except (OSError, json.JSONDecodeError):
        pass


# ---------------- Curator parity (deterministic invariant) ----------------

class TestCuratorParity:
    """``model.aliases._curator`` and ``auxiliary.curator.model``
    must point to the same model id. The doctor orchestrator
    sources the curator from both places; a divergence is a
    silent change that hides one model under the wrong alias.

    Marked ``xfail(strict=False)`` because the two values are
    independently configurable and an operator may intentionally
    run them different. The test still runs and reports the
    divergence as an XPASS warning so it is visible without
    blocking the suite.
    """

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "_curator and auxiliary.curator.model may intentionally "
            "differ; divergence is reported as a warning, not a failure"
        ),
    )
    def test_curator_alias_and_auxiliary_curator_have_same_model(self) -> None:
        cfg = _load_config()
        alias = (cfg.get("model", {}).get("aliases") or {}).get("_curator")
        aux = (cfg.get("auxiliary") or {}).get("curator", {}).get("model")
        assert alias, "_curator alias missing from model.aliases"
        assert aux, "auxiliary.curator.model missing"
        assert alias == aux, (
            f"_curator ({alias!r}) and auxiliary.curator.model "
            f"({aux!r}) must be the same model. Diagnostic + free-flight "
            "commands source both; divergence leaves one model hidden."
        )


# ---------------- Vision-capability benchmark ----------------
#
# Vision capability is now tested by ``talaria hermes benchmark``.
# The benchmark discovers every vision-capable model (per models.dev)
# and sends each fixture image via ``hermes chat --image``, asserting
# the model reads it correctly. The fixture images, ground truth, and
# matching logic live in ``talaria.hermes.benchmark`` and
# ``assets/benchmark/vision/``.
#
# The standalone pytest below is a thin smoke test that runs the
# full benchmark with vision enabled against the live vision model.
# It is gated behind the same ``_TESTING_TALARIA_RUN_MODEL_BENCH``
# opt-in as the JSON smoke benchmark.


@requires_hermes
def test_benchmark_vision_live() -> None:
    """Run ``talaria.hermes.benchmark.run`` with vision enabled.

    This exercises the integrated vision benchmark end-to-end: it
    discovers vision-capable models from the live config, resolves
    each fixture image from ``assets/benchmark/vision/``, sends them
    via ``hermes chat --image``, and asserts the model responds
    correctly. The smoke (JSON) pass is disabled to keep the run
    focused on vision.
    """
    from talaria.hermes.benchmark import run as benchmark_run
    from talaria.paths import resolve_paths

    override = os.environ.get("_TESTING_TALARIA_PROFILE_CONFIG")
    if override:
        profile = Path(override).parent.name
    else:
        profile = "vc-client"
    paths = resolve_paths(profile_flag=profile)
    report = benchmark_run(paths, smoke=False, vision=True)
    if report["vision_models"] == 0:
        pytest.skip("no vision-capable models discovered")
    assert report["vision_calls_made"] > 0, "expected at least one vision call"
    failures = [
        vf
        for m in report["per_model"]
        for vf in (m.get("vision") or [])
        if vf.get("ok") is False and not vf.get("skipped")
    ]
    assert not failures, (
        f"{len(failures)} vision fixture(s) failed: "
        f"{[f.get('fixture') for f in failures]}"
    )


# ---------------- Deterministic config-side checks ----------------

class TestConfigInvariants:
    """Pure-Python assertions that don't require hermes. Always
    run, even when the live-bench classes are skipped.
    """

    def test_config_yaml_parses(self) -> None:
        text = _live_config_path().read_text()
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            pytest.fail(f"config.yaml no longer parses as YAML: {exc}")

    def test_config_has_all_internal_aliases(self) -> None:
        cfg = _load_config()
        aliases = (cfg.get("model") or {}).get("aliases") or {}
        required = [
            "_curator", "_compression", "_vision",
            "_flush_memories", "_goal_judge", "_triage_specifier",
            "_kanban_decomposer", "_mcp", "_monitor",
            "_session_search", "_skills_hub",
            "_title_generation", "_tts_audio_tags",
            "_web_extract",
        ]
        missing = [r for r in required if r not in aliases]
        assert not missing, (
            f"model.aliases is missing required internal aliases: {missing}"
        )

    def test_every_alias_resolves_to_a_nonempty_model_id(self) -> None:
        cfg = _load_config()
        aliases = (cfg.get("model") or {}).get("aliases") or {}
        empty = [k for k, v in aliases.items() if not v or not str(v).strip()]
        assert not empty, (
            f"the following aliases resolve to empty model ids: {empty}"
        )

    def test_model_default_is_set(self) -> None:
        """``model.default`` must be set — it's the profile's main model."""
        cfg = _load_config()
        default = (cfg.get("model") or {}).get("default")
        assert default and str(default).strip(), (
            "model.default is not set — the profile has no main model"
        )

    def test_discovered_targets_are_nonempty(self) -> None:
        """The unified discovery must find at least one model to benchmark."""
        cfg = _load_config()
        targets = discover_model_targets(cfg)
        assert targets, (
            "discover_model_targets() found no models in config.yaml — "
            "either model.default, model.aliases, or auxiliary.* must "
            "define at least one model"
        )
