"""Install every skill below a recursive Hermes skill identifier.

Hermes itself installs one skill identifier at a time. This Talaria
feature expands wildcard identifiers such as
``skills-sh/addyosmani/agent-skills/*`` into the child skill identifiers,
invokes ``hermes skills install`` for each one, and then writes the
profile's ``skills.disabled`` list so recursively installed third-party
skills are disabled by default.

Enable policy:

* default — every successfully installed skill is added to
  ``skills.disabled``.
* ``--force-enable`` — every successfully installed skill is removed from
  ``skills.disabled``.
* ``--enable A B`` — only selected installed skills are enabled; every
  other successfully installed skill is disabled.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable
from urllib import request as urllib_request

from talaria.paths import DEFAULT_PROFILE_NAME, ResolvedPaths
from talaria.sync.writer import write_with_backup
from talaria.sync.yaml_io import dump_yaml, load_yaml, validate_yaml


HTTP_TIMEOUT = 30


@dataclass(frozen=True)
class InstallResult:
    """Result from one ``hermes skills install`` invocation."""

    identifier: str
    name: str
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


Installer = Callable[[str, ResolvedPaths, bool, str], InstallResult]


class SkillInstallError(RuntimeError):
    """Raised for identifier expansion or config-write failures."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def profile_config_path(paths: ResolvedPaths) -> Path:
    """Return the config.yaml path for the resolved Hermes profile."""
    if paths.profile == DEFAULT_PROFILE_NAME:
        return paths.hermes_root / "config.yaml"
    return paths.hermes_root / "profiles" / paths.profile / "config.yaml"


def profile_hermes_home(paths: ResolvedPaths) -> str:
    """Return the ``HERMES_HOME`` value for the resolved profile.

    Hermes resolves profiles by setting ``HERMES_HOME`` to the profile
    directory (e.g. ``~/.hermes/profiles/vc-client``), not by reading a
    ``HERMES_PROFILE`` env var. Every subprocess delegation to
    ``hermes skills ...`` must set ``HERMES_HOME`` so the child process
    operates on the correct profile.
    """
    if paths.profile == DEFAULT_PROFILE_NAME:
        return str(paths.hermes_root)
    return str(paths.hermes_root / "profiles" / paths.profile)


def is_recursive_identifier(identifier: str) -> bool:
    """True iff *identifier* uses the recursive ``/*`` suffix."""
    return identifier.strip().endswith("/*")


def _strip_recursive_suffix(identifier: str) -> str:
    return identifier.strip()[:-2].rstrip("/")


def _split_github_like_identifier(identifier: str) -> tuple[str, str, str, str]:
    """Return ``(source_prefix, owner, repo, parent)`` for a GitHub-backed ID."""
    base = _strip_recursive_suffix(identifier)
    source_prefix = ""
    for prefix in ("skills-sh/", "skills.sh/", "skils-sh/", "skils.sh/"):
        if base.startswith(prefix):
            source_prefix = "skills-sh/"
            base = base[len(prefix):]
            break
    parts = base.split("/", 2)
    if len(parts) < 2:
        raise SkillInstallError(
            f"unsupported recursive identifier: {identifier!r}", kind="config"
        )
    owner, repo = parts[0], parts[1]
    parent = parts[2].strip("/") if len(parts) == 3 else ""
    if not owner or not repo:
        raise SkillInstallError(
            f"unsupported recursive identifier: {identifier!r}", kind="config"
        )
    return source_prefix, owner, repo, parent


def _github_json(url: str, *, token: str | None = None) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "talaria-skill-install",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib_request.Request(url, headers=headers)
    with urllib_request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def expand_recursive_identifier(identifier: str) -> list[str]:
    """Expand ``owner/repo/path/*`` or ``skills-sh/owner/repo/path/*``.

    The expansion scans the GitHub repository tree recursively and returns
    every directory under the selected parent that contains ``SKILL.md``.
    Hidden/underscore-prefixed path components are skipped.
    """
    if not is_recursive_identifier(identifier):
        return [identifier]

    source_prefix, owner, repo, parent = _split_github_like_identifier(identifier)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo_api = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        meta = _github_json(repo_api, token=token)
        branch = str(meta.get("default_branch") or "main")
        tree = _github_json(f"{repo_api}/git/trees/{branch}?recursive=1", token=token)
    except Exception as exc:
        raise SkillInstallError(f"failed to query GitHub tree: {exc}", kind="network") from exc

    prefix = f"{parent}/" if parent else ""
    skill_dirs: set[str] = set()
    for item in tree.get("tree") or []:
        if item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        if not path.startswith(prefix) or not path.endswith("/SKILL.md"):
            continue
        rel_dir = path[len(prefix):-len("/SKILL.md")]
        if not rel_dir:
            continue
        rel_parts = rel_dir.split("/")
        if any(not part or part.startswith((".", "_")) for part in rel_parts):
            continue
        skill_dirs.add(f"{parent}/{rel_dir}" if parent else rel_dir)

    if not skill_dirs:
        raise SkillInstallError(
            f"no child skills found below {identifier!r}", kind="config"
        )

    repo_id = f"{owner}/{repo}"
    return [
        f"{source_prefix}{repo_id}/{skill_dir}"
        for skill_dir in sorted(skill_dirs, key=lambda p: (p.split("/")[-1], p))
    ]


