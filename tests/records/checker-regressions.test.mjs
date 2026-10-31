import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  FIXTURE_SPEC,
  addChangePackage,
  addFixtureFile,
  addVisuals,
  createFixtureTree,
  patchFixture,
} from "./fixtures.mjs";
import { validateOpenSpec } from "../../scripts/check-openspec.mjs";

const CHECKER = fileURLToPath(new URL("../../scripts/check-openspec.mjs", import.meta.url));
const DELTA = "openspec/changes/add-fixture-behaviour/specs/fixture-workflow/spec.md";
const VISUALS = "docs/product/wireframes";
const CAPABILITY = "fixture-workflow";
const SCREEN = "fixture-overview";
const SYMLINK = "is a symlink; OpenSpec records and visual artifacts must be real files";

/** Synthetic tree outside the fixture repository. */
function outsideTree(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "talaria-openspec-outside-"));
  for (const [relative, content] of Object.entries(files)) {
    const target = path.join(root, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  }
  return root;
}

function removeTree(target) {
  fs.rmSync(target, { recursive: true, force: true });
}

function manifestScreens(root, screens) {
  addFixtureFile(root, `${VISUALS}/manifest.json`, JSON.stringify({ screens }, null, 2));
}

function screenEntry(overrides = {}) {
  return {
    capability: CAPABILITY,
    screen: SCREEN,
    title: "Workflow overview",
    html: `html/${CAPABILITY}-${SCREEN}.html`,
    png: `exports/${CAPABILITY}-${SCREEN}.png`,
    viewport: { width: 1280, height: 800 },
    ...overrides,
  };
}

test("Markdown links to executable tests count as verification evidence", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  patchFixture(root, FIXTURE_SPEC, "[Behavior test](../../../tests/workflow.test.mjs)", "`tests/workflow.test.mjs`");
  assert.deepEqual((await validateOpenSpec(root)).errors, []);
});

test("empty native directories can be retained with an empty marker file", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  for (const directory of ["changes", "changes/archive"]) {
    addFixtureFile(root, `openspec/${directory}/.gitkeep`, "");
  }
  assert.deepEqual((await validateOpenSpec(root)).errors, []);
  addFixtureFile(root, "openspec/changes/.gitkeep", "not a marker\n");
  assert.ok((await validateOpenSpec(root)).errors.some((error) => error.includes(".gitkeep")));
});

test("a native delta ## Purpose section is accepted", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  addChangePackage(root);
  patchFixture(
    root,
    DELTA,
    "## Purpose\n\nSynthetic delta purpose.\n\n## ADDED Requirements",
    "## ADDED Requirements",
  );
  assert.deepEqual((await validateOpenSpec(root)).errors, []);
});

test("a delta with YAML frontmatter before its first section fails", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  addChangePackage(root);
  patchFixture(
    root,
    DELTA,
    "---\ntitle: fixture delta\n---\n\n## ADDED Requirements",
    "## ADDED Requirements",
  );
  const errors = (await validateOpenSpec(root)).errors;
  assert.ok(
    errors.some((error) => error.includes("native deltas carry no frontmatter or preamble")),
    errors.join("\n"),
  );
});

test("a delta with a prose preamble before its first section fails", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  addChangePackage(root);
  patchFixture(root, DELTA, "Fixture preamble.\n\n## ADDED Requirements", "## ADDED Requirements");
  const errors = (await validateOpenSpec(root)).errors;
  assert.ok(
    errors.some((error) => error.includes("native deltas carry no frontmatter or preamble")),
    errors.join("\n"),
  );
});

test("a symlinked openspec tree fails closed before its contents are read", async (t) => {
  const { root, cleanup } = createFixtureTree();
  const outside = outsideTree({
    "config.yaml": "schema: spec-driven\n",
    "specs/leaked-capability/spec.md": "# Leaked\n\nPreamble only.\n",
  });
  t.after(() => {
    cleanup();
    removeTree(outside);
  });
  removeTree(path.join(root, "openspec"));
  fs.symlinkSync(outside, path.join(root, "openspec"), "dir");

  const result = await validateOpenSpec(root);

  assert.deepEqual(result.errors, [`openspec ${SYMLINK}`]);
  assert.equal(result.specs, 0);
  assert.equal(result.changes, 0);
  assert.equal(result.visuals, false);
});

