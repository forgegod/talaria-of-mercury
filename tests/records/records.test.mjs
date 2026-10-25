import { test } from "node:test";
import assert from "node:assert/strict";
import { validateRecords } from "../../scripts/check-product-records.mjs";
import {
  addFixtureFile,
  createFixtureTree,
  patchFixture,
} from "./fixtures.mjs";

function errorsFor(root) {
  return validateRecords(root).then((result) => result.errors);
}

test("a valid fixture tree passes without errors", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    const result = await validateRecords(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.capabilities, 1);
    assert.equal(result.changes, 0);
    assert.equal(result.manifest, false);
  } finally {
    cleanup();
  }
});

test("a CAP missing the Behaviour section fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      "docs/product/capabilities/CAP-001-fixture-capability.md",
      "",
      "## Behaviour\n\n- The fixture engine runs.\n\n",
    );
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("must include a Behaviour section"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("an invalid capability status fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      "docs/product/capabilities/CAP-001-fixture-capability.md",
      "**Status:** shipped",
      "**Status:** implemented",
    );
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("has invalid capability status"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a missing implementation file fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      "docs/product/capabilities/CAP-001-fixture-capability.md",
      "`src/other.mjs`",
      "`src/engine.mjs`",
    );
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("references missing implementation file: src/other.mjs"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a CAP without a behaviour-test reference fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      "docs/product/capabilities/CAP-001-fixture-capability.md",
      "",
      "- `src/engine.test.mjs` — proves the fixture engine runs.\n\n",
    );
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("must reference at least one executable behaviour test"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a missing behaviour-test file fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      "docs/product/capabilities/CAP-001-fixture-capability.md",
      "`src/missing.test.mjs`",
      "`src/engine.test.mjs`",
    );
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("references missing behaviour test: src/missing.test.mjs"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a CAP duplicated by a second file with the same ID fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "docs/product/capabilities/CAP-001-second-slug.md",
      `# CAP-001 — Duplicate capability\n\n**Status:** implemented\n\n## Behaviour\n\n- Nothing.\n\n## Implementation\n\n- \`src/engine.mjs\` — fixture engine.\n\n## Verification\n\n- \`src/engine.test.mjs\` — proves the fixture engine runs.\n`,
    );
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("duplicates capability ID CAP-001"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a CAP not linked from docs/product/index.md fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      "docs/product/index.md",
      "# Capability index\n",
      "# Capability index\n\n- [CAP-001 — Fixture capability](capabilities/CAP-001-fixture-capability.md)\n",
    );
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("docs/product/index.md must link CAP-001"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a broken local Markdown link inside a CAP fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    patchFixture(
      root,
      "docs/product/capabilities/CAP-001-fixture-capability.md",
      "[Architecture](../../missing.md)",
      "[Architecture](../../architecture.md)",
    );
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("has broken local link: ../../missing.md"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

const capability = "docs/product/capabilities/CAP-001-fixture-capability.md";
const javascriptEvidence = "- `src/engine.test.mjs` — proves the fixture engine runs.";

/** Point the fixture CAP's Verification section at `references`. */
function useVerification(root, references) {
  patchFixture(
    root,
    capability,
    references.map((reference) => `- \`${reference}\` — fixture evidence.`).join("\n"),
    javascriptEvidence,
  );
}

test("a pytest test_*.py module satisfies the behaviour-test reference", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine.py",
      "from engine import run\n\n\ndef test_engine_runs():\n    assert run() == 'fixture'\n",
    );
    useVerification(root, ["tests/test_engine.py"]);
    const result = await validateRecords(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.capabilities, 1);
  } finally {
    cleanup();
  }
});

test("a pytest *_test.py module satisfies the behaviour-test reference", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/engine_test.py",
      "async def test_engine_runs():\n    assert await run() == 'fixture'\n",
    );
    useVerification(root, ["tests/engine_test.py"]);
    const errors = await errorsFor(root);
    assert.deepEqual(errors, []);
  } finally {
    cleanup();
  }
});

test("a pytest class using self.assert* satisfies the behaviour-test reference", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine_class.py",
      "import unittest\n\n\nclass TestEngine(unittest.TestCase):\n    def test_runs(self):\n        self.assertEqual(run(), 'fixture')\n",
    );
    useVerification(root, ["tests/test_engine_class.py"]);
    const errors = await errorsFor(root);
    assert.deepEqual(errors, []);
  } finally {
    cleanup();
  }
});

test("a pytest module using pytest.raises as its only check is accepted", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine_errors.py",
      "import pytest\n\n\ndef test_engine_rejects_empty():\n    with pytest.raises(ValueError):\n        run('')\n",
    );
    useVerification(root, ["tests/test_engine_errors.py"]);
    const errors = await errorsFor(root);
    assert.deepEqual(errors, []);
  } finally {
    cleanup();
  }
});

test("a pytest module with several behaviour modules is accepted", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine.py",
      "def test_engine_runs():\n    assert run() == 'fixture'\n",
    );
    addFixtureFile(
      root,
      "src/engine.test.mjs",
      "import { test } from 'node:test';\n",
    );
    useVerification(root, ["tests/test_engine.py", "src/engine.test.mjs"]);
    const errors = await errorsFor(root);
    assert.deepEqual(errors, []);
  } finally {
    cleanup();
  }
});

test("a test-named Python helper module without test definitions is rejected", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine_helpers.py",
      "def build_engine():\n    return 'fixture'\n",
    );
    useVerification(root, ["tests/test_engine_helpers.py"]);
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes(
          "references a Python file without a test definition and assertion: tests/test_engine_helpers.py",
        ),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
    assert.ok(
      errors.some((error) =>
        error.includes("must reference at least one executable behaviour test"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a test function with no assertion is rejected as evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(
      root,
      "tests/test_engine_smoke.py",
      "def test_engine_runs():\n    run()\n",
    );
    useVerification(root, ["tests/test_engine_smoke.py"]);
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes(
          "references a Python file without a test definition and assertion: tests/test_engine_smoke.py",
        ),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
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
      "import pytest\n\n\n@pytest.fixture\ndef engine():\n    return 'fixture'\n",
    );
    useVerification(root, ["tests/conftest.py"]);
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("must reference at least one executable behaviour test"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
    assert.ok(
      !errors.some((error) => error.includes("without a test definition")),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a missing pytest evidence file is reported as missing", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    useVerification(root, ["tests/test_missing.py"]);
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("references missing behaviour test: tests/test_missing.py"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});

test("a non-test Python source file is not evidence", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(root, "src/engine.py", "def run():\n    return 'fixture'\n");
    useVerification(root, ["src/engine.py"]);
    const errors = await errorsFor(root);
    assert.ok(
      errors.some((error) =>
        error.includes("must reference at least one executable behaviour test"),
      ),
      `errors were: ${errors.join(" | ")}`,
    );
  } finally {
    cleanup();
  }
});
