#!/usr/bin/env python3
"""
RoboScout Monitoring — Feedback Sheet
========================================
Creates and manages the "Feedback" tab in the Google Sheet where the
agent manager can rate queries and provide notes.

Layout:
  - RUN header rows: one per request, with stats summary and run-level rating/notes
  - Query detail rows: one per valid query, with per-query rating/notes/suggested query
  - Conditional formatting highlights RUN rows (bold + light blue background)

Also provides ingestion: reads flagged queries and converts them into
GEPA training signals (positive examples, negative feedback text,
run-level feedback, and suggested query gold examples).
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger("roboscout_daily.feedback")

FEEDBACK_DIR = Path(__file__).parent.parent / "optimization" / "training_data"


class FeedbackSheet:
    """Manage the Feedback tab for human quality ratings."""

    HEADERS = [
        "Row Type",        # A: "RUN" or "" (empty for query rows)
        "Date",            # B
        "Request ID",      # C
        "Request Title",   # D: filled on RUN rows only
        "Query",           # E: stats summary on RUN rows, actual query on query rows
        "SOI",             # F
        "Result Count",    # G
        "Category",        # H
        "Rating",          # I: combined dropdown
        "Notes",           # J: free text
        "Suggested Query", # K: alternative query phrasing
        "Prompt Version",  # L
        "Processed",       # M: TRUE once ingested by optimizer
    ]

    # Column indices (0-based) for reading data
    COL_ROW_TYPE = 0
    COL_DATE = 1
    COL_REQUEST_ID = 2
    COL_REQUEST_TITLE = 3
    COL_QUERY = 4
    COL_SOI = 5
    COL_RESULT_COUNT = 6
    COL_CATEGORY = 7
    COL_RATING = 8
    COL_NOTES = 9
    COL_SUGGESTED_QUERY = 10
    COL_PROMPT_VERSION = 11
    COL_PROCESSED = 12

    # All rating options in a single dropdown (context-dependent by row type)
    ALL_RATING_OPTIONS = ["good", "bad", "wrong SOI", "needs improvement"]

    def __init__(self, sheet):
        """
        Args:
            sheet: An open gspread Spreadsheet object.
        """
        self.sheet = sheet

    def setup_feedback_tab(self):
        """Create the Feedback tab with headers, validation, and conditional formatting."""
        name = "Feedback"
        try:
            ws = self.sheet.worksheet(name)
            logger.info("Feedback tab already exists")
        except Exception:
            ws = self.sheet.add_worksheet(name, rows=1000, cols=len(self.HEADERS))
            ws.update(range_name="A1", values=[self.HEADERS])
            ws.format("A1:M1", {"textFormat": {"bold": True}})
            ws.freeze(rows=1)
            logger.info("Created Feedback tab with headers")

        # Data validation: Rating column (I) with all options
        try:
            ws.set_data_validation(
                "I2:I1000",
                {"condition": {"type": "ONE_OF_LIST", "values": self.ALL_RATING_OPTIONS},
                 "showCustomUi": True, "strict": False},
            )
            logger.info("Set up dropdown validation on Rating column")
        except Exception as e:
            logger.warning(f"Could not set data validation: {e}")

        # Conditional formatting: highlight RUN rows with bold + light blue background
        try:
            body = {
                "requests": [{
                    "addConditionalFormatRule": {
                        "rule": {
                            "ranges": [{
                                "sheetId": ws.id,
                                "startRowIndex": 1,
                                "endRowIndex": 1000,
                                "startColumnIndex": 0,
                                "endColumnIndex": len(self.HEADERS),
                            }],
                            "booleanRule": {
                                "condition": {
                                    "type": "CUSTOM_FORMULA",
                                    "values": [{"userEnteredValue": '=$A2="RUN"'}],
                                },
                                "format": {
                                    "backgroundColor": {
                                        "red": 0.85, "green": 0.92, "blue": 1.0
                                    },
                                    "textFormat": {"bold": True},
                                },
                            },
                        },
                        "index": 0,
                    }
                }]
            }
            ws.spreadsheet.batch_update(body)
            logger.info("Applied conditional formatting for RUN rows")
        except Exception as e:
            logger.warning(f"Could not apply conditional formatting: {e}")

        return ws

    def dedup_untouched_rows(self, request_id) -> int:
        """Delete prior rows for this request_id that the manager hasn't touched.

        "Touched" = Rating, Notes, Suggested Query, or Processed column
        has any value. We preserve those rows so re-runs can't clobber
        human feedback or already-ingested training signal.

        Returns the number of rows deleted. Best-effort: errors are
        logged and swallowed so a dedup failure never blocks append.
        """
        try:
            ws = self.sheet.worksheet("Feedback")
        except Exception:
            return 0

        try:
            all_values = ws.get_all_values()
        except Exception as e:
            logger.warning("Feedback dedup: could not read rows: %s", e)
            return 0

        if len(all_values) <= 1:
            return 0

        want = str(request_id)
        # 1-indexed row numbers to delete, collected in descending order
        # so deletes don't shift remaining indexes.
        to_delete = []
        for idx, row in enumerate(all_values[1:], start=2):  # skip header
            if len(row) <= self.COL_REQUEST_ID:
                continue
            if str(row[self.COL_REQUEST_ID]).strip() != want:
                continue
            # If ANY of the human / ingestion columns have content, keep.
            touched = any(
                len(row) > c and str(row[c]).strip()
                for c in (
                    self.COL_RATING,
                    self.COL_NOTES,
                    self.COL_SUGGESTED_QUERY,
                    self.COL_PROCESSED,
                )
            )
            if not touched:
                to_delete.append(idx)

        if not to_delete:
            return 0

        deleted = 0
        for row_num in sorted(to_delete, reverse=True):
            try:
                ws.delete_rows(row_num)
                deleted += 1
            except Exception as e:
                logger.warning(
                    "Feedback dedup: delete_rows(%d) failed: %s", row_num, e
                )
                break
        if deleted:
            logger.info(
                "Feedback dedup: removed %d untouched rows for #%s",
                deleted, request_id,
            )
        return deleted

    def populate_queries_for_feedback(self, pipeline_output: dict,
                                       request_id=None, prompt_version: str = "baseline"):
        """Add a RUN header row + query detail rows to the Feedback tab.

        Pre-fills Row Type, Date, Request ID, Query, SOI, Result Count,
        Category, Prompt Version. Leaves Rating, Notes, Suggested Query
        blank for the manager to fill in.

        Before append, removes any prior untouched rows for this
        request_id so re-runs don't accumulate duplicate entries.
        """
        try:
            ws = self.sheet.worksheet("Feedback")
        except Exception:
            ws = self.setup_feedback_tab()

        rid_for_dedup = request_id or pipeline_output.get("request_id", "")
        if rid_for_dedup != "":
            self.dedup_untouched_rows(rid_for_dedup)

        rows = []
        date_str = datetime.now().strftime("%Y-%m-%d")
        rid = request_id or pipeline_output.get("request_id", "")
        title = pipeline_output.get("request_title", "")
        stats = pipeline_output.get("stats", {})

        # Build stats summary for the RUN row
        valid_count = stats.get("valid", 0)
        total_gen = stats.get("total_generated", 0)
        sois_covered = stats.get("sois_covered", 0)
        sois_total = stats.get("sois_total", 0)
        summary = f"{valid_count} valid / {total_gen} total, {sois_covered}/{sois_total} SOIs"

        # RUN header row
        rows.append([
            "RUN",           # Row Type
            date_str,        # Date
            rid,             # Request ID
            title,           # Request Title
            summary,         # Query (stats summary)
            "",              # SOI
            "",              # Result Count
            "",              # Category
            "",              # Rating
            "",              # Notes
            "",              # Suggested Query
            prompt_version,  # Prompt Version
            "",              # Processed
        ])

        # Query detail rows
        for q in pipeline_output.get("valid_queries", []):
            rows.append([
                "",                          # Row Type (empty = query row)
                date_str,                    # Date
                rid,                         # Request ID
                "",                          # Request Title (empty on query rows)
                q.get("query", ""),          # Query
                q.get("target_soi", ""),     # SOI
                q.get("result_count", ""),   # Result Count
                q.get("category", ""),       # Category
                "",                          # Rating
                "",                          # Notes
                "",                          # Suggested Query
                prompt_version,              # Prompt Version
                "",                          # Processed
            ])

        if rows:
            try:
                ws.append_rows(rows, value_input_option="USER_ENTERED")
                logger.info(f"Added RUN header + {len(rows) - 1} queries to Feedback tab")
            except Exception as e:
                logger.error(f"Failed to populate Feedback tab: {e}")

    def ingest_feedback(self) -> Tuple[List[dict], List[dict], List[dict]]:
        """Read unprocessed feedback and convert to training signals.

        Returns:
            (positive_examples, negative_feedback, run_feedback)
            positive_examples: queries rated "good" → become training examples
            negative_feedback: queries rated "bad"/"wrong SOI" → become GEPA feedback text
            run_feedback: RUN rows with ratings → run-level quality signals
        """
        try:
            ws = self.sheet.worksheet("Feedback")
            all_values = ws.get_all_values()
        except Exception as e:
            logger.error(f"Could not read Feedback tab: {e}")
            return [], [], []

        if len(all_values) < 2:
            return [], [], []

        positive = []
        negative = []
        run_feedback = []
        rows_to_mark = []

        for i, row in enumerate(all_values[1:], start=2):  # 1-indexed, skip header
            try:
                # Pad short rows for backwards compatibility
                while len(row) < len(self.HEADERS):
                    row.append("")

                row_type = (row[self.COL_ROW_TYPE] or "").strip().upper()
                rating = (row[self.COL_RATING] or "").strip().lower()
                processed = (row[self.COL_PROCESSED] or "").strip().upper()

                if not rating or processed == "TRUE":
                    continue

                if row_type == "RUN":
                    # Run-level feedback
                    entry = {
                        "date": row[self.COL_DATE],
                        "request_id": row[self.COL_REQUEST_ID],
                        "request_title": row[self.COL_REQUEST_TITLE],
                        "summary": row[self.COL_QUERY],
                        "rating": rating,
                        "notes": row[self.COL_NOTES],
                        "prompt_version": row[self.COL_PROMPT_VERSION],
                    }
                    run_feedback.append(entry)
                    rows_to_mark.append(i)

                else:
                    # Query-level feedback
                    entry = {
                        "date": row[self.COL_DATE],
                        "request_id": row[self.COL_REQUEST_ID],
                        "query": row[self.COL_QUERY],
                        "soi": row[self.COL_SOI],
                        "result_count": row[self.COL_RESULT_COUNT],
                        "category": row[self.COL_CATEGORY],
                        "rating": rating,
                        "notes": row[self.COL_NOTES],
                        "suggested_query": row[self.COL_SUGGESTED_QUERY],
                        "prompt_version": row[self.COL_PROMPT_VERSION],
                    }

                    if rating == "good":
                        positive.append(entry)
                    elif rating in ("bad", "wrong soi"):
                        negative.append(entry)

                    rows_to_mark.append(i)

            except (IndexError, ValueError):
                continue

        # Mark processed rows
        if rows_to_mark:
            try:
                for row_num in rows_to_mark:
                    ws.update_cell(row_num, self.COL_PROCESSED + 1, "TRUE")
                logger.info(f"Marked {len(rows_to_mark)} feedback entries as processed")
            except Exception as e:
                logger.warning(f"Could not mark rows as processed: {e}")

        logger.info(
            f"Ingested feedback: {len(positive)} positive, {len(negative)} negative, "
            f"{len(run_feedback)} run-level"
        )
        return positive, negative, run_feedback

    def save_training_data(self, positive: List[dict], negative: List[dict],
                           run_feedback: List[dict] = None):
        """Persist ingested feedback to local JSON for GEPA consumption."""
        FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
        output_path = FEEDBACK_DIR / "feedback_data.json"

        existing = {"positive": [], "negative": [], "run_feedback": []}
        if output_path.exists():
            try:
                existing = json.loads(output_path.read_text())
                if "run_feedback" not in existing:
                    existing["run_feedback"] = []
            except json.JSONDecodeError:
                pass

        existing["positive"].extend(positive)
        existing["negative"].extend(negative)
        if run_feedback:
            existing["run_feedback"].extend(run_feedback)
        existing["last_updated"] = datetime.now().isoformat()

        output_path.write_text(json.dumps(existing, indent=2, default=str))
        logger.info(f"Saved training data to {output_path}")

    def check_pending_approval(self) -> dict:
        """Check if there's a prompt candidate awaiting approval.

        The agent manager marks candidates as 'approved' or 'rejected'
        via a special row in the Feedback tab.

        Returns:
            {"status": "approved"/"rejected"/"pending"/"none", "candidate_path": str}
        """
        try:
            ws = self.sheet.worksheet("Feedback")
            all_values = ws.get_all_values()
        except Exception:
            return {"status": "none"}

        for row in reversed(all_values):
            try:
                while len(row) < len(self.HEADERS):
                    row.append("")
                if row[self.COL_QUERY] == "__PROMPT_CANDIDATE__":
                    notes = (row[self.COL_NOTES] or "").strip().lower()
                    candidate_path = row[self.COL_SOI]
                    if notes in ("approved", "approve", "yes"):
                        return {"status": "approved", "candidate_path": candidate_path}
                    elif notes in ("rejected", "reject", "no"):
                        return {"status": "rejected", "candidate_path": candidate_path}
                    else:
                        return {"status": "pending", "candidate_path": candidate_path}
            except IndexError:
                continue

        return {"status": "none"}
