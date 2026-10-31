import { test } from "node:test";
import assert from "node:assert/strict";
import { validateOpenSpec } from "../../scripts/check-openspec.mjs";
import {
  FIXTURE_SPEC,
  addCapability,
  addFixtureFile,
  createFixtureTree,
  patchFixture,
} from "./fixtures.mjs";

function errorsFor(root) {
  return validateOpenSpec(root).then((result) => result.errors);
}

function assertError(errors, expected) {
  assert.ok(
    errors.some((error) => error.includes(expected)),
    `expected an error containing ${JSON.stringify(expected)}; got:\n${errors.join("\n")}`,
  );
}

test("a valid OpenSpec fixture tree passes without errors", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    const result = await validateOpenSpec(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.specs, 1);
    assert.equal(result.changes, 0);
    assert.equal(result.visuals, false);
  } finally {
    cleanup();
  }
});

test("a missing openspec/config.yaml fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, "openspec/config.yaml", "");
    assertError(await errorsFor(root), "openspec/config.yaml is required");
  } finally {
    cleanup();
  }
});

test("an empty openspec/config.yaml fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, "openspec/config.yaml", "\n");
    assertError(await errorsFor(root), "openspec/config.yaml is required");
  } finally {
    cleanup();
  }
});

test("a spec missing ## Purpose fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "", "## Purpose\n\nFixture workflow capability.\n\n");
    assertError(await errorsFor(root), "must include a ## Purpose section");
  } finally {
    cleanup();
  }
});

test("a spec missing ## Requirements fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "", "## Requirements\n\n");
    assertError(await errorsFor(root), "must include a ## Requirements section");
  } finally {
    cleanup();
  }
});

test("a spec missing ## Verification fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "", "## Verification\n\n- `tests/workflow.test.mjs` proves the fixture capability.\n");
    assertError(await errorsFor(root), "must include a ## Verification section");
  } finally {
    cleanup();
  }
});

test("a requirement without a scenario fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      FIXTURE_SPEC,
      "",
      "\n#### Scenario: A proposal is opened\n\n- **WHEN** an operator opens a change package\n- **THEN** the package carries a proposal\n",
    );
    assertError(await errorsFor(root), "requirement 'Change proposals are reviewable' must include at least one #### Scenario");
  } finally {
    cleanup();
  }
});

test("a spec without any requirement fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "## Requirements\n\nNo requirements yet.\n", /## Requirements[\s\S]*?\n## Verification/);
    assertError(await errorsFor(root), "must declare at least one ### Requirement");
  } finally {
    cleanup();
  }
});

test("a requirement heading without the colon fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "### Requirement Change proposals are reviewable", "### Requirement: Change proposals are reviewable");
    assertError(await errorsFor(root), "has an invalid heading '### Requirement Change proposals are reviewable'");
  } finally {
    cleanup();
  }
});

test("a scenario written as ### Scenario fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "### Scenario: A proposal is opened", "#### Scenario: A proposal is opened");
    assertError(await errorsFor(root), "has an invalid heading '### Scenario: A proposal is opened'");
  } finally {
    cleanup();
  }
});

test("a scenario outside any requirement fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      FIXTURE_SPEC,
      "## Requirements\n\n#### Scenario: Stray scenario\n\n- **WHEN** it is stray\n- **THEN** it fails\n\n",
      "## Requirements\n\n",
    );
    assertError(await errorsFor(root), "scenario 'Stray scenario' is not inside a ### Requirement");
  } finally {
    cleanup();
  }
});

test("an empty requirement statement fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "### Requirement: ", "### Requirement: Change proposals are reviewable");
    assertError(await errorsFor(root), "has an empty requirement statement");
  } finally {
    cleanup();
  }
});

test("an unknown ## section fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "## Notes\n\nSynthetic.\n\n## Verification", "## Verification");
    assertError(await errorsFor(root), "has an unsupported section '## Notes'");
  } finally {
    cleanup();
  }
});

test("a ## Requirements section before ## Purpose fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    const source = `# Fixture workflow

**Primary surface:** none

## Requirements

### Requirement: Ordering holds

The fixture SHALL keep canonical section order.

#### Scenario: Ordering is checked

- **WHEN** sections are read
- **THEN** order is enforced

## Purpose

Fixture workflow capability.

## Verification

- \`tests/workflow.test.mjs\` proves the fixture capability.
`;
    patchFixture(root, FIXTURE_SPEC, source);
    assertError(await errorsFor(root), "must order its sections as ## Purpose, ## Requirements, ## Verification");
  } finally {
    cleanup();
  }
});

test("a missing primary surface declaration fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "", "**Primary surface:** none\n\n");
    assertError(await errorsFor(root), "invalid primary surface 'missing'; use human or none");
  } finally {
    cleanup();
  }
});

test("a primary surface declaration only in a code fence fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "```md\n**Primary surface:** none\n```", "**Primary surface:** none");
    assertError(await errorsFor(root), "invalid primary surface 'missing'; use human or none");
  } finally {
    cleanup();
  }
});

test("an invalid primary surface declaration fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "**Primary surface:** yes", "**Primary surface:** none");
    assertError(await errorsFor(root), "invalid primary surface 'yes'; use human or none");
  } finally {
    cleanup();
  }
});

test("a spec without a behaviour-test reference fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "", "- `tests/workflow.test.mjs` proves the fixture capability.\n");
    assertError(await errorsFor(root), "must reference at least one executable behaviour test in ## Verification");
  } finally {
    cleanup();
  }
});

test("a spec referencing a missing behaviour test fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(root, FIXTURE_SPEC, "`tests/missing.test.mjs`", "`tests/workflow.test.mjs`");
    assertError(await errorsFor(root), "references missing behaviour test: tests/missing.test.mjs");
  } finally {
    cleanup();
  }
});

