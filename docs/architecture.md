# Talaria architecture

## Purpose and runtime

Talaria gives Hermes Agent operators a Python maintenance CLI for diagnostics,
model checks, skills, profile configuration, catalogs, logs, and backend processes.
It is not the agent runtime or a server. The distribution is `talaria-of-mercury`;
the `talaria` console script calls `talaria.cli:main`.

`pyproject.toml` declares Python >=3.10, setuptools packaging, PyYAML for YAML,
and psutil for process/socket discovery. `uv.lock` pins development resolution.
Node 24 runs the development-only structural validator and its fixture tests;
OpenSpec 1.15.0 owns native artifact validation and lifecycle commands. Neither
is a Talaria runtime dependency. CI installs the CLI into a job-local npm prefix;
there is no Node application manifest or build step for Talaria.

## Structure and flow

| Boundary | Responsibility |
| --- | --- |
| `src/talaria/cli/` | argparse command tree, dispatch, JSON/human output, exit codes, parser-derived bash/zsh completion |
| `src/talaria/paths.py` | profile selection and state/log paths |
| `src/talaria/hermes/` | diagnostics, benchmarks, catalog/process/log maintenance, skill orchestration, single-profile config helpers |
| `src/talaria/sync/` | source/target profile resolution, independent artifact sync phases, structured results, atomic backup writer |
| `tests/` | pytest behaviour evidence with synthetic profiles, SQLite data, logs, and stubbed external calls |
| `assets/` | brand artwork and checked-in vision benchmark fixtures |
| `docs/product/` | capability navigation and optional canonical visuals |
| `openspec/specs/` | current requirements with implementation/test evidence |
| `openspec/changes/` | native material-change packages, sole task lists, and verified archives |
| `scripts/`, `tests/records/` | structural validator and synthetic lifecycle/reference tests |

Typical flow is CLI parser → path resolution → feature `run` → structured report
→ output/exit-code dispatcher. Sync uses `SyncOptions`, `SyncProfile`, and
`SyncReport` rather than the inspection path pair. Completion introspects the
same parser rather than maintaining a second command specification.

Profile selection is explicit `--profile`, then `HERMES_PROFILE`, then
`~/.hermes/active_profile`, then `default`. Explicit state/log path flags override
those artifact paths. Default lives directly under `~/.hermes/`; named profiles
live under `~/.hermes/profiles/<name>/`. Talaria does not use `HERMES_HOME` for its
own selection; skill subprocesses receive an explicitly scoped `HERMES_HOME`.

## Side effects and trust boundaries

Do not infer write safety from a command group or from the word “inspection.”
These are separate contracts, not a universal `--apply` convention:

| Operation | Default and write boundary |
| --- | --- |
| `paths`, `completion` | report/script generation only |
| `hermes doctor` | reads diagnostic state; default-on curator pass can invoke Hermes/model tools; `--no-free-flight` disables that pass |
| Doctor tactical flags | preview selected state.db repairs; require `--apply` to mutate SQLite; do not create a partial-file database backup |
| Doctor curator suggestions | require `--apply-curator-suggestions`; `--dry-run` previews instead of writing config |
| `hermes benchmark` | reads state/config/capabilities, calls models for enabled smoke/vision checks, and writes result cache; disable both with `--no-smoke --no-vision` |
| `hermes refresh-catalog` | fetches and atomically writes a shared provider cache when stale or forced; profile selection does not scope the cache |
| `hermes serve-stop` | signals listeners on the selected port by default; `--dry-run` only detects; profile selection does not scope processes |
| `hermes log-rotate` | no action without size/age/total flags; supplied limits apply unless `--dry-run`; `--all-profiles` expands scope |
| Skills install/uninstall/category | apply by default; `--dry-run` suppresses installation/config/category writes, not necessarily identifier discovery network calls |
| `skills prune` | no action without prune-class flags; selected repairs preview unless `--apply` |
| `config sync` | applies selected phases by default; `--dry-run` previews; auth phase examines all profiles for newest provider tokens, not only the named source |
| `config apply-auxiliary`, `config sync-env` | apply single-profile changes by default; `--dry-run` previews |

`sync.writer.write_with_backup` uses a sibling temporary file, fsync, and
`os.replace`, with a `.bak` of an existing target unless disabled. This is
per-file atomic replacement, not an all-phases transaction. Doctor tactical
writes use SQLite; log rotation uses copy → gzip → truncate. Neither inherits
a blanket atomic profile rollback guarantee.

Doctor's `--dry-run` is specific to curator suggestions: it does not override
an explicitly supplied tactical `--apply`. See the
[doctor requirements](../openspec/specs/doctor-diagnostics-remediation/spec.md)
before combining them.

Hermes owns actual skill installation/uninstallation and model execution.
Talaria invokes its CLI. Network access also includes catalog fetches and skill
identifier/frontmatter discovery. The doctor curator pass prepares redacted
configuration and database slices and references logs; this is not a claim that
all log/message content is secret-free. Reports/diffs from config operations
may contain sensitive values. Keep real profiles, tokens, logs, and model output
out of repository records and CI artifacts.

## Output and current coverage

Commands expose text and, where supported, JSON or path-resolution reports.
Maintenance commands generally print only with `--verbose`; inspections print
by default. Per-command exceptions and exit semantics belong to the
[capability index](product/index.md) and [operator guide](../README.md), not an
assumed universal flag set. Exit codes generally distinguish clean (`0`), an
alert/action (`1`), and tool errors (`2`); current specs describe important exceptions.

Talaria has no primary visual screen, pane, or dialog. Its non-interactive CLI
specs declare `Primary surface: none`. Future primary visual interactions must
activate the canonical wireframe workflow in [product visuals](product/README.md).

Vision fixture paths currently resolve relative to the checkout unless
`--vision-fixtures-dir` is supplied. A successful wheel build does not establish
that checkout assets are installed or that live models/providers work.

## Maintenance and verification

[Current specifications](product/index.md) describe present requirements;
[native OpenSpec changes](../openspec/AGENTS.md) own implementation progress in
`tasks.md`, with one orchestrator writing tasks and Git. Proposal visuals and
factual evidence remain change-owned; they never replace current requirements
or executable tests. [OpenSpec workflow](openspec-workflow.md) defines bounded
invocation authority, independent exact-SHA review, and separately authorized
integration/verified archive. Local validation alone is not integration.
[Design decisions](design-decisions.md) records durable cross-cutting choices.

From the repository root:

```sh
uv sync --locked --extra dev
uv run --extra dev pytest
uv build
node scripts/check-openspec.mjs
OPENSPEC_TELEMETRY=0 openspec validate --all --strict
node --test tests/records/*.test.mjs
```

CI forces `_TESTING_TALARIA_SKIP_MODEL_BENCH=1`. Default model-config checks use
synthetic fixtures; live calls require explicit opt-in and are not acceptance
evidence for the offline gate. Structural checks validate references, native
shape, task ownership and visual inventory, not test execution, assertion quality,
honest surface classification, render freshness or integration. Verify OpenSpec
reports exactly `1.15.0`; native workflows/companions are separately installed
user/profile prerequisites, never vendored or initialized as local host adapters.
No lint/type-check gate is configured.
