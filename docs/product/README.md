# Product capability records

`capabilities/CAP-*.md` is the repository's current, human-readable product-behaviour index. It complements executable code and tests; it does not replace them.

## Authority

| Question                                                | Authority                                                               |
| ------------------------------------------------------- | ----------------------------------------------------------------------- |
| Why should work happen?                                 | External ticket or direct operator request                              |
| What does this repository revision do?                  | CAP contract, implementation, and executable tests at the same revision |
| How far is a requested change implemented?              | `../changes/active/CHG-*.md`                                            |
| Why was an irreversible or cross-cutting option chosen? | `../design-decisions.md`                                                |

## When a CAP changes

Update a CAP in the same change when behaviour visible to a user, caller, operator, or tool contract changes. Do not update it for a refactor that preserves those outcomes.

A CAP claim must be specific enough for a test to falsify. Link existing architecture contracts instead of copying their text.

## CAP shape

```markdown
# CAP-001 — Capability name

**Status:** implemented
**Primary surface:** none

## Behaviour

- <present-tense, falsifiable outcome>

## Implementation

- `<repository source path>` — <where the behaviour is implemented>

## Rules and boundaries

- <durable local rule or link to an architecture contract>

## Verification

- `<test path>` — <what the test proves>

## Related contracts

- [Architecture](../../architecture.md)
```

Allowed status values: `implemented`, `partial`, `retired`. `partial` states the implemented boundary precisely; it is not a substitute for a future-work list.

`Primary surface` is required on every CAP, including partial or retired records. Use the literal `human` for a primary screen, pane, dialog, or multi-step visual interaction; use `none` otherwise, including headless capabilities. The validator rejects missing or other values rather than inferring a surface from prose. A human-facing CAP requires its own canonical wireframe; a `none` CAP has no manifest screen.

## Capability-local decisions

Record a decision in the CAP only when it changes how a reader must use or understand the current behaviour. Put irreversible runtime, persistence, privacy, or observability decisions in `../design-decisions.md` instead.

## Wireframes

Talaria is a non-interactive text/JSON CLI with no primary visual screen, pane, or dialog, and ships no `wireframes/` directory. Current CAPs therefore declare `none`. A CAP declaring `human`, or the presence of that directory, activates the workflow; deleting the manifest does not disable validation.

- `wireframes/generate.mjs` is the only editable canonical screen-definition source. It generates `manifest.json`, `index.html`, and `html/CAP-<number>-<slug>.html`; the project's renderer exports matching `exports/CAP-<number>-<slug>.png` files.
- The manifest is an object with a `screens` array. Each screen has `id` (an existing human-facing CAP ID), `title` (non-empty text), `html`, `png`, and `viewport` with positive integer `width` and `height`. IDs are unique; HTML and PNG filenames have the same CAP ID and slug.
- Every human-facing CAP has exactly one screen. The manifest and both flat artifact directories agree in both directions; missing, extra, or misnamed artifacts fail validation. `index.html` links every screen through an HTML anchor.
- Each human-facing CAP links its HTML and PNG using relative Markdown links under the exact `## Links` heading. For a CAP in `capabilities/`, paths begin `../wireframes/html/` and `../wireframes/exports/`.
- Proposal packages stay in `docs/changes/reviews/CHG-<number>/` under the [change-review contract](../changes/README.md#review-packages). CAPs never link review-package files; proposals never enter the product manifest.

For new and existing CAPs, a material interaction change updates the canonical generator, regenerated HTML, rendered PNG, CAP links, and behavior tests in the same implementation slice. Do not promote a proposal by merely moving or renaming its files. Create the wireframe child DOX on first activation and index it in `AGENTS.md`.

`node scripts/check-product-records.mjs` enforces declarations, inventory, naming, and links. It does not render PNGs, establish source freshness, infer honest surface classification, or prove behavior. Run generation/rendering and inspect the affected visuals as explicit phase gates; wireframes never replace executable behavior evidence.

## Agent integration

Optional installed `application-records`, `capability-wireframes`, and
`phased-plan-*` skills assist record maintenance. They are execution aids,
not an alternate authority or a required Talaria runtime dependency. The active
CHG remains the tracked plan; no local initializer or skill publisher is shipped.

## Verification

Run `node scripts/check-product-records.mjs`, then the affected behaviour tests.

Evidence paths use repository-relative filenames, not pytest node IDs. Name the
proving test classes/functions in adjacent prose. The validator recognizes pytest
`test_*.py` and `*_test.py` files and JavaScript test/spec files; structural
recognition does not prove execution, collection, or adequate coverage.
