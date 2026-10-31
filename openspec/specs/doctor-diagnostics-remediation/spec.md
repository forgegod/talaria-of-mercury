# Doctor diagnostics and remediation

**Primary surface:** none

## Purpose

Scan one resolved Hermes profile for deterministic anomalies, optionally add a
curator-model pass, and offer explicitly gated repairs. Detection reads profile
state; the default-on free-flight pass is a model/network call; profile repairs
happen only through the named apply paths. The report is not a secret-free
artifact even though the model prompt is redacted.

## Requirements

### Requirement: Deterministic detector inventory and look-back window

Doctor SHALL provide twelve deterministic detectors over `state.db`, logs and
the skill registry. The window SHALL use `--days` (default 2) or `--since
YYYY-MM-DD`. Discovery SHALL cover top-level `*.log` and `*.log.*`, excluding
`logs/curator/` unless `--include-curator` is supplied. Log-marker counts SHALL
include only `WARNING`, `ERROR` and `CRITICAL` lines inside the window.

#### Scenario: All deterministic detectors are selected

- **WHEN** neither `--only` nor `--skip` restricts the scan
- **THEN** it selects `truncation_output`, `truncation_finish_reason`, `truncation_log_markers`, `stream_drops`, `compression_stale_locks`, `compression_failures`, `rewinds`, `handoff_errors`, `cost_anomalies`, `zombie_sessions`, `ghost_sessions`, and `skill_index_drift`

#### Scenario: A curator snapshot sits under logs

- **WHEN** `logs/curator/<timestamp>/agent.log` exists and `--include-curator` is absent
- **THEN** that snapshot contributes no matches and is not counted as a scanned top-level log
- **AND** passing `--include-curator` walks the snapshot tree in addition to the top-level files

#### Scenario: A long log mixes old and recent lines

- **WHEN** one log file contains matching lines both before and after the window start
- **THEN** only lines at or after the window start are counted
- **AND** an `INFO` line that mentions a length pattern is not counted as a truncation marker

### Requirement: Comma-millisecond log timestamps

Doctor SHALL parse standard comma-millisecond log timestamps
(`YYYY-MM-DD HH:MM:SS,mmm`) on Python 3.10 and newer by normalising the comma
separator before ISO parsing. The interpreter's version-specific
fractional-second support MUST NOT suppress log findings.

#### Scenario: A comma-millisecond warning falls inside the window

- **WHEN** a log line reads `2026-01-02 12:00:00,123 WARNING agent: finish_reason='length'` and the resolved look-back window starts at `2026-01-02 12:00:00` UTC, on Python 3.10 or newer
- **THEN** `truncation_log_markers` counts the line as a length-class hit instead of skipping it as unparsable
- **AND** the hit is attributed to the log file that contains it
- **AND** the same line before the window start is ignored

### Requirement: Detector selection, isolation, and report shape

`--only` and `--skip` SHALL select detector ids, and unknown ids MUST exit 2.
Detector failures SHALL be isolated: errors are recorded in
`detector_errors` while the remaining detectors still run. A missing
`state.db` MUST mark SQL-backed detectors as skipped rather than failing the
command. Every report SHALL carry `selected_detectors`, `skipped_detectors`,
`detector_errors`, and a top-level `fired` flag, and the human renderer SHALL
print a `skipped:` header when any detector was excluded.

#### Scenario: One detector raises

- **WHEN** a detector throws while others are selected
- **THEN** the report records the failure in `detector_errors` and the other detectors still produce verdicts

#### Scenario: The profile has no state database

- **WHEN** `state.db` is absent
- **THEN** SQL-backed detectors are reported as skipped and a log-only run can still complete

#### Scenario: An unknown detector id is requested

- **WHEN** `--only` or `--skip` names an id outside the twelve
- **THEN** the command prints an error to stderr and exits 2

### Requirement: Free-flight curator pass

A curator pass SHALL run by default and send a redacted
`config.yaml`, log references, and sliced `state.db` metadata to the profile's
curator model through `hermes chat -q`. `--no-free-flight` SHALL disable it. A
missing `hermes` binary, a timeout, or an unparseable reply MUST degrade to an
informational finding, not fail the run. The per-file log budget
SHALL default to 200 lines and the per-call timeout to 180 seconds; a
zero-line budget MUST short-circuit to `free_flight:skipped`.

#### Scenario: The curator model is unavailable

- **WHEN** the free-flight pass runs but `hermes chat` is missing, times out, or returns unparseable output
- **THEN** the report carries a degraded free-flight finding and the deterministic verdict is unaffected

#### Scenario: The operator opts out of model calls

- **WHEN** `--no-free-flight` is supplied
- **THEN** no curator subprocess is started and no evidence slices are sent to a model

### Requirement: Redaction and evidence boundaries

Free-flight redaction SHALL protect the model prompt only. Config suggestions,
dry-run diffs, and the resulting report MAY contain raw config values, so the
doctor report MUST NOT be treated as a secret-free artifact.

#### Scenario: A secret-bearing config is summarised

- **WHEN** redaction removes secret parent keys and credential-like children from the prompt
- **THEN** legitimate non-secret keys remain available to the model
- **AND** the returned suggestion text and diff can still echo raw values from the profile

### Requirement: Remediation hints

Each detector result SHALL carry a `remediation` hint when an action exists.
Hints SHALL be doctor flags (`--prune-stale-locks [--apply]`,
`--close-zombies [--apply]`, `--prune-ghost-sessions [--apply]`) or a sibling
command (`talaria skills prune …`). The renderer MUST print `fix:` only for
fired findings.

