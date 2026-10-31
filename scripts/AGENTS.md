# scripts

## Purpose

Development-only record tooling for the Talaria OpenSpec workflow. The scripts
directory is not part of the Python package and is never imported at runtime.

## Ownership

| File | Owns |
| --- | --- |
| `check-openspec.mjs` | Dependency-free structural validation of the OpenSpec tree: capability specs, change packages, verification evidence, implementation references, and canonical visuals. |

## Local Contracts

- `node scripts/check-openspec.mjs [rootDir]` defaults `rootDir` to the repository root (this directory's parent). `validateOpenSpec(root)` is exported and returns `{ errors, specs, changes, visuals }` without setting an exit code, so tests assert on the returned error list. Exit code is `0` on pass and `1` on any error.
- Capability specs live at `openspec/specs/<capability>/spec.md` with ordered `## Purpose`, `## Requirements`, `## Verification` sections, at least one `### Requirement`, and at least one `#### Scenario` per requirement. Every spec declares `Primary surface: human` or `none`.
- Change packages live at `openspec/changes/<slug>/`, or `openspec/changes/archive/<YYYY-MM-DD>-<slug>/` once archived, with `proposal.md`, `tasks.md`, optional `design.md`, and native deltas at `specs/<capability>/spec.md`. `tasks.md` is the only checkbox progress writer.
- Verification evidence accepts JavaScript test files (`*.test.*` / `*.spec.*` with a JS/TS extension) and pytest modules (`test_*.py` and `*_test.py`). Each must contain a test definition (`test()`/`it()` for JavaScript; a `test*` function or `Test*` class for pytest) and an assertion (`assert`/`expect` for JavaScript; `assert`, `self.assert*`, `self.fail`, or a raising `pytest` helper for Python). `conftest.py`, helper modules, fixture modules, and assertion-free files are rejected. Detection is structural, not proof the test ran or failed correctly.
- Backticked repository paths in `## Verification` that are not behaviour tests are implementation references and must resolve to real files inside the repository. Both backticked paths and relative Markdown links recognize JavaScript and pytest evidence through the same filename/content checks.
- A human surface or an existing `docs/product/wireframes/` directory requires the generator, manifest, index, and complete capability/screen artifact inventory; a missing manifest cannot disable the check.
- Symlinked record and visual trees fail closed before any artifact is read, and local Markdown links must stay inside the repository.
- Checks establish artifact structure and ownership, not truthful surface classification, render freshness, approval, or historical immutability.
- The script stays dependency-free: Node 24 standard library only. No package manifest, no installs, no network, no pnpm/npm scripts, and no runtime dependency for the Python package. The directory must not contain product records.

## Work Guidance

Extend the validator with new invariants by adding an explicit error and covering both a fixture that trips it and the happy path in `tests/records/`. Keep error messages stable: they are the test surface. Keep record semantics generic — target-specific behaviour belongs in specs and tests, not in the validator. Evidence kinds stay additive: JavaScript and pytest support must both remain executable filters.

## Verification

- `node scripts/check-openspec.mjs` — validates this repository's OpenSpec tree.
- `node --test tests/records/*.test.mjs` — validator behaviour suite; must exit 0 after any change here.

## Child DOX Index

No child DOX documents. Parent contract: `../AGENTS.md`.
