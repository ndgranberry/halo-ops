"""Tests for run_daily hardening — the stuff that bit us on 2026-04-16.

Specifically:
- Timeout leaves a failure-marker JSON (not a silent drop).
- Webhook retry helper backs off and returns None on exhaustion.
"""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import requests

import run_daily


def _fake_settings(**overrides):
    """Build a mutable stand-in for the frozen Settings dataclass."""
    base = {
        "webhook_max_retries": 3,
        "webhook_backoff_seconds": 0,
        "sheets_dedup": True,
        "per_request_timeout": 1800,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_timeout_writes_failure_marker(tmp_path, monkeypatch):
    """A subprocess timeout must leave a marker file for traceability."""
    monkeypatch.setattr(run_daily, "LOG_DIR", tmp_path)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1, output=b"partial log")

    monkeypatch.setattr(run_daily.subprocess, "run", fake_run)

    out = run_daily.run_pipeline(9999)

    assert out["error_kind"] == "timeout"
    marker = tmp_path / "stdout_9999_FAILED.json"
    assert marker.exists(), "timeout must leave a failure marker"
    data = json.loads(marker.read_text())
    assert data["request_id"] == 9999
    assert "timeout" in data["reason"]


def test_post_with_retry_exhausts_and_returns_none(monkeypatch):
    """Network errors trigger retry; after max_retries, returns None."""
    monkeypatch.setattr(run_daily.time, "sleep", lambda *_: None)
    monkeypatch.setattr(run_daily, "settings", _fake_settings(webhook_max_retries=3))

    call_count = {"n": 0}

    def always_fail(*a, **kw):
        call_count["n"] += 1
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(run_daily.requests, "post", always_fail)
    resp = run_daily._post_with_retry(
        "http://example.invalid", {"x": 1}, label="test"
    )
    assert resp is None
    assert call_count["n"] == 3


def test_post_with_retry_retries_5xx_then_succeeds(monkeypatch):
    """5xx should retry; success on second attempt returns the response."""
    monkeypatch.setattr(run_daily.time, "sleep", lambda *_: None)
    monkeypatch.setattr(run_daily, "settings", _fake_settings(webhook_max_retries=3))

    call_count = {"n": 0}

    def flaky(*a, **kw):
        call_count["n"] += 1
        resp = MagicMock()
        resp.status_code = 500 if call_count["n"] == 1 else 200
        resp.text = ""
        return resp

    monkeypatch.setattr(run_daily.requests, "post", flaky)
    resp = run_daily._post_with_retry(
        "http://example.invalid", {"x": 1}, label="test"
    )
    assert resp is not None
    assert resp.status_code == 200
    assert call_count["n"] == 2


def test_post_with_retry_skips_when_no_url(monkeypatch):
    """Empty URL short-circuits — no retry, no error."""
    resp = run_daily._post_with_retry("", {}, label="test")
    assert resp is None
