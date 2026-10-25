# GitHub automation

## Purpose

Credential-free verification of Talaria source, packaging, and product/change records.

## Ownership

- `workflows/ci.yml` — pull-request and main-branch push quality gate.

## Local Contracts

- CI installs from `uv.lock` with the dev extra and runs the repository's Python
  tests, build, record validator, and validator fixture suites.
- Python matrix checks the declared minimum (3.10) and development runtime (3.14).
- Node 24 is tooling-only. Do not add Node packages to Talaria's runtime.
- Force `_TESTING_TALARIA_SKIP_MODEL_BENCH=1`; never pass operator profiles,
  API tokens, or model credentials to tests. Runtime network/model paths are
  stubbed; dependency installation may access public registries.
- Workflow permissions are read-only. No publish, deployment, or auto-commit step.

## Work Guidance

- Keep workflow commands aligned with root `AGENTS.md` and README development
  instructions. Pin action revisions and retain their version comments.
- Changes to the quality gate must be exercised locally; report local success
  separately from an actual GitHub Actions run.

## Verification

- `uv sync --locked --extra dev`
- `uv run --extra dev pytest`
- `uv build`
- `node scripts/check-product-records.mjs`
- `node --test tests/records/*.test.mjs`

## Child DOX Index

No child DOX documents; this contract owns `workflows/`.
