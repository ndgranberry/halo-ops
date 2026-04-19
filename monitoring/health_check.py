#!/usr/bin/env python3
"""
RoboScout Monitoring — Health Check
======================================
Checks operational health of external dependencies:
  - Anthropic API key validity
  - Semantic Scholar API reachability
  - Google Sheets access
  - Disk space for logs

Called by run_daily.py before pipeline execution.
"""

import logging
import os
import shutil
from pathlib import Path
from typing import List, Tuple

import requests as http_requests

logger = logging.getLogger("roboscout_daily.health")

PROJECT_DIR = Path(__file__).parent.parent
LOG_DIR = PROJECT_DIR / "logs"


def run_all_checks() -> Tuple[bool, List[str]]:
    """Run all health checks.

    Returns:
        (all_passed, issues) — True if all checks pass, list of issue strings.
    """
    issues = []

    # 1. Anthropic API key
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        issues.append("ANTHROPIC_API_KEY not set")
    elif len(api_key) < 20:
        issues.append("ANTHROPIC_API_KEY looks invalid (too short)")

    # 2. Semantic Scholar API reachability
    try:
        resp = http_requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search?query=test&limit=1",
            timeout=10,
        )
        if resp.status_code == 429:
            issues.append("Semantic Scholar API: rate limited (429)")
        elif resp.status_code != 200:
            issues.append(f"Semantic Scholar API: HTTP {resp.status_code}")
    except http_requests.RequestException as e:
        issues.append(f"Semantic Scholar API unreachable: {e}")

    # 3. Google Sheets credentials
    creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not creds_path:
        issues.append("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    elif not Path(creds_path).exists():
        issues.append(f"Google service account file not found: {creds_path}")

    # 4. Disk space for logs
    try:
        disk = shutil.disk_usage(str(LOG_DIR))
        free_mb = disk.free / (1024 * 1024)
        if free_mb < 100:
            issues.append(f"Low disk space: {free_mb:.0f} MB free")
    except OSError as e:
        issues.append(f"Could not check disk space: {e}")

    all_passed = len(issues) == 0

    if all_passed:
        logger.info("Health check: all systems OK")
    else:
        for issue in issues:
            logger.warning(f"Health check issue: {issue}")

    return all_passed, issues


def format_health_alert(issues: List[str]) -> str:
    """Format health issues as a Slack alert message."""
    lines = [
        ":rotating_light: *RoboScout health check failed*",
        "",
    ]
    for issue in issues:
        lines.append(f"  - {issue}")
    lines.append("")
    lines.append("The daily run may fail or produce incomplete results.")
    return "\n".join(lines)
