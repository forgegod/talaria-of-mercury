import { test } from "node:test";
import assert from "node:assert/strict";
import { appendFileSync, readFileSync, renameSync, rmSync } from "node:fs";
import path from "node:path";
import { validateRecords } from "../../scripts/check-product-records.mjs";
import { addFixtureFile, createFixtureTree, patchFixture } from "./fixtures.mjs";

const cap = "docs/product/capabilities/CAP-001-fixture-capability.md";
const wireframes = "docs/product/wireframes";
const review = "docs/changes/reviews/CHG-001";
const change = "docs/changes/active/CHG-001-fixture-change.md";
const screen = {
  id: "CAP-001",
  title: "Fixture screen",
  html: "html/CAP-001-fixture-capability.html",
  png: "exports/CAP-001-fixture-capability.png",
  viewport: { width: 1280, height: 800 },
};

function append(root, file, text) {
  appendFileSync(path.join(root, file), text);
}

function manifest(root, screens = [screen]) {
  addFixtureFile(root, `${wireframes}/manifest.json`, JSON.stringify({ screens }));
}

function visualFixture(root) {
  patchFixture(root, cap, "**Primary surface:** human", "**Primary surface:** none");
  addFixtureFile(root, `${wireframes}/generate.mjs`, "// Synthetic generator fixture; never executed.\n");
  addFixtureFile(root, `${wireframes}/${screen.html}`, "<html>Synthetic screen fixture</html>");
  // The validator checks inventory, not PNG bytes or rendering fidelity.
  addFixtureFile(root, `${wireframes}/${screen.png}`, "synthetic PNG inventory placeholder");
  addFixtureFile(root, `${wireframes}/index.html`, `<a href="${screen.html}">Fixture</a>`);
  manifest(root);
  append(root, cap, `\n## Links\n\n[HTML](../wireframes/${screen.html})\n[PNG](../wireframes/${screen.png})\n`);
}

function reviewFixture(root) {
  addFixtureFile(root, change, `# CHG-001 — Fixture change

**Status:** planned
**External request:** Direct operator request: fixture
**Impacts:** CAP-001
**Baseline:** abc1234

| # | Phase | Status | Verification gate |
| --- | --- | --- | --- |
| 1 | Implement | pending | pytest exits 0 |

## Phase 1 — Implement

Synthetic fixture.

[Review package](../reviews/CHG-001/README.md)
`);
  addFixtureFile(root, `${review}/README.md`, "# Review proposal\n\n**Status:** review-only\n\n[HTML](screen.html) · [Design](design.md)\n");
  addFixtureFile(root, `${review}/design.md`, "# Design proposal\n\nReview-only, no implementation claim.\n");
  addFixtureFile(root, `${review}/screen.html`, "<html>Review-only synthetic proposal</html>");
}

for (const status of ["planned", "done", "cancelled"]) {
  test(`review package remains valid with a ${status} owner`, async () => {
    const { root, cleanup } = createFixtureTree();
    try {
      reviewFixture(root);
      if (status !== "planned") {
        patchFixture(root, change, `**Status:** ${status}`, "**Status:** planned");
        renameSync(path.join(root, change), path.join(root, change.replace("/active/", "/archive/")));
      }
      const result = await validateRecords(root);
      assert.deepEqual(result.errors, []);
      assert.equal(result.changes, 1, "review Markdown must not count as CHG records");
      assert.equal(result.manifest, false, "reviews must not activate product wireframes");
    } finally {
      cleanup();
    }
  });
}

test("canonical human-surface wireframes pass", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    const result = await validateRecords(root);
    assert.deepEqual(result.errors, []);
    assert.equal(result.manifest, true);
  } finally {
    cleanup();
  }
});

