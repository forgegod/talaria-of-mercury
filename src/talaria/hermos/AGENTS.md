# src/talaria/hermos

## Purpose

Hermes-specific Talaria features — read-only inspection of the agent's
`state.db` and `logs/` to verify mitigations, surface regressions, and
deliver verdicts to operators.

## Ownership

- Each feature is a single module exposing `run(paths, **opts) -> dict`
  and `render_human(report) -> tuple[int, str]`.
- The first feature is `moa_truncation`, ported from
  `~/.hermes/scripts/check_moa_truncation.py`.

## Local Contracts

- `moa_truncation.MOA_OUTPUT_TOKEN_ALERT` and `DEFAULT_LOOKBACK_DAYS` are
  the canonical thresholds. Override per-call via flags, not by mutating
  module attributes.
- Log severity gating: Signal B only counts matches on
  `WARNING|ERROR|CRITICAL` lines, never INFO. The
  `STREAM_DROP_PATTERN` count is reported separately and never triggers
  the alert.
- Reports must include `fired: bool` so JSON consumers can branch on the
  exit signal without parsing human output.

## Work Guidance

- When porting from the standalone `~/.hermes/scripts/` directory, keep the
  behaviour identical but reshape into `run()` + `render_human()`. Do not
  preserve the standalone CLI flag spelling unless it matches Talaria
  conventions (`--state-db`, `--log-dir`, `--profile`).
- Feature-specific constants (regexes, thresholds, default windows) live at
  the top of the module.

## Verification

- Signal functions are tested against a synthetic SQLite database created
  with `tests._helpers.make_sessions_db`.
- Log scans are tested with hand-crafted lines in a tmpdir; severity gating
  is the must-test edge case.

## Child DOX Index

- `moa_truncation.py` — Signal A (output_tokens trend) + Signal B (length-class
  log markers). First feature; canonical shape for future ports.