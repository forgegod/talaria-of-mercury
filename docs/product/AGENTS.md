# Product navigation and canonical visuals

## Purpose

Own navigation to current OpenSpec requirements and the optional canonical visual layer. Requirements and change progress live only under `../../openspec/`.

## Ownership

| Path                    | Owns                                                                      |
| ----------------------- | ------------------------------------------------------------------------- |
| `README.md` | Specification navigation and canonical visual contract. |
| `index.md` | Links to current requirements without duplicating behavior or progress. |
| `wireframes/` | Optional canonical visuals, activated by a human-facing spec or the directory's presence. |

## Local Contracts

- Read `../../openspec/AGENTS.md` and `../openspec-workflow.md` for requirements and change lifecycle. Do not create a parallel record system here.
- Current specs declare `Primary surface: human` or `none`. Human-facing specs link canonical HTML/PNG under `## Visuals`, never change-owned proposals.
- `README.md` owns the optional manifest schema and inventory contract. Rendering freshness and honest classification remain review gates.

## Work Guidance

- Keep the index navigational; do not duplicate requirements or task status.
- Read this contract and the OpenSpec contract before choosing a visual destination. Regenerate canonical artifacts at implementation time; never promote a proposal by moving files.

## Verification

- Run `node scripts/check-openspec.mjs`, pinned upstream strict validation, and affected behaviour tests.
- When visuals activate, verify manifest/artifact/spec agreement and separately generate, render and inspect affected screens.

## Child DOX Index

No child DOX documents while visuals are inactive. Create and index `wireframes/AGENTS.md` on activation. Parent: `../AGENTS.md`; specifications and changes: `../../openspec/AGENTS.md`.
