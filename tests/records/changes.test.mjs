import { test } from "node:test";
import assert from "node:assert/strict";
import { validateRecords } from "../../scripts/check-product-records.mjs";
import { addFixtureFile, createFixtureTree } from "./fixtures.mjs";

const PHASES = `
| #   | Phase                | Status      | Verification gate      |
| --- | -------------------- | ----------- | ---------------------- |
| 1   | Ship the slice       | in-progress | \`pytest tests\` exits 0 |
`;

/**
 * @param {string} root
 * @param {object} options
 * @param {string} [options.place]
 * @param {string} [options.status]
 * @param {string} [options.impacts]
 * @param {string} [options.phases]
 * @param {boolean} [options.dropPhaseSection]
 * @param {boolean} [options.dropTable]
 * @param {boolean} [options.dropRequest]
 */
function addChange(root, {
  place = "active",
  status = "in-progress",
  impacts = "CAP-001",
  phases = PHASES,
  dropPhaseSection = false,
  dropTable = false,
  dropRequest = false,
} = {}) {
  const lines = [
    "# CHG-001 — Fixture change",
    "",
    `**Status:** ${status}`,
  ];
  if (!dropRequest) lines.push("**External request:** Direct operator request: fixture");
  lines.push(`**Impacts:** ${impacts}`, "**Baseline:** `abc1234`", "");
  if (!dropTable) lines.push(phases);
  if (!dropPhaseSection) {
    lines.push(
      "",
      "## Phase 1 — Ship the slice",
      "",
      "**Goal:** fixture",
      "",
      "1. do it",
      "",
      "**Verification gate:** `pytest tests` exits 0",
      "",
      "## Out of scope",
      "",
      "- none",
      "",
    );
  }
  addFixtureFile(root, `docs/changes/${place}/CHG-001-fixture-change.md`, lines.join("\n"));
}

test("a valid in-progress CHG in active/ passes", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChange(root);
    const result = await validateRecords(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.changes, 1);
  } finally {
    cleanup();
  }
});

test("an in-progress CHG in archive/ fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChange(root, { place: "archive" });
    const errors = await validateRecords(root).then((result) => result.errors);
    assert.ok(
      errors.some((error) =>
        error.includes("status 'in-progress' is invalid for archive/"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a done CHG in active/ fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChange(root, { status: "done", phases: PHASES.replace("in-progress", "done") });
    const errors = await validateRecords(root).then((result) => result.errors);
    assert.ok(
      errors.some((error) =>
        error.includes("status 'done' is invalid for active/"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a planned CHG with an in-progress phase fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChange(root, { status: "planned" });
    const errors = await validateRecords(root).then((result) => result.errors);
    assert.ok(
      errors.some((error) =>
        error.includes("cannot have an in-progress phase while planned"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("an in-progress CHG with two in-progress phases fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    const twoPhases = `
| #   | Phase                | Status      | Verification gate      |
| --- | -------------------- | ----------- | ---------------------- |
| 1   | Ship the slice       | in-progress | \`pytest tests\` exits 0 |
| 2   | Integrate records    | in-progress | \`node --test\` exits 0  |
`;
    addChange(root, { phases: twoPhases });
    const errors = await validateRecords(root).then((result) => result.errors);
    assert.ok(
      errors.some((error) =>
        error.includes("has more than one in-progress phase"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("Impacts referencing an unknown CAP fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChange(root, { impacts: "CAP-999" });
    const errors = await validateRecords(root).then((result) => result.errors);
    assert.ok(
      errors.some((error) =>
        error.includes("references unknown capability CAP-999"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a CHG missing the phase table header fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChange(root, { dropTable: true });
    const errors = await validateRecords(root).then((result) => result.errors);
    assert.ok(
      errors.some((error) =>
        error.includes("must include the canonical phase table header"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a CHG missing a numbered Phase section fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChange(root, { dropPhaseSection: true });
    const errors = await validateRecords(root).then((result) => result.errors);
    assert.ok(
      errors.some((error) =>
        error.includes("must include at least one numbered Phase section"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a CHG missing the External request metadata fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChange(root, { dropRequest: true });
    const errors = await validateRecords(root).then((result) => result.errors);
    assert.ok(
      errors.some((error) => error.includes("must name an External request")),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});
