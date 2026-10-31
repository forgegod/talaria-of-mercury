# Skill lifecycle

**Primary surface:** none

## Purpose

Expand skill identifiers, delegate installation and removal to Hermes, maintain
the profile's `skills.disabled` policy, create category directories, and
reconcile three sources of skill state. Talaria does not vendor Hermes'
installer: install and uninstall semantics belong to `hermes skills …`, while
Talaria expands identifiers, forwards flags, detects false successes, and edits
profile configuration. Scope is one profile.

## Requirements

### Requirement: Recursive install expansion

`talaria skills install <identifier>` SHALL install one skill, or every child
skill when the identifier ends in `/*`. Recursive expansion SHALL read the
GitHub repository tree, keep directories containing `SKILL.md`, skip hidden and
underscore-prefixed path components, and delegate each child to
`hermes skills install --yes [--force] [--category]`.

#### Scenario: A repository root is expanded

- **WHEN** the identifier ends in `/*` and the repository root contains several skill directories
- **THEN** each child directory with a `SKILL.md` is installed individually and hidden or underscore-prefixed components are skipped

#### Scenario: A nested path is expanded

- **WHEN** the identifier names a nested path ending in `/*`
- **THEN** only children below that parent are expanded, not unrelated repository directories

### Requirement: Profile scoping and disabled-by-default policy

Every delegated install SHALL receive `HERMES_HOME` for the selected profile.
Installed skills SHALL be added to `skills.disabled` by default.
`--force-enable` SHALL enable all of them, and `--enable A B` SHALL enable only
the named selectors (name, identifier, or trailing name) while disabling the
rest. Existing policy entries outside the installed set MUST be preserved.

#### Scenario: A recursive install completes

- **WHEN** several skills are installed into a named profile without enable flags
- **THEN** each installed name is present in that profile's `skills.disabled` and the subprocess environment points `HERMES_HOME` at the profile

#### Scenario: Selected skills are enabled

- **WHEN** `--enable` names a subset of the installed skills
- **THEN** those names leave `skills.disabled`, the remaining installed names stay disabled, and unrelated policy entries are untouched

### Requirement: Category forwarding

`--category` SHALL forward Hermes' literal category directory name so a skill
lands under `skills/<category>/<name>/`. An empty value MUST mean the flat
root. The value is a literal directory name, not a display-name mapping.

#### Scenario: A category is supplied

- **WHEN** `--category software-development` is passed
- **THEN** each delegated install receives that exact flag and the skill is expected under `skills/software-development/<name>/`

#### Scenario: No category is supplied

- **WHEN** `--category` is empty
- **THEN** the delegated command omits the flag and the skill installs at the flat skills root

### Requirement: Name collision reporting

Two identifiers with the same trailing name SHALL be reported as
`name_collisions` and in the human renderer. The install MUST NOT be blocked by
a collision.

#### Scenario: Two identifiers share a trailing name

- **WHEN** expansion yields two identifiers ending in the same skill name
- **THEN** the report lists the collision and the renderer warns, while both installs still run

### Requirement: Similarity assessment and replacement

When a skill name already exists in the profile's `lock.json`, an optional
similarity assessment SHALL compare `name + description` frontmatter between
the installed `SKILL.md` on disk and the incoming `SKILL.md` from GitHub raw,
using `difflib.SequenceMatcher` at threshold 0.65. Without
`--replace-similar-skill` the similarity MUST be reported as a hint only; with
it, the old skill SHALL be uninstalled before the new install.

#### Scenario: A similar skill is already installed

- **WHEN** the existing and incoming frontmatter score at or above the threshold and `--replace-similar-skill` is absent
- **THEN** the report carries a hint and the existing skill is left in place

#### Scenario: Replacement is requested

- **WHEN** the same similarity is found and `--replace-similar-skill` is supplied
- **THEN** the existing skill is uninstalled before the incoming install runs

### Requirement: Uninstall and false-zero detection

Uninstall SHALL expand identifiers as install does, reduce them to trailing
names, and delegate to `hermes skills uninstall` with `input="y"`. Exit-0
responses containing `Cancelled`, `Error:`, `not found`, or `not a hub-installed`
SHALL become failures. Successfully removed names SHALL be dropped from `skills.disabled`;
partial failures SHALL still clean up successes and return `ok: false`.

#### Scenario: Hermes cancels the prompt

- **WHEN** the delegated uninstall exits 0 but stdout contains a cancellation or not-installed marker
- **THEN** that identifier is reported as failed and the command's result is not a success

#### Scenario: One of several uninstalls fails

- **WHEN** some identifiers are removed and one fails
- **THEN** the removed names are cleaned from `skills.disabled` and the report returns `ok: false`

### Requirement: Category creation

`talaria skills create-category <name> [--description TEXT]` SHALL validate the
category against `^[a-z][a-z0-9_/-]*$`, create `<profile>/skills/<name>/`, and
optionally write `DESCRIPTION.md` with a `description:` frontmatter field. An
existing directory SHALL be reused, and an existing `DESCRIPTION.md` SHALL be
rewritten through the atomic backup writer. Invalid names MUST raise a tool
error instead of being normalised.

#### Scenario: A category is created with a description

- **WHEN** a valid lowercase category name and `--description` are supplied
- **THEN** the directory exists and `DESCRIPTION.md` carries the description frontmatter

#### Scenario: An invalid category name is supplied

- **WHEN** the name contains uppercase letters, spaces, or a leading digit
- **THEN** validation raises an error and no directory or description file is created