def skill_name_from_identifier(identifier: str) -> str:
    """Best-effort installed skill name for an identifier."""
    return identifier.strip().strip("/").split("/")[-1]


def _detect_name_collisions(identifiers: list[str]) -> dict[str, list[str]]:
    """Return ``{name: [identifiers]}`` for names appearing more than once.

    Hermes' ``lock.json`` keys installed skills by name (the trailing path
    component). Two identifiers with the same trailing component collide:
    the second install overwrites the first's lock entry and orphans its
    directory. This function surfaces those collisions so the operator can
    decide whether to proceed.
    """
    from collections import Counter

    name_map: dict[str, list[str]] = {}
    for ident in identifiers:
        name_map.setdefault(skill_name_from_identifier(ident), []).append(ident)
    counts = Counter(skill_name_from_identifier(i) for i in identifiers)
    return {name: name_map[name] for name, cnt in counts.items() if cnt > 1}


def default_installer(
    identifier: str, paths: ResolvedPaths, force: bool, category: str = "",
) -> InstallResult:
    """Invoke Hermes to install one skill into the selected profile.

    ``category`` is forwarded to ``hermes skills install --category`` so the
    skill lands in ``skills/<category>/<name>/`` instead of the flat root.
    """
    cmd = ["hermes", "skills", "install", identifier, "--yes"]
    if force:
        cmd.append("--force")
    if category:
        cmd += ["--category", category]
    env = os.environ.copy()
    env["HERMES_HOME"] = profile_hermes_home(paths)
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False, env=env)
    return InstallResult(
        identifier=identifier,
        name=skill_name_from_identifier(identifier),
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _matches_enable_selector(selector: str, *, name: str, identifier: str) -> bool:
    needle = selector.strip().strip("/").lower()
    if not needle:
        return False
    normalized_identifier = identifier.strip().strip("/").lower()
    return needle in {
        name.lower(),
        normalized_identifier,
        normalized_identifier.split("/")[-1],
    }


def apply_disabled_policy(
    config_path: Path,
    installed: list[InstallResult],
    *,
    force_enable: bool = False,
    enable: list[str] | None = None,
    apply: bool = True,
    no_backup: bool = False,
) -> dict[str, Any]:
    """Update ``skills.disabled`` for successfully installed skills."""
    enable = enable or []
    installed_ok = [r for r in installed if r.ok]
    installed_names = {r.name for r in installed_ok if r.name}
    if force_enable:
        enabled = set(installed_names)
    elif enable:
        enabled = {
            r.name
            for r in installed_ok
            if any(_matches_enable_selector(sel, name=r.name, identifier=r.identifier) for sel in enable)
        }
    else:
        enabled = set()
    disabled = installed_names - enabled

    config = load_yaml(config_path)
    skills_cfg = config.setdefault("skills", {})
    current_disabled = set(skills_cfg.get("disabled") or [])
    current_disabled -= enabled
    current_disabled |= disabled
    skills_cfg["disabled"] = sorted(current_disabled)

    changed = True
    backup_path = None
    if apply:
        payload = dump_yaml(config)
        ok, err = validate_yaml(payload)
        if not ok:
            raise SkillInstallError(f"produced YAML failed validation: {err}", kind="write")
        outcome = write_with_backup(config_path, payload, no_backup=no_backup)
        backup_path = str(outcome.backup) if outcome.backup else None

    return {
        "enabled": sorted(enabled),
        "disabled": sorted(disabled),
        "config_path": str(config_path),
        "config_changed": changed,
        "backup_path": backup_path,
    }


def run(
    paths: ResolvedPaths,
    *,
    identifier: str,
    force: bool = False,
    force_enable: bool = False,
    enable: list[str] | None = None,
    category: str = "",
    replace_similar: bool = False,
    similarity_threshold: float = 0.65,
    apply: bool = True,
    no_backup: bool = False,
    verbose: bool = False,
    out: Any = None,
    installer: Installer = default_installer,
    uninstaller: Any = None,
) -> dict[str, Any]:
    """Expand, install, and apply the recursive-install enable policy."""
    import sys

    out = sys.stderr if out is None else out

    def _say(msg: str) -> None:
        if verbose:
            out.write(msg.rstrip("\n") + "\n")
            out.flush()

    try:
        _say(f"expanding identifier {identifier!r} ...")
        identifiers = expand_recursive_identifier(identifier)
    except SkillInstallError as exc:
        _say(f"  expansion failed: {exc}")
        return {
            "ok": False,
            "reason": exc.kind,
            "profile": paths.profile,
            "identifier": identifier,
            "category": category,
            "expanded": [],
            "installed": [],
            "enabled": [],
            "disabled": [],
            "config_path": str(profile_config_path(paths)),
            "error": str(exc),
            "dry_run": not apply,
        }

    _say(f"  found {len(identifiers)} skill(s): {', '.join(identifiers)}")

    # Detect name collisions across expanded identifiers. Two identifiers
    # with the same trailing component (e.g. cat-a/foo and cat-b/foo)
    # produce the same skill name. Hermes' lock.json keys by name, so the
    # second install silently overwrites the first's lock entry and leaves
    # the first directory orphaned. Surface this to the operator.
    name_collisions = _detect_name_collisions(identifiers)
    if name_collisions:
        _say(f"  WARNING: {len(name_collisions)} name collision(s) detected:")
        for name, idents in name_collisions.items():
            _say(f"    {name}: {', '.join(idents)}")
            _say("    (later installs overwrite earlier lock.json entries)")

    # Assess similarity against already-installed skills. For each expanded
    # identifier whose skill name matches an installed skill, compare
    # frontmatter (name + description) via difflib.SequenceMatcher. When
    # replace_similar is set and the ratio >= threshold, uninstall the old
    # skill before installing the new one.
    similarity_assessments: list[dict[str, Any]] = []
    replaced_skills: list[dict[str, Any]] = []
    if apply:
        from talaria.hermos import skill_similarity

        for skill_id in identifiers:
            name = skill_name_from_identifier(skill_id)
            existing = skill_similarity.get_installed_entry(paths, name)
            if not existing:
                continue
            _say(f"  assessing similarity: {name} (installed vs {skill_id}) ...")
            result = skill_similarity.assess_collision(
                paths, skill_id, name, threshold=similarity_threshold,
            )
            assessment = {
                "skill_name": name,
                "incoming_identifier": skill_id,
                "installed_identifier": result.installed_identifier,
                "ratio": result.ratio,
                "similar": result.similar,
                "threshold": result.threshold,
                "error": result.error,
            }
            similarity_assessments.append(assessment)
            if result.similar:
                _say(
                    f"    SIMILAR ({result.ratio:.0%} >= {result.threshold:.0%}): "
                    f"{name} already installed from {result.installed_identifier}"
                )
                if replace_similar:
                    _say(f"    --replace-similar-skill: uninstalling {name} ...")
                    if uninstaller is None:
                        from talaria.hermos import skill_uninstall
                        uninstaller = skill_uninstall.default_uninstaller
                    unres = uninstaller(skill_id, paths)
                    replaced_skills.append({
                        "name": name,
                        "identifier": skill_id,
                        "returncode": unres.returncode,
                        "ok": unres.ok,
                    })
                    if unres.ok:
                        _say(f"    uninstalled {name}")
                    else:
                        _say(f"    FAILED to uninstall {name} (rc={unres.returncode})")
                else:
                    _say(
                        f"    hint: pass --replace-similar-skill to uninstall "
                        f"the existing '{name}' before installing the new one"
                    )

    installed: list[InstallResult] = []
    if apply:
        for skill_id in identifiers:
            _say(f"install {skill_id} ...")
            installed.append(installer(skill_id, paths, force, category))
            last = installed[-1]
            _say(f"  -> rc={last.returncode} name={last.name}")
    else:
        installed = [InstallResult(skill_id, skill_name_from_identifier(skill_id), 0) for skill_id in identifiers]

    ok_installed = [r for r in installed if r.ok]
    failed = [r for r in installed if not r.ok]
    policy = {"enabled": [], "disabled": [], "config_path": str(profile_config_path(paths)), "backup_path": None}
    if ok_installed:
        _say(f"updating skills.disabled in {profile_config_path(paths)} ...")
        try:
            policy = apply_disabled_policy(
                profile_config_path(paths),
                ok_installed,
                force_enable=force_enable,
                enable=enable,
                apply=apply,
                no_backup=no_backup,
            )
            _say(f"  enabled={policy.get('enabled')} disabled={policy.get('disabled')}")
        except SkillInstallError as exc:
            _say(f"  config write failed: {exc}")
            return {
                "ok": False,
                "reason": exc.kind,
                "profile": paths.profile,
                "identifier": identifier,
                "category": category,
                "expanded": identifiers,
                "installed": [r.__dict__ for r in installed],
                "enabled": [],
                "disabled": [],
                "config_path": str(profile_config_path(paths)),
                "error": str(exc),
                "dry_run": not apply,
            }

    return {
        "ok": not failed and bool(ok_installed),
        "reason": "dry_run" if not apply else ("installed" if not failed else "install"),
        "profile": paths.profile,
        "identifier": identifier,
        "category": category,
        "expanded": identifiers,
        "installed": [r.__dict__ for r in installed],
        "enabled": policy["enabled"],
        "disabled": policy["disabled"],
        "name_collisions": name_collisions,
        "similarity_assessments": similarity_assessments,
        "replaced_skills": replaced_skills,
        "config_path": policy["config_path"],
        "backup_path": policy.get("backup_path"),
        "error": None if not failed else "one or more skill installs failed",
        "dry_run": not apply,
    }


def render_human(report: dict[str, Any]) -> tuple[int, str]:
    """Format a recursive install report for terminal output."""
    lines = ["recursive skill install", "=" * 60, ""]
    lines.append(f"profile: {report.get('profile')}")
    lines.append(f"source:  {report.get('identifier')}")
    category = report.get("category") or ""
    if category:
        lines.append(f"category: {category}")
    lines.append(f"config:  {report.get('config_path')}")
    lines.append("")

    if not report.get("ok"):
        lines.append(f"ERROR: recursive skill install failed ({report.get('reason')}).")
        if report.get("error"):
            lines.append(f"  {report['error']}")
        for item in report.get("installed") or []:
            if item.get("returncode"):
                lines.append(f"  failed: {item.get('identifier')} rc={item.get('returncode')}")
                if item.get("stderr"):
                    lines.append(f"    {str(item.get('stderr')).strip()}")
        lines.append("")
        lines.append("=" * 60)
        lines.append("VERDICT: tool error — install did not complete.")
        return 2, "\n".join(lines)

    lines.append(f"Expanded:  {len(report.get('expanded') or [])} skill(s)")
    lines.append(f"Installed: {len(report.get('installed') or [])} skill(s)")
    collisions = report.get("name_collisions") or {}
    if collisions:
        lines.append("")
        lines.append("WARNING: name collisions detected (later installs overwrite earlier lock.json entries):")
        for name, idents in collisions.items():
            lines.append(f"  {name}: {', '.join(idents)}")
    if report.get("disabled"):
        lines.append("Disabled by default: " + ", ".join(report["disabled"]))
    if report.get("enabled"):
        lines.append("Enabled: " + ", ".join(report["enabled"]))
    assessments = report.get("similarity_assessments") or []
    if assessments:
        lines.append("")
        lines.append("Similarity checks (existing vs incoming):")
        for a in assessments:
            pct = int(a.get("ratio", 0) * 100)
            tag = "SIMILAR" if a.get("similar") else "different"
            lines.append(
                f"  {a['skill_name']}: {pct}% ({tag}) — "
                f"installed: {a['installed_identifier']}"
            )
            if not a.get("similar") and a.get("ratio", 0) > 0:
                lines.append(f"    hint: --replace-similar-skill would replace it")
    replaced = report.get("replaced_skills") or []
    if replaced:
        lines.append("Replaced similar skills: " + ", ".join(r["name"] for r in replaced))
    if report.get("dry_run"):
        lines.append("Dry run: no hermes install commands or config writes were performed.")
    lines.append("")
    lines.append("=" * 60)
    lines.append("VERDICT: clean — recursive skill install complete.")
    return 0, "\n".join(lines)


def show_resolution(paths: ResolvedPaths, *, identifier: str, category: str = "") -> str:
    """Return JSON showing expansion and target config path."""
    try:
        expanded = expand_recursive_identifier(identifier)
        error = None
    except SkillInstallError as exc:
        expanded = []
        error = {"kind": exc.kind, "message": str(exc)}
    return json.dumps(
        {
            "profile": paths.profile,
            "identifier": identifier,
            "category": category,
            "expanded": expanded,
            "config_path": str(profile_config_path(paths)),
            "error": error,
        },
        indent=2,
    )
