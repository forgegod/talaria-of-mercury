import { test } from "node:test";
import assert from "node:assert/strict";
import { appendFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { validateOpenSpec } from "../../scripts/check-openspec.mjs";
import {
  FIXTURE_SPEC,
  addChangePackage,
  addFixtureFile,
  addVisuals,
  createFixtureTree,
  patchFixture,
} from "./fixtures.mjs";

const visuals = "docs/product/wireframes";
const cap = "fixture-workflow";
const screen = "fixture-overview";
const html = `html/${cap}-${screen}.html`;
const png = `exports/${cap}-${screen}.png`;

function append(root, file, text) {
  appendFileSync(path.join(root, file), text);
}

function manifest(root, screens) {
  addFixtureFile(root, `${visuals}/manifest.json`, JSON.stringify({ screens }, null, 2));
}

function screenEntry(overrides = {}) {
  return {
    capability: cap,
    screen,
    title: "Workflow overview",
    html,
    png,
    viewport: { width: 1280, height: 800 },
    ...overrides,
  };
}

/** A valid human-surface capability plus canonical visuals. */
function visualFixture(root) {
  addVisuals(root, { capability: cap, screen });
}

function assertError(errors, expected) {
  assert.ok(
    errors.some((error) => error.includes(expected)),
    `expected an error containing ${JSON.stringify(expected)}; got:\n${errors.join("\n")}`,
  );
}

test("canonical visuals for a human surface pass", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    const result = await validateOpenSpec(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.visuals, true);
  } finally {
    cleanup();
  }
});

test("a none surface without a visuals directory passes", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    const result = await validateOpenSpec(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.visuals, false);
  } finally {
    cleanup();
  }
});

const requiredVisuals = [
  ["manifest", root => rmSync(path.join(root, visuals, "manifest.json")), "manifest.json is required for the visual workflow"],
  ["generator", root => rmSync(path.join(root, visuals, "generate.mjs")), "generate.mjs is required for the visual workflow"],
  ["index", root => rmSync(path.join(root, visuals, "index.html")), "index.html is required for the visual workflow"],
];

for (const [label, mutate, expected] of requiredVisuals) {
  test(`a human surface missing its ${label} fails`, async () => {
    const { root, cleanup } = createFixtureTree();
    try {
      visualFixture(root);
      mutate(root);
      assertError(await validateOpenSpec(root).then((result) => result.errors), expected);
    } finally {
      cleanup();
    }
  });
}

test("a visuals directory without a manifest fails even when no surface is human", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addFixtureFile(root, `${visuals}/index.html`, "<html>Synthetic</html>\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "manifest.json is required for the visual workflow",
    );
  } finally {
    cleanup();
  }
});

test("an empty manifest is rejected as empty visual scaffolding", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    manifest(root, []);
    const errors = await validateOpenSpec(root).then((result) => result.errors);
    assertError(errors, "wireframe manifest must contain a non-empty screens array");
    assertError(errors, "wireframe manifest must list primary-surface capability fixture-workflow");
  } finally {
    cleanup();
  }
});

test("a human surface missing from the manifest fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    manifest(root, [screenEntry({ capability: "unrelated" })]);
    const errors = await validateOpenSpec(root).then((result) => result.errors);
    assertError(errors, "wireframe manifest must list primary-surface capability fixture-workflow");
    assertError(errors, "wireframe manifest lists unknown capability unrelated");
  } finally {
    cleanup();
  }
});

test("a visual artifact listed in the manifest but absent fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    rmSync(path.join(root, visuals, html));
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      `wireframe manifest lists missing artifact: ${html}`,
    );
  } finally {
    cleanup();
  }
});

test("a visual artifact absent from the manifest fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    addFixtureFile(root, `${visuals}/html/${cap}-extra.html`, "<html>Unlisted</html>\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      `visual artifact is missing from the manifest: html/${cap}-extra.html`,
    );
  } finally {
    cleanup();
  }
});

test("a proposal in the canonical visual directory fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    addFixtureFile(root, `${visuals}/html/add-fixture-behaviour-proposal.html`, "<html>Proposal</html>\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "invalid visual artifact",
    );
  } finally {
    cleanup();
  }
});