#### Scenario: A zombie session fires

- **WHEN** `zombie_sessions` fires
- **THEN** its result carries the `--close-zombies [--apply]` hint and the human report prints a `fix:` line

#### Scenario: A clean detector is rendered

- **WHEN** a detector with a known hint does not fire
- **THEN** the renderer omits the `fix:` line for it

### Requirement: Curator suggestion application

`--apply-curator-suggestions` SHALL write only `free_flight:config:*`
suggestions to `config.yaml` through the atomic backup writer. `--dry-run`
SHALL compute the same change and report a unified diff without writing.
Anomaly findings MUST NOT be applied by this path.

#### Scenario: Suggestions are previewed

- **WHEN** `--apply-curator-suggestions --dry-run` is supplied
- **THEN** the report contains the computed diff and `config.yaml` is byte-identical afterwards

#### Scenario: Suggestions are applied

- **WHEN** `--apply-curator-suggestions` runs without `--dry-run`
- **THEN** only config-suggestion findings are written, a backup is created, and anomaly findings remain report-only

### Requirement: Tactical repairs and the shared apply gate

`--prune-stale-locks`, `--close-zombies`, and `--prune-ghost-sessions` SHALL
preview unless `--apply` enables SQLite WAL writes, without `state.db.bak`.
Ghost deletion SHALL stay inside the look-back window. `--dry-run` governs
curator suggestions only and MUST NOT override tactical `--apply`. Tactical
flags SHALL be independent of each other and curator application; detection
SHALL precede repair.

#### Scenario: Tactical and curator repairs are combined

- **WHEN** independent tactical and curator apply flags are selected together
- **THEN** each uses its own gate, and tactical database writes do not create a `state.db.bak`
- **AND** per-detector findings describe the pre-repair scan; out-of-window ghosts remain diagnostic-only

#### Scenario: A tactical repair is previewed

- **WHEN** a tactical flag runs without `--apply`
- **THEN** the report lists the planned rows and `state.db` is unchanged

#### Scenario: Preview and apply flags are combined

- **WHEN** `--prune-stale-locks --apply --dry-run` is supplied
- **THEN** the tactical repair still writes because `--dry-run` applies only to curator suggestions

#### Scenario: An out-of-window ghost is present

- **WHEN** `--prune-ghost-sessions --apply` runs with a ghost session older than the window
- **THEN** in-window ghosts are deleted and the older ghost is preserved

### Requirement: Detector inventory disclosure

`detector_ids` SHALL list all twelve detectors, while the machine-readable
`detector_catalog` block in `--show-resolution` currently lists eleven and
omits `skill_index_drift`. Consumers MUST NOT use the catalog block as a
complete inventory.

#### Scenario: An operator reads the catalog block

- **WHEN** `--show-resolution` prints `detector_catalog`
- **THEN** it documents eleven detectors even though `detector_ids` and the runnable set contain twelve

### Requirement: Output modes and exit codes

The human report SHALL print by default, `-q/--quiet` SHALL reduce the run to
an exit code, and `--json` MUST always print. Exit code SHALL be 0 when clean,
1 when any deterministic or free-flight anomaly fires, and 2 for unknown
detector ids.

#### Scenario: A clean profile is checked quietly

- **WHEN** `hermes doctor --quiet` finds nothing
- **THEN** stdout is empty and the exit code is 0

#### Scenario: A finding fires

- **WHEN** at least one deterministic detector or the free-flight pass reports an anomaly and the run is not quiet
- **THEN** the human report ends in a fired verdict and the exit code is 1

## Verification

- `src/talaria/hermes/doctor.py` — detector SQL and scans, orchestrator, report shape, `apply_config_suggestions`, `apply_tactical_actions`, renderer, `show_resolution`, cutoff and warning logic, and comma-millisecond timestamp normalisation.
- `src/talaria/hermes/doctor_free_flight.py` — evidence assembly, config redaction, prompt building, finding parsing.
- `src/talaria/hermes/doctor_llm.py` — curator model resolution and the `hermes chat -q` subprocess runner.
- `src/talaria/hermes/skill_index.py` — three-source skill index consumed by the `skill_index_drift` detector.
- `tests/test_doctor.py` — `TestOrchestrator` (selection, unknown ids, error isolation, missing DB), `TestRenderer`, `TestApplySuggestions` (dry-run, backup, parent creation, coercion), `TestTacticalActions` (dry-run versus apply, idempotency, out-of-window preservation, stable report keys), `TestRemediationHints` (hint shape, consistency, renderer gating), `TestRedaction`, `TestFreeFlight` (finding kinds, parsing, degradation), `TestDiscoverLogFiles`, `TestScanLogTruncations`, `TestSkillIndexDrift`, and `TestCli` (exit codes, `--only`, quiet and default printing, `--show-resolution`, skipped header).
- `tests/test_doctor.py` — `TestScanLogTruncations::test_comma_millisecond_timestamp` feeds a comma-millisecond warning line and asserts one length-class hit, which is the deterministic minimum-Python compatibility regression.
- `tests/test_skill_index.py` — `TestReadIndex` drift classes shared with `skills prune`.
- [Architecture](../../../docs/architecture.md) — diagnostic and repair side-effect table.
- [Repository contract](../../../AGENTS.md) — record authority, write boundaries, and evidence isolation.
- Related current specs: [CLI entry point, profile resolution, and shell completion](../cli-entry-profile-resolution-completion/spec.md), [Skill lifecycle](../skill-lifecycle/spec.md).
