/**
 * Fixture-tree builders for the OpenSpec structural-check behaviour tests.
 *
 * Each builder writes a minimal, valid OpenSpec tree under a fresh `node:os`
 * tmpdir and returns `{ root, cleanup }`. Mutation helpers and per-test patches
 * introduce exactly one structural break so a test can assert the matching
 * error. Trees are synthetic: no product code, no network, no upstream parser.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const FIXTURE_SPEC = "openspec/specs/fixture-workflow/spec.md";
/** Executable JavaScript evidence: a real test definition plus an assertion. */
export const WORKFLOW_TEST = "tests/workflow.test.mjs";

/** A minimal valid OpenSpec tree. */
export function buildValidTree(root) {
  write(root, "openspec/config.yaml", "schema: spec-driven\n");
  write(
    root,
    WORKFLOW_TEST,
    `import { test } from "node:test";
import assert from "node:assert/strict";

test("fixture workflow capability", () => {
  assert.equal("fixture", "fixture");
});
`,
  );
  write(
    root,
    FIXTURE_SPEC,
    `# Fixture workflow

**Primary surface:** none

## Purpose

Fixture workflow capability.

## Requirements

### Requirement: Change proposals are reviewable

The fixture SHALL record every material change as a reviewable proposal.

#### Scenario: A proposal is opened

- **WHEN** an operator opens a change package
- **THEN** the package carries a proposal

## Verification

- \`tests/workflow.test.mjs\` proves the fixture capability.
`,
  );
  return { root };
}

/** @param {string} root @param {string} relative @param {string} content */
function write(root, relative, content) {
  const target = path.join(root, relative);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content);
}

/**
 * Create a tmpdir with a valid tree and return it plus cleanup.
 *
 * @returns {{root: string, cleanup: () => void}}
 */
export function createFixtureTree() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "talaria-openspec-"));
  buildValidTree(root);
  return {
    root,
    cleanup: () => fs.rmSync(root, { recursive: true, force: true }),
  };
}

/**
 * Mutate a fixture file.
 *
 * @param {string} root
 * @param {string} relative
 * @param {string} replacement
 * @param {string} [match] when given, replaces the first occurrence of `match`
 */
export function patchFixture(root, relative, replacement, match) {
  const target = path.join(root, relative);
  const source = fs.readFileSync(target, "utf8");
  if (match === undefined) {
    fs.writeFileSync(target, replacement);
    return;
  }
  fs.writeFileSync(target, source.replace(match, replacement));
}

/** @param {string} root @param {string} relative @param {string} content */
export function addFixtureFile(root, relative, content) {
  write(root, relative, content);
}

/** A second valid capability spec with a stable capability key. */
export function addCapability(root, capability, { surface = "none" } = {}) {
  write(
    root,
    `openspec/specs/${capability}/spec.md`,
    `# ${capability}

**Primary surface:** ${surface}

## Purpose

Fixture capability ${capability}.

## Requirements

### Requirement: ${capability} behaves

The fixture SHALL support ${capability}.

#### Scenario: ${capability} works

- **WHEN** the capability is exercised
- **THEN** it works

## Verification

- \`tests/workflow.test.mjs\` proves ${capability}.
`,
  );
}

/** A complete active change package whose delta modifies the workflow spec. */
export function addChangePackage(root, {
  name = "add-fixture-behaviour",
  place = "changes",
  proposal = true,
  design = true,
  tasks = true,
  delta = true,
  tasksBody = "## 1. Implementation\n\n- [ ] 1.1 Add the fixture behaviour\n- [ ] 1.2 Prove it\n",
} = {}) {
  const base = `openspec/${place}/${name}`;
  if (proposal) {
    write(
      root,
      `${base}/proposal.md`,
      `# Add fixture behaviour

## Why

The fixture needs a reviewable change.

## What Changes

- Extend the fixture-workflow capability.
`,
    );
  }
  if (design) {
    write(
      root,
      `${base}/design.md`,
      `# Design

## Decisions

- Keep the fixture dependency-free.
`,
    );
  }
  if (tasks) write(root, `${base}/tasks.md`, `# Tasks\n\n${tasksBody}`);
  if (delta) {
    write(
      root,
      `${base}/specs/fixture-workflow/spec.md`,
      `## ADDED Requirements

### Requirement: Fixture behaviour is reviewable

The fixture SHALL make the fixture behaviour reviewable.

#### Scenario: Reviewing the fixture behaviour

- **WHEN** the change is reviewed
- **THEN** the delta states the behaviour
`,
    );
  }
  return base;
}

/** Canonical visuals for a human-surface capability, keyed by capability and screen. */
export function addVisuals(root, {
  capability = "fixture-workflow",
  screen = "fixture-overview",
  title = "Workflow overview",
  html = true,
  png = true,
  generate = true,
  index = true,
  manifestBody,
  specLink = true,
} = {}) {
  const base = "docs/product/wireframes";
  if (generate) write(root, `${base}/generate.mjs`, "// Synthetic generator fixture; never executed.\n");
  if (html) write(root, `${base}/html/${capability}-${screen}.html`, "<html>Synthetic screen fixture</html>\n");
  if (png) write(root, `${base}/exports/${capability}-${screen}.png`, "synthetic PNG inventory placeholder\n");
  if (index) {
    write(
      root,
      `${base}/index.html`,
      `<a href="html/${capability}-${screen}.html">${title}</a>\n`,
    );
  }
  write(
    root,
    `${base}/manifest.json`,
    manifestBody ??
      JSON.stringify({
        screens: [
          {
            capability,
            screen,
            title,
            html: `html/${capability}-${screen}.html`,
            png: `exports/${capability}-${screen}.png`,
            viewport: { width: 1280, height: 800 },
          },
        ],
      }, null, 2),
  );
  if (specLink) {
    const spec = path.join(root, "openspec", "specs", capability, "spec.md");
    const source = fs.readFileSync(spec, "utf8");
    fs.writeFileSync(
      spec,
      `${source.replace("**Primary surface:** none", "**Primary surface:** human").trimEnd()}

## Visuals

- [HTML](../../../${base}/html/${capability}-${screen}.html)
- [PNG](../../../${base}/exports/${capability}-${screen}.png)
`,
    );
  }
  return { capability, screen, html: `html/${capability}-${screen}.html`, png: `exports/${capability}-${screen}.png` };
}
