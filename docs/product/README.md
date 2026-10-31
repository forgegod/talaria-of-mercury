# Product specifications and visuals

The [capability index](index.md) links Talaria's current requirements under
`openspec/specs/<capability>/spec.md`. Code and executable tests decide conflicts.
This directory is navigation and optional canonical visuals, not a second
requirement or task system. Read [OpenSpec DOX](../../openspec/AGENTS.md) and
[the workflow](../openspec-workflow.md) for authoring and lifecycle rules.

## Current requirements

Current specs have `## Purpose`, `## Requirements`, and `## Verification`, in
that order. Each `### Requirement:` states an observable SHALL/MUST outcome
with at least one `#### Scenario:` using WHEN/THEN. Verification names actual
repository-relative implementation and test files, with proving test functions
in adjacent prose. Do not use pytest node IDs as file paths. Known limitations
and evidence gaps remain explicit rather than being promoted to proven guarantees.

Material changes evolve native delta specs, implementation, behavior tests, and
affected visuals together. Sync accepted requirements before terminal closure;
behavior-preserving maintenance does not churn requirements. Capability-local
rules belong in their spec; cross-cutting decisions belong in
[design decisions](../design-decisions.md).

Every current spec declares `**Primary surface:** human` or `none`. Talaria's
non-interactive text/JSON CLI has no primary screen, pane, or dialog and declares
`none`. This does not exempt a future visual interface.

## Canonical wireframes

A human-facing spec or the presence of `wireframes/` activates this contract;
deleting the manifest does not disable validation. No empty scaffolding ships.

- `wireframes/generate.mjs` owns canonical screen definitions and generates
  `manifest.json`, `index.html`, and `html/<capability>-<screen>.html`. The
  project's renderer exports matching `exports/<capability>-<screen>.png`.
- The manifest has a non-empty `screens` array. Each entry has `capability`
  (an existing human-facing spec key), `screen` (a lowercase hyphenated key),
  non-empty `title`, `html`, `png`, and positive integer viewport width/height.
  Capability/screen pairs are unique; a capability can have multiple screens.
- Every human-facing capability has at least one screen. The manifest and flat
  artifact directories agree in both directions; missing, extra or misnamed
  artifacts fail. `index.html` links every screen using an HTML anchor.
- Current specs link each HTML/PNG pair under `## Visuals` using relative links
  into `docs/product/wireframes/`. A `none` spec has no visual section or screen.
- Proposal assets stay review-only inside their native change package. Current
  specs never link change packages; proposals never enter the canonical manifest.

Update the generator, generated HTML, rendered PNG, manifest/spec links, and
behavior tests in the same interaction change. Never promote proposal files by
moving them. Create and index `wireframes/AGENTS.md` on activation. Load the
installed `capability-wireframes` companion and stop if its prerequisites are
missing; initialization does not install it.

## Verification

Run `node scripts/check-openspec.mjs`, OpenSpec 1.15.0
`validate --all --strict` with telemetry disabled, and affected behavior tests.
The checker recognizes JavaScript test files and pytest modules with test and
assertion structure. Structural recognition is not test execution or adequate
coverage. Visual inventory checks do not render PNGs, establish freshness, infer
honest surface classification or grant approval: generate/render and inspect
affected screens separately.