const reviewFailures = [
  ["orphan package", root => rmSync(path.join(root, change)), "has no owning CHG"],
  ["missing package README", root => rmSync(path.join(root, review, "README.md")), "requires README.md"],
  ["missing review-only marker", root => patchFixture(root, `${review}/README.md`, "", "**Status:** review-only"), "must declare Status: review-only"],
  ["review-only marker only in a code fence", root => patchFixture(root, `${review}/README.md`, "```md\n**Status:** review-only\n```", "**Status:** review-only"), "must declare Status: review-only"],
  ["unlinked package", root => patchFixture(root, change, "", "[Review package](../reviews/CHG-001/README.md)"), "must link its review package README.md"],
  ["package link only in code fence", root => patchFixture(root, change, "```md\n[Review package](../reviews/CHG-001/README.md)\n```", "[Review package](../reviews/CHG-001/README.md)"), "must link its review package README.md"],
  ["broken review Markdown link", root => append(root, `${review}/design.md`, "\n[Missing](missing.png)\n"), "has broken local link"],
  ["escaping review Markdown link", root => append(root, `${review}/design.md`, "\n[Outside](../../../../../outside.md)\n"), "links outside the repository"],
  ["loose review file", root => addFixtureFile(root, "docs/changes/reviews/screen.html", "fixture"), "must be a CHG-<number> review directory"],
  ["malformed owner ID", root => renameSync(path.join(root, review), path.join(root, "docs/changes/reviews/CHG-one")), "must be a CHG-<number> review directory"],
  ["CAP links proposal HTML", root => append(root, cap, "\n[Proposal](../../changes/reviews/CHG-001/screen.html)\n"), "must not link review artifacts"],
  ["CAP links proposal README", root => append(root, cap, "\n[Proposal](../../changes/reviews/CHG-001/README.md)\n"), "must not link review artifacts"],
];

for (const place of ["active", "archive"]) {
  for (const artifact of ["CHG-001-review.html", "CHG-001-review.png", "design.md", "CHG-001/design.md"]) {
    reviewFailures.push([
      `${place} contains ${artifact}`,
      root => addFixtureFile(root, `docs/changes/${place}/${artifact}`, "Synthetic review artifact"),
      "contains non-record entry",
    ]);
  }
}

for (const [name, mutate, expected] of reviewFailures) {
  test(`rejects ${name}`, async () => {
    const { root, cleanup } = createFixtureTree();
    try {
      reviewFixture(root);
      mutate(root);
      const { errors } = await validateRecords(root);
      assert.ok(errors.some(error => error.includes(expected)), errors.join("\n"));
    } finally {
      cleanup();
    }
  });
}