test("a symlinked visuals ancestor fails closed", async (t) => {
  const { root, cleanup } = createFixtureTree();
  const outside = outsideTree({
    "wireframes/generate.mjs": "// Synthetic generator fixture; never executed.\n",
    "wireframes/index.html": "<html>Outside</html>\n",
    "wireframes/manifest.json": JSON.stringify({ screens: [] }),
  });
  t.after(() => {
    cleanup();
    removeTree(outside);
  });
  fs.mkdirSync(path.join(root, "docs"), { recursive: true });
  fs.symlinkSync(outside, path.join(root, "docs", "product"), "dir");

  const result = await validateOpenSpec(root);

  assert.deepEqual(result.errors, [`docs/product ${SYMLINK}`]);
  assert.equal(result.visuals, false);
});

test("a nested symlink stops the scan before record contents are parsed", async (t) => {
  const { root, cleanup } = createFixtureTree();
  const outside = outsideTree({ "outside.md": "# Outside\n" });
  t.after(() => {
    cleanup();
    removeTree(outside);
  });
  // Would fail on its own: the ## Purpose section was removed.
  patchFixture(root, FIXTURE_SPEC, "", "## Purpose\n");
  fs.symlinkSync(
    path.join(outside, "outside.md"),
    path.join(root, "openspec", "specs", "fixture-workflow", "linked.md"),
  );

  const result = await validateOpenSpec(root);

  assert.deepEqual(result.errors, [
    `openspec/specs/fixture-workflow/linked.md ${SYMLINK}`,
  ]);
  assert.equal(result.specs, 0);
});

const malformedArtifacts = [
  ["missing html and png", { html: undefined, png: undefined }],
  ["null and number paths", { html: null, png: 42 }],
  ["object and array paths", { html: { path: "html/other.html" }, png: ["exports/other.png"] }],
];

for (const [label, overrides] of malformedArtifacts) {
  test(`a manifest screen with ${label} reports diagnostics instead of throwing`, async (t) => {
    const { root, cleanup } = createFixtureTree();
    t.after(cleanup);
    addVisuals(root, { capability: CAPABILITY, screen: SCREEN });
    manifestScreens(root, [screenEntry(overrides)]);

    const result = await validateOpenSpec(root);
    const errors = result.errors.join("\n");
    assert.ok(
      result.errors.some((error) =>
        error.includes(`manifest html path must be html/${CAPABILITY}-${SCREEN}.html`),
      ),
      errors,
    );
    assert.ok(
      result.errors.some((error) =>
        error.includes(`manifest png path must be exports/${CAPABILITY}-${SCREEN}.png`),
      ),
      errors,
    );
  });
}

test("a RENAMED-only delta needs no ### Requirement headings", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  addChangePackage(root);
  patchFixture(
    root,
    DELTA,
    "## RENAMED Requirements\n\n- FROM: `### Requirement: Fixture behaviour is reviewable`\n- TO: `### Requirement: Fixture behaviour is observable`\n",
  );
  assert.deepEqual((await validateOpenSpec(root)).errors, []);
});

test("plain bullets are allowed only in an explicit ## Workflow follow-up section", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  addChangePackage(root, {
    tasksBody:
      "## 1. Implementation\n\n- [ ] 1.1 Add the fixture behaviour\n\n## Workflow follow-up\n\n- Archive after review.\n- Verify the archived result.\n",
  });
  assert.deepEqual((await validateOpenSpec(root)).errors, []);
});

test("an existing native specs directory may be empty with a marker", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  fs.rmSync(path.join(root, "openspec", "specs"), { recursive: true, force: true });
  fs.mkdirSync(path.join(root, "openspec", "specs"));
  addFixtureFile(root, "openspec/specs/.gitkeep", "");
  const result = await validateOpenSpec(root);
  assert.deepEqual(result.errors, []);
  assert.equal(result.specs, 0);
});

test("a missing native specs directory fails", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  fs.rmSync(path.join(root, "openspec", "specs"), { recursive: true, force: true });
  const errors = (await validateOpenSpec(root)).errors;
  assert.ok(
    errors.some((error) => error.includes("openspec/specs directory is required")),
    errors.join("\n"),
  );
});

test("CommonMark titles and angle destinations resolve test evidence", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  addFixtureFile(
    root,
    "tests/workflow test.test.mjs",
    `import { test } from "node:test";
import assert from "node:assert/strict";

test("spaced fixture workflow", () => {
  assert.ok(true);
});
`,
  );
  patchFixture(
    root,
    FIXTURE_SPEC,
    [
      '- [Behaviour test](../../../tests/workflow.test.mjs "Executable test")',
      "- [Angle test](<../../../tests/workflow test.test.mjs> 'Spaced test')",
      "- [Paren test](../../../tests/workflow.test.mjs (Executable test))",
    ].join("\n"),
    "`tests/workflow.test.mjs` proves the fixture capability.",
  );
  assert.deepEqual((await validateOpenSpec(root)).errors, []);
});

