#!/usr/bin/env node
/**
 * OpenSpec structural checks.
 *
 * Validates the repository's OpenSpec tree without reimplementing the upstream
 * parser: `openspec/config.yaml` presence, current capability specs, change
 * packages with native deltas, a single task-progress writer, safe links,
 * executable verification evidence, resolvable implementation references, and
 * canonical visuals for human-facing capabilities. Verification evidence must
 * name a behaviour test that defines a test and an assertion (JavaScript or
 * pytest); `conftest.py`, helper modules, fixtures, and assertion-free files
 * are not evidence. Symlinked source trees are refused before any artifact is
 * read. It cannot infer whether a source change altered observable behaviour;
 * that stays a review and agent responsibility.
 *
 * Usage:
 *   node check-openspec.mjs [rootDir]
 *
 * `rootDir` (optional) defaults to the parent directory of this script. The
 * argument exists so tests can exercise fixture trees.
 *
 * Importable API: `validateOpenSpec(root)` returns
 * `{ errors, specs, changes, visuals }` without touching the process exit code.
 */
import { lstat, readdir, readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

const capabilityKey = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const specSections = ["Purpose", "Requirements", "Verification"];
const deltaSections = [
  "ADDED Requirements",
  "MODIFIED Requirements",
  "REMOVED Requirements",
  "RENAMED Requirements",
];
const testFilePattern = /(?:^|\/)[^/]+\.(?:test|spec)\.[cm]?[jt]sx?$/;
const pythonTestFilePattern = /(?:^|\/)(?:test_[^/]+|[^/]+_test)\.py$/;
/**
 * Executable evidence is structural, never execution proof: a pytest module
 * must define a test function or `Test*` class and carry an assertion
 * (`assert`, `self.assert*`, `self.fail`, or a raising `pytest` helper); a
 * JavaScript module must register a `test()`/`it()` case and assert. Helper
 * modules, fixtures, and `conftest.py` never qualify.
 */
const pythonTestDefinition =
  /^[ \t]*(?:async[ \t]+)?def[ \t]+test[A-Za-z0-9_]*[ \t]*\(|^[ \t]*class[ \t]+Test[A-Za-z0-9_]*[ \t]*[(:]/m;
const pythonAssertion =
  /^[ \t]*(?:with[ \t]+)?(?:assert\b|self\.assert[A-Za-z_]*[ \t]*\(|self\.fail[ \t]*\(|pytest\.(?:raises|warns|fail|approx)\b)/m;
const javascriptTestDefinition = /^[ \t]*(?:test|it)\s*\(/m;
const javascriptAssertion =
  /^[ \t]*(?:assert(?:\.\w+)?\s*\(|(?:t|ctx|context)\.assert\.\w+\s*\(|expect\s*\()/m;
const requirementSections = new Set(["Requirements", ...deltaSections]);
const checkboxItem = /^\s*[-*]\s+\[[ xX]\]\s+/;
const listItem = /^\s*[-*]\s+/;

/** @param {string} source */
function withoutCodeFences(source) {
  return source.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~/g, "");
}

/** @param {string} source @param {string} label */
function requiredField(source, label) {
  const match = withoutCodeFences(source).match(
    new RegExp(`^\\*\\*${label}:\\*\\*\\s*(.+?)\\s*$`, "m"),
  );
  return match?.[1]?.trim() ?? null;
}

/** Body of `## heading`, fenced code removed. */
function section(source, heading) {
  const cleaned = withoutCodeFences(source);
  const match = cleaned.match(new RegExp(`^## ${heading}[ \\t]*\\r?$`, "m"));
  if (!match) return null;
  const afterHeading = cleaned.indexOf("\n", match.index);
  if (afterHeading < 0) return "";
  const remainder = cleaned.slice(afterHeading + 1);
  const nextHeading = remainder.search(/^## /m);
  return nextHeading < 0 ? remainder : remainder.slice(0, nextHeading);
}

/** A backticked candidate that plausibly names a repository path. */
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

/** @returns {string[]} backticked repository file refs inside a section */
function sectionFileRefs(source, heading, filter) {
  const body = section(source, heading);
  if (!body) return [];
  return [...body.matchAll(/`([^`]+)`/g)]
    .map((match) => match[1].trim())
    .filter(filter);
}

/**
 * CommonMark inline-link destinations with optional titles.
 *
 * Accepts `<angle destinations with spaces>` and bare destinations followed by
 * a single-, double-, or parenthesized title. Modest extraction only: evidence
 * paths need no full Markdown parser.
 */
const markdownLink =
  /\]\(\s*(?:<([^<>\n]*)>|([^\s()]+(?:\([^\s()]*\)[^\s()]*)*))(?:\s+(?:"[^"]*"|'[^']*'|\([^()]*\)))?\s*\)/g;

function localMarkdownTargets(source) {
  return [...withoutCodeFences(source).matchAll(markdownLink)]
    .map((match) => (match[1] ?? match[2] ?? "").trim())
    .filter((target) => target && !target.startsWith("#") && !/^(?:https?:|mailto:)/.test(target))
    .map((target) => target.split("#", 1)[0]);
}

function localMarkdownPaths(source, file) {
  return localMarkdownTargets(source).map((target) => path.resolve(path.dirname(file), target));
}

function isWithin(directory, file) {
  const relative = path.relative(directory, file);
  return relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

/** @param {string} directory */
async function directoryEntries(directory) {
  return readdir(directory, { withFileTypes: true }).catch((error) => {
    if (error && typeof error === "object" && error.code === "ENOENT") return [];
    throw error;
  });
}

/** @param {string} file */
async function isFile(file) {
  return (await stat(file).catch(() => null))?.isFile() === true;
}

/** Local Markdown links must stay inside the tree and resolve to real files. */
async function validateLocalMarkdownLinks(file, root, errors) {
  const source = await readFile(file, "utf8");
  const relative = path.relative(root, file);
  for (const target of localMarkdownTargets(source)) {
    const resolved = path.resolve(path.dirname(file), target);
    if (!isWithin(root, resolved)) {
      errors.push(`${relative} links outside the repository: ${target}`);
      continue;
    }
    const info = await stat(resolved).catch(() => null);
    if (!info) {
      errors.push(`${relative} has broken local link: ${target}`);
      continue;
    }
    // Refuse a symlink that resolves outside the repository.
    const realRoot = await realpath(root);
    const realTarget = await realpath(resolved).catch(() => null);
    if (!realTarget || !isWithin(realRoot, realTarget)) {
      errors.push(`${relative} links outside the repository: ${target}`);
    }
  }
}

/**
 * Resolve a repository-relative evidence path with link-style containment.
 *
 * @param {string} root
 * @param {string} relative
 * @returns {Promise<"ok"|"missing"|"outside">}
 */
async function repositoryFileStatus(root, relative) {
  const resolved = path.resolve(root, relative);
  if (!isWithin(root, resolved)) return "outside";
  const info = await stat(resolved).catch(() => null);
  if (!info?.isFile()) return "missing";
  // Resolve symlinks, including any symlinked ancestor, before trusting it.
  const realRoot = await realpath(root);
  const realFile = await realpath(resolved).catch(() => null);
  if (!realFile || !isWithin(realRoot, realFile)) return "outside";
  return "ok";
}

/**
 * Whether a resolved evidence file is an executable behaviour test: a test
 * definition plus at least one assertion. Structural only; it does not prove
 * the test runs or fails correctly.
 *
 * @param {string} file
 * @param {string} relative repository-relative path (selects the language)
 * @returns {Promise<boolean>}
 */
async function hasExecutableEvidence(file, relative) {
  const source = await readFile(file, "utf8");
  if (pythonTestFilePattern.test(relative)) {
    return pythonTestDefinition.test(source) && pythonAssertion.test(source);
  }
  if (testFilePattern.test(relative)) {
    return javascriptTestDefinition.test(source) && javascriptAssertion.test(source);
  }
  return false;
}

/**
 * Strict heading scan shared by capability specs and change deltas.
 *
 * @param {string} source
 * @param {string} relative
 * @param {string[]} errors
 * @param {{allowed: string[], label: string}} options
 * @returns {{requirements: Map<string, {count: number, section: string}>, sections: string[]}}
 */
function scanHeadings(source, relative, errors, { allowed, label }) {
  const requirements = new Map();
  const sections = [];
  let fenced = false;
  let currentSection = null;
  let currentRequirement = null;

  for (const line of source.split(/\r?\n/)) {
    if (/^\s*(?:```|~~~)/.test(line)) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;

    const heading = line.match(/^(#{1,6})[ \t]+(.*?)[ \t]*$/);
    if (!heading) continue;
    const level = heading[1].length;
    const text = heading[2];

    if (level === 1) {
      currentSection = null;
      currentRequirement = null;
      continue;
    }
    if (level === 2) {
      if (!allowed.includes(text)) {
        errors.push(`${relative} has an unsupported ${label} '## ${text}'`);
      }
      currentSection = text;
      currentRequirement = null;
      sections.push(text);
      continue;
    }
    if (level === 3) {
      const match = text.match(/^Requirement:[ \t]*(.*)$/);
      if (!match) {
        errors.push(`${relative} has an invalid heading '${line.trim()}'`);
        currentRequirement = null;
        continue;
      }
      const statement = match[1].trim();
      if (!statement) {
        errors.push(`${relative} has an empty requirement statement`);
        currentRequirement = null;
        continue;
      }
      if (!requirementSections.has(currentSection)) {
        errors.push(
          `${relative} requirement '${statement}' is not inside a requirement section`,
        );
      }
      currentRequirement = statement;
      if (!requirements.has(statement)) {
        requirements.set(statement, { count: 0, section: currentSection });
      }
      continue;
    }
    if (level === 4) {
      const match = text.match(/^Scenario:[ \t]*(.*)$/);
      if (!match) {
        errors.push(`${relative} has an invalid heading '${line.trim()}'`);
        continue;
      }
      const name = match[1].trim();
      if (!name) {
        errors.push(`${relative} has an empty scenario statement`);
        continue;
      }
      if (!currentRequirement) {
        errors.push(`${relative} scenario '${name}' is not inside a ### Requirement`);
        continue;
      }
      requirements.get(currentRequirement).count += 1;
      continue;
    }
    // Level 5/6 headings are ordinary prose structure, not OpenSpec records.
  }

  return { requirements, sections };
}

/**
 * Parse and validate one capability spec.
 *
 * @param {string} file
 * @param {string} root
 * @param {string[]} errors
 * @returns {Promise<{key: string, surface: string|null, source: string}|null>}
 */
async function validateSpec(file, root, errors) {
  const relative = path.relative(root, file);
  const source = await readFile(file, "utf8");
  const capability = path.basename(path.dirname(file));
  const surface = requiredField(source, "Primary surface");

  if (!["human", "none"].includes(surface)) {
    errors.push(
      `${relative} has invalid primary surface '${surface ?? "missing"}'; use human or none`,
    );
  }

  const { requirements, sections } = scanHeadings(source, relative, errors, {
    allowed: [...specSections, "Visuals"],
    label: "section",
  });

  for (const name of specSections) {
    if (!section(source, name)) {
      errors.push(`${relative} must include a ## ${name} section`);
    }
  }
  // Order from the fence-stripped heading scan: fenced samples are not sections.
  const orderedSections = sections.filter((name) => specSections.includes(name));
  const expectedSections = specSections.filter((name) => orderedSections.includes(name));
  if (orderedSections.join("\u0000") !== expectedSections.join("\u0000")) {
    errors.push(
      `${relative} must order its sections as ${specSections.map((name) => `## ${name}`).join(", ")}`,
    );
  }
  if (requirements.size === 0) {
    errors.push(`${relative} must declare at least one ### Requirement`);
  }
  for (const [name, { count }] of requirements) {
    if (count === 0) {
      errors.push(
        `${relative} requirement '${name}' must include at least one #### Scenario`,
      );
    }
  }

  const verificationRefs = sectionFileRefs(source, "Verification", isRepositoryReference);
  const isBehaviourTest = (candidate) =>
    testFilePattern.test(candidate) || pythonTestFilePattern.test(candidate);
  const tests = verificationRefs.filter(isBehaviourTest);
  const linkedTests = localMarkdownPaths(section(source, "Verification") ?? "", file)
    .filter((target) => isWithin(root, target) && isBehaviourTest(target));
  tests.push(...linkedTests.map((target) => path.relative(root, target)));

  // Evidence must be an executable test, not just a resolvable path. A spec
  // with only missing or non-executable references has no evidence at all.
  let executable = 0;
  for (const testPath of tests) {
    const status = await repositoryFileStatus(root, testPath);
    if (status === "outside") {
      errors.push(
        `${relative} references a behaviour test outside the repository: ${testPath}`,
      );
      continue;
    }
    if (status === "missing") {
      errors.push(`${relative} references missing behaviour test: ${testPath}`);
      continue;
    }
    if (await hasExecutableEvidence(path.join(root, testPath), testPath)) {
      executable += 1;
      continue;
    }
    const language = pythonTestFilePattern.test(testPath) ? "Python" : "JavaScript";
    errors.push(
      `${relative} references a ${language} file without a test definition and assertion: ${testPath}`,
    );
  }
  if (executable === 0) {
    errors.push(
      `${relative} must reference at least one executable behaviour test in ## Verification`,
    );
  }

  // Implementation paths live beside the evidence in ## Verification now that
  // specs carry no separate implementation section.
  for (const implementationPath of verificationRefs.filter(
    (candidate) => !isBehaviourTest(candidate),
  )) {
    const status = await repositoryFileStatus(root, implementationPath);
    if (status === "outside") {
      errors.push(
        `${relative} references an implementation path outside the repository: ${implementationPath}`,
      );
    } else if (status === "missing") {
      errors.push(
        `${relative} references missing implementation file: ${implementationPath}`,
      );
    }
  }

  if (
    localMarkdownPaths(source, file).some((target) =>
      isWithin(path.join(root, "openspec", "changes"), target),
    )
  ) {
    errors.push(
      `${relative} must not link change proposals; link current specs or canonical visuals instead`,
    );
  }

  if (surface === "none" && /^## Visuals[ \t]*$/m.test(withoutCodeFences(source))) {
    errors.push(
      `${relative} must not declare ## Visuals while Primary surface: none`,
    );
  }

  await validateLocalMarkdownLinks(file, root, errors);

  return { key: capability, file, surface, source };
}

/** Markdown files under a directory, recursively. */
async function markdownFiles(directory) {
  const files = [];
  for (const entry of await directoryEntries(directory)) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await markdownFiles(file)));
    } else if (entry.isFile() && entry.name.endsWith(".md")) {
      files.push(file);
    }
  }
  return files.sort();
}

/** A body carries content once headings, checkboxes, and whitespace are removed. */
function hasBody(source) {
  return withoutCodeFences(source)
    .split(/\r?\n/)
    .some((line) => line.trim() && !/^#{1,6}\s/.test(line));
}

/**
 * Native deltas begin at their first `##` section: no YAML frontmatter and no
 * prose preamble.
 *
 * @param {string} source
 * @returns {string|null} the first offending line
 */
function deltaPreamble(source) {
  for (const line of source.split(/\r?\n/)) {
    if (/^##(?:[ \t]|$)/.test(line)) return null;
    if (!line.trim()) continue;
    return line.trim();
  }
  return null;
}

/**
 * Validate one active or archived change package.
 *
 * @param {string} directory
 * @param {string} root
 * @param {string[]} errors
 * @param {boolean} archived
 */
async function validateChangePackage(directory, root, errors, archived) {
  const relative = path.relative(root, directory);
  const name = path.basename(directory);
  if (archived) {
    if (!/^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
      errors.push(`${relative} must use <YYYY-MM-DD>-<slug>`);
    }
  } else if (!capabilityKey.test(name)) {
    errors.push(`${relative} must use a lowercase change slug (a-z, 0-9, hyphen)`);
  }

  const proposal = path.join(directory, "proposal.md");
  const design = path.join(directory, "design.md");
  const tasks = path.join(directory, "tasks.md");
  // design.md is optional in the native schema; proposal and tasks are required.
  for (const [file, label] of [[proposal, "proposal.md"], [tasks, "tasks.md"]]) {
    if (!(await isFile(file))) errors.push(`${relative} is missing ${label}`);
  }

  if (await isFile(proposal)) {
    const source = await readFile(proposal, "utf8");
    for (const heading of ["Why", "What Changes"]) {
      if (!section(source, heading)?.trim()) {
        errors.push(`${relative}/proposal.md must include a ## ${heading} section`);
      }
    }
  }

  if (await isFile(design)) {
    const source = await readFile(design, "utf8");
    if (!hasBody(source)) {
      errors.push(`${relative}/design.md must state at least one design decision`);
    }
  }

  const specsDirectory = path.join(directory, "specs");
  const deltaFiles = [];
  for (const entry of await directoryEntries(specsDirectory)) {
    const target = path.join(specsDirectory, entry.name);
    if (!entry.isDirectory()) {
      errors.push(`${path.relative(root, target)} must live at specs/<capability>/spec.md`);
      continue;
    }
    if (!capabilityKey.test(entry.name)) {
      errors.push(
        `${path.relative(root, target)} must use a lowercase capability key (a-z, 0-9, hyphen)`,
      );
    }
    const delta = path.join(target, "spec.md");
    if (!(await isFile(delta))) {
      errors.push(`${path.relative(root, delta)} is required`);
      continue;
    }
    deltaFiles.push(delta);
  }
  // Zero-delta admission (including `.openspec.yaml` skip_specs) belongs to the
  // native strict validator. Only deltas that exist are checked here.

  for (const delta of deltaFiles) {
    const deltaRelative = path.relative(root, delta);
    const source = await readFile(delta, "utf8");
    const preamble = deltaPreamble(source);
    if (preamble) {
      errors.push(
        `${deltaRelative} must start at a ## section; native deltas carry no frontmatter or preamble: ${preamble}`,
      );
    }
    const { requirements, sections } = scanHeadings(source, deltaRelative, errors, {
      allowed: [...deltaSections, "Purpose"],
      label: "delta section",
    });
    const declared = deltaSections.filter((name) => sections.includes(name));
    if (declared.length === 0) {
      errors.push(
        `${deltaRelative} must declare an ## ADDED Requirements, ## MODIFIED Requirements, ## REMOVED Requirements, or ## RENAMED Requirements section`,
      );
    }
    // RENAMED-only deltas carry FROM:/TO: pairs, not ### Requirement headings.
    // Native strict validation owns the pair syntax.
    const renamedOnly = declared.length > 0 && declared.every((name) => name === "RENAMED Requirements");
    if (requirements.size === 0 && !renamedOnly) {
      errors.push(`${deltaRelative} must declare at least one ### Requirement`);
    }
    for (const [requirement, { count, section: owner }] of requirements) {
      if (owner !== "REMOVED Requirements" && owner !== "RENAMED Requirements" && count === 0) {
        errors.push(
          `${deltaRelative} requirement '${requirement}' must include at least one #### Scenario`,
        );
      }
    }
  }

  if (await isFile(tasks)) {
    const source = withoutCodeFences(await readFile(tasks, "utf8"));
    const lines = source.split(/\r?\n/);
    if (!lines.some((line) => checkboxItem.test(line))) {
      errors.push(`${relative}/tasks.md must include at least one checkbox task`);
    }
    // `## Workflow follow-up` is reference material outside tracked progress,
    // so plain bullets are allowed there only.
    let currentSection = null;
    let stray = null;
    for (const line of lines) {
      const heading = line.match(/^##[ \t]+(.*?)[ \t]*$/);
      if (heading) {
        currentSection = heading[1];
        continue;
      }
      if (listItem.test(line) && !checkboxItem.test(line) && currentSection !== "Workflow follow-up") {
        stray = line;
        break;
      }
    }
    if (stray) {
      errors.push(
        `${relative}/tasks.md must use checkbox list items only: ${stray.trim()}`,
      );
    }
  }

  // tasks.md is the single progress writer; checkboxes elsewhere are a second authority.
  for (const file of await markdownFiles(directory)) {
    if (path.resolve(file) === path.resolve(tasks)) continue;
    const source = withoutCodeFences(await readFile(file, "utf8"));
    if (source.split(/\r?\n/).some((line) => checkboxItem.test(line))) {
      errors.push(
        `${path.relative(root, file)} must be the only task-progress writer; use tasks.md`,
      );
    }
  }

  for (const file of await markdownFiles(directory)) {
    await validateLocalMarkdownLinks(file, root, errors);
  }
}

/**
 * Refuse symlinks: records and canonical visuals must be real repository files.
 *
 * Every path component from `root` down to `target` is checked before the tree
 * is descended, so a symlinked ancestor (`openspec`, `docs/product`, …) is
 * reported instead of followed. A caller must not read artifacts when this
 * returns false.
 *
 * @param {string} target
 * @param {string} root
 * @param {string[]} errors
 * @returns {Promise<boolean>} whether the tree contains no symlink
 */
async function scanSymlinks(target, root, errors) {
  let current = root;
  for (const component of path.relative(root, target).split(path.sep).filter(Boolean)) {
    current = path.join(current, component);
    const info = await lstat(current).catch(() => null);
    if (!info) return true; // absent trees are reported by their own checks
    if (info.isSymbolicLink()) {
      errors.push(
        `${path.relative(root, current)} is a symlink; OpenSpec records and visual artifacts must be real files`,
      );
      return false;
    }
    if (!info.isDirectory()) return true;
  }
  return scanNestedSymlinks(target, root, errors);
}

/**
 * @param {string} directory
 * @param {string} root
 * @param {string[]} errors
 * @returns {Promise<boolean>} whether the subtree contains no symlink
 */
async function scanNestedSymlinks(directory, root, errors) {
  let clean = true;
  for (const entry of await directoryEntries(directory)) {
    const target = path.join(directory, entry.name);
    const info = await lstat(target).catch(() => null);
    if (!info) continue;
    if (info.isSymbolicLink()) {
      errors.push(
        `${path.relative(root, target)} is a symlink; OpenSpec records and visual artifacts must be real files`,
      );
      clean = false;
      continue;
    }
    if (info.isDirectory() && !(await scanNestedSymlinks(target, root, errors))) {
      clean = false;
    }
  }
  return clean;
}

/**
 * Validate canonical visuals for human-facing capability specs.
 *
 * @param {string} root
 * @param {Map<string, {file: string, surface: string, source: string}>} specs
 * @param {string[]} errors
 * @param {string} visualsRoot
 * @returns {Promise<boolean>} whether a manifest is in sync
 */
async function validateVisuals(root, specs, errors, visualsRoot) {
  const manifestPath = path.join(visualsRoot, "manifest.json");
  const index = path.join(visualsRoot, "index.html");
  const humanSpecs = [...specs].filter(([, spec]) => spec.surface === "human");
  const visualsInfo = await stat(visualsRoot).catch(() => null);
  let visuals = false;

  if (humanSpecs.length > 0 || visualsInfo) {
    for (const name of ["manifest.json", "generate.mjs", "index.html"]) {
      if (!(await isFile(path.join(visualsRoot, name)))) {
        errors.push(
          `docs/product/wireframes/${name} is required for the visual workflow`,
        );
      }
    }
  }

  if (!(await isFile(manifestPath))) return visuals;

  visuals = true;
  let parsed;
  try {
    parsed = JSON.parse(await readFile(manifestPath, "utf8"));
  } catch {
    errors.push("docs/product/wireframes/manifest.json is not valid JSON");
    return visuals;
  }

  const screens = Array.isArray(parsed?.screens) ? parsed.screens : null;
  if (!screens) {
    errors.push("wireframe manifest must contain a non-empty screens array");
    return visuals;
  }
  if (screens.length === 0) {
    errors.push("wireframe manifest must contain a non-empty screens array");
  }

  const indexSource = await readFile(index, "utf8").catch(() => "");
  const indexLinks = [
    ...indexSource
      .replace(/<!--[\s\S]*?-->/g, "")
      .matchAll(/<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']/gi),
  ].map((match) => match[1]);

  const listedKeys = new Set();
  const listedArtifacts = new Set();

  for (const screen of screens) {
    const capability = typeof screen?.capability === "string" ? screen.capability : "";
    if (!capabilityKey.test(capability)) {
      errors.push(
        `wireframe manifest screen must carry a capability key: ${JSON.stringify(screen)}`,
      );
      continue;
    }
    const key = typeof screen?.screen === "string" ? screen.screen : "";
    if (!capabilityKey.test(key)) {
      errors.push(
        `wireframe manifest ${capability} must use a lowercase visual screen key: ${JSON.stringify(screen.screen)}`,
      );
      continue;
    }
    const visual = `${capability}/${key}`;
    if (listedKeys.has(visual)) {
      errors.push(`wireframe manifest duplicates visual ${visual}`);
    }
    listedKeys.add(visual);

    const spec = specs.get(capability);
    if (!spec) {
      errors.push(`wireframe manifest lists unknown capability ${capability}`);
    } else if (spec.surface !== "human") {
      errors.push(`wireframe manifest ${capability} must declare Primary surface: human`);
    }

    if (typeof screen.title !== "string" || !screen.title.trim()) {
      errors.push(`wireframe manifest ${visual} must have a non-empty title`);
    }
    if (
      ![screen.viewport?.width, screen.viewport?.height].every(
        (value) => Number.isInteger(value) && value > 0,
      )
    ) {
      errors.push(
        `wireframe manifest ${visual} must have a positive integer viewport width and height`,
      );
    }

    const expectedHtml = `html/${capability}-${key}.html`;
    const expectedPng = `exports/${capability}-${key}.png`;
    for (const [artifact, expected, label] of [
      [screen.html, expectedHtml, "html"],
      [screen.png, expectedPng, "png"],
    ]) {
      if (artifact !== expected) {
        errors.push(
          `wireframe manifest ${label} path must be ${expected}: ${artifact}`,
        );
        continue;
      }
      listedArtifacts.add(artifact);
      if (!(await isFile(path.join(visualsRoot, artifact)))) {
        errors.push(`wireframe manifest lists missing artifact: ${artifact}`);
      }
    }

    if (spec) {
      const links = localMarkdownPaths(section(spec.source, "Visuals") ?? "", spec.file);
      // Only string artifact paths reach path resolution; malformed entries
      // already produced a diagnostic above.
      const missing = [screen.html, screen.png]
        .filter((artifact) => typeof artifact === "string")
        .filter((artifact) => !links.includes(path.resolve(visualsRoot, artifact)));
      if (missing.length > 0) {
        errors.push(
          `${path.relative(root, spec.file)} must link its visual HTML and PNG under ## Visuals (${missing.join(", ")})`,
        );
      }
    }

    if (typeof screen.html === "string" && !indexLinks.includes(screen.html)) {
      errors.push(`docs/product/wireframes/index.html must link ${screen.html}`);
    }
  }

  for (const [capability] of humanSpecs) {
    if (![...listedKeys].some((visual) => visual.startsWith(`${capability}/`))) {
      errors.push(
        `wireframe manifest must list primary-surface capability ${capability}`,
      );
    }
  }

  for (const [directory, extension] of [["html", "html"], ["exports", "png"]]) {
    for (const entry of await directoryEntries(path.join(visualsRoot, directory))) {
      const artifact = `${directory}/${entry.name}`;
      const owner = [...specs.keys()].find((capability) =>
        entry.name.startsWith(`${capability}-`),
      );
      if (
        !entry.isFile() ||
        !entry.name.endsWith(`.${extension}`) ||
        !owner
      ) {
        errors.push(`invalid visual artifact: ${artifact}`);
      } else if (!listedArtifacts.has(artifact)) {
        errors.push(
          `visual artifact is missing from the manifest: ${artifact}`,
        );
      }
    }
  }

  return visuals;
}

/**
 * Validate the OpenSpec tree under `root`.
 *
 * @param {string} root
 * @param {string[]} errors
 * @returns {Promise<{errors: string[], specs: number, changes: number, visuals: boolean}>}
 */
async function validateOpenSpecTree(root, errors) {
  const openspecRoot = path.join(root, "openspec");
  const specsRoot = path.join(openspecRoot, "specs");
  const changesRoot = path.join(openspecRoot, "changes");
  const archiveRoot = path.join(changesRoot, "archive");
  const visualsRoot = path.join(root, "docs", "product", "wireframes");

  // Fail closed before any config read or source-tree traversal: a symlinked
  // ancestor must be reported, never followed.
  const openspecSafe = await scanSymlinks(openspecRoot, root, errors);
  const visualsSafe = await scanSymlinks(visualsRoot, root, errors);
  if (!openspecSafe || !visualsSafe) {
    return { errors, specs: 0, changes: 0, visuals: false };
  }

  const configPath = path.join(openspecRoot, "config.yaml");
  const config = await readFile(configPath, "utf8").catch(() => "");
  if (!config.trim()) {
    errors.push("openspec/config.yaml is required");
  }

  const specs = new Map();
  const specsInfo = await lstat(specsRoot).catch(() => null);
  if (!specsInfo) {
    errors.push("openspec/specs directory is required");
  }
  for (const entry of await directoryEntries(specsRoot)) {
    const target = path.join(specsRoot, entry.name);
    if (entry.name === ".gitkeep" && entry.isFile() && (await stat(target)).size === 0) continue;
    if (!entry.isDirectory()) {
      errors.push(
        `${path.relative(root, target)} must live at openspec/specs/<capability>/spec.md`,
      );
      continue;
    }
    if (!capabilityKey.test(entry.name)) {
      errors.push(
        `openspec/specs/${entry.name} must use a lowercase capability key (a-z, 0-9, hyphen)`,
      );
    }
    const specPath = path.join(target, "spec.md");
    for (const inner of await directoryEntries(target)) {
      if (inner.name !== "spec.md") {
        errors.push(
          `${path.relative(root, path.join(target, inner.name))} must live at openspec/specs/<capability>/spec.md`,
        );
      }
    }
    if (!(await isFile(specPath))) {
      errors.push(`openspec/specs/${entry.name}/spec.md is required`);
      continue;
    }
    const parsed = await validateSpec(specPath, root, errors);
    if (parsed) specs.set(parsed.key, parsed);
  }
  // An existing native specs directory may be empty; native admission owns
  // whether a product has declared capabilities yet.

  const changeDirectories = [];
  for (const entry of await directoryEntries(changesRoot)) {
    const target = path.join(changesRoot, entry.name);
    if (entry.name === ".gitkeep" && entry.isFile() && (await stat(target)).size === 0) continue;
    if (entry.name === "archive" && entry.isDirectory()) {
      for (const archived of await directoryEntries(archiveRoot)) {
        const archivedTarget = path.join(archiveRoot, archived.name);
        if (archived.name === ".gitkeep" && archived.isFile() && (await stat(archivedTarget)).size === 0) continue;
        if (!archived.isDirectory()) {
          errors.push(
            `${path.relative(root, archivedTarget)} must be a change package directory`,
          );
          continue;
        }
        changeDirectories.push({ directory: archivedTarget, archived: true });
      }
      continue;
    }
    if (!entry.isDirectory()) {
      errors.push(`${path.relative(root, target)} must be a change package directory`);
      continue;
    }
    changeDirectories.push({ directory: target, archived: false });
  }

  for (const { directory, archived } of changeDirectories) {
    await validateChangePackage(directory, root, errors, archived);
  }

  const visuals = await validateVisuals(root, specs, errors, visualsRoot);

  return {
    errors,
    specs: specs.size,
    changes: changeDirectories.length,
    visuals,
  };
}

/**
 * Validate the OpenSpec tree under `root`.
 *
 * Filesystem boundary failures (a path that is not a directory, a directory
 * where a spec file belongs, …) become actionable diagnostics instead of
 * escaping to the caller.
 *
 * @param {string} root
 * @returns {Promise<{errors: string[], specs: number, changes: number, visuals: boolean}>}
 */
export async function validateOpenSpec(root) {
  const errors = [];
  try {
    return await validateOpenSpecTree(root, errors);
  } catch (error) {
    if (!error || typeof error !== "object" || typeof error.code !== "string") throw error;
    const target =
      typeof error.path === "string" ? path.relative(root, error.path) || "." : "repository";
    errors.push(`filesystem error ${error.code} at ${target}: ${error.message}`);
    return { errors, specs: 0, changes: 0, visuals: false };
  }
}

/** @returns {Promise<number>} process exit code */
async function main() {
  const arg = process.argv[2];
  const root = arg ? path.resolve(arg) : scriptRoot;
  const { errors, specs, changes, visuals } = await validateOpenSpec(root);

  if (errors.length > 0) {
    console.error(`OpenSpec check failed (${errors.length}):`);
    for (const error of errors) console.error(`- ${error}`);
    return 1;
  }

  console.log(
    `OpenSpec check passed: ${specs} capability specs, ${changes} change packages${
      visuals ? ", canonical visuals in sync" : ""
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
