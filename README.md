# Talaria — Winged Sandals for the Hermes Agent

<p align="center"><img src="assets/logo.svg" alt="Talaria"></p>

> *“With these sandals I shall bear the words of Olympus across wind and wave, swift as thought, returning before the laurel of my errand has time to wither.”*

**Talaria** is a maintenance CLI for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent). It runs alongside the agent's existing `state.db` and `logs/`, verifying mitigations, surfacing regressions, and giving the operator a single foot-stool from which to oversee every Hermes profile running on the host.

The name is deliberate. In the [Greek mythos](https://en.wikipedia.org/wiki/Talaria), the *talaria* were the winged sandals of Hermes: golden, swift, granted by Zeus, and worn by the messenger god to travel between mortal and divine realms. Hermes Agent carries his namesake's errands — tool calls, model swaps, subagent dispatch. Talaria carries its operator's errands: profile resolution, signal verification, and the verdict that tells you whether the agent is still flying cleanly or has begun to drag a wing.

## Why

Hermes Agent stores its session telemetry in a per-profile SQLite database and rotates `agent.log` / `errors.log` next to it. The original verification script (`check_moa_truncation.py`) shipped as a standalone utility and was duplicated across three profiles. Talaria consolidates that plumbing into a single installable CLI, with:

- **Profile-aware path resolution** — explicit flags win, then `$HERMES_PROFILE`, then `~/.hermes/active_profile`, then `default`.
- **Structured JSON output** for cron, dashboards, and other agents.
- **Reusable feature layout** — adding a new maintenance check is a new module under `talaria.hermos`, not a new top-level script.
- **Zero network dependencies.** Talaria only reads files the agent has already written.

## Install

```bash
# from a clone of this repo
pip install -e ".[dev]"

# or, once published
pip install talaria
```

The `[dev]` extra pulls in `pytest` and `pytest-cov` for the test suite.

## Usage

Talaria exposes subcommands grouped by feature. The first feature is **MoA truncation verification**:

```bash
# auto-detect the active Hermes profile and run both signals
talaria hermes moa-truncation

# inspect a specific profile
talaria hermes moa-truncation --profile hermes-vc

# cron-friendly: explicit paths, JSON output
talaria hermes moa-truncation \
  --state-db /var/lib/hermes/state.db \
  --log-dir  /var/log/hermes \
  --json --days 7

# debug: which profile and paths did Talaria resolve?
talaria hermes moa-truncation --show-resolution
```

Resolve paths without running a feature:

```bash
talaria paths
talaria paths --json
```

### Exit codes

All `talaria hermes *` commands return:

| Code | Meaning                                                  |
|------|----------------------------------------------------------|
| `0`  | Clean — both signals within tolerance.                  |
| `1`  | At least one signal fired; printed guidance next steps. |
| `2`  | Tool error (state.db unreadable, bad flag, etc.).       |

## The MoA truncation feature

`talaria hermes moa-truncation` runs two signals originally defined in the MoA truncation analysis:

### Signal A — session output_tokens trend

Queries `state.db` for the top 15 sessions in the look-back window by `output_tokens`. Flags any session whose `output_tokens` exceeds the alert threshold (default `64_000`, configurable in `talaria.hermos.moa_truncation.MOA_OUTPUT_TOKEN_ALERT`). A regression here usually means a MoA preset's `max_tokens` is still too high, or a long-running alias was left routed through MoA.

### Signal B — length-class truncation in logs

Scans `agent.log` and `errors.log` for the four length-truncation markers:

- `finish_reason='length'` and `finish_reason="length"` (Hermes runtime + providers)
- `Response truncated (finish_reason='length')`
- `hit max output tokens`

To avoid false positives from user-message INFO echoes, hits only count on lines whose severity prefix is `WARNING`/`ERROR`/`CRITICAL`. A separate `stream_drop_warnings` count surfaces mid-tool-call stream drops, which are not length events and are reported for visibility only.

### Look-back window

The default is `--days 2` (UTC, sliding). Override with `--days N` or pin an absolute start with `--since 2026-07-01`.

## Configuration

Talaria reads no configuration files itself; every input is a CLI flag or environment variable.

| Var                | Effect                                                                  |
|--------------------|-------------------------------------------------------------------------|
| `HERMES_PROFILE`   | Profile name to inspect when `--profile` is omitted.                   |

Talaria does **not** consume `HERMES_HOME` for resolution — that env var is set by a running Hermes session and would mislead a script invoked from a cron or another shell. Resolution always starts from `~/.hermes/`.

## Adding a new feature

Each Talaria feature is a module under `talaria/hermos/`:

1. Add `talaria/hermos/<feature>.py` exposing `run(paths, **opts)` and `render_human(report)`.
2. Wire its argparse subparser into `talaria.cli.build_parser`.
3. Add tests under `tests/test_<feature>.py` using the shared `fake_hermes_root` fixture.

See `talaria/hermos/moa_truncation.py` for the canonical shape.

## Development

```bash
# install + tests
pip install -e ".[dev]"
pytest

# install + tests against a real Hermes install
talaria paths   # confirm path resolution is sane
talaria hermes moa-truncation --show-resolution
```

The test suite uses an in-memory SQLite `sessions` table and tmpdir logs — no live Hermes install is required.

## License

MIT — see `LICENSE`.

## References

- [Talaria — Wikipedia](https://en.wikipedia.org/wiki/Talaria) — mythology behind the name.
- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — the agent this CLI maintains.
- [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com) — the visual reference for the logo's puristic flat-mark style.
- `~/.hermes/scripts/check_moa_truncation.py` — the standalone script Talaria's first feature replaces.

## Brand assets

- `assets/logo.svg` + `logo-256.png`, `logo-512.png`, `logo-1024.png` — primary lock-up.
- `assets/logo-mark.svg` + `logo-mark-128.png`, `logo-mark-256.png` — square mark only.
- `assets/logo-inverse.svg` — white-on-transparent for dark backgrounds.
- `assets/build_logo.py` — regenerates the SVG sources from the `ASCII_GLYPH` constant (single source of truth; edit the ASCII to redesign the silhouette).