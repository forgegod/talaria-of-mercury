# Design

## Decisions

Normalize the comma fractional separator to a dot before ISO parsing in the log
scanner. Keep timezone treatment, invalid-input suppression, severity filtering,
and the time window unchanged. Retain a fixed timestamp regression independent
of wall-clock time, and run it on Python 3.10 as well as the development runtime.

Current requirements already describe the implementation. The retained native
modified delta records this request's scope; do not blindly replay it or infer
integration approval from its presence. At authorized closeout, reconcile it
against the current requirement before sync/archive.

## Verification

Use synthetic log/database fixtures and isolated HOME directories. Force live
model calls off. Run the full Python suite on 3.10 and 3.14, build artifacts,
local structural validation, pinned upstream strict validation, Node fixture
suites and `git diff --check`. Independent exact-candidate review and authorized
integrated-revision gates remain separate from local verification.
