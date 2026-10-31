# Python 3.10 doctor log timestamp compatibility

## Why

Direct operator request: Include the compatibility fix here; do not stage or commit anything.

Python 3.10 rejects a comma fractional separator passed directly to
`datetime.fromisoformat`. The doctor scanner catches ValueError and silently
skips those log lines. The original implementation baseline is `5c31786`.
This existing request retains its `python310-log-timestamps` identity, evidence
and unfinished integration boundary; workflow migration does not authorize closure.

## What Changes

- Normalize comma fractional separators before parsing doctor log timestamps.
- Keep severity gating, invalid-input handling and look-back filtering unchanged.
- Preserve the deterministic regression and verify Python 3.10 and 3.14.
- Retain current doctor requirements and independent review/integration gates.

The implementation is already present in commit `50fbbc7` and in the current
checkout. Resume by verifying the existing code, not replaying the fix. Its prior
local evidence is in [evidence](evidence.md); it is not an integrated acceptance
receipt. Native [tasks](tasks.md) owns remaining work.

## Capabilities

### Modified Capabilities

- `doctor-diagnostics-remediation`: count standard comma-millisecond log timestamps on every supported Python version.

## Impact

Implementation: `src/talaria/hermes/doctor.py`.
Regression: `tests/test_doctor.py`, `TestScanLogTruncations.test_comma_millisecond_timestamp`.
No primary visual surface. No runtime dependency or Python-minimum change.

## Authority and non-goals

The original bounded fix was authorized in the current checkout without a
committed reservation or worktree. No staging, commits, pushes or new worktree
were authorized by that request. The migration request authorizes local artifact
conversion and gates only; it does not lift those restrictions or authorize
integration, archive or profile writes. Other runtime bugs, doctor write defaults,
thresholds, detector inventory and model calls remain out of scope.