### Requirement: Prune reconciliation

`skills prune` SHALL reconcile the `SKILL.md` walk, `<skills_root>/.hub/lock.json`
and `skills.disabled` through three independent prune-class flags, all off by
default. Writes SHALL require `--apply`; omission is preview, with no
`--dry-run` flag. Filesystem deletes SHALL be recursive without prompting.
`disabled_present` names MUST remain untouched.

#### Scenario: Drift classes are selected

- **WHEN** the operator selects a prune-class flag and `--apply`
- **THEN** `--prune-filesystem-only` deletes on-disk orphans, `--prune-lock-only` drops entries whose directory is absent, and `--prune-disabled-orphans` drops stale disabled names
- **AND** only selected classes are repaired, never `disabled_present` names

#### Scenario: The bare command runs

- **WHEN** `skills prune` runs without any prune-class flag
- **THEN** it reports a no-op verdict, deletes nothing, and exits 0

#### Scenario: A selected class is previewed

- **WHEN** a prune-class flag is supplied without `--apply`
- **THEN** the report lists the planned deletions and the filesystem, lock, and config remain unchanged

#### Scenario: Selected drift is applied

- **WHEN** a prune-class flag is combined with `--apply`
- **THEN** only that drift class is repaired, lock and config writes take a backup unless `--no-backup`, and the exit code is 1 when state changed

### Requirement: Write defaults and exit codes

Install, uninstall, and create-category SHALL write by default, with
`--dry-run` as their opt-out, and SHALL be silent unless `-v` or `--json`.
Install and uninstall SHALL return 2 for reported failures. An invalid category
name raises uncaught `SkillCategoryError` (exit 1, not structured exit 2).
`skills prune` SHALL always
print its human verdict, exit 0 for a no-op or preview, exit 1 after an apply
that changed state, and its `--json` path SHALL exit 0.

#### Scenario: A dry-run install is requested

- **WHEN** `skills install … --dry-run` runs
- **THEN** expansion and the planned policy are reported, no installer subprocess runs, and `config.yaml` is unchanged

#### Scenario: An install reports failure

- **WHEN** a delegated install or uninstall reports failure
- **THEN** the command exits 2

#### Scenario: An invalid category reaches the CLI

- **WHEN** `skills create-category` receives a name that fails validation
- **THEN** the uncaught `SkillCategoryError` exits 1 instead of producing a structured exit-2 report

### Requirement: Network and delegation boundary

Recursive install and similarity assessment SHALL use the network (GitHub API
and `raw.githubusercontent.com`), using `GITHUB_TOKEN` or `GH_TOKEN` when
present. Talaria MUST NOT vendor Hermes' installer, and cross-profile skill
pruning MUST NOT be implemented.

#### Scenario: A token is available

- **WHEN** `GITHUB_TOKEN` or `GH_TOKEN` is set during expansion or similarity lookup
- **THEN** the GitHub request uses it

#### Scenario: Another profile has drifted skills

- **WHEN** pruning runs for one profile
- **THEN** only that profile's skills tree, lock, and config are read or written

## Verification

- `src/talaria/hermes/skill_install.py` — expansion, install delegation, disabled-policy update, collision detection.
- `src/talaria/hermes/skill_uninstall.py` — name-based uninstall delegation, false-zero detection, disabled cleanup.
- `src/talaria/hermes/skill_similarity.py` — frontmatter comparison, threshold, and GitHub raw fetch.
- `src/talaria/hermes/skill_category.py` — validation, directory creation, and category-description output.
- `src/talaria/hermes/skill_index.py` — read-only three-source index.
- `src/talaria/hermes/skill_prune.py` — prune execution and renderer.
- `src/talaria/cli/__init__.py` — `cmd_hermes_skills_*` dispatchers.
- `tests/test_skill_install.py` — `TestRecursiveExpansion`, `TestDisabledPolicy`, `TestRun` (`HERMES_HOME`, dry-run suppression, named-profile config), `TestCategoryForwarding`, `TestNameCollisions`, and the similarity and replace tests.
- `tests/test_skill_uninstall.py` — `TestCleanupDisabledPolicy`, `TestRun` (name delegation, cancelled and not-installed false-zero detection, dry-run, partial-failure cleanup), and `TestCli`.
- `tests/test_skill_category.py` — `TestValidateCategoryName`, `TestSkillsDirPath`, `TestCreateCategory`, `TestRenderHuman`, `TestShowResolution`, and `TestCli`.
- `tests/test_skill_similarity.py` — `TestParseFrontmatter`, `TestReadInstalledFrontmatter`, `TestCompareSkills` (threshold behaviour), `TestAssessCollision`, and `TestLockReading`.
- `tests/test_skill_index.py` — `TestReadFilesystemSkillNames`, `TestReadLockSkillNames`, `TestReadDisabledSkillNames`, and `TestReadIndex`.
- `tests/test_skill_prune.py` — `TestRunNoOp`, `TestPruneFilesystemOnly`, `TestPruneLockOnly`, `TestPruneDisabledOrphans`, and `TestRenderer`.
- [Architecture](../../../docs/architecture.md) — skills delegation and profile scoping.
- [Repository contract](../../../AGENTS.md) — write boundaries.
- Related current specs: [Doctor diagnostics and remediation](../doctor-diagnostics-remediation/spec.md), [CLI entry point, profile resolution, and shell completion](../cli-entry-profile-resolution-completion/spec.md).
