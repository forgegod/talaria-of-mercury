# CAP-004 — Hermes runtime maintenance: catalog, serve process, logs

**Status:** implemented
**Primary surface:** none

## Behaviour

- `talaria hermes refresh-catalog` fetches a gateway model catalog and reshapes
  it into the Hermes provider-manifest schema. `--gateway` selects the catalog
  (only `kilocode`) and therefore the provider key that is written.
- The default source is `https://api.kilo.ai/api/gateway/models`. The credential
  is `KILOCODE_API_KEY`, taken from the environment first and then from
  `~/.hermes/.env`. `--src-url` overrides the endpoint.
- The default destination is `$XDG_CACHE_HOME/kilocode_catalog.json`. The fetch
  is skipped when that file is younger than `--max-age-seconds` (default 6 h)
  unless `--force` is passed. Writes are atomic (`tempfile` + `os.replace`).
- Refresh exit code is 0 for refreshed, reshaped, or fresh-cache-skip runs and 2
  for auth, network, parse, write, or unknown-gateway errors. The report's
  `reason` distinguishes them. The command is silent unless `-v`, `--json`, or
  `--show-resolution` is used. `--profile` is recorded only; it never selects
  the cache.
- `talaria hermes serve-stop` finds processes listening on a TCP port (default
  9119) through `psutil.net_connections("inet")`, excludes its own PID, and
  sends `SIGTERM`, polls for up to 5 s, then `SIGKILL`s survivors.
  `--dry-run` detects and reports PIDs without signalling.
- Serve-stop reports `reason` as `stopped`, `none`, `detected`, or `partial`;
  exit code is 0 for the first three and 2 for `partial`. `--profile` is
  recorded only. The command is silent unless `-v`, `--json`, or
  `--show-resolution` is used.
- `talaria hermes log-rotate` is explicit-only: with no `--max-size`,
  `--max-age`, or `--max-total` it scans and reports but never writes, and
  `dry_run` is true. With at least one of those flags it applies by default;
  `--dry-run` previews. There is no `--apply` flag for this command.
- Active-file rotation is copy → gzip → truncate: an oversized file is copied to
  `<name>.1.gz` (gzip level 6) and the source is truncated to zero. The active
  file is never deleted. `--max-age` deletes rotated copies and
  `logs/curator/<ts>/` snapshot directories older than the threshold.
  `--max-total` deletes the oldest rotated copies until the directory total drops
  below the cap. `--keep` (default 1) protects the newest N rotated copies per
  base name. `--all-profiles` sweeps root `logs/` plus every
  `profiles/*/logs/`.
- Log-rotate always prints its human report and exits 0; `--json` emits
  `{"reports": [...]}`. `README.md`, `AGENTS.md`, and `.gitkeep` are never
  touched.

## Implementation

- `src/talaria/hermes/refresh_catalog.py` — gateway table, credential
  discovery, fetch, reshape, cache freshness, run/renderer.
- `src/talaria/hermes/serve_stop.py` — port detection, signal escalation,
  run/renderer.
- `src/talaria/hermes/log_rotate.py` — classification, planning, apply,
  renderer, resolution.
- `src/talaria/cli/__init__.py` — `cmd_hermes_refresh_catalog`,
  `cmd_hermes_serve_stop`, `cmd_hermes_log_rotate`, and
  `_log_rotate_target_paths`.

## Rules and boundaries

- Catalog refresh is the only network-writing surface here and performs its
  fetch by default. It never reads or writes `state.db`.
- Serve-stop acts on a listening port, not a command-line pattern, so it can stop
  a backend whose argv `hermes serve --stop` cannot match. Signalling is the
  default action; `--dry-run` is the safe preview. Permission failures surface as
  `partial`/exit 2.
- Log rotation destroys bytes by default once an action flag is present. A
  second rotation overwrites the single `.1.gz` slot for that base name, so the
  previous rotated copy is lost. Use `--dry-run` to inspect the plan first.
- Curator snapshot directories are deleted as whole units, never partially.
  Archival copies are not produced beyond the single gzip slot.
- `refresh-catalog --profile` and `serve-stop --profile` are report metadata only;
  neither command consumes profile state.

## Verification

- `tests/test_refresh_catalog.py` — `TestReshape` (field normalisation, free
  detection, sort order, atomic write), `TestCredentialDiscovery`,
  `TestCacheFreshness`, `TestFetch` (stubbed urllib; auth, HTTP, network,
  timeout), `TestRun` (fresh skip, force, full refresh, error reasons, temp
  cleanup), `TestRenderer`, and `TestCli`.
- `tests/test_serve_stop.py` — `TestFindPids`, `TestRun` (none, dry-run,
  SIGTERM→SIGKILL, partial failure, process-lookup), `TestPidAlive`,
  `TestRenderer`, and `TestCli`.
- `tests/test_log_rotate.py` — `TestParseRotated`, `TestClassify`,
  `TestRotateActive`, `TestAgePrune`, `TestTotalPrune`, `TestKeepFloor`,
  `TestDryRun`, `TestMultiProfile`, `TestRunReport`, and `TestCli`
  (default-prints, no-flags scan).

## Related contracts

- [Architecture](../../architecture.md)
- [Repository contract](../../../AGENTS.md)
