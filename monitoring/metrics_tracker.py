#!/usr/bin/env python3
"""
RoboScout Monitoring — Metrics Tracker
========================================
Collects quality metrics from each pipeline run and appends them to a
"Performance Trends" tab in the Google Sheet.

Detects quality degradation over a rolling 7-day window and returns a
warning string (logged by run_daily) when thresholds are breached.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("roboscout_daily.metrics")


# Quality alert thresholds (7-day rolling average)
VALID_RATE_ALERT = 0.60     # Alert if valid query rate drops below 60%
COVERAGE_RATE_ALERT = 0.70  # Alert if SOI coverage drops below 70%
ROLLING_WINDOW_DAYS = 7


class MetricsTracker:
    """Tracks run quality metrics in Google Sheets."""

    HEADERS = [
        "Date",
        "Request ID",
        "Request Title",
        "Company",
        "Total Generated",
        "Valid Queries",
        "Valid Rate",
        "Unvalidated",
        "Rejected Broad",
        "Rejected Irrelevant",
        "SOIs Covered",
        "SOIs Total",
        "Coverage Rate",
        "Highly Specific",
        "Specific",
        "Moderate",
        "General",
        "Refinement Rate",
        "Recovery Queries",
        "Prompt Version",
    ]

    def __init__(self, sheet, prompt_version: str = "baseline"):
        """
        Args:
            sheet: An open gspread Spreadsheet object.
            prompt_version: Current prompt version identifier.
        """
        self.sheet = sheet
        self.prompt_version = prompt_version

    def _get_or_create_worksheet(self):
        """Get or create the Performance Trends worksheet."""
        name = "Performance Trends"
        try:
            ws = self.sheet.worksheet(name)
        except Exception:
            ws = self.sheet.add_worksheet(name, rows=1000, cols=len(self.HEADERS))
            ws.update(range_name="A1", values=[self.HEADERS])
            # Bold + freeze header row
            ws.format("A1:T1", {"textFormat": {"bold": True}})
            ws.freeze(rows=1)
        return ws

    def append_metrics(self, pipeline_output: dict, request_id=None,
                       title: str = "", company: str = "") -> dict:
        """Append a metrics row for a completed pipeline run.

        Args:
            pipeline_output: The full JSON dict from roboscout_query_gen --output-json.
            request_id: The request ID.
            title: Request title.
            company: Company name.

        Returns:
            Dict of computed metrics for this run.
        """
        stats = pipeline_output.get("stats", {})
        by_cat = stats.get("by_category", {})

        total = stats.get("total_generated", 0)
        valid = stats.get("valid", 0)
        valid_rate = valid / max(total, 1)

        sois_covered = stats.get("sois_covered", 0)
        sois_total = stats.get("sois_total", 0)
        coverage_rate = sois_covered / max(sois_total, 1)

        # Count refinement and recovery queries
        valid_queries = pipeline_output.get("valid_queries", [])
        refined_count = sum(1 for q in valid_queries if q.get("refinement_round", 0) > 0)
        refinement_rate = refined_count / max(valid, 1)
        recovery_count = sum(1 for q in valid_queries if q.get("is_recovery", False))

        metrics = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "request_id": request_id or pipeline_output.get("request_id", ""),
            "title": title or pipeline_output.get("request_title", ""),
            "company": company,
            "total_generated": total,
            "valid": valid,
            "valid_rate": round(valid_rate, 3),
            "unvalidated": stats.get("unvalidated", 0),
            "rejected_broad": stats.get("rejected_too_broad", 0),
            "rejected_irrelevant": stats.get("rejected_irrelevant", 0),
            "sois_covered": sois_covered,
            "sois_total": sois_total,
            "coverage_rate": round(coverage_rate, 3),
            "highly_specific": by_cat.get("highly_specific", 0),
            "specific": by_cat.get("specific", 0),
            "moderate": by_cat.get("moderate", 0),
            "general": by_cat.get("general", 0),
            "refinement_rate": round(refinement_rate, 3),
            "recovery_queries": recovery_count,
            "prompt_version": self.prompt_version,
        }

        row = [
            metrics["date"],
            metrics["request_id"],
            metrics["title"],
            metrics["company"],
            metrics["total_generated"],
            metrics["valid"],
            f"{metrics['valid_rate']:.1%}",
            metrics["unvalidated"],
            metrics["rejected_broad"],
            metrics["rejected_irrelevant"],
            metrics["sois_covered"],
            metrics["sois_total"],
            f"{metrics['coverage_rate']:.1%}",
            metrics["highly_specific"],
            metrics["specific"],
            metrics["moderate"],
            metrics["general"],
            f"{metrics['refinement_rate']:.1%}",
            metrics["recovery_queries"],
            metrics["prompt_version"],
        ]

        try:
            ws = self._get_or_create_worksheet()
            ws.append_rows([row], value_input_option="USER_ENTERED")
            logger.info(f"Appended metrics for request #{metrics['request_id']}")
        except Exception as e:
            logger.error(f"Failed to append metrics: {e}")

        return metrics

    def check_quality_degradation(self) -> Optional[str]:
        """Check if quality metrics have degraded over the rolling window.

        Returns:
            Alert message string if degradation detected, None otherwise.
        """
        try:
            ws = self._get_or_create_worksheet()
            all_values = ws.get_all_values()
        except Exception as e:
            logger.warning(f"Could not read Performance Trends for degradation check: {e}")
            return None

        if len(all_values) < 2:
            return None  # Not enough data

        # Parse recent rows (skip header)
        cutoff = datetime.now() - timedelta(days=ROLLING_WINDOW_DAYS)
        recent_valid_rates = []
        recent_coverage_rates = []

        for row in all_values[1:]:
            try:
                row_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M")
                if row_date < cutoff:
                    continue
                # Valid Rate is column 6 (0-indexed), Coverage Rate is column 12
                vr = float(row[6].rstrip("%")) / 100
                cr = float(row[12].rstrip("%")) / 100
                recent_valid_rates.append(vr)
                recent_coverage_rates.append(cr)
            except (ValueError, IndexError):
                continue

        if len(recent_valid_rates) < 3:
            return None  # Need at least 3 runs to detect a trend

        avg_valid = sum(recent_valid_rates) / len(recent_valid_rates)
        avg_coverage = sum(recent_coverage_rates) / len(recent_coverage_rates)

        alerts = []
        if avg_valid < VALID_RATE_ALERT:
            alerts.append(
                f"Valid query rate: {avg_valid:.0%} "
                f"(below {VALID_RATE_ALERT:.0%} threshold)"
            )
        if avg_coverage < COVERAGE_RATE_ALERT:
            alerts.append(
                f"SOI coverage rate: {avg_coverage:.0%} "
                f"(below {COVERAGE_RATE_ALERT:.0%} threshold)"
            )

        if alerts:
            n = len(recent_valid_rates)
            msg = (
                f":warning: *RoboScout quality degradation detected* "
                f"({ROLLING_WINDOW_DAYS}-day avg, {n} runs)\n"
                + "\n".join(f"  - {a}" for a in alerts)
                + "\nReview recent runs in the Performance Trends tab."
            )
            return msg

        return None
