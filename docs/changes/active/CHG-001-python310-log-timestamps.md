# CHG-001 — Python 3.10 doctor log timestamp compatibility

**Status:** in-progress
**External request:** Direct operator request: Include the compatibility fix here; do not stage or commit anything.
**Impacts:** CAP-002
**Baseline:** `5c31786`

| # | Phase | Status | Verification gate |
| --- | --- | --- | --- |
| 1 | Normalize log fractions and verify supported runtimes | in-progress | `uv run --extra dev pytest`; same suite on Python 3.10; `node scripts/check-product-records.mjs` |
| 2 | Integrate the approved candidate | pending | Authorized integration, followed by Python and record gates on the integrated revision |

## Phase 1 — Normalize log fractions and verify supported runtimes

**Goal:** Doctor counts standard comma-millisecond log records on Python 3.10
as it does on the development interpreter, retaining severity and time filtering.

- Python 3.10 rejects `datetime.fromisoformat("2026-10-25 17:00:00,123")`;
  the scanner catches ValueError and silently skips the line. The baseline
  minimum-version suite had five log-scanning failures; Python 3.14 passed.
- Add a deterministic comma-millisecond timestamp regression; normalize the fractional
  separator before parsing. Keep invalid-input and window behaviour unchanged.
- Update CAP-002 and local DOX; execute the full suite on Python 3.10 and 3.14.
- Operator explicitly permits this bounded fix in the current working tree
  without the usual committed reservation/worktree workflow. No staging or
  commits are authorized. CAP-002 has no primary visual surface.

**Verification gate:** Both Python suites, record validation, and `git diff --check`
exit 0.

**Evidence:** The deterministic comma-millisecond regression failed on Python
3.10 before normalization. Full-suite reruns after the fix on Python 3.10 and
3.14 each returned 574 passed, 5 live-model checks skipped, and 1 non-strict
curator-parity XPASS (exit 0). Runs used temporary HOME directories, unset the
profile-config override/live opt-in, and forced live calls off. `uv build`,
`node scripts/check-product-records.mjs` (7 CAPs, 1 CHG), all 81 Node validator
tests, and `git diff --check` passed. These are local working-tree results,
not a GitHub Actions run or committed/integrated revision.
The implementation gate is green; the phase remains in progress until authorized
review/integration closeout rather than claiming a committed completion.

## Phase 2 — Integrate the approved candidate

**Goal:** Integrate the reviewed candidate only when separately authorized,
verify the integrated revision, then archive this record and update its index.

**Verification gate:** Authorized integration plus the phase-1 gates on that
revision; no archive claim before integration. This phase is not authorized by
the current no-commit request.

## Out of scope

- Other runtime bugs, changing the Python minimum, or weakening CI tests.
- Changing doctor write defaults, thresholds, detector catalog, or model calls.
- Staging, commits, pushes, or worktree creation without new authorization.
