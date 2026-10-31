# tests/records

## Purpose

Behaviour tests for the development-only OpenSpec validator in
`scripts/check-openspec.mjs`. They exercise the validator against synthetic
fixture trees and never read the repository's own `openspec/` records.

## Ownership

| File | Owns |
| --- | --- |
| `fixtures.mjs` | Fixture-tree builders: `buildValidTree`, `createFixtureTree`, `patchFixture`, `addFixtureFile`, `addCapability`, `addChangePackage`, `addVisuals`. |
| `records.test.mjs` | Capability spec identity, metadata, sections, ordering, requirement/scenario shape, verification evidence (JavaScript and pytest), implementation references, index paths, and links. |
| `changes.test.mjs` | Change package naming, active/archive placement, proposal/design/tasks shape, the single task-progress writer, and native delta sections. |
| `artifacts.test.mjs` | Canonical visuals: manifest, artifact inventory, capability visual links, and link containment. |
| `checker-regressions.test.mjs` | Regression coverage: symlink fail-closed behaviour, filesystem diagnostics, CommonMark links, section ordering, delta preambles, and CLI exit status. |

## Local Contracts

- These are `node:test` suites, not pytest modules. `pytest` never collects this directory (only `test_*.py` / `*_test.py` files are collected, and none live here); the canonical command is in `../AGENTS.md`.
- Imports reach the validator as `../../scripts/check-openspec.mjs`; fixtures are imported relatively as `./fixtures.mjs`.
- Every test builds its tree with `createFixtureTree()` under `os.tmpdir()` (honours `TMPDIR`) and calls `cleanup()` in a `finally` block or registers it with `t.after()`. Tests must not read or write the repository's real `openspec/` or `docs/` tree.
- Fixture trees are generic: a minimal valid capability spec plus the mutation that trips one error. Do not copy real product specs into fixtures.
- `fixtures.mjs` is a helper module, not a test file; do not rename existing fixture keys or builder signatures by hand — extend with new helpers instead. The fixture evidence file (`tests/workflow.test.mjs`) must stay a real test with an assertion.

## Work Guidance

- Add the failing fixture test before changing the validator, then make the minimal validator change that satisfies it. Assert on stable error substrings, and include the actual error list in the assertion message.
- Cover both the accepted shape and its rejection twin (for example, a `test_*.py` module with a test definition and assertion versus a `test_*_helpers.py` module without them).
- Keep evidence kinds additive: Python and JavaScript support must both stay covered, and new filters must not replace existing ones.

## Verification

- `node --test tests/records/*.test.mjs` from the repository root must exit 0.
- Full pytest suite (`pytest`) must stay green; these files must not be collected by it.

## Child DOX Index

No child DOX documents. Parent contract: `../AGENTS.md`.
