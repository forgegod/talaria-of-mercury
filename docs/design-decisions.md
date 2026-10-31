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

## Native OpenSpec with DOX underneath

DOX owns repository instructions. Native current specs own material requirements
with implementation/test evidence. Native change packages own proposal, delta
specs, design and `tasks.md`; only the orchestrator writes tasks and Git. Issues
and PRs are request/review documents, not task mirrors. This avoids maintaining
a bespoke lifecycle parser or competing progress systems. It also requires
mediating native apply checkbox writes and reconciling shared-spec overlaps.

Material changes evolve code, tests, deltas and affected visuals together;
accepted requirements are synced before terminal closure. Behavior-preserving
work does not churn requirements. Local validation is not integration: exact-SHA
independent review, separately authorized integration, integrated gates and factual
Git-tracked evidence precede archive. No setup change or retrospective integration
receipt is invented during initialization. Existing unfinished work retains its
evidence and pending acceptance rather than being implicitly closed.

## Specification tooling stays outside the Python runtime

The dependency-free local checker and synthetic fixture suites run on Node 24.
OpenSpec 1.15.0 supplies native strict validation rather than a parallel parser.
The local checker adds repository containment, pytest evidence recognition,
single-task-writer structure and canonical visual inventory. These are structural
checks; real Python and Node test execution remain separate gates.

OpenSpec is a separately installed maintenance CLI. CI obtains the exact package
version in a job-local npm prefix with install scripts disabled; transitive
dependencies resolve at installation, not from a Talaria Node lockfile. Python
packaging stays in `pyproject.toml`/`uv.lock`; no Node application manifest or
runtime dependency is introduced. This costs contributors a tooling prerequisite
and CI a public-registry install, but never adds OpenSpec to the installed CLI.
Native host workflows and companion packages remain externally owned and require
separate user/profile installation authority; no local adapters are shipped.

## Offline acceptance evidence

CI and default tests use synthetic profile data, temporary files/databases, and
stub external calls. Model integration is explicitly opt-in and force-disabled
in CI. Local profile paths and process listeners are not reliable test fixtures;
CLI maintenance probes must not signal arbitrary host processes. This keeps the
quality gate reproducible and prevents verification from spending model tokens
or mutating an operator's environment.
