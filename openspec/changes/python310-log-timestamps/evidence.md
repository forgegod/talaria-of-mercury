# Evidence provenance

## Prior local verification

The original change record reported the deterministic comma-millisecond
regression failing on Python 3.10 before normalization; the baseline minimum
runtime suite had five log-scanning failures while Python 3.14 passed.

Its recorded post-fix suites on Python 3.10 and 3.14 each returned 574 passed,
5 live-model checks skipped, and 1 non-strict curator-parity XPASS (exit 0).
Those runs used temporary HOME directories, unset the profile-config override
and live opt-in, and forced live calls off. It also recorded a successful wheel
and sdist build, structural validation of seven capability records and one
change record, all 81 former validator tests, and `git diff --check`.

These are preserved prior local results, not results fabricated or rerun by
conversion, not GitHub Actions evidence, and not an integrated acceptance receipt.
The original record deliberately left review/integration unfinished.

## Source identity

Original implementation baseline: `5c31786`. The normalization is present in
commit `50fbbc7` (`fix(doctor): parse comma log timestamps`), included in the
migration starting revision `8b640c8`. Presence in Git does not establish the
missing authorized independent review or integrated-revision verification.

The behavioral regression uses `2026-01-02 12:00:00,123 WARNING agent:
finish_reason='length'` with a fixed UTC window in `tests/test_doctor.py`.
This evidence document does not own task progress; see [tasks](tasks.md).

## Current local reverification

The uncommitted working tree based on `8b640c8` was reverified after replacing
the superseded records and validator. Python runtime files, Python tests,
`pyproject.toml` and `uv.lock` are unchanged from that base.

- Full pytest suites on Python 3.10.20 and 3.14.7 each returned 574 passed,
  5 skipped and 1 non-strict XPASS, exit 0. Each used a temporary HOME, unset
  `_TESTING_TALARIA_PROFILE_CONFIG` and `_TESTING_TALARIA_RUN_MODEL_BENCH`, and
  set `_TESTING_TALARIA_SKIP_MODEL_BENCH=1`.
- `uv build` produced the wheel and source distribution successfully.
- `node scripts/check-openspec.mjs` passed seven specs and one change package.
- OpenSpec 1.15.0 `validate --all --strict` passed all eight items with
  telemetry disabled and isolated HOME/XDG configuration.
- `node --test tests/records/*.test.mjs` passed 105 tests with no failures.
- `git diff --check` passed. Local Markdown links and preservation of every
  original implementation/test file reference were checked separately.

These are local working-tree results, not GitHub Actions, independent exact-SHA
review, authorization to integrate, or an integrated acceptance receipt.
