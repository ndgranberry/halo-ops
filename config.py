#!/usr/bin/env python3
"""
RoboScout — Shared configuration

Single source of truth for:
- .env loading (previously duplicated between roboscout_query_gen.py and run_daily.py)
- Environment variable validation (fast-fail with clear messages)
- Tunable pipeline constants (model, thresholds, timeouts)

Usage:
    from config import settings, load_env, require

    load_env()                # idempotent — safe to call multiple times
    require("ANTHROPIC_API_KEY", "SNOWFLAKE_PASSWORD")  # raises ConfigError if missing
    model = settings.default_model
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

logger = logging.getLogger("roboscout_query_gen.config")

PROJECT_DIR = Path(__file__).parent

_ENV_SEARCH_PATHS = [
    PROJECT_DIR / ".env",
    PROJECT_DIR / "config" / ".env",
    PROJECT_DIR.parent / ".env",
    PROJECT_DIR.parent.parent / ".env",
]

_env_loaded = False


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def load_env(force: bool = False) -> Path | None:
    """Load environment variables from the first .env found in the search path.

    Idempotent by default — repeated calls are no-ops unless ``force=True``.
    Returns the path loaded, or None if no .env was found (falls back to
    process environment).
    """
    global _env_loaded
    if _env_loaded and not force:
        return None

    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("python-dotenv not installed — relying on process env")
        _env_loaded = True
        return None

    for path in _ENV_SEARCH_PATHS:
        if path.exists():
            load_dotenv(path, override=True)
            _env_loaded = True
            return path

    load_dotenv()  # fall back to CWD
    _env_loaded = True
    return None


def require(*keys: str) -> None:
    """Raise ConfigError if any of ``keys`` is missing or empty in the env."""
    load_env()
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        raise ConfigError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Check config/.env — see config/.env.example for the full list."
        )


@dataclass(frozen=True)
class Settings:
    """Pipeline-tunable constants. Override via environment where sensible."""

    # Model
    default_model: str = field(
        default_factory=lambda: os.getenv(
            "ROBOSCOUT_MODEL", "claude-sonnet-4-20250514"
        )
    )
    # Temperature for LLM calls (0.3 = deterministic but creative)
    lm_temperature: float = field(
        default_factory=lambda: float(os.getenv("ROBOSCOUT_LM_TEMPERATURE", "0.3"))
    )
    lm_max_tokens: int = field(
        default_factory=lambda: int(os.getenv("ROBOSCOUT_LM_MAX_TOKENS", "4096"))
    )

    # Validation thresholds
    max_refinement_rounds: int = field(
        default_factory=lambda: int(os.getenv("ROBOSCOUT_MAX_REFINEMENT_ROUNDS", "2"))
    )
    relevance_threshold: float = field(
        default_factory=lambda: float(os.getenv("ROBOSCOUT_RELEVANCE_THRESHOLD", "0.6"))
    )
    papers_to_check: int = field(
        default_factory=lambda: int(os.getenv("ROBOSCOUT_PAPERS_TO_CHECK", "20"))
    )

    # run_daily timeouts (seconds)
    per_request_timeout: int = field(
        default_factory=lambda: int(os.getenv("ROBOSCOUT_PER_REQUEST_TIMEOUT", "1800"))
    )
    find_new_timeout: int = field(
        default_factory=lambda: int(os.getenv("ROBOSCOUT_FIND_NEW_TIMEOUT", "120"))
    )

    # Webhooks (max retries for transient failures)
    webhook_max_retries: int = field(
        default_factory=lambda: int(os.getenv("ROBOSCOUT_WEBHOOK_MAX_RETRIES", "3"))
    )
    webhook_backoff_seconds: float = field(
        default_factory=lambda: float(os.getenv("ROBOSCOUT_WEBHOOK_BACKOFF", "2.0"))
    )

    # Logging
    log_json: bool = field(
        default_factory=lambda: os.getenv("ROBOSCOUT_LOG_JSON", "").lower()
        in ("1", "true", "yes")
    )
    log_level: str = field(
        default_factory=lambda: os.getenv("ROBOSCOUT_LOG_LEVEL", "INFO").upper()
    )

    # Excluded companies for auto-discovery
    # Comma-separated IDs; defaults to internal/test companies.
    #   2825 = Halo Science internal
    #   1669 = QA test company
    excluded_company_ids: List[int] = field(
        default_factory=lambda: _parse_int_list(
            os.getenv("ROBOSCOUT_EXCLUDED_COMPANY_IDS", "2825,1669")
        )
    )

    # Google Sheets
    sheet_url: str = field(
        default_factory=lambda: os.getenv(
            "ROBOSCOUT_SHEET_URL",
            "https://docs.google.com/spreadsheets/d/1MvQXMXLyyNMs2bfWg1JsSRLBfOGVF7Z5nPlj9fED-bU",
        )
    )
    # When True, run_daily deletes existing rows for a request_id before
    # appending new ones (prevents duplicate rows from re-runs).
    sheets_dedup: bool = field(
        default_factory=lambda: os.getenv("ROBOSCOUT_SHEETS_DEDUP", "true").lower()
        in ("1", "true", "yes")
    )


def _parse_int_list(raw: str) -> List[int]:
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                logger.warning("Skipping non-int excluded id: %r", part)
    return out


# Load env and freeze settings at import time so downstream modules see a
# consistent view. Import-time load mirrors previous behavior.
load_env()
settings = Settings()


# Required-keys groups (for `require()` calls)
REQUIRED_ALWAYS = ("ANTHROPIC_API_KEY",)
REQUIRED_SNOWFLAKE = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")
REQUIRED_SHEETS = ("GOOGLE_SERVICE_ACCOUNT_JSON",)


def validate_for(modes: Iterable[str]) -> None:
    """Validate env for one or more modes: 'llm', 'snowflake', 'sheets'.

    Raises ConfigError listing ALL missing vars (not just the first), so
    users fix them in one pass.
    """
    needed: List[str] = []
    for mode in modes:
        if mode == "llm":
            needed.extend(REQUIRED_ALWAYS)
        elif mode == "snowflake":
            needed.extend(REQUIRED_SNOWFLAKE)
        elif mode == "sheets":
            needed.extend(REQUIRED_SHEETS)
        else:
            raise ValueError(f"Unknown validation mode: {mode}")
    require(*needed)
