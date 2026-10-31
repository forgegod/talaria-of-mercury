# Model benchmark

**Primary surface:** none

## Purpose

Report per-model health, cost, latency, and capability data for every model
pair the profile routes through. The command is read-only against Hermes state
but is not side-effect free: it writes its own result cache and, by default,
makes model calls. Smoke latency and gateway latency are different measures and
must not be substituted for each other.

## Requirements

### Requirement: Config-driven model discovery

`talaria hermes benchmark` SHALL report every unique `(model, provider)` pair
referenced by the profile's `config.yaml`: `model.default`, every
`model.aliases` entry, and every `auxiliary.<usecase>.model`. Duplicate pairs
MUST be collapsed and each entry SHALL list its `sources`. Discovery is
config-driven: a reachable model absent from `config.yaml` MUST NOT be
benchmarked.

#### Scenario: Several config paths name one model

- **WHEN** the same model and provider appear as the default, an alias target, and an auxiliary pin
- **THEN** the report contains one entry for the pair whose `sources` names all three config paths

#### Scenario: An auxiliary pin is empty

- **WHEN** an `auxiliary.<usecase>.model` value is missing or empty
- **THEN** that use case contributes no benchmark target

### Requirement: State database aggregation

Aggregation SHALL cover the `--days` window (default 7) and report call count,
average input, output, cache, and reasoning tokens, total, average, and maximum
actual cost, the most recent `model_config` reasoning level, and average
first-response latency measured from the first user message to the next
assistant message. `state.db` MUST be opened in read-only mode.

#### Scenario: Sessions span the window boundary

- **WHEN** the database contains sessions both inside and outside `--days`
- **THEN** only in-window sessions contribute to the aggregates

#### Scenario: No model calls are made

- **WHEN** `--no-smoke --no-vision` is supplied
- **THEN** the report contains state.db and capability sections only and no subprocess is started

### Requirement: Capability enrichment from models.dev

Capabilities SHALL come from `~/.hermes/models_dev_cache.json`, matched by the
model slug after the last `/`. Enrichment SHALL cover reasoning, tool call,
vision (attachment or image input), structured output, context and output
limits, per-million-token cost, and family and name. A missing or unparseable
cache MUST degrade the capability section instead of failing the report.

#### Scenario: A gateway prefixes the model id

- **WHEN** the config names `kilocode/zai-coding/glm-5.2` and the cache keys the same slug
- **THEN** capabilities resolve by the trailing slug and the gateway prefix does not defeat the match

### Requirement: Smoke calls and cache

A JSON smoke call through `hermes chat -q` SHALL be made once per discovered
model when its cache entry is stale. `--ttl` SHALL default to 1800 seconds.
`--no-smoke` MUST disable smoke calls only, not vision calls; disabling both
SHALL produce a local-only report. Results SHALL be cached at
`$XDG_CACHE_HOME/talaria/benchmark-cache-<profile>.json`, keyed by
`<model>--<provider>` for smoke and `<model_id>::vision::<fixture>` for vision.

#### Scenario: A fresh cache entry exists

- **WHEN** a model's smoke result is younger than `--ttl`
- **THEN** the cached result is reused and no smoke subprocess runs

#### Scenario: The cache entry is stale

- **WHEN** the smoke result is older than the TTL and smoke is enabled
- **THEN** one fresh smoke call replaces the entry for that model

#### Scenario: The model fails the smoke prompt

- **WHEN** the reply is not parseable JSON or the call fails
- **THEN** the failure is recorded for that model and other models still run

### Requirement: Vision checks

Vision checks SHALL run for models whose capabilities include vision. Four
checked-in images under `assets/benchmark/vision/` SHALL cover counting and colour, OCR, spatial
reasoning, and logo recognition, and ground truth SHALL accept
`|`-separated alternatives. `--no-vision` SHALL disable the checks and
`--vision-fixtures-dir` SHALL override the fixture root. A missing fixture slot
MUST be marked `skipped` and MUST NOT fail the verdict.

#### Scenario: A non-vision model is discovered

- **WHEN** the capability record does not advertise image input
- **THEN** no vision call is made for that model

