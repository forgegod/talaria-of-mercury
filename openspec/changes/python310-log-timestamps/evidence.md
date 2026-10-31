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
