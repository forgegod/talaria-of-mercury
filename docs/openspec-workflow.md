# Talaria OpenSpec workflow

## Authority

DOX is the binding repository instruction hierarchy. OpenSpec owns current
requirements in `openspec/specs/<capability>/spec.md` and native proposal, delta
specs, design, tasks, sync, and archive. Code and executable tests decide conflicts.
Read [the OpenSpec contract](../openspec/AGENTS.md) before changing these artifacts.

A change's native `tasks.md` is the sole mutable task list. Only the orchestrator
updates tasks and Git; implementers report evidence and reviewers are read-only.
Mediate native apply's checkbox writes rather than introducing a second writer.
Issues and pull requests document requests and review, not implementation progress.

## Tooling and installation boundaries

Use Node 24 and OpenSpec 1.15.0 for maintenance. Neither is required to install
or run Talaria; Python packaging remains in `pyproject.toml` and `uv.lock`.
Verify `openspec --version` is exactly `1.15.0`; do not silently use another pin.
The local structural checker uses only Node's standard library. CI installs the
exact OpenSpec package version into a job-local prefix using npm, with install
scripts disabled, and runs the same upstream validator. Transitive OpenSpec
packages are resolved at installation; Talaria does not carry a Node lockfile.
Dependency acquisition may need network; ordinary validation is offline.

Initialization uses `openspec init --tools none --no-animation
--no-copilot-cloud` only for an absent environment. Preserve existing native
artifacts. Probe/bootstrap CLI operations use disposable HOME and XDG_CONFIG_HOME
under the session scratch directory, telemetry off, and an allowlisted environment
with executable lookup, temp paths and required platform variables only. Do not
inherit credentials, profile selectors, npm configuration or NODE_OPTIONS.
A scratch home is not an OS sandbox or package trust guarantee.

Native workflows and companion skills are separate user/profile prerequisites,
not project dependencies or proof of installed host support. This repository
neither vendors skill packages nor creates project-local host adapters. Missing
companions block their corresponding invocation, not standalone local validation.
Installation and profile changes need separate explicit authority.

### Hermes native workflow prerequisite

OpenSpec 1.15.0 generates Hermes workflows under a project's `.hermes/skills/`;
it has no direct Hermes profile target. In a separately authorized setup:

1. Generate with the pinned CLI in a disposable scratch project using isolated
   HOME/XDG and telemetry off: `openspec init --tools hermes --profile core
   --no-copilot-cloud --no-animation`.
2. Review the complete generated packages. Explicitly select the destination
   Hermes profile; inspect collisions without overwriting local customizations.
3. Copy complete reviewed packages unchanged only under installation authority,
   then confirm discovery in a fresh session. Do not register this checkout as
   an external skill directory or bypass installation scans.

Scratch generation does not prove real profile installation or host execution.
Other hosts follow their supported user-scoped installation procedure.

## Companions and invocation scope

