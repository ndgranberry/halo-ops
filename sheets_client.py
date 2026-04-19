#!/usr/bin/env python3
"""
RoboScout — shared gspread client factory.

Three call sites were building identical gspread clients from scratch
(run_daily.py::_get_gspread_client, output_formatter.py::write_sheets,
and monitoring/feedback_sheet.py via injected sheet). This module
centralizes:

- service-account credentials loading (with a clear error when the path
  is unset or missing)
- scopes
- a lightweight per-process cache so repeated calls reuse one client

We do NOT cache Spreadsheet handles — they're cheap and caching them
across a long-running batch masks auth-token expiry issues.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("roboscout_query_gen.sheets_client")

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_cached_client = None


class SheetsAuthError(RuntimeError):
    """Raised when we can't authenticate to Google Sheets."""


def get_client(*, refresh: bool = False):
    """Return an authorized gspread client. Cached per-process.

    ``refresh=True`` forces re-authentication (useful if the caller
    suspects the token has expired or the service-account file has been
    rotated).
    """
    global _cached_client
    if _cached_client is not None and not refresh:
        return _cached_client

    creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_path:
        raise SheetsAuthError(
            "GOOGLE_SERVICE_ACCOUNT_JSON not set. "
            "Point it to your service account JSON file."
        )
    if not os.path.exists(creds_path):
        raise SheetsAuthError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON points to a missing file: {creds_path}"
        )

    # Lazy imports so this module stays cheap to import in code paths
    # (like tests) that never touch Sheets.
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
    _cached_client = gspread.authorize(creds)
    return _cached_client


def open_sheet(url: str):
    """Shortcut: open a Spreadsheet by URL using the cached client."""
    return get_client().open_by_url(url)


def reset_cache() -> None:
    """Drop the cached client. Mainly for tests."""
    global _cached_client
    _cached_client = None
