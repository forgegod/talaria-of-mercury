"""Tests for talaria.paths — profile and path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from talaria.paths import (
    ACTIVE_PROFILE_FILE,
    DEFAULT_PROFILE_NAME,
    HERMES_ROOT,
    profile_paths,
    resolve_paths,
    resolve_profile_name,
)


class TestResolveProfileName:
    def test_explicit_flag_wins(self, clean_env, fake_hermes_root: Path) -> None:
        # Even with both an env var and an active_profile file, --profile wins.
        (fake_hermes_root / "active_profile").write_text("from-file")
        name = resolve_profile_name(
            profile_flag="from-cli",
            env_value="from-env",
            active_profile_file=fake_hermes_root / "active_profile",
        )
        assert name == "from-cli"

    def test_env_var_wins_over_file(self, clean_env, fake_hermes_root: Path) -> None:
        (fake_hermes_root / "active_profile").write_text("from-file")
        name = resolve_profile_name(
            env_value="from-env",
            active_profile_file=fake_hermes_root / "active_profile",
        )
        assert name == "from-env"

    def test_active_profile_file(self, clean_env, fake_hermes_root: Path) -> None:
        (fake_hermes_root / "active_profile").write_text("hermes-vc")
        name = resolve_profile_name(active_profile_file=fake_hermes_root / "active_profile")
        assert name == "hermes-vc"

    def test_empty_file_falls_through(self, clean_env, fake_hermes_root: Path) -> None:
        (fake_hermes_root / "active_profile").write_text("   \n")
        name = resolve_profile_name(active_profile_file=fake_hermes_root / "active_profile")
        assert name == DEFAULT_PROFILE_NAME

    def test_missing_file_defaults(self, clean_env, fake_hermes_root: Path) -> None:
        name = resolve_profile_name(active_profile_file=fake_hermes_root / "active_profile")
        assert name == DEFAULT_PROFILE_NAME


class TestProfilePaths:
    def test_default_profile_uses_root(self, fake_hermes_root: Path) -> None:
        state_db, log_dir = profile_paths("default", fake_hermes_root)
        assert state_db == fake_hermes_root / "state.db"
        assert log_dir == fake_hermes_root / "logs"

    def test_named_profile_uses_subdir(self, fake_hermes_root: Path) -> None:
        state_db, log_dir = profile_paths("vc-client", fake_hermes_root)
        assert state_db == fake_hermes_root / "profiles" / "vc-client" / "state.db"
        assert log_dir == fake_hermes_root / "profiles" / "vc-client" / "logs"


class TestResolvePaths:
    def test_no_overrides_uses_default(
        self, clean_env, monkeypatch: pytest.MonkeyPatch, fake_hermes_root: Path
    ) -> None:
        # Default profile when no env, no flag, no active_profile file.
        result = resolve_paths(hermes_root=fake_hermes_root)
        assert result.profile == DEFAULT_PROFILE_NAME
        assert result.state_db == fake_hermes_root / "state.db"
        assert result.log_dir == fake_hermes_root / "logs"

    def test_path_flags_win_over_profile_resolution(
        self, clean_env, fake_hermes_root: Path
    ) -> None:
        custom_state = fake_hermes_root / "alt.db"
        custom_logs = fake_hermes_root / "alt-logs"
        result = resolve_paths(
            state_db_flag=custom_state,
            log_dir_flag=custom_logs,
            hermes_root=fake_hermes_root,
        )
        assert result.state_db == custom_state
        assert result.log_dir == custom_logs
        # Profile still resolves; only the *paths* are overridden.
        assert result.profile == DEFAULT_PROFILE_NAME

    def test_active_profile_drives_paths(self, clean_env, fake_hermes_root: Path) -> None:
        (fake_hermes_root / "active_profile").write_text("hermes-vc")
        result = resolve_paths(hermes_root=fake_hermes_root)
        assert result.profile == "hermes-vc"
        assert result.state_db == fake_hermes_root / "profiles" / "hermes-vc" / "state.db"
        assert result.log_dir == fake_hermes_root / "profiles" / "hermes-vc" / "logs"


class TestCanonicalRoot:
    """HERMES_ROOT must always point at ~/.hermes regardless of env."""

    def test_hermes_root_is_home_dot_hermes(self) -> None:
        assert HERMES_ROOT == Path.home() / ".hermes"

    def test_active_profile_file_under_root(self) -> None:
        assert ACTIVE_PROFILE_FILE == Path.home() / ".hermes" / "active_profile"