import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { validateOpenSpec } from "../../scripts/check-openspec.mjs";
import {
  addChangePackage,
  addFixtureFile,
  createFixtureTree,
  patchFixture,
} from "./fixtures.mjs";

const PACKAGE = "openspec/changes/add-fixture-behaviour";

function assertError(errors, expected) {
  assert.ok(
    errors.some((error) => error.includes(expected)),
    `expected an error containing ${JSON.stringify(expected)}; got:\n${errors.join("\n")}`,
  );
}

test("a valid active change package passes", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    const result = await validateOpenSpec(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.changes, 1);
  } finally {
    cleanup();
  }
});

test("a valid archived change package passes", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root, { name: "2024-05-01-add-fixture-behaviour", place: "changes/archive" });
    const result = await validateOpenSpec(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.changes, 1);
  } finally {
    cleanup();
  }
});

test("an archived package without a date prefix fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root, { place: "changes/archive" });
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must use <YYYY-MM-DD>-<slug>",
    );
  } finally {
    cleanup();
  }
});

test("an active package with an invalid slug fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root, { name: "Add_Fixture" });
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must use a lowercase change slug",
    );
  } finally {
    cleanup();
  }
});

const missingParts = [
  ["proposal.md", { proposal: false }, "is missing proposal.md"],
  ["tasks.md", { tasks: false }, "is missing tasks.md"],
];

for (const [label, options, expected] of missingParts) {
  test(`a change package missing ${label} fails`, async () => {
    const { root, cleanup } = createFixtureTree();
    try {
      addChangePackage(root, options);
      assertError(await validateOpenSpec(root).then((result) => result.errors), expected);
    } finally {
      cleanup();
    }
  });
}

test("a change package may omit optional design.md", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root, { design: false });
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});

// Native strict validation owns zero-delta and skip_specs admission. The local
// checker validates deltas that are present and parses no YAML metadata.
test("a change package with no spec delta defers zero-delta admission to native validation", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root, { delta: false });
    addFixtureFile(root, `${PACKAGE}/.openspec.yaml`, "skip_specs: true\n");
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});

test("a proposal without ## Why fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(root, `${PACKAGE}/proposal.md`, "", "## Why\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "proposal.md must include a ## Why section",
    );
  } finally {
    cleanup();
  }
});

test("a proposal without ## What Changes fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(root, `${PACKAGE}/proposal.md`, "", "## What Changes\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "proposal.md must include a ## What Changes section",
    );
  } finally {
    cleanup();
  }
});

test("an empty design fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(root, `${PACKAGE}/design.md`, "# Design\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "design.md must state at least one design decision",
    );
  } finally {
    cleanup();
  }
});

test("tasks without a checkbox task fail", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(root, `${PACKAGE}/tasks.md`, "# Tasks\n\n## 1. Implementation\n\nDo the work.\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "tasks.md must include at least one checkbox task",
    );
  } finally {
    cleanup();
  }
});

test("a non-checkbox list item in tasks.md fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(
      root,
      `${PACKAGE}/tasks.md`,
      "# Tasks\n\n## 1. Implementation\n\n- [ ] 1.1 Add behaviour\n- plain note\n",
    );
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "tasks.md must use checkbox list items only",
    );
  } finally {
    cleanup();
  }
});

test("task progress outside tasks.md fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(
      root,
      `${PACKAGE}/proposal.md`,
      "- [x] hidden progress\n",
      "- Extend the fixture-workflow capability.\n",
    );
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must be the only task-progress writer",
    );
  } finally {
    cleanup();
  }
});

const delta = `${PACKAGE}/specs/fixture-workflow/spec.md`;

test("a delta with no ADDED, MODIFIED, REMOVED, or RENAMED section fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(root, delta, "## Notes\n\nSynthetic.\n", "## ADDED Requirements\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must declare an ## ADDED Requirements, ## MODIFIED Requirements, ## REMOVED Requirements, or ## RENAMED Requirements section",
    );
  } finally {
    cleanup();
  }
});

test("an unsupported delta section fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(root, delta, "## Notes\n\nSynthetic.\n\n## ADDED Requirements", "## ADDED Requirements");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "has an unsupported delta section '## Notes'",
    );
  } finally {
    cleanup();
  }
});

test("an ADDED requirement without a scenario fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(
      root,
      delta,
      "The fixture SHALL make the fixture behaviour reviewable.\n",
      "The fixture SHALL make the fixture behaviour reviewable.\n\n#### Scenario: Reviewing the fixture behaviour\n\n- **WHEN** the change is reviewed\n- **THEN** the delta states the behaviour\n",
    );
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "requirement 'Fixture behaviour is reviewable' must include at least one #### Scenario",
    );
  } finally {
    cleanup();
  }
});

test("a delta with an invalid requirement heading fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(root, delta, "### Requirement Fixture behaviour is reviewable", "### Requirement: Fixture behaviour is reviewable");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "has an invalid heading '### Requirement Fixture behaviour is reviewable'",
    );
  } finally {
    cleanup();
  }
});

test("a delta for an invalid capability key fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    const source = readFileSync(path.join(root, delta), "utf8");
    addFixtureFile(
      root,
      `${PACKAGE}/specs/Bad_Capability/spec.md`,
      source,
    );
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must use a lowercase capability key",
    );
  } finally {
    cleanup();
  }
});

test("a broken link inside a change package fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    patchFixture(root, `${PACKAGE}/design.md`, "\n[Missing](missing.md)\n", "## Decisions");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "has broken local link: missing.md",
    );
  } finally {
    cleanup();
  }
});