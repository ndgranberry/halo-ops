#!/usr/bin/env python3
"""
RoboScout Daily Runner
=======================
Runs locally on any team member's machine (or via macOS LaunchAgent).
1. Finds new requests from Snowflake
2. Runs the query generation pipeline for each
3. Appends results directly to Google Sheets
4. Sends Slack notification

No n8n required — everything runs locally.

Usage:
    # Auto-discover new requests from last 24h
    python run_daily.py

    # Custom lookback window
    python run_daily.py --hours 48

    # Run for specific request IDs
    python run_daily.py --request-ids 1597 1600 1582

    # Dry run (pipeline only, skip Sheets + Slack)
    python run_daily.py --dry-run

    # Skip Slack notification only
    python run_daily.py --no-slack
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Load .env before anything else
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent
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

import requests

# --- Config ---
PYTHON = sys.executable

# Google Sheet for query results (append mode)
SHEET_URL = os.getenv(
    "ROBOSCOUT_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1MvQXMXLyyNMs2bfWg1JsSRLBfOGVF7Z5nPlj9fED-bU",
)

# Slack webhook for notifications
# Create one at: https://api.slack.com/messaging/webhooks
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# n8n webhook for Google Sheets population
# When set, pipeline results are POSTed to this URL instead of writing to Sheets directly
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            LOG_DIR / f"daily_{datetime.now().strftime('%Y%m%d')}.log"
        ),
    ],
)
logger = logging.getLogger("roboscout_daily")


# =============================================================================
# Pipeline execution (subprocess calls)
# =============================================================================

def find_new_requests(hours: int = 24) -> dict:
    """Run --find-new to discover new requests from Snowflake."""
    logger.info(f"Finding new requests (last {hours}h)...")
    result = subprocess.run(
        [PYTHON, "roboscout_query_gen.py", "--find-new",
         "--hours", str(hours), "--output-json"],
        capture_output=True, text=True, cwd=PROJECT_DIR,
        timeout=120,
    )
    if result.returncode != 0:
        logger.error(f"find-new failed: {result.stderr}")
        return {"count": 0, "requests": []}

    return json.loads(result.stdout)


def run_pipeline(request_id: int) -> dict:
    """Run the query generation pipeline for a single request.

    Saves per-request JSON and log files under logs/ for traceability.
    """
    logger.info(f"Running pipeline for request #{request_id}...")
    json_path = LOG_DIR / f"stdout_{request_id}_v6.json"
    log_path = LOG_DIR / f"run_{request_id}.log"

    try:
        result = subprocess.run(
            [PYTHON, "roboscout_query_gen.py",
             "--request-id", str(request_id),
             "--output-json", str(json_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=PROJECT_DIR,
            timeout=1800,  # 30 min max per request
        )
        try:
            log_path.write_text(result.stdout)
        except Exception as e:
            logger.warning(f"Could not write log for #{request_id}: {e}")
    except subprocess.TimeoutExpired:
        logger.error(f"Pipeline timed out for #{request_id} (30 min limit)")
        return {"error": "Pipeline timed out after 30 minutes", "request_id": request_id}

    if result.returncode != 0:
        logger.error(f"Pipeline failed for #{request_id}: {result.stdout[-500:]}")
        return {"error": result.stdout[-1000:], "request_id": request_id}

    # Read from the JSON file the pipeline wrote
    try:
        with open(json_path) as f:
            return {"pipeline_output": json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Bad/missing JSON from pipeline #{request_id}: {e}")
        # Fallback: try parsing stdout (old behavior)
        try:
            return {"pipeline_output": json.loads(result.stdout)}
        except Exception:
            return {"error": str(e), "request_id": request_id}


# =============================================================================
# Google Sheets — append results
# =============================================================================

def _get_gspread_client():
    """Create authenticated gspread client."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_path:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON not set. "
            "Point it to your service account JSON file."
        )

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    return gspread.authorize(creds)


def append_to_sheets(all_results: list) -> bool:
    """Append pipeline results to Google Sheets (Queries, Coverage, Run Metadata tabs).

    Each result in all_results has:
      - request_id, title, company
      - pipeline_output: full JSON from roboscout_query_gen.py --output-json
    """
    if not SHEET_URL:
        logger.warning("ROBOSCOUT_SHEET_URL not set — skipping Sheets")
        return False

    try:
        gc = _get_gspread_client()
        sh = gc.open_by_url(SHEET_URL)
    except Exception as e:
        logger.error(f"Failed to open Google Sheet: {e}")
        return False

    success = True

    for result in all_results:
        if "error" in result:
            continue  # Skip failed pipelines

        output = result["pipeline_output"]
        rid = result.get("request_id", output.get("request_id", ""))
        title = result.get("title", output.get("request_title", ""))
        company = result.get("company", "")

        try:
            # --- Queries tab ---
            _append_queries(sh, output, rid, title, company)

            # --- Coverage tab ---
            _append_coverage(sh, output, rid, title)

            # --- Run Metadata tab ---
            _append_metadata(sh, output, rid, title, company)

            logger.info(f"  Appended #{rid} to Google Sheets")

        except Exception as e:
            logger.error(f"  Failed to append #{rid} to Sheets: {e}")
            success = False

    return success


