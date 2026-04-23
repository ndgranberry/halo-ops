"""Tests for config loading and env validation."""


import pytest

from roboscout.config import (
    ConfigError,
    Settings,
    _parse_int_list,
    require,
    validate_for,
)


def test_parse_int_list_handles_whitespace_and_empty():
    assert _parse_int_list("1, 2 , 3") == [1, 2, 3]
    assert _parse_int_list("") == []
    assert _parse_int_list("  ") == []


def test_parse_int_list_skips_non_ints(caplog):
    with caplog.at_level("WARNING"):
        out = _parse_int_list("1, banana, 3")
    assert out == [1, 3]
    assert any("banana" in r.message for r in caplog.records)


def test_settings_respects_env(monkeypatch):
    monkeypatch.setenv("ROBOSCOUT_MAX_REFINEMENT_ROUNDS", "7")
    monkeypatch.setenv("ROBOSCOUT_EXCLUDED_COMPANY_IDS", "10,20,30")
    # Re-instantiate to pick up the new env.
    s = Settings()
    assert s.max_refinement_rounds == 7
    assert s.excluded_company_ids == [10, 20, 30]


def test_require_raises_on_missing(monkeypatch):
    monkeypatch.delenv("ROBOSCOUT_FAKE_KEY_DOES_NOT_EXIST", raising=False)
    with pytest.raises(ConfigError) as exc:
        require("ROBOSCOUT_FAKE_KEY_DOES_NOT_EXIST")
    assert "ROBOSCOUT_FAKE_KEY_DOES_NOT_EXIST" in str(exc.value)


def test_validate_for_groups_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)
    with pytest.raises(ConfigError) as exc:
        validate_for(["llm", "snowflake"])
    msg = str(exc.value)
    # Must list BOTH missing vars so user fixes in one pass.
    assert "ANTHROPIC_API_KEY" in msg
    assert "SNOWFLAKE_PASSWORD" in msg
