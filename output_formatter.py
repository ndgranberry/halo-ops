#!/usr/bin/env python3
"""
RoboScout Query Generator — Output Formatter
==============================================
Write validated queries to Google Sheets or CSV.
"""

import csv
import json
import logging
import os
import sys
from typing import List, Optional

from models import GeneratedQuery, QueryRun, QueryCategory, SOICoverage

logger = logging.getLogger("roboscout_query_gen.output_formatter")


class OutputFormatter:
    """Write query results to Google Sheets, CSV, or JSON."""

    def write_json(self, run: QueryRun, output_path: str = None) -> str:
        """Write results as JSON to a file or stdout.

        Args:
            run: The completed QueryRun
            output_path: File path to write. If None or "-", writes to stdout.

        Returns:
            The output path (or "stdout")
        """
        data = run.to_full_dict()

        if not output_path or output_path == "-":
            # Write to stdout — n8n Execute Command captures this
            json.dump(data, sys.stdout, indent=2, default=str)
            sys.stdout.write("\n")
            return "stdout"
        else:
            with open(output_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
            logger.info(f"JSON output written to {output_path}")
            return output_path

    def write_csv(self, run: QueryRun, output_path: str) -> str:
        """Write results to CSV file."""
        valid = run.valid_queries
        unvalidated = run.unvalidated_queries
        rejected = [
            q for q in run.queries
            if not q.is_valid and not q.is_unvalidated
        ]

        # Main queries file (valid + unvalidated)
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Query",
                "Result Count",
                "Category",
                "Status",
                "SOI Covered",
                "Rationale",
                "Relevance Passed",
                "Relevance Details",
                "Refinement Round",
                "Original Query",
                "Recovery",
                "Regeneration",
            ])

            for q in sorted(valid, key=lambda x: x.result_count or 0):
                writer.writerow([
                    q.query,
                    q.result_count,
                    q.category.value if q.category else "",
                    "valid",
                    q.target_soi,
                    q.rationale,
                    q.relevance_passed,
                    q.relevance_details,
                    q.refinement_round,
                    q.original_query or "",
                    q.is_recovery,
                    q.is_regeneration,
                ])

            # Include unvalidated queries — they might be good, just couldn't reach S2
            for q in unvalidated:
                writer.writerow([
                    q.query,
                    "",
                    "",
                    "unvalidated — needs manual S2 check",
                    q.target_soi,
                    q.rationale,
                    "",
                    "S2 API unreachable during validation",
                    q.refinement_round,
                    q.original_query or "",
                    q.is_recovery,
                    q.is_regeneration,
                ])

        # Rejected queries (separate file)
        if rejected:
            rejected_path = output_path.replace(".csv", "_rejected.csv")
            with open(rejected_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Query", "Result Count", "Category", "SOI Covered",
                    "Rejection Reason",
                ])
                for q in rejected:
                    if q.category == QueryCategory.TOO_BROAD:
                        reason = f"Too broad ({q.result_count} > 3000)"
                    elif q.category == QueryCategory.TOO_NARROW:
                        reason = f"Too narrow ({q.result_count} < 20)"
                    elif q.result_count == 0:
                        reason = "Zero results — query too narrow"
                    else:
                        reason = f"Low relevance ({q.relevance_details})"
                    writer.writerow([
                        q.query, q.result_count,
                        q.category.value if q.category else "", q.target_soi,
                        reason,
                    ])
            logger.info(f"Rejected queries written to {rejected_path}")

        logger.info(
            f"Written to {output_path}: "
            f"{len(valid)} valid, {len(unvalidated)} unvalidated, {len(rejected)} rejected"
        )
        return output_path

    def write_sheets(self, run: QueryRun, sheet_url: str) -> str:
        """Write results to Google Sheets."""
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
        gc = gspread.authorize(creds)
        sh = gc.open_by_url(sheet_url)

        # Tab 1: Valid Queries
        self._write_queries_tab(sh, run.valid_queries)

        # Tab 2: SOI Coverage
        self._write_coverage_tab(sh, run)

        # Tab 3: Run Metadata
        self._write_metadata_tab(sh, run)

        # Tab 4 & 5: Performance Trends + Feedback (initialize if missing)
        self._ensure_monitoring_tabs(sh)

        logger.info(f"Results written to Google Sheet: {sheet_url}")
        return sheet_url

    def _write_queries_tab(self, sh, queries: List[GeneratedQuery]):
        """Write queries to the first tab."""
        try:
            ws = sh.worksheet("Queries")
            ws.clear()
        except Exception:
            ws = sh.add_worksheet("Queries", rows=100, cols=10)

        headers = [
            "Query", "Result Count", "Category", "SOI Covered",
            "Rationale", "Relevance Passed", "Relevance Details",
            "Refinement Round", "Original Query", "Recovery", "Regeneration",
        ]

        rows = [headers]
        for q in sorted(queries, key=lambda x: x.result_count or 0):
            rows.append([
                q.query,
                q.result_count or 0,
                q.category.value if q.category else "",
                q.target_soi,
                q.rationale,
                str(q.relevance_passed),
                q.relevance_details,
                q.refinement_round,
                q.original_query or "",
                str(q.is_recovery),
                str(q.is_regeneration),
            ])

        ws.update(range_name="A1", values=rows)

    def _write_coverage_tab(self, sh, run: QueryRun):
        """Write SOI coverage analysis."""
        try:
            ws = sh.worksheet("Coverage")
            ws.clear()
        except Exception:
            ws = sh.add_worksheet("Coverage", rows=50, cols=5)

        headers = ["Solution of Interest", "# Queries", "Best Query", "Best Result Count"]
        rows = [headers]

        for cov in run.soi_coverage:
            rows.append([
                cov.soi,
                len(cov.queries),
                cov.best_query,
                cov.best_result_count or 0,
            ])

        ws.update(range_name="A1", values=rows)

    def _ensure_monitoring_tabs(self, sh):
        """Create Performance Trends and Feedback tabs if they don't exist."""
        try:
            from monitoring.metrics_tracker import MetricsTracker
            tracker = MetricsTracker(sh)
            tracker._get_or_create_worksheet()
        except Exception as e:
            logger.warning(f"Could not create Performance Trends tab: {e}")

        try:
            from monitoring.feedback_sheet import FeedbackSheet
            fb = FeedbackSheet(sh)
            fb.setup_feedback_tab()
        except Exception as e:
            logger.warning(f"Could not create Feedback tab: {e}")

    def _write_metadata_tab(self, sh, run: QueryRun):
        """Write run metadata."""
        try:
            ws = sh.worksheet("Run Metadata")
            ws.clear()
        except Exception:
            ws = sh.add_worksheet("Run Metadata", rows=20, cols=2)

        stats = run.stats
        rows = [
            ["Run ID", run.run_id],
            ["Request ID", run.request.request_id if run.request else ""],
            ["Request Title", run.request.title if run.request else ""],
            ["Started At", run.started_at],
            ["Completed At", run.completed_at],
            ["Model Used", run.model_used],
            ["", ""],
            ["Total Queries Generated", stats["total_generated"]],
            ["Valid Queries", stats["valid"]],
            ["Rejected (Too Broad)", stats["rejected_too_broad"]],
            ["Rejected (Too Narrow)", stats.get("rejected_too_narrow", 0)],
            ["Rejected (Irrelevant)", stats["rejected_irrelevant"]],
            ["", ""],
            ["SOIs Covered", f"{stats['sois_covered']}/{stats['sois_total']}"],
            ["", ""],
            ["By Category:", ""],
            ["  Specific (20-499)", stats["by_category"].get("specific", 0)],
            ["  Moderate (500-1000)", stats["by_category"].get("moderate", 0)],
            ["  General (1001-3000)", stats["by_category"].get("general", 0)],
        ]

        ws.update(range_name="A1", values=rows)
