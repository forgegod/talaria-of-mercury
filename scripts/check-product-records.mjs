#!/usr/bin/env node
/**
 * Product capability/change-record checks.
 *
 * This validates structural, lifecycle, and reference invariants. It cannot
 * infer whether an arbitrary source change altered observable behaviour; that
 * remains a review and agent responsibility.
 *
 * Usage:
 *   node scripts/check-product-records.mjs [rootDir]
 *
 * `rootDir` (optional) is the repository root to validate; it defaults to the
 * parent directory of this script. The argument exists so the behaviour can be
 * exercised against fixture trees from the `node:test` suites in `tests/records/`.
 *
 * The validator is importable: `validateRecords(root)` returns
 * `{ errors, capabilities, changes, manifest }` without touching the process
 * exit code, so tests assert on the returned error list.
 */
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

const capabilityStatuses = new Set(["implemented", "partial", "retired"]);
const activeChangeStatuses = new Set(["planned", "in-progress", "blocked"]);
const archivedChangeStatuses = new Set(["done", "cancelled"]);
const changeTableHeader =
  /^\|\s*#\s*\|\s*Phase\s*\|\s*Status\s*\|\s*Verification gate\s*\|/m;

const javascriptBehaviourTest =
  /(?:^|\/)[^/]+\.(test|spec)\.[cm]?[jt]sx?$/;
const pythonBehaviourTest = /(?:^|\/)(?:test_[^/]+|[^/]+_test)\.py$/;

/**
 * Python evidence detection is structural, not execution proof: a pytest
 * module must define a test function or `Test*` class and contain at least one
 * assertion statement (`assert`, `self.assert*`, `self.fail`, or a raising
 * `pytest` helper). Helper modules, fixtures, and `conftest.py` never qualify.
 */
