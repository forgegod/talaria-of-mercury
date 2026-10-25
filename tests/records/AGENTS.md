# tests/records

## Purpose

Behaviour tests for the development-only record validator in
`scripts/check-product-records.mjs`. They exercise the validator against
synthetic fixture trees and never read the repository's own CAP/CHG records.

## Ownership

| File | Owns |
| --- | --- |
| `fixtures.mjs` | Fixture-tree builders: `buildValidTree`, `createFixtureTree`, `patchFixture`, `addFixtureFile`. |
| `records.test.mjs` | CAP identity, metadata, sections, implementation and verification evidence (JavaScript and pytest), index, and link checks. |
| `changes.test.mjs` | CHG identity, active/archive status placement, phase table and phase sections, Impacts references. |
| `artifacts.test.mjs` | Review packages, wireframe manifest/inventory, CAP visual links, and non-record entry rejection. |

## Local Contracts

- These are `node:test` suites, not pytest modules. `pytest` never collects this directory (only `test_*.py` / `*_test.py` files are collected, and none live here); the canonical command is in `../AGENTS.md`.
- Imports reach the validator as `../../scripts/check-product-records.mjs`; fixtures are imported relatively as `./fixtures.mjs`.
- Every test builds its tree with `createFixtureTree()` under `os.tmpdir()` (honours `TMPDIR`) and calls `cleanup()` in a `finally` block. Tests must not read or write the repository's real `docs/` tree.
- Fixture trees are generic: a minimal valid CAP plus the mutation that trips one error. Do not copy real product CAP/CHG records into fixtures.
- `fixtures.mjs` is a helper module, not a test file; do not rename existing fixture keys or builder signatures by hand — extend with new helpers instead.

## Work Guidance

- Add the failing fixture test before changing the validator, then make the minimal validator change that satisfies it. Assert on stable error substrings, and include the actual error list in the assertion message.
- Cover both the accepted shape and its rejection twin (for example, a `test_*.py` module that qualifies versus a `test_*_helpers.py` module that does not).
- Keep generic validator semantics intact when adding toolchain support: new evidence kinds are additive filters, not replacements for existing JavaScript evidence.

## Verification

- `node --test tests/records/*.test.mjs` from the repository root must exit 0.
- Full pytest suite (`pytest`) must stay green; these files must not be collected by it.

## Child DOX Index

No child DOX documents. Parent contract: `../AGENTS.md`.
