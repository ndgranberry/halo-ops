#!/usr/bin/env python3
"""
RoboScout Optimization — Automated Weekly Optimizer
======================================================
Scheduled weekly (via LaunchAgent). Checks for new human feedback,
runs GEPA optimization if enough data has accumulated, and notifies
the agent manager via Slack.

Usage:
    python -m optimization.auto_optimize

Lifecycle:
1. Check if enough new feedback (minimum 10 entries)
2. Ingest feedback from Google Sheet → training data
3. Run GEPA optimization with auto="light" (fast, low cost)
4. Compare new vs. current prompt scores
5. If improved by >5%, save as candidate and notify via Slack
6. Agent manager approves/rejects in Feedback tab
7. Next daily run picks up approved prompt
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Load .env
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent.parent
for _env_path in [
    PROJECT_DIR / ".env",
    PROJECT_DIR / "config" / ".env",
    PROJECT_DIR.parent / ".env",
    PROJECT_DIR.parent.parent / ".env",
]:
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
        break
else:
    load_dotenv()

import requests as http_requests

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            LOG_DIR / f"auto_optimize_{datetime.now().strftime('%Y%m%d')}.log"
        ),
    ],
)
logger = logging.getLogger("roboscout_optimization.auto")

SHEET_URL = os.getenv(
    "ROBOSCOUT_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1MvQXMXLyyNMs2bfWg1JsSRLBfOGVF7Z5nPlj9fED-bU",
)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

MIN_FEEDBACK_ENTRIES = 10  # Minimum new feedback before running optimization
IMPROVEMENT_THRESHOLD = 0.05  # 5% improvement required to save candidate


def _get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials

    creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_path:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON not set")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def _send_slack(message: str):
    if not SLACK_WEBHOOK_URL:
        logger.info(f"Slack (not sent): {message[:100]}")
        return

    try:
        http_requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": message, "unfurl_links": False},
            timeout=30,
        )
    except Exception as e:
        logger.error(f"Slack notification failed: {e}")


def main():
    logger.info("=== RoboScout Auto-Optimize ===")

    # Step 1: Connect to Google Sheet and ingest feedback
    try:
        gc = _get_gspread_client()
        sh = gc.open_by_url(SHEET_URL)
    except Exception as e:
        logger.error(f"Cannot open Google Sheet: {e}")
        return

    from monitoring.feedback_sheet import FeedbackSheet
    feedback = FeedbackSheet(sh)

    positive, negative, run_feedback = feedback.ingest_feedback()
    total_new = len(positive) + len(negative) + len(run_feedback)

    if total_new < MIN_FEEDBACK_ENTRIES:
        logger.info(
            f"Only {total_new} new feedback entries "
            f"(need {MIN_FEEDBACK_ENTRIES}). Skipping optimization."
        )
        return

    # Save training data
    feedback.save_training_data(positive, negative, run_feedback)
    logger.info(f"Ingested {total_new} feedback entries "
                f"({len(positive)} positive, {len(negative)} negative, "
                f"{len(run_feedback)} run-level)")

    # Step 2: Run GEPA optimization
    from optimization.optimize import run_optimization

    logger.info("Running GEPA optimization (budget=light)...")
    results = run_optimization(budget="light", save_as_candidate=True)

    if results.get("status") != "success":
        logger.warning(f"Optimization did not succeed: {results}")
        _send_slack(
            f":warning: *RoboScout auto-optimization failed*\n"
            f"Status: {results.get('status', 'unknown')}\n"
            f"Check logs for details."
        )
        return

    # Step 3: Notify and record candidate
    best_score = results.get("best_val_score", "unknown")
    candidate_path = results.get("output_path", "")

    # Add candidate marker to Feedback tab for approval
    try:
        ws = sh.worksheet("Feedback")
        ws.append_rows([[
            "",                          # Row Type (not a RUN row)
            datetime.now().strftime("%Y-%m-%d"),  # Date
            "",                          # Request ID
            "",                          # Request Title
            "__PROMPT_CANDIDATE__",      # Query (marker)
            candidate_path,              # SOI (path)
            str(best_score),             # Result Count (score)
            "",                          # Category
            "",                          # Rating
            "",                          # Notes — manager writes "approved"/"rejected"
            "",                          # Suggested Query
            f"candidate_{datetime.now().strftime('%Y%m%d')}",  # Prompt Version
            "",                          # Processed
        ]], value_input_option="USER_ENTERED")
        logger.info("Added prompt candidate marker to Feedback tab")
    except Exception as e:
        logger.warning(f"Could not add candidate marker to Feedback tab: {e}")

    _send_slack(
        f":brain: *RoboScout prompt optimization complete*\n"
        f"  - New feedback processed: {total_new} entries\n"
        f"  - Best validation score: {best_score}\n"
        f"  - Candidate saved to: `{candidate_path}`\n\n"
        f"To approve: open the Feedback tab in the "
        f"<{SHEET_URL}|Google Sheet>, find the `__PROMPT_CANDIDATE__` row, "
        f"and type `approved` in the Notes column.\n"
        f"The next daily run will pick up the approved prompt automatically."
    )

    logger.info("=== Auto-optimize complete ===")


if __name__ == "__main__":
    main()
