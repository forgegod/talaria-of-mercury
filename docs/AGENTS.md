# docs

## Purpose

Current Talaria architecture, capability navigation, and OpenSpec operating
policy. Current requirements and change packages are owned by `../openspec/`.

## Ownership

- `architecture.md` — current runtime, dependencies, data flow, side effects,
  and verification boundaries.
- `design-decisions.md` — durable cross-cutting choices and their consequences.
- `openspec-workflow.md` — invocation authority, native lifecycle, tooling and companion prerequisites.
- `product/` — navigation to current specifications and the optional canonical visual layer.

## Local Contracts

- Current specs state present, falsifiable behaviour; native changes state
  requested work and verified progress. Never describe unimplemented work as a capability.
- No standalone future-work briefs acting as a second progress authority.
  External Hermes/profile tooling is outside Talaria's implementation scope.
- Do not record private configuration, credentials, session transcripts, or
  model responses as evidence. Use synthetic inputs and reproducible tests.
- Root architectural decisions govern cross-cutting changes. Link contracts
  rather than copying competing versions into each document.

## Work Guidance

- Read `../openspec/AGENTS.md` and `openspec-workflow.md` before changing requirements or placing
  review assets. Use relative Markdown links for document navigation and
  repository-relative backticked paths for implementation/test evidence.
- Keep records understandable without chat history. Preserve explicit write
  defaults and network boundaries; do not infer safety from command names.

## Verification

- Run `node scripts/check-openspec.mjs`, pinned upstream strict validation, and affected behaviour tests.
- Review architecture and local links against source; the record validator
  does not infer architecture truth or validate every repository document.

## Child DOX Index

- `product/AGENTS.md` — capability navigation and optional canonical visual contracts.