test("a spec directory with an invalid capability key fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addCapability(root, "Fixture_Workflow");
    assertError(await errorsFor(root), "openspec/specs/Fixture_Workflow must use a lowercase capability key");
  } finally {
    cleanup();
  }
});

test("a spec stored outside a capability directory fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(root, "openspec/specs/loose-spec.md", "# Loose\n");
    assertError(await errorsFor(root), "openspec/specs/loose-spec.md must live at openspec/specs/<capability>/spec.md");
  } finally {
    cleanup();
  }
});

test("two specs for the same capability key fail", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(root, "openspec/specs/fixture-workflow/extra.md", "# Extra spec\n");
    assertError(await errorsFor(root), "must live at openspec/specs/<capability>/spec.md");
  } finally {
    cleanup();
  }
});

const EVIDENCE_BULLET = "- `tests/workflow.test.mjs` proves the fixture capability.";

/** Point the fixture spec's ## Verification section at `references`. */
function useVerification(root, references) {
  patchFixture(
    root,
    FIXTURE_SPEC,
    references
      .map((reference) => `- \`${reference}\` — fixture evidence.`)
      .join("\n"),
    EVIDENCE_BULLET,
  );
}

test("a pytest test_*.py module is executable verification evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine.py",
      'from fixture import run\n\n\ndef test_engine_runs():\n    assert run() == "fixture"\n',
    );
    useVerification(root, ["tests/test_engine.py"]);
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});

test("a pytest *_test.py module is executable verification evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/engine_test.py",
      'async def test_engine_runs():\n    assert await run() == "fixture"\n',
    );
    useVerification(root, ["tests/engine_test.py"]);
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});

test("a unittest TestCase using self.assert* is executable evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine_class.py",
      'import unittest\n\n\nclass TestEngine(unittest.TestCase):\n    def test_runs(self):\n        self.assertEqual(run(), "fixture")\n',
    );
    useVerification(root, ["tests/test_engine_class.py"]);
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});

test("a pytest module with pytest.raises as its only assertion is accepted", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine_errors.py",
      'import pytest\n\n\ndef test_engine_rejects_empty():\n    with pytest.raises(ValueError):\n        run("")\n',
    );
    useVerification(root, ["tests/test_engine_errors.py"]);
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});

test("a test-named pytest helper without a test definition is rejected", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine_helpers.py",
      'def build_engine():\n    assert True\n    return "fixture"\n',
    );
    useVerification(root, ["tests/test_engine_helpers.py"]);
    const errors = await errorsFor(root);
    assertError(
      errors,
      "references a Python file without a test definition and assertion: tests/test_engine_helpers.py",
    );
    assertError(errors, "must reference at least one executable behaviour test");
  } finally {
    cleanup();
  }
});

test("a pytest test function without an assertion is rejected as evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(root, "tests/test_engine_smoke.py", "def test_engine_runs():\n    run()\n");
    useVerification(root, ["tests/test_engine_smoke.py"]);
    const errors = await errorsFor(root);
    assertError(
      errors,
      "references a Python file without a test definition and assertion: tests/test_engine_smoke.py",
    );
    assertError(errors, "must reference at least one executable behaviour test");
  } finally {
    cleanup();
  }
});

test("pytest support modules such as conftest.py are not evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/conftest.py",
      'import pytest\n\n\n@pytest.fixture\ndef engine():\n    return "fixture"\n',
    );
    useVerification(root, ["tests/conftest.py"]);
    const errors = await errorsFor(root);
    assertError(errors, "must reference at least one executable behaviour test");
    assert.ok(
      !errors.some((error) => error.includes("without a test definition")),
      errors.join("\n"),
    );
  } finally {
    cleanup();
  }
});

test("a JavaScript test module without an assertion is rejected as evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/fixture-helper.test.mjs",
      'import { test } from "node:test";\n\ntest("does nothing", () => {});\n',
    );
    useVerification(root, ["tests/fixture-helper.test.mjs"]);
    const errors = await errorsFor(root);
    assertError(
      errors,
      "references a JavaScript file without a test definition and assertion: tests/fixture-helper.test.mjs",
    );
    assertError(errors, "must reference at least one executable behaviour test");
  } finally {
    cleanup();
  }
});

test("a test-named JavaScript helper without a test definition is rejected", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/fixture-helper.test.mjs",
      'export function buildFixture() {\n  return "fixture";\n}\n',
    );
    useVerification(root, ["tests/fixture-helper.test.mjs"]);
    assertError(
      await errorsFor(root),
      "references a JavaScript file without a test definition and assertion: tests/fixture-helper.test.mjs",
    );
  } finally {
    cleanup();
  }
});

test("a JavaScript test module with a test and assertion is executable evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    useVerification(root, ["tests/workflow.test.mjs"]);
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});

test("a missing implementation reference in ## Verification fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    useVerification(root, ["tests/workflow.test.mjs", "src/missing-engine.mjs"]);
    assertError(
      await errorsFor(root),
      "references missing implementation file: src/missing-engine.mjs",
    );
  } finally {
    cleanup();
  }
});

test("an existing implementation reference in ## Verification passes", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(root, "src/engine.mjs", "export function run() {\n  return 'fixture';\n}\n");
    useVerification(root, ["tests/workflow.test.mjs", "src/engine.mjs"]);
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});

test("Markdown links to pytest modules count as executable evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(root, "tests/test_engine.py", "def test_engine():\n    assert True\n");
    patchFixture(
      root,
      FIXTURE_SPEC,
      '[Python evidence](../../../tests/test_engine.py "Behavior test")',
      "`tests/workflow.test.mjs`",
    );
    assert.deepEqual((await validateOpenSpec(root)).errors, []);
  } finally {
    cleanup();
  }
});
