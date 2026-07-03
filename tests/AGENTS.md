# tests

## Purpose

Pytest suite for the Talaria CLI and library.

## Ownership

- One test module per feature: `test_paths.py` for path resolution,
  `test_moa_truncation.py` for the first feature.
- Shared fixtures live in `conftest.py`; shared test helpers live in
  `_helpers.py` (importable, not auto-discovered by pytest).

## Local Contracts

- Test modules must NOT hit the real `~/.hermes/` — every test isolates its
  filesystem via `tmp_path` and its environment via the `clean_env` fixture.
- CLI tests must use `subprocess.run([sys.executable, "-m", "talaria.cli", ...])`
  — invoking the entry point proves the installed CLI surface works, not
  just the library functions.
- `tests/__init__.py` exists so Pyright/pytest can import from `tests._helpers`.

## Work Guidance

- New feature: drop a `tests/test_<feature>.py` alongside the existing
  Signal/Run/Renderer/Cli test classes (see `test_moa_truncation.py` for
  the canonical layout).
- Tests assert on real exit codes (`0` / `1`), not just stdout content.
- Use the `fake_hermes_root` fixture for layout, `make_sessions_db` for
  SQLite fixtures, and `_log_line(level, body, when)`-style helpers for
  log fixtures.

## Verification

- `pytest` from the repo root must exit 0 before any change ships.
- Coverage gaps: `talaria.cli.cmd_paths` is currently uncovered — add a
  CLI test when adding new top-level commands.

## Child DOX Index

- `test_paths.py` — `talaria.paths` resolution precedence.
- `test_moa_truncation.py` — Signal A, Signal B, renderer, JSON output, CLI.
- `_helpers.py` — `make_sessions_db(path, rows)`.
- `conftest.py` — `fake_hermes_root`, `clean_env` fixtures.