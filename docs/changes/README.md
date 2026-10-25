# Change records

A `CHG-*.md` file is the repository's progress record for one material implementation request. The external tracker remains the request and discussion source; it does not define current behaviour or implementation progress.

## Lifecycle

```text
planned → in-progress → blocked → done
                    ↘ cancelled
```

`planned`, `in-progress`, and `blocked` records live in `active/`. `done` and `cancelled` records move to `archive/`. Archived records are implementation receipts, not product specifications; update the affected CAP files before archiving.

## Required metadata

Every CHG names:

- its stable `CHG-<number>` identity;
- the external ticket or direct operator request;
- affected CAP IDs;
- the baseline commit or release being changed;
- execution phases with a status and executable verification gate.

Use `pending`, `in-progress`, and `done (<evidence>)` for phase rows. A phase is done only after its gate passes. Keep one phase in progress at a time.

## CHG shape

```markdown
# CHG-001 — Change name

**Status:** planned
**External request:** <ticket URL or direct operator request>
**Impacts:** CAP-001
**Baseline:** `<commit or release>`

| #   | Phase                           | Status  | Verification gate                                          |
| --- | ------------------------------- | ------- | ---------------------------------------------------------- |
| 1   | Specify changed behaviour       | pending | CAP and behaviour-test change reviewed                     |
| 2   | Implement vertical slice        | pending | Targeted test command exits 0                              |
| 3   | Integrate current-state records | pending | `node scripts/check-product-records.mjs && uv run --extra dev pytest` exits 0                  |

## Phase 1 — Specify changed behaviour

**Goal:** <observable target>

1. <exact steps>

**Verification gate:** <executable predicate>

## Out of scope

- <tempting but excluded work>
```

## Splitting and decisions

Split a CHG only at a verified phase boundary. Link the direct dependency and keep the external request plus impacted CAPs in every resulting active record.

A local implementation choice belongs in the affected CAP only if it constrains current behaviour. An irreversible or cross-cutting decision belongs in `../design-decisions.md`. Do not use a CHG as a decision diary.

## Agent integration

Optional installed `application-records` and `phased-plan-*` skills drive this
lifecycle. Design creates the CHG phase table; execution implements a bounded
phase; closeout verifies integration and archives completed work. The active
CHG is the tracked plan, not a profile-private checklist. One orchestrator owns
CHG/Git writes; delegated executors return evidence instead of changing progress.
Approval of a plan does not itself authorize implementation or commits.

## Current records

| Change | Status |
| --- | --- |
| [CHG-001 — Python 3.10 doctor log timestamps](active/CHG-001-python310-log-timestamps.md) | in-progress; implementation verified, integration not authorized |

## Archive

No change record is archived.

## Review packages

Keep `active/` and `archive/` flat and record-only: each entry is a `CHG-<number>-<slug>.md` file. Put proposal HTML, PNGs, design sources, and Markdown notes in an optional `reviews/CHG-<number>/` package instead, using the exact owning CHG ID. Do not create empty package scaffolding.

- The package requires `README.md` with the literal field `**Status:** review-only`. Use that README as the review entry point and link its artifacts; visible proposal pages should also say review-only. Filenames inside the package need not use a CHG prefix, and supporting subdirectories are allowed.
- The owning active or archived CHG links the package README with a relative Markdown link targeting `../reviews/CHG-<number>/README.md`. The package directory names its owner; the README is a label and entry point, not another progress record.
- The validator rejects malformed or unowned packages, missing README labels, and missing owner links. It checks local links in every package Markdown file without parsing or counting those files as CHGs.
- CAPs never link review-package files, and proposals never enter the product wireframe manifest. Read both product and change DOX, plus the wireframe child if present, before choosing where to write a visual artifact.

On implementation, add or update the affected CAPs and regenerate their canonical HTML/PNG pairs through the [product wireframe workflow](../product/README.md#wireframes). A proposal is not promoted by moving its files. The CHG's visual handoff gate names the generator, renderer, affected CAPs, and tests; it also verifies honest `Primary surface` declarations and current renders, which structural validation cannot prove.

On `done` or `cancelled` closure, keep the review package at its same path as a frozen review-only receipt. Repair references when archiving the owner; do not move packages into `archive/` or the product tree. For cancellation, retain the review without claiming implementation. If an owner is removed, remove its package and repair inbound links. A scope split assigns each package exactly one owner and updates its ID/path and references when ownership changes; do not duplicate mutable progress.

## Verification

Run `node scripts/check-product-records.mjs` before the relevant phase gate and again before archiving. The repository's test gate remains required for the implementation itself.