test("a titled local link reports the destination path, not its title", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  patchFixture(
    root,
    FIXTURE_SPEC,
    '- [Missing](missing.md "Missing evidence")',
    "`tests/workflow.test.mjs` proves the fixture capability.",
  );
  const errors = (await validateOpenSpec(root)).errors;
  assert.ok(
    errors.includes(`${FIXTURE_SPEC} has broken local link: missing.md`),
    errors.join("\n"),
  );
});

test("section order is judged on fence-stripped headings", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  patchFixture(
    root,
    FIXTURE_SPEC,
    `# Fixture workflow

**Primary surface:** none

\`\`\`text
## Purpose
\`\`\`

## Requirements

### Requirement: Change proposals are reviewable

The fixture SHALL record every material change as a reviewable proposal.

#### Scenario: A proposal is opened

- **WHEN** an operator opens a change package
- **THEN** the package carries a proposal

## Verification

- \`tests/workflow.test.mjs\` proves the fixture capability.

## Purpose

Fixture workflow capability.
`,
  );
  const errors = (await validateOpenSpec(root)).errors;
  assert.ok(
    errors.some((error) => error.includes("must order its sections")),
    errors.join("\n"),
  );
});

test("level 5 and 6 prose headings are ignored", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  patchFixture(
    root,
    FIXTURE_SPEC,
    "##### Notes\n\nSynthetic prose.\n\n###### Detail\n\nMore detail.\n",
    "Fixture workflow capability.",
  );
  assert.deepEqual((await validateOpenSpec(root)).errors, []);
});

test("a backticked test reference cannot escape through a symlinked ancestor", async (t) => {
  const { root, cleanup } = createFixtureTree();
  const outside = outsideTree({ "workflow.test.mjs": "export const outside = true;\n" });
  t.after(() => {
    cleanup();
    removeTree(outside);
  });
  removeTree(path.join(root, "tests"));
  fs.symlinkSync(outside, path.join(root, "tests"), "dir");

  const errors = (await validateOpenSpec(root)).errors;

  assert.ok(
    errors.some((error) =>
      error.includes("references a behaviour test outside the repository: tests/workflow.test.mjs"),
    ),
    errors.join("\n"),
  );
});

test("a symlinked behaviour test file outside the repository is rejected", async (t) => {
  const { root, cleanup } = createFixtureTree();
  const outside = outsideTree({ "workflow.test.mjs": "export const outside = true;\n" });
  t.after(() => {
    cleanup();
    removeTree(outside);
  });
  fs.rmSync(path.join(root, "tests", "workflow.test.mjs"));
  fs.symlinkSync(
    path.join(outside, "workflow.test.mjs"),
    path.join(root, "tests", "workflow.test.mjs"),
  );

  const errors = (await validateOpenSpec(root)).errors;

  assert.ok(
    errors.some((error) =>
      error.includes("references a behaviour test outside the repository: tests/workflow.test.mjs"),
    ),
    errors.join("\n"),
  );
});

test("validateOpenSpec returns actionable filesystem errors instead of throwing", async (t) => {
  const { root, cleanup } = createFixtureTree();
  t.after(cleanup);
  removeTree(path.join(root, "openspec", "specs"));
  fs.writeFileSync(path.join(root, "openspec", "specs"), "not a directory\n");

  const result = await validateOpenSpec(root);

  assert.equal(result.errors.length, 1, result.errors.join("\n"));
  assert.match(result.errors[0], /ENOTDIR/);
  assert.match(result.errors[0], /openspec\/specs/);
});

test("the CLI exits nonzero with the filesystem diagnostic", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "talaria-openspec-cli-"));
  t.after(() => removeTree(root));
  const target = path.join(root, "not-a-repository");
  fs.writeFileSync(target, "synthetic\n");
  const result = spawnSync(process.execPath, [CHECKER, target], {
    encoding: "utf8",
    env: { PATH: process.env.PATH, OPENSPEC_TELEMETRY: "0" },
    timeout: 30_000,
  });

  assert.ifError(result.error);
  assert.equal(result.status, 1, result.stdout);
  assert.match(result.stderr, /OpenSpec check failed/);
  assert.match(result.stderr, /ENOTDIR/);
  assert.match(result.stderr, /openspec\/specs/);
});
