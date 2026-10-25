# Change records

## Purpose

Own repository-tracked implementation progress for requested changes without becoming a second source of current product behaviour.

## Ownership

| Path               | Owns                                                                               |
| ------------------ | ---------------------------------------------------------------------------------- |
| `README.md`        | Change lifecycle, status vocabulary, required metadata, and authoring template.    |
| `active/CHG-*.md`  | The single implementation-progress authority for an active material change.        |
| `archive/CHG-*.md` | Completed or cancelled implementation receipts; never current-behaviour authority. |
| `reviews/CHG-<number>/` | Optional CHG-owned review assets and design notes, retained at a stable path after closure. |

## Local Contracts

- Tickets request change; a CHG record owns its repository implementation status, phases, verification gates, and evidence.
- Every CHG names its external request, affected CAP IDs, baseline, and executable phase gates.
- Only `planned`, `in-progress`, or `blocked` records belong in `active/`; only `done` or `cancelled` records belong in `archive/`.
- Both directories contain only flat CHG record files. Review packages belong in `reviews/CHG-<number>/` with a review-only README linked from the owning CHG; see `README.md` for labeling, retention, and removal rules.
- Review Markdown is link-checked but never parsed as a CHG. Packages are not a second progress authority and cannot replace CAP wireframes.
- A CHG is complete only after affected CAPs and tests describe the merged behaviour. Archive it rather than using it to describe the repository.
- The optional externally installed phase-workflow skills operate on the CHG phase table; they do not create a second mutable progress authority. Skill source and distribution are owned by the dedicated skills repository, not this tree.

## Work Guidance

- Create one CHG for a material change before implementation begins. Keep it small enough for a coherent vertical slice; use explicit dependencies when splitting is necessary.
- Update phase state and evidence only after the corresponding gate passes.
- Do not duplicate ticket discussion, capability prose, or ADR rationale in a CHG. Link the authoritative artifact.
- Use `phased-plan-overview` to select active work, `phased-plan-execution` for one phase, and `phased-plan-refactoring` only for a verified split or resequencing.
- Read `README.md` before creating, splitting, completing, or archiving a CHG.
- Before visual work, read the product DOX and wireframe child if present. Include canonical generation/rendering and review-package retention in the relevant phase and closeout gates.

## Verification

Run `node scripts/check-product-records.mjs` and the CHG phase gate.

## Child DOX Index

No child DOX documents. Parent contract: `../AGENTS.md`; current product behaviour: `../product/AGENTS.md`.