const pythonTestDefinition =
  /^[ \t]*(?:async[ \t]+)?def[ \t]+test[A-Za-z0-9_]*[ \t]*\(|^[ \t]*class[ \t]+Test[A-Za-z0-9_]*[ \t]*[(:]/m;
const pythonAssertion =
  /^[ \t]*(?:with[ \t]+)?(?:assert\b|self\.assert[A-Za-z_]*[ \t]*\(|self\.fail[ \t]*\(|pytest\.(?:raises|warns|fail|approx)\b)/m;

/**
 * A candidate is treated as a repository reference when it is relative,
 * contains no whitespace or path traversal, and is either a file reference
 * (its base name carries an extension) or an explicit directory reference
 * (trailing slash). This keeps the "points at a real path" invariant
 * checkable without misfiring on backticked prose.
 *
 * @param {string} candidate
 */
function isRepositoryReference(candidate) {
  if (
    !/^[A-Za-z0-9@._/-]+$/.test(candidate) ||
    path.isAbsolute(candidate) ||
    candidate.includes("..")
  ) {
    return false;
  }
  const base = candidate.split("/").at(-1);
  return base.includes(".") || candidate.endsWith("/");
}

/** @param {string} file */
async function hasPythonTestDefinitionAndAssertion(file) {
  const source = await readFile(file, "utf8");
  return pythonTestDefinition.test(source) && pythonAssertion.test(source);
}

/**
 * Backticked candidates in a section that look like repository files.
 *
 * @param {string} source
 * @param {string} heading
 * @param {(candidate: string) => boolean} filter
 * @returns {string[]}
 */
function sectionFileRefs(source, heading, filter) {
  const body = section(source, heading);
  if (!body) return [];
  return [...body.matchAll(/`([^`]+)`/g)]
    .map((match) => match[1].trim())
    .filter(filter);
}

/** @param {string} directory */
async function directoryEntries(directory) {
  return readdir(directory, { withFileTypes: true }).catch(
    (error) => {
      if (error && typeof error === "object" && error.code === "ENOENT") {
        return [];
      }
      throw error;
    },
  );
}

/** @param {string} directory */
async function markdownFiles(directory) {
  const entries = await directoryEntries(directory);
  const files = [];
  for (const entry of entries) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await markdownFiles(file)));
    } else if (entry.isFile() && entry.name.endsWith(".md")) {
      files.push(file);
    }
  }
  return files.sort();
}

/** Active/archive folders are flat record inventories, never asset stores. */
async function changeRecordFiles(directory, root, errors) {
  const files = [];
  for (const entry of await directoryEntries(directory)) {
    if (entry.isFile() && /^CHG-\d+-[a-z0-9-]+\.md$/.test(entry.name)) {
      files.push(path.join(directory, entry.name));
    } else {
      errors.push(`${path.relative(root, directory)} contains non-record entry: ${entry.name}; use docs/changes/reviews/CHG-<number>/ for review artifacts`);
    }
  }
  return files.sort();
}

/** @param {string} source @param {string} label */
function requiredField(source, label) {
  const match = withoutCodeFences(source).match(
    new RegExp(`^\\*\\*${label}:\\*\\*\\s*(.+?)\\s*$`, "m"),
  );
  return match?.[1]?.trim() ?? null;
}

/** @param {string} source @param {string} heading */
function section(source, heading) {
  source = withoutCodeFences(source);
  const match = source.match(new RegExp(`^## ${heading}[ \\t]*\\r?$`, "m"));
  if (!match) return null;
  const start = match.index;

  const afterHeading = source.indexOf("\n", start);
  if (afterHeading < 0) return "";
  const remainder = source.slice(afterHeading + 1);
  const nextHeading = remainder.search(/^## /m);
  return nextHeading < 0 ? remainder : remainder.slice(0, nextHeading);
}

function withoutCodeFences(source) {
  return source.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~/g, "");
}

function localMarkdownTargets(source) {
  return [...withoutCodeFences(source).matchAll(/\]\(([^)]+)\)/g)]
    .map(match => match[1].trim().replace(/^<|>$/g, ""))
    .filter(target => target && !target.startsWith("#") && !/^(?:https?:|mailto:)/.test(target))
    .map(target => target.split("#", 1)[0]);
}

function localMarkdownPaths(source, file) {
  return localMarkdownTargets(source).map(target => path.resolve(path.dirname(file), target));
}

function isWithin(directory, file) {
  const relative = path.relative(directory, file);
  return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

/** @param {string} file @param {string} root @param {string[]} errors */
async function validateLocalMarkdownLinks(file, root, errors) {
  const source = await readFile(file, "utf8");
  for (const target of localMarkdownTargets(source)) {
    const resolved = path.resolve(path.dirname(file), target);
    if (!isWithin(root, resolved)) {
      errors.push(
        `${path.relative(root, file)} links outside the repository: ${target}`,
      );
      continue;
    }

    const targetInfo = await stat(resolved).catch(() => null);
    if (!targetInfo) {
      errors.push(`${path.relative(root, file)} has broken local link: ${target}`);
    }
  }
}

/**
 * Validate the record tree under `root`.
 *
 * @param {string} root
 * @returns {Promise<{errors: string[], capabilities: number, changes: number, manifest: boolean}>}
 */
export async function validateRecords(root) {
  const errors = [];
  const productRoot = path.join(root, "docs", "product");
  const capabilitiesRoot = path.join(productRoot, "capabilities");
  const changesRoot = path.join(root, "docs", "changes");
  const activeChangesRoot = path.join(changesRoot, "active");
  const archiveChangesRoot = path.join(changesRoot, "archive");
  const reviewsRoot = path.join(changesRoot, "reviews");
  const wireframesRoot = path.join(productRoot, "wireframes");

  const capabilityFiles = await markdownFiles(capabilitiesRoot);
  const capabilityIds = new Set();
  const capabilities = new Map();

  for (const file of capabilityFiles) {
    const relative = path.relative(root, file);
    const source = await readFile(file, "utf8");
    const filename = path.basename(file);
    const filenameMatch = filename.match(/^(CAP-\d+)-[a-z0-9-]+\.md$/);
    const titleMatch = source.match(/^# (CAP-\d+) — .+$/m);
    const status = requiredField(source, "Status");
    const surface = requiredField(source, "Primary surface");
    if (!["human", "none"].includes(surface)) {
      errors.push(`${relative} has invalid primary surface '${surface ?? "missing"}'; use human or none`);
    }
    if (titleMatch) capabilities.set(titleMatch[1], { file, source, surface });
    if (localMarkdownPaths(source, file).some(target => isWithin(reviewsRoot, target))) {
      errors.push(`${relative} must not link review artifacts; link current CAP wireframes instead`);
    }

    if (!filenameMatch) {
      errors.push(`${relative} must use CAP-<number>-<slug>.md`);
    }
    if (!titleMatch) {
      errors.push(`${relative} must begin with '# CAP-<number> — <name>'`);
    }
    if (filenameMatch && titleMatch && filenameMatch[1] !== titleMatch[1]) {
      errors.push(`${relative} filename and title capability IDs differ`);
    }
    if (titleMatch && capabilityIds.has(titleMatch[1])) {
      errors.push(`${relative} duplicates capability ID ${titleMatch[1]}`);
    }
    if (titleMatch) capabilityIds.add(titleMatch[1]);
    if (!status || !capabilityStatuses.has(status)) {
      errors.push(
        `${relative} has invalid capability status '${status ?? "missing"}'`,
      );
    }
    if (!section(source, "Behaviour")) {
      errors.push(`${relative} must include a Behaviour section`);
    }
    if (!section(source, "Implementation")) {
      errors.push(`${relative} must include an Implementation section`);
    }
    if (!section(source, "Verification")) {
      errors.push(`${relative} must include a Verification section`);
    }

    const implementation = sectionFileRefs(
      source,
      "Implementation",
      isRepositoryReference,
    );
    if (implementation.length === 0) {
      errors.push(
        `${relative} must reference at least one existing implementation file in Implementation`,
      );
    }
    for (const implementationPath of implementation) {
      const info = await stat(
        path.join(root, implementationPath),
      ).catch(() => null);
      if (!info) {
        errors.push(
          `${relative} references missing implementation file: ${implementationPath}`,
        );
      }
    }

    const tests = sectionFileRefs(
      source,
      "Verification",
      (candidate) =>
        isRepositoryReference(candidate) &&
        (javascriptBehaviourTest.test(candidate) ||
          pythonBehaviourTest.test(candidate)),
    );
    const testEvidence = await Promise.all(
      tests.map(async (testPath) => {
        const info = await stat(path.join(root, testPath)).catch(() => null);
        if (!info?.isFile()) {
          return `references missing behaviour test: ${testPath}`;
        }
        if (
          pythonBehaviourTest.test(testPath) &&
          !(await hasPythonTestDefinitionAndAssertion(
            path.join(root, testPath),
          ))
        ) {
          return `references a Python file without a test definition and assertion: ${testPath}`;
        }
        return null;
      }),
    );
    if (testEvidence.every((problem) => problem !== null)) {
      errors.push(
        `${relative} must reference at least one executable behaviour test in Verification`,
      );
    }
    for (const problem of testEvidence) {
      if (problem) errors.push(`${relative} ${problem}`);
    }

    await validateLocalMarkdownLinks(file, root, errors);
  }

  const changeFiles = [
    ...(await changeRecordFiles(activeChangesRoot, root, errors)).map((file) => ({
      file,
      place: "active",
    })),
    ...(await changeRecordFiles(archiveChangesRoot, root, errors)).map((file) => ({
      file,
      place: "archive",
    })),
  ];
  const changeIds = new Set();
  const changes = new Map();

  for (const { file, place } of changeFiles) {
    const relative = path.relative(root, file);
    const source = await readFile(file, "utf8");
    const filename = path.basename(file);
    const filenameMatch = filename.match(/^(CHG-\d+)-[a-z0-9-]+\.md$/);
    const titleMatch = source.match(/^# (CHG-\d+) — .+$/m);
    const status = requiredField(source, "Status");
    const externalRequest = requiredField(source, "External request");
    const impacts = requiredField(source, "Impacts");
    const baseline = requiredField(source, "Baseline");

    if (!filenameMatch) {
      errors.push(`${relative} must use CHG-<number>-<slug>.md`);
    }
    if (!titleMatch) {
      errors.push(`${relative} must begin with '# CHG-<number> — <name>'`);
    }
    if (filenameMatch && titleMatch && filenameMatch[1] !== titleMatch[1]) {
      errors.push(`${relative} filename and title change IDs differ`);
    }
    if (titleMatch && changeIds.has(titleMatch[1])) {
      errors.push(`${relative} duplicates change ID ${titleMatch[1]}`);
    }
    if (titleMatch) changeIds.add(titleMatch[1]);
    if (titleMatch) changes.set(titleMatch[1], { file, source });

    const validStatuses =
      place === "active" ? activeChangeStatuses : archivedChangeStatuses;
    if (!status || !validStatuses.has(status)) {
      errors.push(
        `${relative} status '${status ?? "missing"}' is invalid for ${place}/`,
      );
    }
    if (!externalRequest)
      errors.push(`${relative} must name an External request`);
    if (!impacts) errors.push(`${relative} must name impacted CAP IDs`);
    if (!baseline) errors.push(`${relative} must name a Baseline`);
    if (!changeTableHeader.test(source)) {
      errors.push(`${relative} must include the canonical phase table header`);
    }
    if (!/^## Phase \d+ — /m.test(source)) {
      errors.push(
        `${relative} must include at least one numbered Phase section`,
      );
    }
    const inProgressPhases = [
      ...source.matchAll(/^\|\s*\d+\s*\|.*\|\s*in-progress\s*\|/gm),
    ];
    if (status === "in-progress" && inProgressPhases.length !== 1) {
      errors.push(`${relative} must have exactly one in-progress phase`);
    }
    if (status === "planned" && inProgressPhases.length !== 0) {
      errors.push(`${relative} cannot have an in-progress phase while planned`);
    }
    if (inProgressPhases.length > 1) {
      errors.push(`${relative} has more than one in-progress phase`);
    }

    const impactedCapabilities = impacts?.match(/CAP-\d+/g) ?? [];
    if (impactedCapabilities.length === 0) {
      errors.push(`${relative} must reference at least one CAP ID in Impacts`);
    }
    for (const capabilityId of impactedCapabilities) {
      if (!capabilityIds.has(capabilityId)) {
        errors.push(`${relative} references unknown capability ${capabilityId}`);
      }
    }

    await validateLocalMarkdownLinks(file, root, errors);
  }

  for (const entry of await directoryEntries(reviewsRoot)) {
    const directory = path.join(reviewsRoot, entry.name);
    const relative = path.relative(root, directory);
    if (!entry.isDirectory() || !/^CHG-\d+$/.test(entry.name)) {
      errors.push(`${relative} must be a CHG-<number> review directory`);
      continue;
    }
    const owner = changes.get(entry.name);
    if (!owner) errors.push(`${relative} has no owning CHG`);
    const readme = path.join(directory, "README.md");
    if (!(await stat(readme).catch(() => null))?.isFile()) {
      errors.push(`${relative} requires README.md`);
    } else {
      const source = await readFile(readme, "utf8");
      if (requiredField(source, "Status") !== "review-only") {
        errors.push(`${relative}/README.md must declare Status: review-only`);
      }
    }
    if (owner && !localMarkdownPaths(owner.source, owner.file).includes(readme)) {
      errors.push(`${path.relative(root, owner.file)} must link its review package README.md`);
    }
    for (const file of await markdownFiles(directory)) {
      await validateLocalMarkdownLinks(file, root, errors);
    }
  }

  for (const file of [
    path.join(productRoot, "README.md"),
    path.join(productRoot, "index.md"),
    path.join(changesRoot, "README.md"),
  ]) {
    const info = await stat(file).catch(() => null);
    if (!info?.isFile()) {
      errors.push(`${path.relative(root, file)} is required`);
    } else {
      await validateLocalMarkdownLinks(file, root, errors);
    }
  }

  const productIndex = path.join(productRoot, "index.md");
  const indexSource = await readFile(productIndex, "utf8").catch(() => "");
  for (const capabilityId of capabilityIds) {
    if (!indexSource.includes(capabilityId)) {
      errors.push(`docs/product/index.md must link ${capabilityId}`);
    }
  }

  // Explicit surface declarations activate the workflow even without a manifest.
  let manifest = false;
  const manifestPath = path.join(wireframesRoot, "manifest.json");
  const manifestInfo = await stat(manifestPath).catch(() => null);
  const humanCapabilities = [...capabilities].filter(([, cap]) => cap.surface === "human");
  const wireframesInfo = await stat(wireframesRoot).catch(() => null);
  if (humanCapabilities.length > 0 || wireframesInfo) {
    for (const name of ["manifest.json", "generate.mjs", "index.html"]) {
      if (!(await stat(path.join(wireframesRoot, name)).catch(() => null))?.isFile()) {
        errors.push(`docs/product/wireframes/${name} is required for the wireframe workflow`);
      }
    }
  }
  if (manifestInfo?.isFile()) {
    manifest = true;
    let parsed;
    try {
      parsed = JSON.parse(await readFile(manifestPath, "utf8"));
    } catch {
      errors.push("docs/product/wireframes/manifest.json is not valid JSON");
    }
    if (parsed && Array.isArray(parsed.screens)) {
      const listedIds = new Set();
      const listedArtifacts = new Set();
      const indexSource = await readFile(path.join(wireframesRoot, "index.html"), "utf8").catch(() => "");
      const indexLinks = [...indexSource.replace(/<!--[\s\S]*?-->/g, "").matchAll(/<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']/gi)]
        .map(match => match[1]);
      for (const screen of parsed.screens) {
        const id = typeof screen?.id === "string" ? screen.id : "";
        if (!/^CAP-\d+$/.test(id)) {
          errors.push(
            `wireframe manifest screen must carry a CAP-<number> id: ${JSON.stringify(screen)}`,
          );
          continue;
        }
        if (listedIds.has(id)) errors.push(`wireframe manifest duplicates capability ${id}`);
        listedIds.add(id);
        if (!capabilityIds.has(id)) {
          errors.push(`wireframe manifest lists unknown capability ${id}`);
        } else if (capabilities.get(id).surface !== "human") {
          errors.push(`wireframe capability ${id} must declare Primary surface: human`);
        }
        if (typeof screen.title !== "string" || !screen.title.trim()) {
          errors.push(`wireframe manifest ${id} must have a non-empty title`);
        }
        if (![screen.viewport?.width, screen.viewport?.height].every(value => Number.isInteger(value) && value > 0)) {
          errors.push(`wireframe manifest ${id} must have a positive integer viewport width and height`);
        }
        const htmlPattern = new RegExp(`^html/${id}-[a-z0-9-]+\\.html$`);
        const pngPattern = new RegExp(`^exports/${id}-[a-z0-9-]+\\.png$`);
        for (const [artifact, pattern, label] of [
          [screen.html, htmlPattern, "html"],
          [screen.png, pngPattern, "png"],
        ]) {
          if (typeof artifact !== "string" || !pattern.test(artifact)) {
            errors.push(
              `wireframe manifest ${label} path must be ${label === "html" ? "html" : "exports"}/${id}-<slug>${label === "html" ? ".html" : ".png"}: ${artifact}`,
            );
            continue;
          }
          listedArtifacts.add(artifact);
          const info = await stat(
            path.join(wireframesRoot, artifact),
          ).catch(() => null);
          if (!info?.isFile()) {
            errors.push(`wireframe manifest lists missing artifact: ${artifact}`);
          }
        }
        if (typeof screen.html === "string" && typeof screen.png === "string" &&
            path.basename(screen.html, ".html") !== path.basename(screen.png, ".png")) {
          errors.push(`wireframe manifest ${id} HTML and PNG names must match`);
        }
        const cap = capabilities.get(id);
        if (cap) {
          const capLinks = localMarkdownPaths(section(cap.source, "Links") ?? "", cap.file)
            .map(target => path.relative(wireframesRoot, target).split(path.sep).join("/"));
          const missing = [screen.html, screen.png].filter(
            (artifact) => !capLinks.includes(artifact),
          );
          if (missing.length > 0) {
            errors.push(
              `${path.relative(root, cap.file)} must link its wireframe HTML and PNG under ## Links (${missing.join(", ")})`,
            );
          }
        }
        if (!indexLinks.includes(screen.html)) {
          errors.push(`wireframe index.html must link ${screen.html}`);
        }
      }
      for (const [id] of humanCapabilities) {
        if (!listedIds.has(id)) {
          errors.push(`wireframe manifest must list primary-surface capability ${id}`);
        }
      }
      for (const [directory, extension] of [["html", "html"], ["exports", "png"]]) {
        for (const entry of await directoryEntries(path.join(wireframesRoot, directory))) {
          const artifact = `${directory}/${entry.name}`;
          if (!entry.isFile() || !new RegExp(`^CAP-\\d+-[a-z0-9-]+\\.${extension}$`).test(entry.name)) {
            errors.push(`invalid wireframe artifact: ${artifact}`);
          } else if (!listedArtifacts.has(artifact)) {
            errors.push(`wireframe ${extension} artifact is missing from the manifest: ${artifact}`);
          }
        }
      }
    } else if (parsed !== undefined) {
      errors.push("wireframe manifest must contain a `screens` array");
    }
  }

  return {
    errors,
    capabilities: capabilityFiles.length,
    changes: changeFiles.length,
    manifest,
  };
}

/** @returns {Promise<number>} process exit code */
async function main() {
  const arg = process.argv[2];
  const root = arg ? path.resolve(arg) : scriptRoot;
  const { errors, capabilities, changes, manifest } = await validateRecords(root);

  if (errors.length > 0) {
    console.error(`Product record check failed (${errors.length}):`);
    for (const error of errors) console.error(`- ${error}`);
    return 1;
  }

  console.log(
    `Product record check passed: ${capabilities} capabilities, ${changes} change records${
      manifest ? ", wireframe manifest in sync" : ""
    }.`,
  );
  return 0;
}

const isDirectRun =
  process.argv[1] &&
  import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;

if (isDirectRun) {
  process.exit(await main());
}
