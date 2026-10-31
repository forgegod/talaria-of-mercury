# OpenSpec specifications and changes

## Purpose

Own current Talaria requirements and native change packages beneath the root DOX contract.

## Ownership

- `config.yaml` selects the upstream `spec-driven` schema and project guidance.
- `specs/<capability>/spec.md` states current requirements and executable evidence.
- `changes/<full-name>/` owns proposal, optional design, delta specs, native `tasks.md`, optional review-only assets, and factual evidence.
- `changes/archive/` retains completed packages after authorized integrated verification. Its date is storage, not a new identity.

## Local Contracts

- Read `../docs/openspec-workflow.md` before lifecycle work. OpenSpec owns scaffolding, apply instructions, sync, and archive; no parallel trackers.
- Only the orchestrator updates task checkboxes and Git. Implementers supply evidence; reviewers are read-only. A checked task is not acceptance or integration.
- New proposals use the installed delivery naming helper and isolated workspace before scaffolding. Reuse one full identity for resumes, dependencies, evidence, and authorized branches. Existing migrated change `python310-log-timestamps` keeps its name and pending integration boundary.
- Keep delta specs frontmatter-free with native requirement/scenario headings. Do not put progress, approval, status, or PR lists in frontmatter.
- Rename-only deltas use native FROM/TO pairs. A change without requirement changes uses native `skip_specs: true` metadata; upstream strict validation owns admission. Do not invent delta requirements to satisfy a local check.
- Checkboxes track pre-archive work only. Terminal workflow references belong as plain bullets under `## Workflow follow-up`, not prematurely completed archive tasks.
- Current specs declare `**Primary surface:** none` or `human`, with implementation and executable test paths under `## Verification`. Code/tests decide conflicts. Preserve command-specific write/network boundaries and known evidence gaps.
- Human-facing specs link canonical generated HTML/PNG by capability/screen key under `../docs/product/wireframes/`, never proposal assets. Proposal visuals remain review-only inside their package.
- Local validation never authorizes archive or remote writes. Preserve factual prior evidence without treating it as this run's test result or integrated acceptance.

## Work Guidance

Write falsifiable SHALL requirements and WHEN/THEN scenarios. Keep repository-relative implementation and test filenames in backticks; name test functions in adjacent prose, not as pytest node-ID paths. Independent changes use separate agent-managed workspaces and one orchestrator each. Reconcile shared-spec overlap before integration.

## Verification

- `node scripts/check-openspec.mjs`
- `OPENSPEC_TELEMETRY=0 openspec validate --all --strict` with OpenSpec 1.15.0
- `node --test tests/records/*.test.mjs`
- `uv run --extra dev pytest` and affected minimum-version checks

Structural checks do not establish semantic correctness, test execution, approval, render freshness, independent review, or integration.

## Child DOX Index

No child DOX documents. This contract owns nested specs and change packages; root DOX owns repository-wide authority.