test("a human spec that does not link its visuals fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addVisuals(root, { capability: cap, screen, specLink: false });
    patchFixture(root, FIXTURE_SPEC, "**Primary surface:** human", "**Primary surface:** none");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must link its visual HTML and PNG under ## Visuals",
    );
  } finally {
    cleanup();
  }
});

test("a none-surface spec declaring visuals fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    append(
      root,
      FIXTURE_SPEC,
      `\n## Visuals\n\n- [HTML](../../../${visuals}/${html})\n`,
    );
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must not declare ## Visuals while Primary surface: none",
    );
  } finally {
    cleanup();
  }
});

test("a manifest entry for a none-surface capability fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addVisuals(root, { capability: cap, screen });
    patchFixture(root, FIXTURE_SPEC, "**Primary surface:** none", "**Primary surface:** human");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must declare Primary surface: human",
    );
  } finally {
    cleanup();
  }
});

test("a duplicate capability/screen key fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    manifest(root, [screenEntry(), screenEntry()]);
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "wireframe manifest duplicates visual fixture-workflow/fixture-overview",
    );
  } finally {
    cleanup();
  }
});

test("an invalid screen key fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    manifest(root, [screenEntry({ screen: "Workflow_Overview", html: "html/Workflow_Overview.html", png: "exports/Workflow_Overview.png" })]);
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must use a lowercase visual screen key",
    );
  } finally {
    cleanup();
  }
});

const manifestFailures = [
  ["a proposal path", { html: "../../openspec/changes/add-fixture-behaviour/proposal.md" }, "manifest html path must be html/fixture-workflow-fixture-overview.html"],
  ["a mismatched png path", { png: "exports/other.png" }, "manifest png path must be exports/fixture-workflow-fixture-overview.png"],
  ["an invalid viewport", { viewport: { width: 0, height: 800 } }, "positive integer viewport width and height"],
  ["a missing title", { title: "" }, "must have a non-empty title"],
  ["a non-string capability", { capability: null }, "must carry a capability key"],
];

for (const [label, overrides, expected] of manifestFailures) {
  test(`a manifest entry with ${label} fails`, async () => {
    const { root, cleanup } = createFixtureTree();
    try {
      visualFixture(root);
      manifest(root, [screenEntry(overrides)]);
      assertError(await validateOpenSpec(root).then((result) => result.errors), expected);
    } finally {
      cleanup();
    }
  });
}

test("a malformed manifest fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    addFixtureFile(root, `${visuals}/manifest.json`, "{");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "manifest.json is not valid JSON",
    );
  } finally {
    cleanup();
  }
});

test("an index that does not link the canonical HTML fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    addFixtureFile(root, `${visuals}/index.html`, "<!-- html/fixture-workflow-fixture-overview.html -->\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      `docs/product/wireframes/index.html must link ${html}`,
    );
  } finally {
    cleanup();
  }
});

test("a spec linking a change proposal fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    addChangePackage(root);
    append(
      root,
      FIXTURE_SPEC,
      "\n[Proposal](../../changes/add-fixture-behaviour/proposal.md)\n",
    );
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "must not link change proposals",
    );
  } finally {
    cleanup();
  }
});

test("a spec link escaping the repository fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    append(root, FIXTURE_SPEC, "\n[Outside](../../../../../outside.md)\n");
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "links outside the repository",
    );
  } finally {
    cleanup();
  }
});

test("a symlink escaping the OpenSpec tree fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    const outside = path.join(root, "outside.md");
    writeFileSync(outside, "# Outside\n");
    symlinkSync(outside, path.join(root, "openspec", "specs", "fixture-workflow", "linked.md"));
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "is a symlink; OpenSpec records and visual artifacts must be real files",
    );
  } finally {
    cleanup();
  }
});

test("a symlink in the visual artifact inventory fails", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    rmSync(path.join(root, visuals, png));
    symlinkSync(path.join(root, "outside.png"), path.join(root, visuals, png));
    assertError(
      await validateOpenSpec(root).then((result) => result.errors),
      "is a symlink; OpenSpec records and visual artifacts must be real files",
    );
  } finally {
    cleanup();
  }
});