const visualFailures = [
  ["missing surface declaration", root => patchFixture(root, cap, "", "**Primary surface:** human"), "invalid primary surface"],
  ["surface declaration only in a code fence", root => patchFixture(root, cap, "```md\n**Primary surface:** human\n```", "**Primary surface:** human"), "invalid primary surface"],
  ["invalid surface declaration", root => patchFixture(root, cap, "**Primary surface:** yes", "**Primary surface:** human"), "invalid primary surface"],
  ["absent manifest", root => rmSync(path.join(root, wireframes, "manifest.json")), "manifest.json is required"],
  ["human CAP with no wireframe directory", root => rmSync(path.join(root, wireframes), { recursive: true }), "manifest.json is required"],
  ["wireframes without manifest even with no human CAP", root => {
    patchFixture(root, cap, "**Primary surface:** none", "**Primary surface:** human");
    rmSync(path.join(root, wireframes, "manifest.json"));
  }, "manifest.json is required"],
  ["empty manifest omits human CAP", root => manifest(root, []), "must list primary-surface capability CAP-001"],
  ["missing generator", root => rmSync(path.join(root, wireframes, "generate.mjs")), "generate.mjs is required"],
  ["missing index", root => rmSync(path.join(root, wireframes, "index.html")), "index.html is required"],
  ["missing HTML", root => rmSync(path.join(root, wireframes, screen.html)), "lists missing artifact"],
  ["missing PNG", root => rmSync(path.join(root, wireframes, screen.png)), "lists missing artifact"],
  ["unlisted HTML", root => addFixtureFile(root, `${wireframes}/html/CAP-001-extra.html`, "fixture"), "artifact is missing from the manifest"],
  ["unlisted PNG", root => addFixtureFile(root, `${wireframes}/exports/CAP-001-extra.png`, "fixture"), "artifact is missing from the manifest"],
  ["proposal in canonical HTML directory", root => addFixtureFile(root, `${wireframes}/html/CHG-001-review.html`, "fixture"), "invalid wireframe artifact"],
  ["links outside Links section", root => patchFixture(root, cap, "## Related visuals", "## Links"), "must link its wireframe HTML and PNG under ## Links"],
  ["misnamed Links heading", root => patchFixture(root, cap, "## Links elsewhere", "## Links"), "must link its wireframe HTML and PNG under ## Links"],
  ["links only in a code fence", root => {
    patchFixture(root, cap, "## Links\n\n```md", "## Links");
    append(root, cap, "```\n");
  }, "must link its wireframe HTML and PNG under ## Links"],
  ["missing index hyperlink", root => addFixtureFile(root, `${wireframes}/index.html`, `<!-- ${screen.html} -->`), "index.html must link"],
  ["duplicate screen ID", root => manifest(root, [screen, screen]), "duplicates capability CAP-001"],
  ["unknown screen ID", root => manifest(root, [{ ...screen, id: "CAP-999" }]), "unknown capability CAP-999"],
  ["non-human CAP in manifest", root => patchFixture(root, cap, "**Primary surface:** none", "**Primary surface:** human"), "must declare Primary surface: human"],
  ["mismatching artifact slugs", root => {
    addFixtureFile(root, `${wireframes}/exports/CAP-001-other.png`, "fixture");
    manifest(root, [{ ...screen, png: "exports/CAP-001-other.png" }]);
  }, "HTML and PNG names must match"],
  ["proposal paths in manifest", root => manifest(root, [{ ...screen, html: "../../changes/reviews/CHG-001/screen.html" }]), "manifest html path must be"],
  ["invalid viewport", root => manifest(root, [{ ...screen, viewport: { width: 0, height: 800 } }]), "positive integer viewport"],
  ["missing title", root => manifest(root, [{ ...screen, title: "" }]), "non-empty title"],
  ["null screen entry", root => manifest(root, [null]), "must carry a CAP-<number> id"],
  ["null manifest", root => addFixtureFile(root, `${wireframes}/manifest.json`, "null"), "must contain a `screens` array"],
  ["malformed JSON", root => addFixtureFile(root, `${wireframes}/manifest.json`, "{"), "not valid JSON"],
];

for (const [name, mutate, expected] of visualFailures) {
  test(`rejects ${name}`, async () => {
    const { root, cleanup } = createFixtureTree();
    try {
      visualFixture(root);
      mutate(root);
      const { errors } = await validateRecords(root);
      assert.ok(errors.some(error => error.includes(expected)), errors.join("\n"));
    } finally {
      cleanup();
    }
  });
}

test("review-only links cannot replace canonical visuals with an empty manifest", async () => {
  const { root, cleanup } = createFixtureTree();
  try {
    visualFixture(root);
    reviewFixture(root);
    manifest(root, []);
    rmSync(path.join(root, wireframes, "html"), { recursive: true });
    rmSync(path.join(root, wireframes, "exports"), { recursive: true });
    const source = readFileSync(path.join(root, cap), "utf8");
    patchFixture(root, cap, `${source.split("## Links")[0]}## Links\n\n[Review](../../changes/reviews/CHG-001/screen.html)\n`);
    const { errors } = await validateRecords(root);
    assert.ok(errors.some(error => error.includes("must not link review artifacts")));
    assert.ok(errors.some(error => error.includes("must list primary-surface capability CAP-001")));
  } finally {
    cleanup();
  }
});
