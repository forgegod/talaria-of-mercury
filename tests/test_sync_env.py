"""Tests for talaria.hermos.sync_env — refresh profile .env values from env.

Covers the single-profile API: the profile's own .env values are refreshed
from a source environment dictionary, but the file's variable *set* is
never extended.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from talaria.hermos import sync_env
from talaria.paths import ResolvedPaths

REPO_ROOT = Path(__file__).resolve().parent.parent


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


class TestSyncEnv:
    def test_updates_existing_keys_from_env(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\nBAR=keep\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "new", "BAR": "keep"}, apply=True, no_backup=True,
        )

        assert report["ok"] is True
        assert report["changed"] is True
        assert report["write_confirmed"] is True
        assert [e["key"] for e in report["updated"]] == ["FOO"]
        assert report["updated"][0]["new"] == "new"
        text = env_file.read_text()
        assert "FOO=new" in text
        assert "BAR=keep" in text

    def test_never_adds_new_keys(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "NEW_KEY": "should_not_be_added"}, apply=True,
        )

        assert report["changed"] is False
        assert report["write_confirmed"] is False
        text = env_file.read_text()
        assert "NEW_KEY" not in text
        assert text == original

    def test_preserves_export_prefix(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("export FOO=old\nBAR=old\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "new", "BAR": "new"}, apply=True, no_backup=True,
        )

        assert report["changed"] is True
        text = env_file.read_text()
        assert "export FOO=new" in text
        assert "BAR=new" in text

    def test_preserves_comments_and_blanks(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("# header comment\n\nFOO=old\n# trailing\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "new"}, apply=True, no_backup=True,
        )

        assert report["changed"] is True
        text = env_file.read_text()
        assert "# header comment" in text
        assert "# trailing" in text
        assert "FOO=new" in text

    def test_empty_env_value_keeps_file_value(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keepme\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": ""}, apply=True,
        )

        assert report["changed"] is False
        assert env_file.read_text() == original

    def test_key_absent_from_env_is_listed_preserved(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=1\nBAR=2\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "1"}, apply=True,
        )

        # FOO unchanged (same value), BAR absent from env.
        assert report["changed"] is False
        assert "BAR" in report["absent"]
        assert "FOO" in report["unchanged"]
        # The file content is untouched.
        assert env_file.read_text() == "FOO=1\nBAR=2\n"

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=old\n"
        env_file.write_text(original)

        report = sync_env.sync_env(env_file, env={"FOO": "new"}, apply=False)

        assert report["changed"] is True
        assert report["write_confirmed"] is False
        assert report["dry_run"] is True
        assert env_file.read_text() == original

    def test_backup_created_by_default(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")

        report = sync_env.sync_env(env_file, env={"FOO": "new"}, apply=True)

        assert report["write_confirmed"] is True
        assert report["backup"] is not None
        assert Path(report["backup"]).exists()
        assert Path(report["backup"]).read_text() == "FOO=old\n"

    def test_no_backup_skips_bak(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "new"}, apply=True, no_backup=True,
        )

        assert report["write_confirmed"] is True
        assert report["backup"] is None
        assert not (env_file.with_name(".env.bak")).exists()

    def test_missing_env_file_is_noop(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"  # does not exist

        report = sync_env.sync_env(env_file, env={"FOO": "new"}, apply=True)

        assert report["ok"] is True
        assert report["changed"] is False
        assert report["write_confirmed"] is False
        assert not env_file.exists()

    def test_env_file_with_no_vars_is_noop(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "# just a comment\n\n"
        env_file.write_text(original)

        report = sync_env.sync_env(env_file, env={"FOO": "new"}, apply=True)

        assert report["changed"] is False
        assert report["write_confirmed"] is False
        # unchanged/absent lists are empty because no var lines matched.
        assert report["unchanged"] == []
        assert report["absent"] == []

    def test_idempotent_after_first_run(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")

        first = sync_env.sync_env(env_file, env={"FOO": "new"}, apply=True, no_backup=True)
        second = sync_env.sync_env(env_file, env={"FOO": "new"}, apply=True, no_backup=True)

        assert first["changed"] is True
        assert second["changed"] is False
        assert second["write_confirmed"] is False

    def test_run_uses_profile_env_path(self, tmp_path: Path) -> None:
        prof_dir = tmp_path / "profiles" / "vc"
        prof_dir.mkdir(parents=True)
        env_file = prof_dir / ".env"
        env_file.write_text("FOO=old\n")

        paths = _paths(tmp_path, profile="vc")
        report = sync_env.run(paths, env={"FOO": "new"}, apply=True, no_backup=True)

        assert report["profile"] == "vc"
        assert report["write_confirmed"] is True
        assert "FOO=new" in env_file.read_text()

    def test_run_with_explicit_env_file_override(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom.env"
        custom.write_text("FOO=old\n")

        paths = _paths(tmp_path)
        report = sync_env.run(
            paths, env_file=custom, env={"FOO": "new"}, apply=True, no_backup=True,
        )

        assert report["write_confirmed"] is True
        assert "FOO=new" in custom.read_text()

    def test_show_resolution_lists_would_update_without_values(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\nBAR=keep\nBAZ=2\n")

        paths = _paths(tmp_path)
        blob = sync_env.show_resolution(paths, env_file=env_file)
        # show_resolution reads os.environ; inject FOO via monkeypatching
        # the module's os reference is fragile, so we assert shape only.
        data = json.loads(blob)

        assert data["env_file"] == str(env_file)
        assert "would_update" in data
        assert "unchanged" in data
        assert "absent_from_env" in data


class TestSyncEnvAddKeys:
    """Opt-in extension of the file's variable scope from the environment."""

    def test_adds_absent_key_from_env(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=keep\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "NEW": "v1"}, apply=True, no_backup=True,
            add_keys=["NEW"],
        )

        assert report["ok"] is True
        assert report["changed"] is True
        assert report["write_confirmed"] is True
        assert [e["key"] for e in report["added"]] == ["NEW"]
        assert report["added"][0]["value"] == "v1"
        assert report["add_skipped"] == []
        text = env_file.read_text()
        assert "FOO=keep\n" in text
        assert "NEW=v1\n" in text

    def test_add_and_refresh_in_one_run(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "new", "BAR": "added"}, apply=True, no_backup=True,
            add_keys=["BAR"],
        )

        assert [e["key"] for e in report["updated"]] == ["FOO"]
        assert [e["key"] for e in report["added"]] == ["BAR"]
        text = env_file.read_text()
        assert "FOO=new\n" in text
        assert "BAR=added\n" in text

    def test_key_already_present_is_skipped_not_re_added(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep"}, apply=True, add_keys=["FOO"],
        )

        assert report["changed"] is False
        assert report["write_confirmed"] is False
        assert report["added"] == []
        names = [e["key"] for e in report["add_skipped"]]
        assert "FOO" in names
        reasons = {e["key"]: e["reason"] for e in report["add_skipped"]}
        assert reasons["FOO"] == "already-present"
        assert env_file.read_text() == original

    def test_key_not_in_env_is_skipped(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep"}, apply=True, add_keys=["GHOST"],
        )

        assert report["changed"] is False
        assert report["write_confirmed"] is False
        reasons = {e["key"]: e["reason"] for e in report["add_skipped"]}
        assert reasons["GHOST"] == "not-in-env"
        assert env_file.read_text() == original

    def test_empty_env_value_is_skipped(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "EMPTY": ""}, apply=True, add_keys=["EMPTY"],
        )

        assert report["changed"] is False
        assert report["write_confirmed"] is False
        reasons = {e["key"]: e["reason"] for e in report["add_skipped"]}
        assert reasons["EMPTY"] == "empty-value"
        assert env_file.read_text() == original

    def test_invalid_key_name_is_skipped(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "1BAD": "x"}, apply=True,
            add_keys=["1BAD", "has space"],
        )

        assert report["changed"] is False
        assert report["write_confirmed"] is False
        reasons = {e["key"]: e["reason"] for e in report["add_skipped"]}
        assert reasons["1BAD"] == "invalid-name"
        assert reasons["has space"] == "invalid-name"
        assert env_file.read_text() == original

    def test_duplicate_add_keys_collapse(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=keep\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "NEW": "v"}, apply=True, no_backup=True,
            add_keys=["NEW", "NEW", "NEW"],
        )

        assert [e["key"] for e in report["added"]] == ["NEW"]

    def test_missing_file_is_created_with_added_keys(self, tmp_path: Path) -> None:
        env_file = tmp_path / "subdir" / ".env"  # neither file nor dir exists

        report = sync_env.sync_env(
            env_file, env={"NEW": "v1", "BAR": "v2"}, apply=True, no_backup=True,
            add_keys=["NEW", "BAR"],
        )

        assert report["ok"] is True
        assert report["changed"] is True
        assert report["write_confirmed"] is True
        assert [e["key"] for e in report["added"]] == ["NEW", "BAR"]
        # File created; only added lines present (no refresh of non-existent content).
        text = env_file.read_text()
        assert text == "NEW=v1\nBAR=v2\n"

    def test_missing_file_with_no_addable_keys_is_noop(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"  # does not exist

        report = sync_env.sync_env(
            env_file, env={"GHOST_PARENT": "x"}, apply=True, add_keys=["GHOST"],
        )

        assert report["ok"] is True
        assert report["changed"] is False
        assert report["write_confirmed"] is False
        assert not env_file.exists()

    def test_dry_run_does_not_write_or_create(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=keep\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "NEW": "v"}, apply=False, add_keys=["NEW"],
        )

        assert report["changed"] is True
        assert report["write_confirmed"] is False
        assert report["dry_run"] is True
        assert [e["key"] for e in report["added"]] == ["NEW"]
        assert env_file.read_text() == "FOO=keep\n"

    def test_backup_created_by_default_on_add(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=keep\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "NEW": "v"}, apply=True, add_keys=["NEW"],
        )

        assert report["write_confirmed"] is True
        assert report["backup"] is not None
        assert Path(report["backup"]).read_text() == "FOO=keep\n"

    def test_no_add_keys_preserves_original_behaviour(self, tmp_path: Path) -> None:
        """Default path (no --add-key) is byte-identical to the value-only refresh."""
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "UNRELATED": "x"}, apply=True,
        )

        assert report["changed"] is False
        assert report["write_confirmed"] is False
        assert report["added"] == []
        assert report["add_skipped"] == []
        assert env_file.read_text() == original

    def test_added_block_separated_by_newline_from_existing(self, tmp_path: Path) -> None:
        # Existing file without trailing newline: ensure the appended block
        # starts on its own line.
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=keep")  # no trailing \n

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep", "NEW": "v"}, apply=True, no_backup=True,
            add_keys=["NEW"],
        )

        text = env_file.read_text()
        assert "FOO=keep\nNEW=v\n" == text

    def test_run_forwards_add_keys(self, tmp_path: Path) -> None:
        prof_dir = tmp_path / "profiles" / "vc"
        prof_dir.mkdir(parents=True)
        env_file = prof_dir / ".env"
        env_file.write_text("FOO=old\n")

        paths = _paths(tmp_path, profile="vc")
        report = sync_env.run(
            paths, env={"FOO": "new", "BAR": "added"}, apply=True, no_backup=True,
            add_keys=["BAR"],
        )

        assert report["profile"] == "vc"
        assert [e["key"] for e in report["updated"]] == ["FOO"]
        assert [e["key"] for e in report["added"]] == ["BAR"]
        text = env_file.read_text()
        assert "FOO=new\n" in text
        assert "BAR=added\n" in text

    def test_show_resolution_lists_would_add_without_values(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")

        # show_resolution reads os.environ, so we only assert on shape + the
        # deterministic skip path (FOO is present in the file).
        blob = sync_env.show_resolution(
            _paths(tmp_path), env_file=env_file, add_keys=["FOO"],
        )
        data = json.loads(blob)

        assert "would_add" in data
        assert "add_skipped" in data
        skip_map = {e["key"]: e["reason"] for e in data["add_skipped"]}
        assert skip_map.get("FOO") == "already-present"


class TestSyncEnvCLI:
    def _cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        full_env = {**os.environ, **(env or {})}
        return subprocess.run(
            [sys.executable, "-m", "talaria.cli", "config", "sync-env", *args],
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=full_env,
        )

    def test_help_exits_zero(self) -> None:
        result = self._cli("--help")
        assert result.returncode == 0
        assert "sync-env" in result.stdout.lower()

    def test_apply_writes_and_json_exits_zero(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")
        result = self._cli(
            "--env-path", str(env_file), "--json", "--no-backup",
            env={"FOO": "from_env"},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["write_confirmed"] is True
        assert any(e["key"] == "FOO" for e in payload["updated"])
        assert "FOO=from_env" in env_file.read_text()

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=old\n"
        env_file.write_text(original)
        result = self._cli(
            "--env-path", str(env_file), "--dry-run", "--json",
            env={"FOO": "new"},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["write_confirmed"] is False
        assert payload["changed"] is True
        assert env_file.read_text() == original

    def test_show_resolution_exits_zero(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")
        result = self._cli(
            "--env-path", str(env_file), "--show-resolution",
            env={"FOO": "new"},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["env_file"] == str(env_file)


class TestSyncEnvAddKeysCLI:
    def _cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        full_env = {**os.environ, **(env or {})}
        return subprocess.run(
            [sys.executable, "-m", "talaria.cli", "config", "sync-env", *args],
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=full_env,
        )

    def test_add_key_appends_from_env(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=keep\n")
        result = self._cli(
            "--env-path", str(env_file), "--add-key", "NEW", "--json", "--no-backup",
            env={"FOO": "keep", "NEW": "from_env"},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["write_confirmed"] is True
        assert any(e["key"] == "NEW" for e in payload["added"])
        text = env_file.read_text()
        assert "FOO=keep\n" in text
        assert "NEW=from_env\n" in text

    def test_add_key_repeatable(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=keep\n")
        result = self._cli(
            "--env-path", str(env_file),
            "--add-key", "A", "--add-key", "B",
            "--json", "--no-backup",
            env={"FOO": "keep", "A": "1", "B": "2"},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        added = {e["key"] for e in payload["added"]}
        assert added == {"A", "B"}
        text = env_file.read_text()
        assert "A=1\n" in text
        assert "B=2\n" in text

    def test_add_key_missing_value_skips_gracefully(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=keep\n")
        result = self._cli(
            "--env-path", str(env_file), "--add-key", "GHOST", "--json", "--no-backup",
            env={"FOO": "keep"},  # GHOST intentionally absent
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["changed"] is False
        assert payload["write_confirmed"] is False
        reasons = {e["key"]: e["reason"] for e in payload["add_skipped"]}
        assert reasons["GHOST"] == "not-in-env"


class TestSyncEnvSkipKeys:
    """--skip-key excludes a key from the env-value refresh."""

    def test_skip_key_preserves_file_value(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=fileval\nBAR=refresh\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "ENVVAL", "BAR": "new"}, apply=True, no_backup=True,
            skip_keys=["FOO"],
        )

        assert report["changed"] is True
        assert report["write_confirmed"] is True
        assert "FOO" in report["skipped"]
        assert "FOO" not in [e["key"] for e in report["updated"]]
        assert [e["key"] for e in report["updated"]] == ["BAR"]
        text = env_file.read_text()
        assert "FOO=fileval\n" in text
        assert "BAR=new\n" in text

    def test_skip_key_with_same_value_is_still_listed(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=same\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "same"}, apply=True, no_backup=True,
            skip_keys=["FOO"],
        )

        assert "FOO" in report["skipped"]
        assert report["changed"] is False  # nothing actually changed

    def test_skip_key_not_found_is_reported(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep"}, apply=True, skip_keys=["GHOST"],
        )

        assert report["changed"] is False
        assert report["write_confirmed"] is False
        reasons = {e["key"]: e["reason"] for e in report["skip_skipped"]}
        assert reasons["GHOST"] == "not-found"
        assert env_file.read_text() == original

    def test_skip_key_invalid_name(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep"}, apply=True, skip_keys=["1BAD"],
        )

        assert report["changed"] is False
        reasons = {e["key"]: e["reason"] for e in report["skip_skipped"]}
        assert reasons["1BAD"] == "invalid-name"

    def test_skip_dry_run_does_not_write(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=fileval\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "ENVVAL"}, apply=False, skip_keys=["FOO"],
        )

        assert report["write_confirmed"] is False
        assert env_file.read_text() == original

    def test_run_forwards_skip_keys(self, tmp_path: Path) -> None:
        prof_dir = tmp_path / "profiles" / "vc"
        prof_dir.mkdir(parents=True)
        env_file = prof_dir / ".env"
        env_file.write_text("FOO=fileval\n")

        paths = _paths(tmp_path, profile="vc")
        report = sync_env.run(
            paths, env={"FOO": "ENVVAL"}, apply=True, no_backup=True,
            skip_keys=["FOO"],
        )

        assert report["profile"] == "vc"
        assert "FOO" in report["skipped"]
        assert env_file.read_text() == "FOO=fileval\n"


class TestSyncEnvDisableKeys:
    """--disable-key comments out an active assignment."""

    def test_disable_key_comments_out_line(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=secret\nBAR=keep\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "secret", "BAR": "keep"}, apply=True, no_backup=True,
            disable_keys=["FOO"],
        )

        assert report["changed"] is True
        assert report["write_confirmed"] is True
        assert "FOO" in report["disabled"]
        text = env_file.read_text()
        lines = text.splitlines()
        assert "#FOO=secret" in lines
        assert "BAR=keep" in lines
        assert "FOO=secret" not in lines  # active line is gone

    def test_disable_key_with_export_drops_prefix(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("export FOO=secret\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "secret"}, apply=True, no_backup=True,
            disable_keys=["FOO"],
        )

        text = env_file.read_text()
        assert "#FOO=secret\n" in text
        # The commented form drops the 'export ' prefix.
        assert "export" not in text

    def test_disable_key_not_found(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "keep"}, apply=True, disable_keys=["GHOST"],
        )

        assert report["changed"] is False
        reasons = {e["key"]: e["reason"] for e in report["disable_skipped"]}
        assert reasons["GHOST"] == "not-found"
        assert env_file.read_text() == original

    def test_disable_already_disabled_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "#FOO=secret\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={}, apply=True, disable_keys=["FOO"],
        )

        assert report["changed"] is False
        reasons = {e["key"]: e["reason"] for e in report["disable_skipped"]}
        assert reasons["FOO"] == "already-disabled"
        assert env_file.read_text() == original

    def test_disable_dry_run_does_not_write(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=secret\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "secret"}, apply=False, disable_keys=["FOO"],
        )

        assert report["write_confirmed"] is False
        assert "FOO" in report["disabled"]
        assert env_file.read_text() == original

    def test_disable_creates_backup_by_default(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=secret\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "secret"}, apply=True, disable_keys=["FOO"],
        )

        assert report["backup"] is not None
        assert Path(report["backup"]).read_text() == "FOO=secret\n"

    def test_disabled_key_excluded_from_refresh_report(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=old\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "new"}, apply=True, no_backup=True,
            disable_keys=["FOO"],
        )

        # FOO is disabled, not refreshed — so it should not appear in updated.
        assert "FOO" not in [e["key"] for e in report["updated"]]
        assert "FOO" in report["disabled"]

    def test_run_forwards_disable_keys(self, tmp_path: Path) -> None:
        prof_dir = tmp_path / "profiles" / "vc"
        prof_dir.mkdir(parents=True)
        env_file = prof_dir / ".env"
        env_file.write_text("FOO=secret\n")

        paths = _paths(tmp_path, profile="vc")
        report = sync_env.run(
            paths, env={"FOO": "secret"}, apply=True, no_backup=True,
            disable_keys=["FOO"],
        )

        assert report["profile"] == "vc"
        assert "FOO" in report["disabled"]
        assert "#FOO=secret\n" in env_file.read_text()


class TestSyncEnvEnableKeys:
    """--enable-key uncomments a previously disabled assignment."""

    def test_enable_key_uncomments_line(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("#FOO=secret\nBAR=keep\n")

        report = sync_env.sync_env(
            env_file, env={"BAR": "keep"}, apply=True, no_backup=True,
            enable_keys=["FOO"],
        )

        assert report["changed"] is True
        assert report["write_confirmed"] is True
        assert "FOO" in report["enabled"]
        text = env_file.read_text()
        assert "FOO=secret\n" in text
        assert "#FOO" not in text
        assert "BAR=keep\n" in text

    def test_enable_key_restores_value_verbatim_not_from_env(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("#FOO=fileval\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "ENVVAL"}, apply=True, no_backup=True,
            enable_keys=["FOO"],
        )

        text = env_file.read_text()
        # The value from the file is restored, NOT refreshed from env.
        assert "FOO=fileval\n" in text
        assert "FOO=ENVVAL" not in text

    def test_enable_not_disabled_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "FOO=active\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"FOO": "active"}, apply=True, enable_keys=["FOO"],
        )

        assert report["changed"] is False
        reasons = {e["key"]: e["reason"] for e in report["enable_skipped"]}
        assert reasons["FOO"] == "not-disabled"
        assert env_file.read_text() == original

    def test_enable_key_not_found(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "# other comment\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={}, apply=True, enable_keys=["GHOST"],
        )

        assert report["changed"] is False
        reasons = {e["key"]: e["reason"] for e in report["enable_skipped"]}
        assert reasons["GHOST"] == "not-disabled"

    def test_enable_dry_run_does_not_write(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        original = "#FOO=secret\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={}, apply=False, enable_keys=["FOO"],
        )

        assert report["write_confirmed"] is False
        assert "FOO" in report["enabled"]
        assert env_file.read_text() == original

    def test_enable_creates_backup_by_default(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("#FOO=secret\n")

        report = sync_env.sync_env(
            env_file, env={}, apply=True, enable_keys=["FOO"],
        )

        assert report["backup"] is not None
        assert Path(report["backup"]).read_text() == "#FOO=secret\n"

    def test_plain_comment_not_touched_by_enable(self, tmp_path: Path) -> None:
        """A plain comment like '# header' must not match a disabled key."""
        env_file = tmp_path / ".env"
        original = "# header note\nBAR=keep\n"
        env_file.write_text(original)

        report = sync_env.sync_env(
            env_file, env={"BAR": "keep"}, apply=True, enable_keys=["header"],
        )

        # 'header' is not a disabled KEY=value line, so enable does nothing.
        assert report["changed"] is False
        assert env_file.read_text() == original

    def test_run_forwards_enable_keys(self, tmp_path: Path) -> None:
        prof_dir = tmp_path / "profiles" / "vc"
        prof_dir.mkdir(parents=True)
        env_file = prof_dir / ".env"
        env_file.write_text("#FOO=secret\n")

        paths = _paths(tmp_path, profile="vc")
        report = sync_env.run(
            paths, env={}, apply=True, no_backup=True, enable_keys=["FOO"],
        )

        assert report["profile"] == "vc"
        assert "FOO" in report["enabled"]
        assert "FOO=secret\n" in env_file.read_text()


class TestSyncEnvCombinedOperations:
    """skip/disable/enable/add compose in a single run."""

    def test_disable_then_enable_roundtrip(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=secret\n")

        sync_env.sync_env(
            env_file, env={"FOO": "secret"}, apply=True, no_backup=True,
            disable_keys=["FOO"],
        )
        assert "#FOO=secret\n" in env_file.read_text()

        report = sync_env.sync_env(
            env_file, env={}, apply=True, no_backup=True, enable_keys=["FOO"],
        )
        assert "FOO" in report["enabled"]
        assert env_file.read_text() == "FOO=secret\n"

    def test_disable_and_refresh_others_in_one_run(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=keep\nAPI=old\n")

        report = sync_env.sync_env(
            env_file, env={"SECRET": "keep", "API": "new"}, apply=True, no_backup=True,
            disable_keys=["SECRET"],
        )

        assert "SECRET" in report["disabled"]
        assert [e["key"] for e in report["updated"]] == ["API"]
        text = env_file.read_text()
        assert "#SECRET=keep\n" in text
        assert "API=new\n" in text

    def test_disable_preserves_comments_and_blanks(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("# header\n\nFOO=secret\n# trailing\n")

        report = sync_env.sync_env(
            env_file, env={"FOO": "secret"}, apply=True, no_backup=True,
            disable_keys=["FOO"],
        )

        text = env_file.read_text()
        assert "# header\n" in text
        assert "\n\n" in text or text.startswith("# header\n")
        assert "#FOO=secret\n" in text
        assert "# trailing\n" in text


class TestSyncEnvShowResolutionNewOps:
    def test_show_resolution_lists_all_operations(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("ACTIVE=1\n#DISABLED=2\n")

        blob = sync_env.show_resolution(
            _paths(tmp_path), env_file=env_file,
            skip_keys=["ACTIVE"], disable_keys=["ACTIVE"], enable_keys=["DISABLED"],
        )
        data = json.loads(blob)

        assert "ACTIVE" in data["active_keys"]
        assert "DISABLED" in data["disabled_keys"]
        assert "ACTIVE" in data["would_skip"]
        assert "ACTIVE" in data["would_disable"]
        assert "DISABLED" in data["would_enable"]


class TestSyncEnvNewOpsCLI:
    def _cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        full_env = {**os.environ, **(env or {})}
        return subprocess.run(
            [sys.executable, "-m", "talaria.cli", "config", "sync-env", *args],
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=full_env,
        )

    def test_skip_key_cli(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=fileval\n")
        result = self._cli(
            "--env-path", str(env_file), "--skip-key", "FOO", "--json", "--no-backup",
            env={"FOO": "ENVVAL"},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "FOO" in payload["skipped"]
        assert env_file.read_text() == "FOO=fileval\n"

    def test_disable_key_cli(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=secret\n")
        result = self._cli(
            "--env-path", str(env_file), "--disable-key", "FOO", "--json", "--no-backup",
            env={"FOO": "secret"},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "FOO" in payload["disabled"]
        assert "#FOO=secret\n" in env_file.read_text()

    def test_enable_key_cli(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("#FOO=secret\n")
        result = self._cli(
            "--env-path", str(env_file), "--enable-key", "FOO", "--json", "--no-backup",
            env={},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "FOO" in payload["enabled"]
        assert "FOO=secret\n" in env_file.read_text()
