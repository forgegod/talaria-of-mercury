# docs

## Purpose

Current Talaria architecture, evidence-backed product capabilities, and
repository-tracked implementation changes. Keep present behaviour separate
from future requests and completed receipts.

## Ownership

- `architecture.md` — current runtime, dependencies, data flow, side effects,
  and verification boundaries.
- `design-decisions.md` — durable cross-cutting choices and their consequences.
- `product/` — current CAP contracts and executable evidence.
- `changes/` — active implementation plans, archived receipts, optional reviews.

## Local Contracts

- CAPs state present, falsifiable behaviour; CHGs state requested work and
  verified progress. Never describe unimplemented work as a capability.
- No standalone future-work briefs acting as a second progress authority.
  External Hermes/profile tooling is outside Talaria's implementation scope.
- Do not record private configuration, credentials, session transcripts, or
  model responses as evidence. Use synthetic inputs and reproducible tests.
- Root architectural decisions govern cross-cutting changes. Link contracts
  rather than copying competing versions into each document.

## Work Guidance

- Read both product and change contracts before changing records or placing
  review assets. Use relative Markdown links for document navigation and
  repository-relative backticked paths for implementation/test evidence.
- Keep records understandable without chat history. Preserve explicit write
  defaults and network boundaries; do not infer safety from command names.

## Verification

- Run `node scripts/check-product-records.mjs` and affected behaviour tests.
- Review architecture and local links against source; the record validator
  does not infer architecture truth or validate every repository document.

## Child DOX Index

- `product/AGENTS.md` — current material capabilities, index, visual contracts.
- `changes/AGENTS.md` — request/progress lifecycle and review-package ownership.