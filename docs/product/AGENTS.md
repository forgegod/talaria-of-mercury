# Product capability records

## Purpose

Own the repository's concise, current-state account of material product behaviour and the executable evidence that proves it.

## Ownership

| Path                    | Owns                                                                      |
| ----------------------- | ------------------------------------------------------------------------- |
| `README.md`             | Capability-record lifecycle, scope rule, metadata, and authoring templates. |
| `index.md`              | Navigable catalogue of current capabilities without duplicating their behaviour. |
| `capabilities/CAP-*.md` | One current behavioural contract per material capability.                 |
| `wireframes/`           | Optional canonical product-visual layer, activated when any CAP declares `**Primary surface:** human` or the directory exists. |

## Local Contracts

- A CAP document describes present behaviour at the repository revision, not a request, plan, or historical narrative.
- A material claim links to the implementation and at least one executable behaviour test. Code and its tests decide a conflict; correct the CAP in the same change.
- Capability-local decisions belong in the CAP; irreversible or cross-cutting decisions belong in `../design-decisions.md`.
- Every CAP declares `Primary surface: human` or `none`. Human-facing CAPs require canonical HTML/PNG links under `## Links`; CAPs must not link CHG review packages.
- Follow `README.md` for manifest activation and schema, inventory completeness, and the implementation handoff for new and existing CAPs. Generator and renderer freshness remain review gates, not inventory checks.
- Optional externally installed phased-plan skills execute material work through its active CHG; the CAP remains the current-state contract rather than a phase-status surface. Talaria does not own their packages or installation.

## Work Guidance

- Add or amend a CAP in the same change as a material user-visible, operator-visible, or tool-behaviour change.
- Do not create CAP churn for refactors that preserve observable behaviour.
- Keep the index as links and status only; do not duplicate CAP prose.
- Read `README.md` before creating or completing a CAP.
- Read both the change and product DOX, plus the wireframe child if present, before choosing a destination for a visual artifact. Never move a pending proposal HTML, screenshot, or design prose into `wireframes/`: the canonical artifact is regenerated at implementation time.

## Verification

- Run `node scripts/check-product-records.mjs` and the affected behaviour tests.
- Confirm every CAP declares a primary surface, that `human` CAPs link current wireframe HTML and PNG, and that the wireframe manifest agrees with the on-disk inventory whenever the workflow is active.

## Child DOX Index

No child DOX documents while the wireframe workflow is inactive. Create and index the wireframe child contract on activation. Parent contract: `../AGENTS.md`; active implementation status: `../changes/AGENTS.md`.
