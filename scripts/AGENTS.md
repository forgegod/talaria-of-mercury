# scripts

## Purpose

Development-only record tooling for the Talaria CAP/CHG workflow. The scripts
directory is not part of the Python package and is never imported at runtime.

## Ownership

| File | Owns |
| --- | --- |
| `check-product-records.mjs` | Dependency-free validation of capability/change-record identity, lifecycle, and references. |

## Local Contracts

- The validator checks record shape, lifecycle placement, resolvable references, executable-evidence file shape, and local Markdown links. It does not infer whether a code change alters user-visible behaviour, and it does not execute tests.
- `node scripts/check-product-records.mjs [rootDir]` defaults `rootDir` to the repository root (this directory's parent). `validateRecords(root)` is exported and returns `{ errors, capabilities, changes, manifest }` without setting an exit code, so tests assert on the returned error list. Exit code is `0` on pass and `1` on any error.
- Every CAP declares `Primary surface: human` or `none`. A human surface or an existing `docs/product/wireframes/` directory requires the generator, manifest, index, and complete CAP-linked HTML/PNG inventory; a missing manifest cannot disable the check.
- Change directories are flat record-only inventories. Review packages live at `docs/changes/reviews/CHG-<number>/` with an owning CHG, a linked review-only README, and link-checked Markdown that is never parsed as a CHG.
- Verification evidence accepts JavaScript test files (`*.test.*` / `*.spec.*` with a JS/TS extension) and pytest modules (`test_*.py` and `*_test.py`). Python evidence must also contain a test definition (a `test*` function or a `Test*` class) and an assertion statement (`assert`, `self.assert*`, `self.fail`, or a raising `pytest` helper); `conftest.py`, `_helpers.py`, fixture modules, and assertion-free smoke files are rejected. Detection is structural, not proof the test ran or failed correctly.
- Checks establish artifact structure and ownership, not truthful surface classification, render freshness, approval, or historical immutability.
- The script stays dependency-free: Node 24 standard library only. No package manifest, no installs, no network, no pnpm/npm scripts, and no runtime dependency for the Python package. The directory must not contain CAP/CHG product records.

## Work Guidance

Extend the validator with new invariants by adding an explicit error and covering both a fixture that trips it and the happy path in `tests/records/`. Keep error messages stable: they are the test surface. Keep record semantics generic — target-specific behaviour belongs in records and tests, not in the validator.

## Verification

- `node scripts/check-product-records.mjs` — validates this repository's records.
- `node --test tests/records/*.test.mjs` — validator behaviour suite; must exit 0 after any change here.

## Child DOX Index

No child DOX documents. Parent contract: `../AGENTS.md`.
