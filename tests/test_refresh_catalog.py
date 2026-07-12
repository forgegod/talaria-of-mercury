"""Tests for talaria.hermes.refresh_catalog.

Layout:

* TestReshape — model/manifest round-trip
* TestCredentialDiscovery — env-var precedence + .env fallback + exact-key matching
* TestCacheFreshness — age + is_cache_fresh
* TestFetch — urllib monkeypatched; 200, 401, network error, auth missing
* TestRun — full orchestration across all branches
* TestRenderer — clean / skipped / error verdicts
* TestCli — argparse + subprocess --help + --show-resolution
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

import pytest

from talaria.hermes import refresh_catalog
from talaria.paths import ResolvedPaths

# ---------- Helpers ----------

def _sample_payload() -> dict:
    """Realistic upstream payload, in the OpenAI `{data: [...]}` shape."""
    return {
        "object": "list",
        "data": [
            {
                "id": "anthropic/claude-sonnet-4",
                "name": "Claude Sonnet 4",
                "description": "Anthropic Claude Sonnet 4",
                "context_length": 200000,
                "pricing": {"prompt": "0.000003", "completion": "0.000015"},
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
            },
            {
                "id": "free/llama-3",
                "name": "Free Llama",
                "description": "",
                "context_length": 8192,
                "pricing": {"prompt": "0", "completion": "0"},
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            },
            {
                "id": "",  # skipped — no id
                "name": "garbage",
            },
            "not-a-dict",  # skipped — bad row
            {
                "id": "bad-price/model",
                "pricing": {"prompt": None, "completion": "NaN"},
            },
        ],
    }


def _payload_file(tmp_path: Path, payload: dict | None = None) -> Path:
    src = tmp_path / "upstream.json"
    src.write_text(json.dumps(payload if payload is not None else _sample_payload()))
    return src


class _StubResponse:
    """Minimal stand-in for ``http.client.HTTPResponse`` returned by urlopen."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, n: int = -1) -> bytes:
        if n < 0 or n >= len(self._body):
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data

    def getcode(self) -> int:
        return self.status

    def __enter__(self): return self

    def __exit__(self, *exc): return False


# ---------- TestReshape ----------