def _get_or_create_worksheet(sh, name: str, headers: list):
    """Get existing worksheet or create it with headers."""
    try:
        ws = sh.worksheet(name)
    except Exception:
        ws = sh.add_worksheet(name, rows=1000, cols=len(headers))
        ws.update(range_name="A1", values=[headers])
    return ws


def _append_queries(sh, output: dict, rid, title, company):
    """Append valid + unvalidated queries to Queries tab."""
    headers = [
        "Request ID", "Request Title", "Company",
        "Query", "Result Count", "Category", "Status",
        "SOI Covered", "Rationale", "Relevance Passed",
        "Relevance Details", "Refinement Round", "Original Query",
    ]
    ws = _get_or_create_worksheet(sh, "Queries", headers)

    rows = []

    # Valid queries
    for q in output.get("valid_queries", []):
        rows.append([
            rid, title, company,
            q.get("query", ""),
            q.get("result_count", ""),
            q.get("category", ""),
            "valid",
            q.get("target_soi", ""),
            q.get("rationale", ""),
            str(q.get("relevance_passed", "")),
            q.get("relevance_details", ""),
            q.get("refinement_round", 0),
            q.get("original_query", ""),
        ])

    # Unvalidated queries (S2 API was unreachable)
    for q in output.get("unvalidated_queries", []):
        rows.append([
            rid, title, company,
            q.get("query", ""),
            "",
            "",
            "unvalidated",
            q.get("target_soi", ""),
            q.get("rationale", ""),
            "",
            "S2 API unreachable during validation",
            q.get("refinement_round", 0),
            q.get("original_query", ""),
        ])

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")


def _append_coverage(sh, output: dict, rid, title):
    """Append SOI coverage rows to Coverage tab."""
    headers = [
        "Request ID", "Request Title",
        "Solution of Interest", "# Queries", "Best Query", "Best Result Count",
    ]
    ws = _get_or_create_worksheet(sh, "Coverage", headers)

    rows = []
    for cov in output.get("soi_coverage", []):
        rows.append([
            rid, title,
            cov.get("soi", ""),
            cov.get("num_queries", 0),
            cov.get("best_query", ""),
            cov.get("best_result_count", ""),
        ])

    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")


def _append_metadata(sh, output: dict, rid, title, company):
    """Append run metadata row to Run Metadata tab."""
    headers = [
        "Run ID", "Request ID", "Request Title", "Company",
        "Started At", "Completed At", "Model Used",
        "Total Generated", "Valid", "Unvalidated",
        "Rejected Broad", "Rejected Irrelevant",
        "SOIs Covered",
    ]
    ws = _get_or_create_worksheet(sh, "Run Metadata", headers)

    stats = output.get("stats", {})
    row = [
        output.get("run_id", ""),
        rid, title, company,
        output.get("started_at", ""),
        output.get("completed_at", ""),
        output.get("model_used", ""),
        stats.get("total_generated", 0),
        stats.get("valid", 0),
        stats.get("unvalidated", 0),
        stats.get("rejected_too_broad", 0),
        stats.get("rejected_irrelevant", 0),
        f"{stats.get('sois_covered', 0)}/{stats.get('sois_total', 0)}",
    ]
    ws.append_rows([row], value_input_option="USER_ENTERED")


# =============================================================================
# n8n webhook — POST results for Sheets population
# =============================================================================

