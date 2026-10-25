# Design decisions

This document records durable choices and their consequences. The live contract
is [architecture.md](architecture.md), supported by implementation and tests.

## Python CLI; Hermes remains external

Talaria uses Python, argparse, PyYAML, and psutil. Hermes owns its agent runtime,
skill installer, and model execution; Talaria invokes its CLI rather than
vendoring those systems. Distribution name `talaria-of-mercury` and executable
`talaria` are distinct. This keeps installation small but means integrations
depend on the installed Hermes CLI's behaviour and must be tested with stubs
plus separately authorized live checks.

## Explicit profile selection and bounded writes

Talaria resolves profiles from its own flags, `HERMES_PROFILE`, the active-profile
file, and the default profile, not the running agent's `HERMES_HOME`. Subprocess
skill operations receive the selected profile home explicitly. This prevents an
inherited agent home from silently selecting a different target.

Write defaults are command-specific. Profile sync/config helpers apply by
default; destructive doctor/skill-prune repairs require `--apply`; log actions
require explicit limits. Unifying these defaults would change the public CLI
contract and is not a documentation cleanup. Atomic replacement and `.bak` apply
to individual supported file writes, not whole profiles or live SQLite databases.

## One authority for each kind of record

CAPs describe current material behaviour with implementation/test evidence.
Active CHGs own progress for material requested work; external requests explain
why. Archived CHGs and review assets are receipts. This avoids competing truth
in README prose, chat plans, and ticket status. Material changes update code,
tests, CAPs, and CHGs together; behaviour-preserving maintenance does not require
record churn. No retrospective change receipt is invented for initialization.

## Record validation is development-only Node tooling

The dependency-free validator and its fixture suites run directly on Node 24.
Retaining the existing lifecycle/link/review/wireframe checks avoids a second,
partial reimplementation in Python. Pytest evidence recognition adapts the gate
to Talaria's source tests. No Node package manager or runtime dependency is
introduced, and Python packaging stays in `pyproject.toml`/`uv.lock`.

The cost is a Node prerequisite for contributors and CI running record checks.
The validator recognizes test-shaped evidence structurally; actual Python and
Node tests are separate required gates, not inferred from references.

## Offline acceptance evidence

CI and default tests use synthetic profile data, temporary files/databases, and
stub external calls. Model integration is explicitly opt-in and force-disabled
in CI. Local profile paths and process listeners are not reliable test fixtures;
CLI maintenance probes must not signal arbitrary host processes. This keeps the
quality gate reproducible and prevents verification from spending model tokens
or mutating an operator's environment.
