"""Tests for talaria.hermes.auxiliary — single-profile alias derivation.

These cover the alias-derivation contract (sentinel skipping, alias
preservation, idempotency, dry-run) against the single-profile API:
the profile's own ``auxiliary`` block feeds its own ``model.aliases``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from talaria.hermes import auxiliary
from talaria.paths import ResolvedPaths
from talaria.sync.yaml_io import dump_yaml, load_yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _paths(root: Path, profile: str = "default") -> ResolvedPaths:
    state = root / "state.db" if profile == "default" else root / "profiles" / profile / "state.db"
    logs = root / "logs" if profile == "default" else root / "profiles" / profile / "logs"
    return ResolvedPaths(profile=profile, hermes_root=root, state_db=state, log_dir=logs)


class TestApplyAuxiliary:
    def test_injects_model_aliases_from_auxiliary(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {
                "vision": {"model": "gpt-4o"},
                "code": {"model": "o3-mini"},
            },
        }))

        report = auxiliary.apply_auxiliary(cfg, apply=True)

        assert report["ok"] is True
        assert report["changed"] is True
        assert report["write_confirmed"] is True
        merged = load_yaml(cfg)
        assert merged["model"]["aliases"]["_vision"] == "gpt-4o"
        assert merged["model"]["aliases"]["_code"] == "o3-mini"

    def test_skips_auxiliary_usecases_with_auto_model(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {
                "vision": {"model": "auto"},
                "code": {"model": "inherit"},
                "summary": {"model": "gpt-4o-mini"},
            },
        }))

        report = auxiliary.apply_auxiliary(cfg, apply=True, no_backup=True)

        assert report["write_confirmed"] is True
        merged = load_yaml(cfg)
        aliases = merged["model"]["aliases"]
        assert "_vision" not in aliases
        assert "_code" not in aliases
        assert aliases["_summary"] == "gpt-4o-mini"

    def test_preserves_existing_aliases(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({
            "model": {"name": "x", "aliases": {"_custom": "kept"}},
            "auxiliary": {"vision": {"model": "gpt-4o"}},
        }))

        report = auxiliary.apply_auxiliary(cfg, apply=True, no_backup=True)

        assert report["write_confirmed"] is True
        merged = load_yaml(cfg)
        assert merged["model"]["aliases"]["_custom"] == "kept"
        assert merged["model"]["aliases"]["_vision"] == "gpt-4o"

    def test_no_auxiliary_is_noop(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"model": {"name": "x"}}))

        report = auxiliary.apply_auxiliary(cfg, apply=True)

        assert report["ok"] is True
        assert report["changed"] is False
        assert report["write_confirmed"] is False
        assert report["aliases"] == {}

    def test_auxiliary_all_auto_is_noop(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {"vision": {"model": "auto"}},
        }))

        report = auxiliary.apply_auxiliary(cfg, apply=True)

        assert report["changed"] is False
        assert report["write_confirmed"] is False

    def test_idempotent_after_first_run(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {"vision": {"model": "gpt-4o"}},
        }))

        first = auxiliary.apply_auxiliary(cfg, apply=True)
        second = auxiliary.apply_auxiliary(cfg, apply=True)

        assert first["changed"] is True
        assert first["write_confirmed"] is True
        assert second["changed"] is False
        assert second["write_confirmed"] is False

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        original = dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {"vision": {"model": "gpt-4o"}},
        })
        cfg.write_text(original)

        report = auxiliary.apply_auxiliary(cfg, apply=False)

        assert report["changed"] is True
        assert report["write_confirmed"] is False
        assert report["dry_run"] is True
        assert cfg.read_text() == original

    def test_run_uses_profile_config_path(self, tmp_path: Path) -> None:
        # Named profile: config.yaml under profiles/<name>/
        prof_dir = tmp_path / "profiles" / "vc"
        prof_dir.mkdir(parents=True)
        cfg = prof_dir / "config.yaml"
        cfg.write_text(dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {"vision": {"model": "gpt-4o"}},
        }))

        paths = _paths(tmp_path, profile="vc")
        report = auxiliary.run(paths, apply=True, no_backup=True)

        assert report["profile"] == "vc"
        assert report["write_confirmed"] is True
        assert load_yaml(cfg)["model"]["aliases"]["_vision"] == "gpt-4o"

    def test_run_with_explicit_config_path_override(self, tmp_path: Path) -> None:
        cfg = tmp_path / "custom.yaml"
        cfg.write_text(dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {"code": {"model": "o3"}},
        }))

        paths = _paths(tmp_path)
        report = auxiliary.run(paths, config_path=cfg, apply=True, no_backup=True)

        assert report["write_confirmed"] is True
        assert load_yaml(cfg)["model"]["aliases"]["_code"] == "o3"

    def test_show_resolution_lists_would_derive(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({
            "auxiliary": {"vision": {"model": "gpt-4o"}, "code": {"model": "auto"}},
        }))
        paths = _paths(tmp_path)

        blob = auxiliary.show_resolution(paths)
        data = json.loads(blob)

        assert data["would_derive"] == {"_vision": "gpt-4o"}
        assert "auto" in data["sentinels"]


class TestAuxiliaryCLI:
    def _cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "talaria.cli", "config", "apply-auxiliary", *args],
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
        )

    def test_help_exits_zero(self) -> None:
        result = self._cli("--help")
        assert result.returncode == 0
        assert "auxiliary" in result.stdout.lower()

    def test_apply_writes_and_json_exits_zero(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {"vision": {"model": "gpt-4o"}},
        }))
        result = self._cli(
            "--config-path", str(cfg), "--json", "--no-backup",
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["write_confirmed"] is True
        assert load_yaml(cfg)["model"]["aliases"]["_vision"] == "gpt-4o"

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        original = dump_yaml({
            "model": {"name": "x"},
            "auxiliary": {"vision": {"model": "gpt-4o"}},
        })
        cfg.write_text(original)
        result = self._cli("--config-path", str(cfg), "--dry-run", "--json")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["write_confirmed"] is False
        assert cfg.read_text() == original

    def test_show_resolution_exits_zero(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(dump_yaml({"auxiliary": {"vision": {"model": "gpt-4o"}}}))
        result = self._cli("--config-path", str(cfg), "--show-resolution")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["would_derive"] == {"_vision": "gpt-4o"}
