/**
 * Fixture-tree builders for the record-validator behaviour tests.
 *
 * Each builder writes a minimal record tree under a fresh `node:os` tmpdir and
 * returns `{ root, cleanup }`. The default tree is valid; the mutation helpers
 * make one targeted break so a test can assert the matching error.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** A minimal valid record tree plus the files a CAP references. */
export function buildValidTree(root) {
  fs.mkdirSync(path.join(root, "src"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "src", "engine.mjs"),
    "export const engine = 'fixture';\n",
  );
  fs.writeFileSync(
    path.join(root, "src", "engine.test.mjs"),
    "export const test = true;\n",
  );

  const product = path.join(root, "docs", "product");
  const changes = path.join(root, "docs", "changes");
  fs.mkdirSync(path.join(product, "capabilities"), { recursive: true });
  fs.mkdirSync(path.join(path.join(changes, "active")), { recursive: true });
  fs.mkdirSync(path.join(path.join(changes, "archive")), { recursive: true });

  write(
    root,
    "docs/product/README.md",
    `# Product capability records\n\n## CAP shape\n\nSee the template.\n`,
  );
  write(
    root,
    "docs/product/index.md",
    `# Capability index\n\n- [CAP-001 — Fixture capability](capabilities/CAP-001-fixture-capability.md)\n`,
  );
  write(
    root,
    "docs/product/capabilities/CAP-001-fixture-capability.md",
    `# CAP-001 — Fixture capability\n\n**Status:** implemented\n**Primary surface:** none\n\n## Behaviour\n\n- The fixture engine runs.\n\n## Implementation\n\n- \`src/engine.mjs\` — fixture engine.\n\n## Verification\n\n- \`src/engine.test.mjs\` — proves the fixture engine runs.\n\n## Related contracts\n\n- [Architecture](../../architecture.md)\n`,
  );
  write(
    root,
    "docs/changes/README.md",
    `# Change records\n\nLifecycle rules live in this file.\n`,
  );
  write(
    root,
    "docs/architecture.md",
    `# Architecture\n\nFixture tree contract.\n`,
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
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "talaria-records-"));
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
