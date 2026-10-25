# CAP-003 — Model benchmark

**Status:** implemented
**Primary surface:** none

## Behaviour

- `talaria hermes benchmark` reports per-model health, cost, latency, and
  capability data for every unique `(model, provider)` pair referenced by the
  profile's `config.yaml`: `model.default`, every `model.aliases` entry, and
  every `auxiliary.<usecase>.model`. Duplicate pairs are collapsed and each
  entry lists its `sources`.
- `state.db` aggregation covers `--days` (default 7): call count, average
  input/output/cache/reasoning tokens, total/average/max actual cost, the most
  recent `model_config` reasoning level, and average first-response latency
  (first user message to the next assistant message).
- Capabilities come from `~/.hermes/models_dev_cache.json`, matched by the model
  slug after the last `/`: reasoning, tool call, vision (attachment or image
  input), structured output, context/output limits, per-million-token cost, and
  family/name.
- A JSON smoke call (`hermes chat -q`) is made once per discovered model when
  its cache entry is stale. `--ttl` defaults to 1800 s. `--no-smoke` disables
  smoke calls, not vision calls; disable both for a local-only report.
- Vision checks run for models whose capabilities include vision. Four fixture
  images in `assets/benchmark/vision/` cover counting/colour, OCR, spatial
  reasoning, and logo recognition; ground truth supports `|`-separated
  alternatives. `--no-vision` disables the checks and
  `--vision-fixtures-dir` overrides the fixture root.
- Smoke and vision calls run in a `ThreadPoolExecutor`; `--jobs/-j` defaults to
  8 and `--jobs 1` is sequential.
- Results are cached at
  `$XDG_CACHE_HOME/talaria/benchmark-cache-<profile>.json`, keyed by
  `<model>--<provider>` for smoke and `<model_id>::vision::<fixture>` for
  vision. These are Talaria cache writes; delegated Hermes/model execution has
  its own runtime side effects.
- Exit code is 0 when every smoke and vision check passes, 1 when any model
  fails. The human report prints by default; `-q/--quiet` is exit-code only;
  `--json` and `--show-resolution` always print.

## Implementation

- `src/talaria/hermes/benchmark.py` — model discovery, state.db aggregation,
  models.dev enrichment, smoke/vision calls, cache, renderer,
  `show_resolution`.
- `src/talaria/hermes/doctor_llm.py` — `hermes_chat`, the subprocess runner
  reused for smoke and vision calls.
- `assets/benchmark/vision/ground_truth.json` — fixture answers; sibling PNGs
  provide image input.

## Rules and boundaries

- The command is read-only against Hermes state: it opens `state.db` with
  `mode=ro`, reads `config.yaml` and the models.dev cache, and writes only its
  own benchmark cache file.
- It makes model/network calls by default. Use `--no-smoke --no-vision` for a
  purely local report.
- Smoke `latency_s` is the full `hermes chat` round trip including harness
  startup; `avg_first_response_latency_s` from `state.db` is the model/gateway
  latency proxy. They are not interchangeable.
- A missing `state.db`, models.dev cache, fixture directory, or `hermes` binary
  degrades the affected section instead of failing the report. Missing fixture
  slots are marked `skipped` and do not fail the verdict.
- Discovery is config-driven; a model that is reachable but absent from
  `config.yaml` is not benchmarked.
- Default vision fixtures resolve relative to the checkout. Wheel installations
  may lack them; use `--vision-fixtures-dir` for an explicit fixture location.

## Verification

- `tests/test_benchmark.py` — `TestDiscoverModelTargets` (default, aliases,
  provider resolution, auxiliary, dedup), `TestStateDbAggregation` (grouping,
  window, reasoning, first-response latency), `TestSlugMatching`,
  `TestEnrichCapabilities`, `TestCacheTTL`, `TestRun` (no-smoke, stub smoke,
  cached reuse, stale refresh, missing DB, failure recording),
  `TestParallelism`, `TestVisionMatching`, `TestVisionRun`, `TestVisionRenderer`,
  `TestRenderer`, and `TestCli` (help, resolution, JSON, quiet, default print,
  no-vision).

## Related contracts

- [Architecture](../../architecture.md)
- [Repository contract](../../../AGENTS.md)
