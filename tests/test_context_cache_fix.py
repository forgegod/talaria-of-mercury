"""Tests for talaria.hermos.context_cache_fix."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from talaria.hermos import context_cache_fix
from talaria.paths import ResolvedPaths
from talaria.sync.yaml_io import dump_yaml, load_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CODEX = context_cache_fix.CODEX_BASE_URL


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


class TestContextCacheFix:
    def test_updates_bad_existing_entries_and_preserves_unrelated(self, tmp_path: Path) -> None:
        cache = tmp_path / "context_length_cache.yaml"
        cache.write_text(dump_yaml({"context_lengths": {
            f"gpt-5.4@{CODEX}": 272000,
            f"gpt-5.5@{CODEX}": 1050000,
            "other@https://example.test": 123,
        }}))

        report = context_cache_fix.apply_fixes(cache, apply=True)

        assert report["changed"] is True
        assert set(report["updated_keys"]) == {f"gpt-5.4@{CODEX}", f"gpt-5.5@{CODEX}"}
        merged = load_yaml(cache)["context_lengths"]
        assert merged[f"gpt-5.4@{CODEX}"] == 1050000
        assert merged[f"gpt-5.5@{CODEX}"] == 272000
        assert merged["other@https://example.test"] == 123
        assert (tmp_path / "context_length_cache.yaml.bak").exists()

    def test_inserts_missing_known_fix_keys_by_default(self, tmp_path: Path) -> None:
        cache = tmp_path / "context_length_cache.yaml"
        cache.write_text(dump_yaml({"context_lengths": {"other": 1}}))

        report = context_cache_fix.apply_fixes(cache, apply=True, no_backup=True)

        assert set(report["new_keys"]) == set(context_cache_fix.KNOWN_CONTEXT_FIXES)
        merged = load_yaml(cache)["context_lengths"]
        for key, value in context_cache_fix.KNOWN_CONTEXT_FIXES.items():
            assert merged[key] == value

    def test_only_existing_does_not_insert_missing_keys(self, tmp_path: Path) -> None:
        cache = tmp_path / "context_length_cache.yaml"
        cache.write_text(dump_yaml({"context_lengths": {f"gpt-5.4@{CODEX}": 32000}}))

        report = context_cache_fix.apply_fixes(cache, apply=True, create_missing=False, no_backup=True)

        merged = load_yaml(cache)["context_lengths"]
        assert report["new_keys"] == []
        assert merged[f"gpt-5.4@{CODEX}"] == 1050000
        assert f"gpt-5.5@{CODEX}" not in merged

    def test_dry_run_reports_without_writing(self, tmp_path: Path) -> None:
        cache = tmp_path / "context_length_cache.yaml"
        before = dump_yaml({"context_lengths": {f"gpt-5.4@{CODEX}": 32000}})
        cache.write_text(before)

        report = context_cache_fix.apply_fixes(cache, apply=False)

        assert report["changed"] is True
        assert report["dry_run"] is True
        assert cache.read_text() == before
        assert not (tmp_path / "context_length_cache.yaml.bak").exists()

    def test_profile_cache_path_uses_named_profile_root(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path / ".hermes", profile="vc-client")
        assert context_cache_fix.profile_cache_path(paths) == (
            tmp_path / ".hermes" / "profiles" / "vc-client" / "context_length_cache.yaml"
        )

    def test_run_uses_resolved_profile_cache(self, tmp_path: Path) -> None:
        root = tmp_path / ".hermes"
        (root / "profiles" / "vc-client").mkdir(parents=True)
        paths = _paths(root, profile="vc-client")

        report = context_cache_fix.run(paths, apply=True, no_backup=True)

        assert report["profile"] == "vc-client"
        assert load_yaml(root / "profiles" / "vc-client" / "context_length_cache.yaml")["context_lengths"][
            f"gpt-5.4@{CODEX}"
        ] == 1050000


class TestContextCacheFixCli:
    def test_cli_json_dry_run_with_cache_path(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.yaml"
        cache.write_text(dump_yaml({"context_lengths": {f"gpt-5.4@{CODEX}": 32000}}))
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "talaria.cli",
                "hermes",
                "fix-context-cache",
                "--cache-path",
                str(cache),
                "--dry-run",
                "--json",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload["changed"] is True
        assert payload["dry_run"] is True
        assert load_yaml(cache)["context_lengths"][f"gpt-5.4@{CODEX}"] == 32000

    def test_cli_writes_named_profile_cache(self, tmp_path: Path) -> None:
        profile_dir = tmp_path / ".hermes" / "profiles" / "vc-client"
        profile_dir.mkdir(parents=True)
        (profile_dir / "context_length_cache.yaml").write_text(
            dump_yaml({"context_lengths": {f"gpt-5.4@{CODEX}": 32000}})
        )
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "talaria.cli",
                "hermes",
                "fix-context-cache",
                "--profile",
                "vc-client",
                "--no-backup",
                "--only-existing",
                "--verbose",
            ],
            cwd=REPO_ROOT,
            env={"HOME": str(tmp_path)},
            text=True,
            capture_output=True,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        assert "cache repaired" in proc.stdout
        merged = load_yaml(profile_dir / "context_length_cache.yaml")["context_lengths"]
        assert merged[f"gpt-5.4@{CODEX}"] == 1050000
        assert f"gpt-5.5@{CODEX}" not in merged