class TestReshape:
    def test_reshape_model_normalises_fields(self) -> None:
        raw = {
            "id": "x/y",
            "description": "d" * 500,
            "context_length": 1024,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        }
        out = refresh_catalog._reshape_model(raw)
        assert out is not None
        assert out["id"] == "x/y"
        assert len(out["description"]) == 200  # truncated
        assert out["context_length"] == 1024
        assert out["is_free"] is False
        assert out["pricing"]["prompt_per_million"] == 1.0
        assert out["pricing"]["completion_per_million"] == 2.0
        assert out["input_modalities"] == ["text"]

    def test_reshape_model_returns_none_when_id_missing(self) -> None:
        assert refresh_catalog._reshape_model({"name": "no-id"}) is None
        assert refresh_catalog._reshape_model({"id": "  "}) is None

    def test_reshape_model_detects_free_via_isFree_flag(self) -> None:
        out = refresh_catalog._reshape_model({"id": "a/b", "isFree": True, "pricing": {}})
        assert out is not None and out["is_free"] is True

    def test_reshape_model_detects_free_via_zero_pricing(self) -> None:
        out = refresh_catalog._reshape_model({
            "id": "a/b", "pricing": {"prompt": "0", "completion": "0"},
        })
        assert out is not None and out["is_free"] is True

    def test_reshape_model_handles_missing_pricing_keys(self) -> None:
        out = refresh_catalog._reshape_model({"id": "a/b", "pricing": {}})
        assert out is not None
        assert out["pricing"] == {
            "prompt_per_million": 0.0,
            "completion_per_million": 0.0,
        }

    def test_build_manifest_rejects_unexpected_shape(self) -> None:
        with pytest.raises(ValueError, match="unexpected"):
            refresh_catalog._build_manifest({"unexpected": True})
        with pytest.raises(ValueError, match="unexpected"):
            refresh_catalog._build_manifest({"data": "not-a-list"})

    def test_build_manifest_sort_order_free_first_then_price(self) -> None:
        payload = {"data": [
            {"id": "z/expensive", "pricing": {"prompt": "0.00001", "completion": "0.00001"}},
            {"id": "a/free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "m/cheap", "pricing": {"prompt": "0.000001", "completion": "0.000001"}},
        ]}
        manifest = refresh_catalog._build_manifest(payload)
        ids = [m["id"] for m in manifest["providers"]["kilocode"]["models"]]
        assert ids == ["a/free", "m/cheap", "z/expensive"]

    def test_build_manifest_wraps_in_selected_gateway_provider(self) -> None:
        payload = {"data": [{"id": "x/y"}]}
        manifest = refresh_catalog._build_manifest(payload, gateway="kilocode")
        assert manifest["version"] == refresh_catalog.MANIFEST_VERSION
        assert manifest["source"] == "kilocode-gateway-api"
        assert "kilocode" in manifest["providers"]
        assert manifest["providers"]["kilocode"]["metadata"]["display_name"] == "Kilo Code"

    def test_unsupported_gateway_is_config_error(self) -> None:
        with pytest.raises(refresh_catalog.CatalogError) as ei:
            refresh_catalog._build_manifest({"data": []}, gateway="unknown")
        assert ei.value.kind == "config"

    def test_reshape_catalog_writes_atomically(self, tmp_path: Path) -> None:
        src = _payload_file(tmp_path)
        dst = tmp_path / "out.json"
        manifest = refresh_catalog.reshape_catalog(src, dst)
        assert dst.exists()
        # Atomic: no leftover tmp files in dst.parent
        leftovers = [p for p in tmp_path.iterdir()
                     if p.name.startswith(dst.name + ".") and p.suffix == ".tmp"]
        assert leftovers == []
        loaded = json.loads(dst.read_text())
        assert loaded["version"] == manifest["version"]
        assert len(loaded["providers"]["kilocode"]["models"]) == 3  # garbage skipped

    def test_reshape_catalog_creates_parent_dirs(self, tmp_path: Path) -> None:
        src = _payload_file(tmp_path)
        dst = tmp_path / "deep" / "nested" / "out.json"
        refresh_catalog.reshape_catalog(src, dst)
        assert dst.exists()

    def test_reshape_catalog_overwrites_existing(self, tmp_path: Path) -> None:
        src = _payload_file(tmp_path)
        dst = tmp_path / "out.json"
        dst.write_text("stale content")
        refresh_catalog.reshape_catalog(src, dst)
        loaded = json.loads(dst.read_text())
        assert loaded["version"] == refresh_catalog.MANIFEST_VERSION


# ---------- TestCredentialDiscovery ----------

class TestCredentialDiscovery:
    def test_env_var_wins(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "from-env")
        # Even an .env file with a different value should lose.
        (tmp_path / ".hermes").mkdir()
        env_file = tmp_path / ".hermes" / ".env"
        env_file.write_text('KILOCODE_API_KEY="from-file"\n')
        monkeypatch.setattr(refresh_catalog, "Path", lambda *a, **kw: tmp_path / ".hermes" / ".env"
                            if (a and str(a[-1]).endswith(".env")) else Path(*a, **kw))
        # Simpler: just patch Path.home() + Path resolution
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert refresh_catalog._discover_api_key() == "from-env"

    def test_env_file_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("KILOCODE_API_KEY", raising=False)
        (tmp_path / ".hermes").mkdir()
        (tmp_path / ".hermes" / ".env").write_text(
            'OTHER_VAR=ignored\nKILOCODE_API_KEY="from-file"\n'
            'KILOCODE_API_KEY_OTHER=should-not-match\n'
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert refresh_catalog._discover_api_key() == "from-file"

    def test_env_file_unquoted(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("KILOCODE_API_KEY", raising=False)
        (tmp_path / ".hermes").mkdir()
        (tmp_path / ".hermes" / ".env").write_text("KILOCODE_API_KEY=plainvalue\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert refresh_catalog._discover_api_key() == "plainvalue"

    def test_env_file_export_prefix(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("KILOCODE_API_KEY", raising=False)
        (tmp_path / ".hermes").mkdir()
        (tmp_path / ".hermes" / ".env").write_text("export KILOCODE_API_KEY=exported-value\n")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert refresh_catalog._discover_api_key() == "exported-value"

    def test_returns_none_when_no_credential(self, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
        monkeypatch.delenv("KILOCODE_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert refresh_catalog._discover_api_key() is None


# ---------- TestCacheFreshness ----------

class TestCacheFreshness:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert refresh_catalog.cache_age_seconds(tmp_path / "missing.json") is None

    def test_age_of_fresh_file_is_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "fresh.json"
        p.write_text("{}")
        age = refresh_catalog.cache_age_seconds(p)
        assert age is not None and age < 5

    def test_is_cache_fresh_within_window(self, tmp_path: Path) -> None:
        p = tmp_path / "fresh.json"
        p.write_text("{}")
        assert refresh_catalog.is_cache_fresh(p, max_age_seconds=3600) is True

    def test_is_cache_fresh_outside_window(self, tmp_path: Path) -> None:
        p = tmp_path / "stale.json"
        p.write_text("{}")
        # mtime = now-1h; max_age = 10s -> stale
        old = time.time() - 3600
        os.utime(p, (old, old))
        assert refresh_catalog.is_cache_fresh(p, max_age_seconds=10) is False

    def test_is_cache_fresh_missing(self, tmp_path: Path) -> None:
        assert refresh_catalog.is_cache_fresh(tmp_path / "missing.json") is False


# ---------- TestFetch ----------

class TestFetch:
    def test_missing_api_key_raises_auth(self, monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
        monkeypatch.delenv("KILOCODE_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with pytest.raises(refresh_catalog.CatalogError) as ei:
            refresh_catalog.fetch_catalog()
        assert ei.value.kind == "auth"

    def test_successful_fetch_returns_200_and_payload(self, monkeypatch: pytest.MonkeyPatch,
                                                       tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        body = json.dumps(_sample_payload()).encode()
        monkeypatch.setattr(
            urllib_request, "urlopen",
            lambda req, timeout=None: _StubResponse(body, status=200),
        )
        code, path = refresh_catalog.fetch_catalog()
        assert code == 200
        assert json.loads(path.read_bytes()) == _sample_payload()
        path.unlink()

    def test_http_error_returns_code_and_drains_body(self, monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")

        def fake_urlopen(req, timeout=None):
            raise urllib_error.HTTPError(
                "https://api.kilo.ai/api/gateway/models", 401,
                "Unauthorized", Message(), BytesIO(b'{"error":"bad key"}'),
            )

        monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)
        code, path = refresh_catalog.fetch_catalog()
        assert code == 401
        assert path.read_bytes() == b'{"error":"bad key"}'
        path.unlink()

    def test_network_error_raises_catalog_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")

        def fake_urlopen(req, timeout=None):
            raise urllib_error.URLError("dns failure")

        monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)
        with pytest.raises(refresh_catalog.CatalogError) as ei:
            refresh_catalog.fetch_catalog()
        assert ei.value.kind == "network"

    def test_timeout_raises_catalog_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")

        def fake_urlopen(req, timeout=None):
            raise TimeoutError("simulated timeout")

        monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)
        with pytest.raises(refresh_catalog.CatalogError) as ei:
            refresh_catalog.fetch_catalog()
        assert ei.value.kind == "network"


# ---------- TestRun ----------

class TestRun:
    def _paths(self, tmp_path: Path) -> ResolvedPaths:
        return ResolvedPaths(
            profile="test", hermes_root=tmp_path,
            state_db=tmp_path / "state.db",
            log_dir=tmp_path / "logs",
        )

    def _stub_urlopen_factory(self, monkeypatch: pytest.MonkeyPatch, body: bytes, status: int):
        def fake_urlopen(req, timeout=None):
            if status >= 400:
                raise urllib_error.HTTPError(req.full_url, status, "err", Message(), BytesIO(body))
            return _StubResponse(body, status=status)
        monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)

    def test_skip_when_cache_fresh(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        dst = tmp_path / "kilocode_catalog.json"
        dst.write_text(json.dumps({"version": 1, "providers": {
            "kilocode": {"models": [{"id": "cached/x"}]}}
        }))
        # urlopen should NOT be called — fetch is skipped
        def boom(*a, **kw): raise AssertionError("urlopen called on fresh cache")
        monkeypatch.setattr(urllib_request, "urlopen", boom)

        report = refresh_catalog.run(self._paths(tmp_path), dst=dst)
        assert report["ok"] is True
        assert report["skipped"] is True
        assert report["reason"] == "fresh"
        assert report["model_count"] == 1

    def test_force_refetches_even_when_fresh(self, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        dst = tmp_path / "kilocode_catalog.json"
        dst.write_text(json.dumps({"providers": {"kilocode": {"models": []}}}))
        body = json.dumps(_sample_payload()).encode()
        self._stub_urlopen_factory(monkeypatch, body, status=200)

        report = refresh_catalog.run(self._paths(tmp_path), dst=dst, force=True, gateway="kilocode")
        assert report["ok"] is True
        assert report["gateway"] == "kilocode"
        assert report["provider_id"] == "kilocode"
        assert report["skipped"] is False
        assert report["reason"] == "refreshed"
        assert report["http_code"] == 200
        assert report["model_count"] == 3  # garbage rows skipped

    def test_full_refresh_when_cache_missing(self, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        body = json.dumps(_sample_payload()).encode()
        self._stub_urlopen_factory(monkeypatch, body, status=200)

        dst = tmp_path / "kilocode_catalog.json"
        report = refresh_catalog.run(self._paths(tmp_path), dst=dst)
        assert report["ok"] is True
        assert report["reason"] == "refreshed"
        assert dst.exists()
        # Re-read and confirm the manifest is valid
        loaded = json.loads(dst.read_text())
        assert loaded["version"] == refresh_catalog.MANIFEST_VERSION

    def test_stale_cache_falls_through_to_real_refresh(self, monkeypatch: pytest.MonkeyPatch,
                                                       tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        dst = tmp_path / "kilocode_catalog.json"
        dst.write_text("not-json")
        old = time.time() - 24 * 3600
        os.utime(dst, (old, old))
        body = json.dumps(_sample_payload()).encode()
        self._stub_urlopen_factory(monkeypatch, body, status=200)

        report = refresh_catalog.run(self._paths(tmp_path), dst=dst)
        assert report["ok"] is True
        assert report["reason"] == "refreshed"

    def test_auth_error_returns_reason(self, monkeypatch: pytest.MonkeyPatch,
                                         tmp_path: Path) -> None:
        monkeypatch.delenv("KILOCODE_API_KEY", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        report = refresh_catalog.run(self._paths(tmp_path), dst=tmp_path / "out.json")
        assert report["ok"] is False
        assert report["reason"] == "auth"
        assert report["http_code"] is None

    def test_http_error_returns_network_reason(self, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        self._stub_urlopen_factory(monkeypatch, b'{"error":"server"}', status=503)

        report = refresh_catalog.run(self._paths(tmp_path), dst=tmp_path / "out.json")
        assert report["ok"] is False
        assert report["reason"] == "network"
        assert report["http_code"] == 503

    def test_parse_error_returns_parse_reason(self, monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        self._stub_urlopen_factory(monkeypatch, b'{"not_data": []}', status=200)

        report = refresh_catalog.run(self._paths(tmp_path), dst=tmp_path / "out.json")
        assert report["ok"] is False
        assert report["reason"] == "parse"
        assert report["http_code"] == 200

    def test_write_error_returns_write_reason(self, monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        body = json.dumps(_sample_payload()).encode()
        self._stub_urlopen_factory(monkeypatch, body, status=200)

        # dst.parent does not exist and cannot be created (path is a file).
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        dst = blocker / "kilocode_catalog.json"  # cannot create dir under a file

        report = refresh_catalog.run(self._paths(tmp_path), dst=dst)
        assert report["ok"] is False
        assert report["reason"] == "write"

    def test_temp_file_cleaned_up_after_parse_error(self, monkeypatch: pytest.MonkeyPatch,
                                                     tmp_path: Path) -> None:
        monkeypatch.setenv("KILOCODE_API_KEY", "test-key")
        self._stub_urlopen_factory(monkeypatch, b'{"not_data": []}', status=200)

        before = set(tmp_path.iterdir())
        refresh_catalog.run(self._paths(tmp_path), dst=tmp_path / "out.json")
        after = set(tmp_path.iterdir())
        # No leftover kilocode_catalog.*.json temp files
        leftover = [p.name for p in after - before if p.name.startswith("kilocode_catalog.")]
        assert leftover == []


# ---------- TestRenderer ----------

class TestRenderer:
    def _report(self, **overrides) -> dict:
        base = {
            "ok": True, "skipped": False, "reason": "refreshed",
            "http_code": 200, "cache_path": "/tmp/c.json",
            "source_url": "https://x", "model_count": 42, "manifest": None,
        }
        base.update(overrides)
        return base

    def test_clean_refreshed(self) -> None:
        code, text = refresh_catalog.render_human(self._report())
        assert code == 0
        assert "VERDICT: clean" in text
        assert "42 models" in text

    def test_skipped_when_fresh(self) -> None:
        code, text = refresh_catalog.render_human(self._report(
            ok=True, skipped=True, reason="fresh", model_count=7,
        ))
        assert code == 0
        assert "skipped refetch" in text
        assert "--force" in text

    def test_auth_error_exits_2_with_hint(self) -> None:
        code, text = refresh_catalog.render_human(self._report(
            ok=False, reason="auth",
        ))
        assert code == 2
        assert "auth" in text
        assert "KILOCODE_API_KEY" in text

    def test_network_error_exits_2_with_hint(self) -> None:
        code, text = refresh_catalog.render_human(self._report(
            ok=False, reason="network",
        ))
        assert code == 2
        assert "network" in text

    def test_parse_error_exits_2_with_hint(self) -> None:
        code, text = refresh_catalog.render_human(self._report(
            ok=False, reason="parse",
        ))
        assert code == 2
        assert "parse" in text

    def test_write_error_exits_2_with_hint(self) -> None:
        code, text = refresh_catalog.render_human(self._report(
            ok=False, reason="write",
        ))
        assert code == 2
        assert "write" in text


# ---------- TestCli ----------

class TestCli:
    """End-to-end CLI tests via subprocess — proves the entry point works."""

    def test_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "refresh-catalog", "--help"],
            capture_output=True, text=True, cwd="/home/raphael/src/rb/talaria-of-mercury",
        )
        assert result.returncode == 0
        assert "kilocode" in result.stdout.lower()

    def test_show_resolution_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "refresh-catalog",
             "--dst", "/tmp/x.json", "--show-resolution"],
            capture_output=True, text=True, cwd="/home/raphael/src/rb/talaria-of-mercury",
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["cache_path"] == "/tmp/x.json"
        assert payload["gateway"] == "kilocode"
        assert payload["provider_id"] == "kilocode"
        assert payload["supported_gateways"] == ["kilocode"]
        assert "kilo.ai" in payload["source_url"]

    def test_kilo_gateway_name_is_rejected(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "refresh-catalog",
             "--gateway", "kilo", "--show-resolution"],
            capture_output=True, text=True, cwd="/home/raphael/src/rb/talaria-of-mercury",
        )
        assert result.returncode == 2
        assert "invalid choice" in result.stderr

    def test_auth_failure_exits_2(self, monkeypatch: pytest.MonkeyPatch,
                                   tmp_path: Path) -> None:
        # No KILOCODE_API_KEY in env -> auth error -> exit 2.
        env = os.environ.copy()
        env.pop("KILOCODE_API_KEY", None)
        env["HOME"] = str(tmp_path)  # no ~/.hermes/.env either
        result = subprocess.run(
            [sys.executable, "-m", "talaria.cli", "hermes", "refresh-catalog",
             "--dst", str(tmp_path / "out.json"), "--json"],
            capture_output=True, text=True,
            env=env, cwd="/home/raphael/src/rb/talaria-of-mercury",
        )
        assert result.returncode == 2
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["reason"] == "auth"