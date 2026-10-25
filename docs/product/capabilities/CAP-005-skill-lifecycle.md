# CAP-005 — Skill lifecycle

**Status:** implemented
**Primary surface:** none

## Behaviour

- `talaria skills install <identifier>` installs one skill, or every child skill
  when the identifier ends in `/*`. Recursive expansion reads the GitHub
  repository tree, keeps directories containing `SKILL.md`, skips hidden and
  underscore-prefixed path components, and delegates each child to
  `hermes skills install --yes [--force] [--category]`.
- Every delegated install receives `HERMES_HOME` for the selected profile.
  Installed skills are added to `skills.disabled` by default;
  `--force-enable` enables all of them, and `--enable A B` enables only the
  named selectors (name, identifier, or trailing name) while disabling the rest.
  Existing policy entries outside the installed set are preserved.
- `--category` forwards Hermes' literal category directory name, so a skill
  lands under `skills/<category>/<name>/`. Empty means the flat root.
- Two identifiers with the same trailing name are reported as
  `name_collisions` and in the human renderer; the install is not blocked.
- When a skill name already exists in the profile's `lock.json`, an optional
  similarity assessment compares `name + description` frontmatter (installed
  `SKILL.md` on disk vs the incoming `SKILL.md` from GitHub raw) using
  `difflib.SequenceMatcher` at threshold 0.65. Without
  `--replace-similar-skill`, similarity is reported as a hint; with it, the old
  skill is uninstalled before the new install.
- `talaria skills uninstall <identifier>` expands the same way, reduces each
  identifier to its trailing name, and delegates to `hermes skills uninstall`
  non-interactively (`input="y"`). Because Hermes can exit 0 after a cancelled
  prompt, a missing skill, or a builtin, stdout markers (`Cancelled`, `Error:`,
  `not found`, `not a hub-installed`) are converted to a non-zero result.
  Successfully removed names are dropped from `skills.disabled`; partial
  failures still clean up the successes and return `ok: false`.
- `talaria skills create-category <name> [--description TEXT]` validates the
  category against `^[a-z][a-z0-9_/-]*$`, creates
  `<profile>/skills/<name>/`, and optionally writes `DESCRIPTION.md` with a
  `description:` frontmatter field. An existing directory is reused; an existing
  `DESCRIPTION.md` is rewritten through the atomic backup writer.
- `talaria skills prune` reconciles the on-disk `SKILL.md` walk,
  `<skills_root>/.hub/lock.json`, and `skills.disabled`. Three independent flags
  select drift classes: `--prune-filesystem-only` deletes on-disk orphans,
  `--prune-lock-only` drops lock entries whose directory is gone, and
  `--prune-disabled-orphans` removes stale disabled names. All default to off,
  so the bare command is a no-op; `--apply` is required to write.
- Install, uninstall, and create-category write by default; `--dry-run` is their
  opt-out. They are silent unless `-v`/`--json`. Install/uninstall return 2 for
  reported failures; invalid category names currently raise an uncaught
  `SkillCategoryError` (CLI exit 1), not a structured exit-2 report. `skills
  prune` always prints its human verdict (no `-v` flag): exit 0 for no-op or
  dry-run, 1 after an apply that changed state; its `--json` path exits 0.

## Implementation

- `src/talaria/hermes/skill_install.py` — expansion, install delegation,
  disabled-policy update, collision detection.
- `src/talaria/hermes/skill_uninstall.py` — name-based uninstall delegation,
  false-zero detection, disabled cleanup.
- `src/talaria/hermes/skill_similarity.py` — frontmatter comparison and GitHub
  raw fetch.
- `src/talaria/hermes/skill_category.py` — validation, directory creation,
  category-description output.
- `src/talaria/hermes/skill_index.py` — read-only three-source index.
- `src/talaria/hermes/skill_prune.py` — prune execution and renderer.
- `src/talaria/cli/__init__.py` — `cmd_hermes_skills_*` dispatchers.

## Rules and boundaries

- Recursive install and similarity assessment use the network (GitHub API and
  `raw.githubusercontent.com`); `GITHUB_TOKEN`/`GH_TOKEN` are used when present.
- Talaria does not vendor Hermes' installer. Install and uninstall semantics
  belong to `hermes skills …`; Talaria expands identifiers, forwards flags,
  detects false successes, and maintains `skills.disabled`.
- Scope is one profile. Cross-profile skill pruning is not implemented.
- `skills prune` never touches `disabled_present` names — only the three drift
  classes. Filesystem deletes are recursive with no prompt; omit `--apply` for
  the safe preview (prune has no `--dry-run` flag).
- `install`/`uninstall`/`create-category` write to `config.yaml` or the skills
  tree without an `--apply` gate. Only `prune` uses the preview-by-default
  contract.
- Category names are literal directory names and must be lowercase; invalid
  names raise a tool error instead of being normalised.

## Verification

- `tests/test_skill_install.py` — `TestRecursiveExpansion`, `TestDisabledPolicy`,
  `TestRun` (HERMES_HOME, dry-run suppression, named-profile config),
  `TestCategoryForwarding`, `TestNameCollisions`, and similarity/replace tests.
- `tests/test_skill_uninstall.py` — `TestCleanupDisabledPolicy`, `TestRun`
  (name delegation, cancelled/not-installed false-zero detection, dry-run,
  partial-failure cleanup), and `TestCli`.
- `tests/test_skill_category.py` — `TestValidateCategoryName`,
  `TestSkillsDirPath`, `TestCreateCategory`, `TestRenderHuman`,
  `TestShowResolution`, and `TestCli`.
- `tests/test_skill_index.py` — `TestReadFilesystemSkillNames`,
  `TestReadLockSkillNames`, `TestReadDisabledSkillNames`, `TestReadIndex`.
- `tests/test_skill_prune.py` — `TestRunNoOp`, `TestPruneFilesystemOnly`,
  `TestPruneLockOnly`, `TestPruneDisabledOrphans`, `TestRenderer`.

## Related contracts

- [Architecture](../../architecture.md)
- [Repository contract](../../../AGENTS.md)