[AI Coding Agent Skills](https://github.com/forgegod/ai-coding-agent-skills)
owns the workflow bundle: `openspec-delivery`, `openspec-next`,
`git-commit-message`, and `capability-wireframes`. The separate
`autonomous-implementation-review` package requires an independent configured
reviewer. Package-relative references resolve inside the installed package,
not a sibling checkout or guessed profile path. Do not substitute legacy phase
skills for these prerequisites.

This project selects the bounded invocation contract below. Examples describe
future requests; adopting the workflow does not execute them. Existing operator
restrictions persist until explicitly lifted; plan approval is not execution authority.

| Request | Authorized scope |
| --- | --- |
| `/openspec-propose add-export` | Generate identity, prepare/reuse an isolated workspace, draft native proposal/deltas/design/tasks. Local planning only; no implementation, commits or issue writes. |
| `/openspec-next` or `/openspec-next add-export` | Resolve an approved change, bootstrap, execute one approved local task group, run gates, update tasks, make previewed local commits, then independently review the frozen exact candidate SHA. No push, PR or merge. |
| `/openspec-next inspect` | Read-only selection and evidence/dependency assessment; no workspace setup or Git writes. |
| `/openspec-delivery add-export` | The same bounded local continuation as next. |
| `/openspec-delivery prepare add-export` | Workspace preparation/bootstrap only; no implementation or commits. |
| `/openspec-delivery document add-export` | Separately authorize one risk-focused documentation issue; search/reuse, read back and bind its real URL. No implementation, commits or PR. |
| `/openspec-delivery finish add-export` | Separately authorize protected candidate publication/integration, exact integrated-revision gates, factual receipt/archive, acceptance-bound authorized issue closure, and clean dedicated workspace retirement. |

Restrictions such as `no commits`, `group 1 only`, or `keep the worktree` override
defaults. Finish does not authorize risk acceptance, release, direct target push,
force, protection bypass/change, branch deletion or another change. Do not invent
a ticket to satisfy a helper. Proposal-time issue writes require separate authority
and exact remote readback. Installation grants no execution or Git/GitHub authority.

## Native identity and artifacts

Before `openspec new change`, follow installed delivery `references/naming.md`:
run its `scripts/change-name.mjs generate --slug <slug>` once to produce
`<slug>-<lowercase-ulid>`. Prepare the isolated workspace before scaffolding.
Reuse that identity for the package, evidence, dependencies, and authorized
`change/<full-name>` branch. The operator supplies a slug, not a ULID. Resolve
resumes without renaming; ambiguous short names need selection. Existing migrated
change `python310-log-timestamps` retains its name. Naming grants no Git authority
and is not a distributed lock or upstream hook.

Outside a change package, cite the full change name and artifact role, not a
moving active/archive path. Keep native headings and frontmatter-free delta specs;
no progress, status, approval or PR metadata is selected for Markdown. Current
capability keys are flat lowercase hyphenated names. Native rename-only deltas use
FROM/TO pairs. For work without requirement changes, native `skip_specs: true`
metadata controls admission; upstream strict validation owns that check. Do not
fabricate a delta or setup change merely to satisfy a local checker.

Hermes native actions are `/openspec-propose`, `/openspec-apply-change`,
`/openspec-sync-specs`, and `/openspec-archive-change`. Other hosts may use
`/opsx:propose`, `/opsx:apply`, `/opsx:sync`, and `/opsx:archive`; do not assume
both spellings work. There is no `openspec verify` CLI gate.

## Execution and completion

1. Review the proposal's scope, acceptance, dependencies, gates and optional
   proposal visuals. Bind approval to the actual immutable plan revision/digest
   when commits are authorized. Changed scope or gates require reapproval.
2. A separate next/delivery invocation advances one approved group with the real
   Talaria bootstrap/gates below. Only the orchestrator updates checkboxes from
   evidence. `/git-commit-message` previews logical batches under the invocation's
   authority; it does not push or use issue-closing keywords.
3. `/autonomous-implementation-review` must actually review the frozen exact SHA
   in a different session and model before merge. Changed SHAs require renewed
   gates and review. If no independent reviewer is available, stop at that boundary;
   an implementer's diff inspection is not independent review.
4. Separately requested finish publishes/reuses one protected implementation PR.
   If it waits only for checks or human review, report it and stop; resume the
   same finish on the same candidate. Do not poll, bypass or open another PR.
5. Read back the real merge and containing target revision, then run its gates.
   Reconcile accepted delta specs into current requirements, validate final-base
   links and repeat verification after any change. Do not replay integrated work.
6. Only then create factual Git-tracked evidence and perform native archive in
   the authorized record-only closure PR. Verify movement and closure. A closure
   PR waiting for checks/review uses the same normal pause. Never predict a merge
   receipt or check archive tasks after local tests alone.
7. Close only separately authorized documentation issues whose acceptance was
   met, read back the close, and retire only a clean dedicated workspace. Issue
   closure is not archive; archive is not issue closure. Stop at this change.

Terminal finish/archive references belong as plain bullets under native
`## Workflow follow-up`, not checked pre-archive tasks. Preserve prior evidence
with its actual provenance; historical local results are not current integrated
acceptance. Independent changes use separate workspaces and orchestrators.
Dependent implementation waits for an accepted integrated prerequisite. Reconcile
shared-spec overlap semantically; a clean Git merge is not compatibility evidence.

## Visual handoff

Every current spec declares `**Primary surface:** human` or `none`. Talaria's
non-interactive text/JSON CLI declares `none`; no empty visual scaffolding ships.
For a future human-facing capability, proposal visuals remain review-only inside
the change package. Canonical generated HTML/PNG use stable capability/screen keys
under `docs/product/wireframes/`; add its child DOX on activation. Update generator,
rendered outputs, manifest, spec links and behavior tests together. Never promote
proposal files by moving them. See [product navigation and visuals](product/README.md).

## Gates

```sh
uv sync --locked --extra dev
uv run --extra dev pytest
uv build
node scripts/check-openspec.mjs
OPENSPEC_TELEMETRY=0 openspec validate --all --strict
node --test tests/records/*.test.mjs
```

CI forces `_TESTING_TALARIA_SKIP_MODEL_BENCH=1` on Python 3.10 and 3.14. Verify
both supported baseline and development runtimes when compatibility is affected.
No gate uses real operator profiles or live models. The checker and upstream CLI
validate artifacts, not executed tasks, independent review, approval, live host
installation, render freshness or protected integration. Synthetic native CLI
probes are not completion evidence for real changes.
