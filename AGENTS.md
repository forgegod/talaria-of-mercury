# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if there are no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists

Default section order:
- Purpose
- Ownership
- Local Contracts
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md.

Project-wide durable preferences (style, workflow, conventions) live in user memory; this section is reserved for contract-level rules that bind every child doc.

- Documentation describes current project state only; git carries the timeline and retired designs.

## Project purpose

- Talaria is an installable maintenance CLI for Hermes Agent operators: diagnostics, model benchmarking, skill lifecycle management, profile configuration, and bounded maintenance actions.
- Python source and packaging remain owned by `src/talaria/` and `pyproject.toml`; the console script is `talaria`, the distribution is `talaria-of-mercury`.
- `README.md` owns operator usage. `docs/architecture.md` owns current cross-cutting design; CAPs own current material behaviour. None is a substitute for executable tests.

## Architectural decisions

- **Agent harness protected by tirith.sh.** Reading passwords or access tokens is prohibited. Extract variables from `.env` / config files without relaying their values; use environment variables by importing them for Bash execution. `***` in output is a tirith redaction marker, not a literal value — never "fix" it to a variable ref.
- **Runtime boundary.** Python with argparse, PyYAML, and psutil; Hermes remains an external CLI/runtime, not a vendored dependency. Node 24 is development-only record-validation tooling and is not required to install or run Talaria.
- **Record authority.** `docs/product/capabilities/CAP-*.md` describes current material behaviour, linked to implementation and executable tests. Code and tests decide conflicts.
- **Change authority.** `docs/changes/active/CHG-*.md` is the single implementation-progress authority for a material request. Tickets or direct operator requests explain why; archived CHGs are receipts, not current contracts.
- **Material changes.** Update affected CAPs, behaviour tests, and the owning CHG together. Do not create CAP/CHG churn for behaviour-preserving refactors or framework initialization.
- **Write boundaries.** Preview/apply defaults differ by command. Preserve the explicit contracts in `docs/architecture.md`; never assume all writes require `--apply` or that inspection commands cannot contact a model.
- **Evidence isolation.** Default verification uses synthetic data and mocked external services, never an operator profile or live model. Live benchmarks require explicit opt-in; CI forces them off.

## Architectural non-goals and change control

- Talaria does not own the Hermes server, agent loop, model providers, or a web/TUI application.
- Do not introduce a second progress tracker, vendor Hermes internals, or add a runtime service/database without explicit approval.
- Approved cross-cutting changes update this rail, `docs/architecture.md`, affected child contracts/tests, and `docs/design-decisions.md` for durable architecture forks.

## Product and change records

- Read `docs/product/README.md` and `docs/changes/README.md` before creating, changing, splitting, or closing CAP/CHG records.
- Every CAP declares `Primary surface: human` or `none`. Talaria's non-interactive text/JSON CLI has no primary visual screen, so current CAPs declare `none`; this is not an exemption for future visual interfaces.
- CHG review assets belong in `docs/changes/reviews/CHG-<number>/`. Future primary visual surfaces require separate CAP-linked canonical HTML/PNG wireframes and a child DOX contract.
- Optional installed `application-records` and `phased-plan-*` skills assist execution; repository contracts remain authoritative. Do not copy initializer packages or private progress files into the project.

## Workspace verification

- `uv sync --locked --extra dev` installs the project and test dependencies from `uv.lock`.
- `uv run --extra dev pytest` runs the Python suite; CI additionally sets `_TESTING_TALARIA_SKIP_MODEL_BENCH=1`.
- `uv build` builds the sdist and wheel.
- `node scripts/check-product-records.mjs` validates CAP/CHG structure, lifecycle, references, review ownership, and activated wireframe inventory.
- `node --test tests/records/*.test.mjs` exercises the validator with synthetic trees, including pytest evidence references.
- `.github/workflows/ci.yml` runs these gates without credentials. Record validation is structural, not proof that claims are true or that tests ran. No lint/type-check gate is configured.

## Codebase Knowledge Graph (codebase-memory-mcp)

This project uses codebase-memory-mcp to maintain a knowledge graph of the codebase.
ALWAYS prefer MCP graph tools over grep/glob/file-search for code discovery.

### Priority Order
1. `search_graph` — find functions, classes, routes, variables by pattern
2. `trace_path` — trace who calls a function or what it calls
3. `get_code_snippet` — read specific function/class source code
4. `query_graph` — run Cypher queries for complex patterns
5. `get_architecture` — high-level project summary

### When to fall back to grep/glob
- Searching for string literals, error messages, config values
- Searching non-code files (Dockerfiles, shell scripts, configs)
- When MCP tools return insufficient results

### Examples
- Find a handler: `search_graph(name_pattern=".*OrderHandler.*")`
- Who calls it: `trace_path(function_name="OrderHandler", direction="inbound")`
- Read source: `get_code_snippet(qualified_name="pkg/orders.OrderHandler")`

## Child DOX Index

- `src/talaria/AGENTS.md` — Python package: CLI entry point, path resolution, Hermes features.
- `tests/AGENTS.md` — pytest suite, synthetic fixtures, and validator-test child.
- `assets/AGENTS.md` — logo sources, rendered PNGs, and benchmark vision fixtures.
- `docs/AGENTS.md` — current architecture, product capability records, and change lifecycle.
- `scripts/AGENTS.md` — development-only record validator.
- `.github/AGENTS.md` — credential-free GitHub Actions verification.

Root owns `README.md`, `pyproject.toml`, `uv.lock`, `.gitignore`, and `LICENSE`.
