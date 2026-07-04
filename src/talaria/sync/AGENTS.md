# src/talaria/sync

## Purpose

The **sync** feature group is Talaria's write-bearing carve-out:
copying Hermes profile artefacts (config.yaml, SOUL.md, skills/,
.env, context_length_cache.yaml) from a source profile to a target
profile. Every other Talaria feature is read-only against the
Hermes runtime; sync is the operator-facing counterpart that
makes those inspections worth running by letting the operator
propagate fixes, additions, and shared configurations across
profiles.

## Ownership

- This package is its own feature group — `talaria config sync` is
  the CLI entry point that drives it. The `config` command group
  also hosts the sibling `apply-auxiliary` command (implemented in
  `talaria/hermos/auxiliary.py`, not here).
- Each phase is a single module: `config.py`, `soul.py`,
  `skills.py`, `env.py`, `context_cache.py`, `mcp_serve.py`. Phases
  are independent and any subset can run via `--skip-*` flags.
- `paths.py` resolves profile specs to `SyncProfile` objects. This
  is *not* the same as `talaria.paths` — sync needs paths to all
  artefacts, not just `state.db` and `logs/`.
- `dotpath.py`, `yaml_io.py`, `writer.py` are private helpers
  shared by multiple phases.
- `result.py` defines the structured output types. Renderer and
  JSON consumer depend on these.
- `render.py` produces both human-readable and JSON output. No
  other module touches ANSI colour codes.
- `run.py` orchestrates the phases and is the only public entry
  point beyond the CLI.

## Local Contracts

- Public surface: `talaria.sync.run.run_sync(source, target, options)`
  and `talaria.sync.run.run_mcp_serve(target, options)`. The CLI
  imports these — phase modules are private.
- Sync WRITES to profiles. This is the only Talaria feature group
  that does; the read-only contract elsewhere is unchanged.
- Writes go through `writer.write_with_backup` (atomic temp +
  rename, optional `.bak`). Direct `Path.write_text` calls in
  phase modules are a code smell.
- `.env` sync is **additive** (target values always win on
  conflict). `context_length_cache.yaml` is **source-wins**
  (factual measurements presumed more recent). `config.yaml` mode
  follows the `--exclude` / `--only` flags.
- Exit codes follow the talaria CLI contract: `0` clean, `2` tool
  error. The `1` "signal fired" exit is unused by sync — there is
  no alert condition.

## Work Guidance

- New sync phase: add a `talaria/sync/<phase>.py` module exposing
  a `sync_<phase>(source, target, *, apply, **opts)` function and
  a result dataclass in `result.py`. Wire the orchestration in
  `run.py` and the CLI flags in `talaria.cli`.
- Default policy is `apply=True`. The CLI exposes `--dry-run` to
  opt out, but a programmatic caller that wants a dry run must
  pass `options.dry_run=True` explicitly.
- Phase log lines stay short and consistent: `  <verb>: <key>=<value>`
  or `  <phase>: <summary>`. The renderer formats these; do not
  embed colour codes in phase output.
- `dotpath` helpers never raise on missing keys — return
  `(False, None)`. Phases that need to warn the operator about a
  missing `--only` path do so explicitly.

## Verification

- `tests/test_sync.py` covers each phase (config, soul, skills,
  env, context_cache, mcp_serve) with the `fake_hermes_root`
  fixture plus a hand-built target tree.
- Tests assert on real exit codes (`0` / `2`), on
  `report.ok` / `report.any_writes`, and on `report.<phase>.status`.
- JSON-mode tests assert against `_report_to_dict` keys (stable
  shape across runs).
- Dry-run tests confirm `apply=False` never writes bytes (no
  `.bak`, no target file modification).

## Child DOX Index

- `paths.py` — `SyncProfile`, `resolve_profile`, `list_profiles`,
  `mcp_serve_entry`. The profile model used by every phase.
- `config.py` — `config.yaml` phase. Modes: `exclude`, `only`,
  `identity`. Optional `add_mcp_serve` injection.
- `soul.py` — `SOUL.md` phase. Single-file copy with backup.
- `skills.py` — `skills/` phase. Tree walk, byte-level diff,
  optional category/skill filters.
- `env.py` — `.env` phase. Additive merge with target precedence.
- `context_cache.py` — `context_length_cache.yaml` phase.
  Source-wins merge.
- `mcp_serve.py` — `mcp_servers` injection (delegates to
  `config.py`).
- `dotpath.py` — get/set/del/list helpers for `--exclude` /
  `--only` path filters.
- `yaml_io.py` — load/dump/validate YAML.
- `writer.py` — atomic write with optional `.bak`.
- `result.py` — `PhaseResult` and friends. JSON output shape.
- `render.py` — human + JSON renderer.
- `run.py` — orchestration. Public entry points.