#### Scenario: The fixture directory is absent

- **WHEN** vision fixtures cannot be found at the default location and no override is given
- **THEN** vision checks report skipped slots and the smoke verdict still determines the exit code

### Requirement: Parallel execution

Smoke and vision calls SHALL run in a `ThreadPoolExecutor` with `--jobs/-j`
defaulting to 8, and `--jobs 1` MUST execute sequentially.

#### Scenario: Several models are checked

- **WHEN** more than one model or fixture is eligible and `--jobs` is above one
- **THEN** calls overlap in worker threads instead of running one after another

### Requirement: Side-effect boundary

The command MUST open `state.db` read-only, read `config.yaml` and the models.dev
cache, and write only its own benchmark cache file. It SHALL make model and
network calls by default; `--no-smoke --no-vision` is the only local-only mode.
Delegated Hermes and model execution carries its own runtime effects.

#### Scenario: Default benchmark runs

- **WHEN** the operator runs the command with no disabling flags
- **THEN** smoke and vision calls may contact model providers and the only Talaria-authored write is the benchmark cache

### Requirement: Latency semantics

Smoke `latency_s` SHALL be the full `hermes chat` round trip including harness
startup, while `avg_first_response_latency_s` from `state.db` SHALL be the
model and gateway latency proxy. The two measures MUST NOT be treated as
interchangeable.

#### Scenario: An operator compares the two latency fields

- **WHEN** both figures are present for one model
- **THEN** they report different measurement boundaries and neither is a substitute for the other

### Requirement: Output and exit codes

Exit code SHALL be 0 when every smoke and vision check passes and 1 when any
model fails. The human report SHALL print by default, `-q/--quiet` SHALL be
exit-code only, and `--json` and `--show-resolution` MUST always print.
Missing state, cache, fixtures, or the `hermes` binary MUST degrade the
affected section rather than fail the report. Wheel installations may lack the
checked-in fixtures; `--vision-fixtures-dir` is the explicit location.

#### Scenario: The state database is missing

- **WHEN** `state.db` does not exist and a smoke call still succeeds
- **THEN** the aggregation section is empty or degraded and the run does not crash

#### Scenario: A quiet run has a failure

- **WHEN** `--quiet` is supplied and one model fails its check
- **THEN** stdout is empty and the exit code is 1

## Verification

- `src/talaria/hermes/benchmark.py` — model discovery, state.db aggregation, models.dev enrichment, smoke and vision calls, cache, renderer, `show_resolution`.
- `src/talaria/hermes/doctor_llm.py` — `hermes_chat`, the subprocess runner reused for smoke and vision calls.
- `assets/benchmark/vision/ground_truth.json` — fixture answers; sibling PNGs provide image input.
- `assets/benchmark/vision/count_grid.png`, `assets/benchmark/vision/error_card.png`, `assets/benchmark/vision/spatial_arrow.png`, and `assets/benchmark/vision/logo/logo-512.png` — the four checked-in vision fixtures.
- `tests/test_benchmark.py` — `TestDiscoverModelTargets` (default, aliases, provider resolution, auxiliary, dedup), `TestStateDbAggregation` (grouping, window, reasoning, first-response latency), `TestSlugMatching`, `TestEnrichCapabilities`, `TestCacheTTL`, `TestRun` (no-smoke, stub smoke, cached reuse, stale refresh, missing DB, failure recording), `TestParallelism`, `TestVisionMatching`, `TestVisionRun`, `TestVisionRenderer`, `TestRenderer`, and `TestCli` (help, resolution, JSON, quiet, default print, no-vision).
- `tests/test_doctor_aux_models.py` — `TestConfigInvariants` and `TestCuratorParity` check synthetic config discovery and curator parity without model calls; `test_benchmark_vision_live` is opt-in live evidence and is not part of the default suite.
- [Architecture](../../../docs/architecture.md) — benchmark side effects and read-only state boundary.
- [Repository contract](../../../AGENTS.md) — evidence isolation and opt-in live benchmarks.
- Related current spec: [CLI entry point, profile resolution, and shell completion](../cli-entry-profile-resolution-completion/spec.md).