def post_to_n8n(processed_results: list, prompt_version: str) -> bool:
    """POST pipeline results to n8n webhook for Google Sheets population.

    The n8n workflow handles writing to all 5 tabs:
    Queries, Coverage, Run Metadata, Performance Trends, Feedback.

    Args:
        processed_results: List of result dicts with 'pipeline_output' key.
        prompt_version: Current prompt version identifier.

    Returns:
        True if POST succeeded, False otherwise.
    """
    payload = {
        "results": [],
        "prompt_version": prompt_version,
    }
    for result in processed_results:
        payload["results"].append({
            "request_id": result.get("request_id"),
            "title": result.get("title", ""),
            "company": result.get("company", ""),
            "pipeline_output": result["pipeline_output"],
        })

    try:
        resp = requests.post(
            N8N_WEBHOOK_URL,
            json=payload,
            timeout=60,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            logger.info(f"Posted {len(processed_results)} results to n8n webhook")
            return True
        else:
            logger.warning(f"n8n webhook returned {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"n8n webhook POST failed: {e}")
        return False


# =============================================================================
# Slack notification
# =============================================================================

def send_slack_notification(all_results: list, request_list: list) -> bool:
    """Send a summary notification to Slack via webhook."""
    if not SLACK_WEBHOOK_URL:
        logger.warning("SLACK_WEBHOOK_URL not set — skipping Slack notification")
        return False

    processed = [r for r in all_results if "pipeline_output" in r]
    failed = [r for r in all_results if "error" in r]

    # Build summary
    lines = [
        f":robot_face: *RoboScout Daily Run — {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        f"Discovered *{len(request_list)}* new requests | "
        f"*{len(processed)}* succeeded | *{len(failed)}* failed",
        "",
    ]

    # Per-request details
    for result in all_results:
        rid = result.get("request_id", "?")
        title = result.get("title", "")
        company = result.get("company", "")

        if "pipeline_output" in result:
            stats = result["pipeline_output"].get("stats", {})
            valid = stats.get("valid", 0)
            total = stats.get("total_generated", 0)
            sois = f"{stats.get('sois_covered', 0)}/{stats.get('sois_total', 0)}"
            lines.append(
                f":white_check_mark: *#{rid}* {title} ({company}) — "
                f"{valid}/{total} valid queries, {sois} SOIs covered"
            )
        else:
            error_snippet = result.get("error", "Unknown error")[:100]
            lines.append(
                f":x: *#{rid}* {title} ({company}) — FAILED: {error_snippet}"
            )

    # Sheet link
    if SHEET_URL:
        lines.append("")
        lines.append(f":bar_chart: <{SHEET_URL}|View in Google Sheets>")

    payload = {
        "text": "\n".join(lines),
        "unfurl_links": False,
    }

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            logger.info("Slack notification sent")
            return True
        else:
            logger.warning(f"Slack returned {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Slack notification: {e}")
        return False


def _send_slack_alert(message: str) -> bool:
    """Send a standalone alert message to Slack."""
    if not SLACK_WEBHOOK_URL:
        return False
    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            json={"text": message, "unfurl_links": False},
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Slack alert failed: {e}")
        return False


# =============================================================================
# Direct gspread write (used when n8n webhook is not configured or as fallback)
# =============================================================================

def _direct_sheets_write(sh, processed: list, prompt_version: str):
    """Write to all 5 Sheets tabs directly via gspread.

    This is the original write path, now extracted into a helper so it can
    be used as a fallback when the n8n webhook is unavailable.
    """
    append_to_sheets(processed)

    # Append performance metrics
    from monitoring.metrics_tracker import MetricsTracker
    tracker = MetricsTracker(sh, prompt_version=prompt_version)
    for result in processed:
        output = result["pipeline_output"]
        tracker.append_metrics(
            output,
            request_id=result.get("request_id"),
            title=result.get("title", ""),
            company=result.get("company", ""),
        )

    # Populate Feedback tab for manager review
    from monitoring.feedback_sheet import FeedbackSheet as FBSheet
    fb = FBSheet(sh)
    fb.setup_feedback_tab()
    for result in processed:
        fb.populate_queries_for_feedback(
            result["pipeline_output"],
            request_id=result.get("request_id"),
            prompt_version=prompt_version,
        )


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="RoboScout Daily Runner — pipeline + Sheets + Slack, all local"
    )
    parser.add_argument("--hours", type=int, default=24,
                        help="Lookback window for new requests (default: 24)")
    parser.add_argument("--request-ids", type=int, nargs="+",
                        help="Run specific request IDs instead of auto-discovery")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run pipelines but don't write to Sheets or send Slack")
    parser.add_argument("--no-slack", action="store_true",
                        help="Skip Slack notification")
    parser.add_argument("--no-sheets", action="store_true",
                        help="Skip Google Sheets append")
    parser.add_argument("--optimize", action="store_true",
                        help="Trigger manual GEPA prompt optimization")
    args = parser.parse_args()

    logger.info(f"=== RoboScout Daily Run — {datetime.now().isoformat()} ===")

    # Manual optimization mode
    if args.optimize:
        from optimization.optimize import run_optimization
        logger.info("Running manual GEPA optimization...")
        results = run_optimization(budget="medium")
        logger.info(f"Optimization result: {results.get('status')}")
        return

    # Step 0: Health check
    from monitoring.health_check import run_all_checks, format_health_alert
    health_ok, health_issues = run_all_checks()
    if not health_ok:
        alert = format_health_alert(health_issues)
        logger.warning(alert)
        if not (args.dry_run or args.no_slack):
            _send_slack_alert(alert)

    # Step 0.5: Check for approved prompt candidates
    prompt_version = "baseline"
    try:
        from monitoring.feedback_sheet import FeedbackSheet
        gc = _get_gspread_client()
        sh = gc.open_by_url(SHEET_URL)
        feedback_mgr = FeedbackSheet(sh)
        approval = feedback_mgr.check_pending_approval()
        if approval["status"] == "approved":
            from optimization.optimize import promote_candidate
            if promote_candidate():
                prompt_version = "optimized"
                logger.info("Promoted approved prompt candidate to active")
        elif approval["status"] == "pending":
            logger.info("Prompt candidate awaiting approval")
    except Exception as e:
        logger.warning(f"Could not check prompt approvals: {e}")

    # Step 1: Get request IDs
    if args.request_ids:
        request_list = [
            {"id": rid, "title": "", "company": ""}
            for rid in args.request_ids
        ]
        logger.info(f"Running {len(request_list)} manually specified requests")
    else:
        discovery = find_new_requests(hours=args.hours)
        request_list = discovery.get("requests", [])
        if not request_list:
            logger.info("No new requests found. Done.")
            return

        logger.info(f"Found {len(request_list)} new requests:")
        for r in request_list:
            logger.info(f"  #{r['id']}: {r['title']} ({r['company']})")

    # Step 2: Run pipeline for each request
    all_results = []
    for req in request_list:
        rid = req["id"]
        result = run_pipeline(rid)
        result["company"] = req.get("company", "")
        result["title"] = req.get("title", "")
        result["request_id"] = rid
        all_results.append(result)

        success = "pipeline_output" in result
        status = "OK" if success else "FAILED"
        logger.info(f"  #{rid}: {status}")

    processed = [r for r in all_results if "pipeline_output" in r]
    failed = [r for r in all_results if "error" in r]
    logger.info(f"\nResults: {len(processed)} succeeded, {len(failed)} failed")

    # Step 3: Save local JSON backup
    backup_path = LOG_DIR / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(backup_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"Results saved to {backup_path}")

    # Step 4: Append to Google Sheets + metrics + feedback
    if args.dry_run or args.no_sheets:
        logger.info("Skipping Google Sheets append")
    elif N8N_WEBHOOK_URL:
        # POST to n8n webhook — n8n handles all 5 Sheets tabs
        logger.info("Using n8n webhook for Google Sheets population")
        webhook_ok = post_to_n8n(processed, prompt_version)

        if not webhook_ok:
            logger.warning("n8n webhook failed — falling back to direct gspread writes")
            try:
                gc_sheets = _get_gspread_client()
                sh_sheets = gc_sheets.open_by_url(SHEET_URL)
                _direct_sheets_write(sh_sheets, processed, prompt_version)
            except Exception as e:
                logger.error(f"Fallback gspread write also failed: {e}")

        # Quality degradation check (reads from Sheets — always via gspread)
        try:
            gc_read = _get_gspread_client()
            sh_read = gc_read.open_by_url(SHEET_URL)
            from monitoring.metrics_tracker import MetricsTracker
            tracker = MetricsTracker(sh_read, prompt_version=prompt_version)
            degradation = tracker.check_quality_degradation()
            if degradation and not (args.dry_run or args.no_slack):
                _send_slack_alert(degradation)
        except Exception as e:
            logger.warning(f"Quality degradation check failed: {e}")
    else:
        # Direct gspread writes (no n8n webhook configured)
        try:
            gc_sheets = _get_gspread_client()
            sh_sheets = gc_sheets.open_by_url(SHEET_URL)
            _direct_sheets_write(sh_sheets, processed, prompt_version)

            # Check for quality degradation
            from monitoring.metrics_tracker import MetricsTracker
            tracker = MetricsTracker(sh_sheets, prompt_version=prompt_version)
            degradation = tracker.check_quality_degradation()
            if degradation and not (args.dry_run or args.no_slack):
                _send_slack_alert(degradation)

        except Exception as e:
            logger.error(f"Google Sheets / monitoring failed: {e}")

    # Step 5: Send Slack notification
    if args.dry_run or args.no_slack:
        logger.info("Skipping Slack notification")
    else:
        send_slack_notification(all_results, request_list)

    logger.info("=== Done ===")


if __name__ == "__main__":
    main()
