# CAP-002 — Doctor diagnostics and remediation

**Status:** implemented
**Primary surface:** none

## Behaviour

- `talaria hermes doctor` scans the resolved profile and reports findings. Twelve
  deterministic detectors run against `state.db`, `logs/`, and the skill
  registry: `truncation_output`, `truncation_finish_reason`,
  `truncation_log_markers`, `stream_drops`, `compression_stale_locks`,
  `compression_failures`, `rewinds`, `handoff_errors`, `cost_anomalies`,
  `zombie_sessions`, `ghost_sessions`, and `skill_index_drift`.
- The look-back window is `--days` (default 2) or `--since YYYY-MM-DD`. Log
  discovery covers `*.log` and `*.log.*` at the top of `logs/`; `logs/curator/`
  is excluded unless `--include-curator` is passed. Log-marker counting only
  considers `WARNING`, `ERROR`, and `CRITICAL` lines, and the window is applied
  per line.
- Standard comma-millisecond log timestamps are parsed on Python 3.10 and newer;
  Python's version-specific ISO parser support does not suppress log findings.
- `--only` and `--skip` select detector ids. Unknown ids exit 2. The report
  always carries `selected_detectors`, `skipped_detectors`, `detector_errors`,
  and a top-level `fired` flag; the human renderer prints a `skipped:` header
  when any detector was excluded.
- A default-on free-flight curator pass sends a redacted `config.yaml`, log
  references, and sliced `state.db` metadata to the profile's curator model via
  `hermes chat -q`. `--no-free-flight` disables it. A missing `hermes` binary,
  timeout, or unparseable reply degrades to an informational finding.
- Each detector result carries a `remediation` hint when an action exists.
  Hints are doctor flags (`--prune-stale-locks [--apply]`,
  `--close-zombies [--apply]`, `--prune-ghost-sessions [--apply]`) or a sibling
  command (`talaria skills prune …`). The renderer prints `fix:` only for fired
  findings.
- Two remediation paths exist. `--apply-curator-suggestions` writes only
  `free_flight:config:*` suggestions to `config.yaml` through the atomic backup
  writer; `--dry-run` computes the same change and reports a unified diff
  without writing. Tactical flags `--prune-stale-locks`, `--close-zombies`, and
  `--prune-ghost-sessions` preview by default; the shared `--apply` gate turns
  them into writes against `state.db`.
- Exit code is 0 clean, 1 when any deterministic or free-flight anomaly fires,
  2 for unknown detector ids. The human report prints by default; `-q/--quiet`
  reduces the run to an exit code. `--json` always prints.

## Implementation

- `src/talaria/hermes/doctor.py` — detector SQL/scans, orchestrator, report
  shape, `apply_config_suggestions`, `apply_tactical_actions`, renderer.
- `src/talaria/hermes/doctor_free_flight.py` — evidence assembly, config
  redaction, prompt building, finding parsing.
- `src/talaria/hermes/doctor_llm.py` — curator model resolution and the
  `hermes chat -q` subprocess runner.
- `src/talaria/hermes/skill_index.py` — three-source skill index consumed by the
  `skill_index_drift` detector.

## Rules and boundaries

- Deterministic detection reads profile state. The curator pass also writes
  temporary evidence slices and invokes Hermes; it is not a side-effect-free
  process. Profile repairs are the explicit curator-apply and tactical paths.
- Doctor `--dry-run` governs curator suggestions, not tactical flags. A tactical
  `--apply` still permits database writes even when `--dry-run` is supplied.
- One failing detector is isolated: its error is recorded in `detector_errors`
  and the remaining detectors still run. A missing `state.db` marks SQL-backed
  detectors as skipped rather than failing the command.
- Tactical writes go straight to `state.db` via SQLite WAL and deliberately do
  not create a `state.db.bak`. Ghost deletion is limited to the look-back window;
  out-of-window ghosts stay diagnostic-only.
- Free-flight redaction protects the model prompt only. Config suggestions,
  dry-run diffs, and the resulting report can contain raw config values; do not
  treat the doctor report itself as a secret-free artifact.
- The free-flight pass is a model/network call and is on by default. Bound it
  with `--no-free-flight`, `free_flight_log_lines` (default 200 per log file),
  and the per-call timeout (default 180 s). A `log_lines=0` run short-circuits to
  `free_flight:skipped`.
- `--apply-curator-suggestions` is curator-only: anomaly findings are never
  applied. Tactical flags are independent of it and of each other.
- The machine-readable `detector_catalog` in `--show-resolution` currently lists
  eleven detectors and omits `skill_index_drift`; `detector_ids` lists all
  twelve. Do not use the catalog block as a complete inventory.

## Verification

- `tests/test_doctor.py` — `TestOrchestrator` (selection, unknown ids, error
  isolation, missing DB), `TestRenderer`, `TestApplySuggestions`
  (dry-run, backup, parent creation, coercion), `TestTacticalActions`
  (dry-run vs apply, idempotency, out-of-window preservation, stable keys),
  `TestRemediationHints` (hint shape, consistency, renderer gating),
  `TestRedaction`, `TestFreeFlight` (finding kinds, parsing, degradation),
  `TestDiscoverLogFiles`, `TestScanLogTruncations`,
  `TestSkillIndexDrift`, and `TestCli` (exit codes, `--only`, quiet/default
  printing, `--show-resolution`).
- `tests/test_doctor.py` — `TestScanLogTruncations::test_comma_millisecond_timestamp`
  is the deterministic minimum-Python compatibility regression.
- `tests/test_skill_index.py` — `TestReadIndex` drift classes shared with
  `skills prune`.

## Related contracts

- [Architecture](../../architecture.md)
- [Repository contract](../../../AGENTS.md)
