# tests

## Purpose

Pytest suite for the Talaria CLI and library.

## Ownership

- One test module per feature: `test_paths.py` for path resolution,
  `test_moa_truncation.py`, `test_refresh_catalog.py`,
  `test_context_cache_fix.py`, `test_auxiliary.py`, and `test_sync.py`
  for feature coverage. `test_skill_install.py` covers recursive skill
  install orchestration.
- Shared fixtures live in `conftest.py`; shared test helpers live in
  `_helpers.py` (importable, not auto-discovered by pytest).

## Local Contracts

- Test modules must NOT hit the real `~/.hermes/` — every test isolates its
  filesystem via `tmp_path` and its environment via the `clean_env` fixture.
- CLI tests must use `subprocess.run([sys.executable, "-m", "talaria.cli", ...])`
  — invoking the entry point proves the installed CLI surface works, not
  just the library functions.
- `tests/__init__.py` exists so Pyright/pytest can import from `tests._helpers`.
- **Silent-by-default.** CLI tests that assert on human-readable stdout
  content must pass `-v/--verbose` explicitly; the default run is exit
  code only. Tests that assert on JSON output, help text, completion
  scripts, or `--show-resolution` output are unaffected because those
  channels always print.

  Carve-outs (default run prints, no `--verbose` needed in tests):

  * `talaria paths` — human-readable default; pass `--json` for the
    JSON channel.
  * `talaria hermes log-rotate` — human-readable default for both
    no-action and action runs.
  * `talaria completion` and `talaria config sync --list` — the
    output is the answer the test asked for.

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
- Coverage gaps: none tracked.

## Child DOX Index

- `test_paths.py` — `talaria.paths` resolution precedence and the
  `talaria paths` CLI dispatch (default-prints contract).
- `test_moa_truncation.py` — Signal A, Signal B, renderer, JSON output, CLI.
- `test_refresh_catalog.py` — reshape, credential discovery, cache
  freshness, urllib-stubbed fetch, run() orchestration, renderer, CLI.
- `test_context_cache_fix.py` — curated context-length cache repairs,
  backups, dry-run behaviour, and CLI profile/path resolution.
- `test_auxiliary.py` — single-profile auxiliary-alias derivation:
  injection, sentinel skipping, preservation, no-op, idempotency,
  dry-run, profile path resolution, CLI flags.
- `test_skill_install.py` — recursive skill identifier expansion, install
  policy updates, dry-run behaviour, and CLI flag coverage.
- `test_sync.py` — sync phases (config, soul, skills, env,
  context_cache), dot-path helpers, profile resolution, run_sync
  orchestration, CLI surface.
- `test_completion.py` — bash/zsh completion script generation: parser-tree
  introspection, script structure, syntax validation (`bash -n` / `zsh -n`),
  functional bash completion via sourced script, and CLI subprocess.
- `test_log_rotate.py` — rotation parser (`_parse_rotated` for active,
  plain rotated, gz rotated, multi-digit index, README exclusion,
  empty string), classifier (active/rotated/other), active
  rotation (under cap skipped, over cap copies+gzip+truncates,
  second rotation overwrites the first), age-based delete (old
  rotated copies, curator snapshot directories, max-age=0 with
  keep floor), aggregate size prune (oldest-first with keep floor,
  under-cap no-op), keep floor (keep=2 protects two newest, keep=0
  protects nothing), dry-run suppression, multi-profile target
  enumeration, run/render shape, `show_resolution` option echo,
  and CLI `--help`.
- `_helpers.py` — `make_sessions_db(path, rows)`.
- `conftest.py` — `fake_hermes_root`, `clean_env` fixtures.