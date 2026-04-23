"""Tests for run_daily hardening — the stuff that bit us on 2026-04-16.

Specifically:
- Timeout leaves a failure-marker JSON (not a silent drop).
"""

import json
import subprocess

import roboscout.run_daily as run_daily


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
