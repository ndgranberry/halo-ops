#!/usr/bin/env python3
"""
Agent Scout — Output Formatter (Module 5)
===========================================
Writes scored leads to Google Sheets or CSV.

For Type 2 (request_with_examples), writes a "Recommended Contacts" tab
back into the INPUT Google Sheet with human-in-the-loop evaluation columns.

Tab layout:
  - "Recommended Contacts" — scored leads with HITL review columns
  - "Below Threshold" — leads below min_fit_score (optional)
  - "Run Metadata" — config details, run stats

When not using the input sheet, creates a new spreadsheet or writes to
the configured output_sheet_url.
"""

import logging
import os
from datetime import datetime
from typing import List, Optional

from models_scout import ScoutLead, ScoutConfig, InputType

logger = logging.getLogger(__name__)

# ============================================================================
# Column definitions for the "Recommended Contacts" output tab
# ============================================================================

# Auto-filled columns (populated by the pipeline)
AUTO_COLUMNS = [
    "First Name",
    "Last Name",
    "Company Name",
    "Title",
    "LinkedIn",
    "Email",
    "Fit Score",
    "Why They're a Fit",
    "Country",
    "Disciplines",
    "Keywords (Areas of Expertise)",
    "Company Description",
    "Discovery Source",
]

# Human-in-the-loop evaluation columns (blank, filled by reviewer)
HITL_COLUMNS = [
    "Perfect Fit?",         # Checkbox (TRUE/FALSE) — mirrors input sheet format
    "Reviewer Decision",    # Dropdown: Approve / Reject / Maybe / Need More Info
    "Reviewer Notes",       # Free text
]

HEADER_ROW = AUTO_COLUMNS + HITL_COLUMNS

# Dropdown validation values
REVIEWER_DECISION_VALUES = ["Approve", "Reject", "Maybe", "Need More Info"]


class OutputFormatter:
    """Write scored leads to Google Sheets or CSV."""

    def __init__(self, config: ScoutConfig):
        self.config = config
        self._gc = None

    @property
    def gc(self):
        """Lazy-load gspread client."""
        if self._gc is None:
            import gspread

            creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
            if creds_path:
                self._gc = gspread.service_account(filename=creds_path)
            else:
                self._gc = gspread.oauth()
        return self._gc

    # =========================================================================
    # Main write method
    # =========================================================================

    def write(self, leads: List[ScoutLead], run_id: str = "", metrics: Optional[dict] = None) -> str:
        """
        Write leads to Google Sheet. Returns the sheet URL.

        For Type 2 (request_with_examples) with an input_sheet_url:
          → Writes "Recommended Contacts" tab into the INPUT sheet.
        Otherwise:
          → Creates/opens a separate output spreadsheet.

        `metrics` is the per-run counter dict from ScoutRun (halo_removed,
        scoring_errors, etc.). Used in the metadata tab.
        """
        metrics = metrics or {}
        # Sort leads by fit_score descending
        scored_leads = sorted(
            [l for l in leads if l.fit_score is not None],
            key=lambda l: l.fit_score,
            reverse=True,
        )

        above = [l for l in scored_leads if l.fit_score >= self.config.min_fit_score]
        below = [l for l in scored_leads if l.fit_score < self.config.min_fit_score]

        # Determine target spreadsheet
        spreadsheet = self._get_target_spreadsheet(run_id)

        # Tab 1: Recommended Contacts (above threshold)
        self._write_recommended_contacts_tab(spreadsheet, above)

        # Tab 2: Below Threshold (optional)
        if below:
            self._write_below_threshold_tab(spreadsheet, below)

        # Tab 3: Run Metadata
        self._write_metadata_tab(spreadsheet, leads, run_id, metrics)

        url = spreadsheet.url
        logger.info(f"Output written to: {url}")
        logger.info(f"  {len(above)} leads in 'Recommended Contacts' (above {self.config.min_fit_score})")
        logger.info(f"  {len(below)} leads in 'Below Threshold'")

        return url

    # =========================================================================
    # Spreadsheet targeting
    # =========================================================================

    def _get_target_spreadsheet(self, run_id: str):
        """
        Determine which spreadsheet to write to.

        Type 2 with input_sheet_url → write back into the input sheet.
        Otherwise → open output_sheet_url or create a new one.
        """
        # For Type 2: write back into the input Google Sheet
        if (
            self.config.input_type == InputType.REQUEST_WITH_EXAMPLES
            and self.config.input_sheet_url
        ):
            logger.info("Writing output to input Google Sheet (Type 2: request_with_examples)")
            return self.gc.open_by_url(self.config.input_sheet_url)

        # Explicit output sheet
        if self.config.output_sheet_url:
            return self.gc.open_by_url(self.config.output_sheet_url)

        # Create new spreadsheet
        name = self.config.output_sheet_name or f"Agent Scout — {run_id}"
        spreadsheet = self.gc.create(name)
        logger.info(f"Created new spreadsheet: {name}")
        return spreadsheet

    # =========================================================================
    # Write "Recommended Contacts" tab
    # =========================================================================

    def _write_recommended_contacts_tab(
        self, spreadsheet, leads: List[ScoutLead]
    ):
        """Append scored leads to the 'Recommended Contacts' tab. Never deletes
        existing content, and skips leads already present in the tab (idempotent
        on re-runs and resumes).
        """
        tab_name = "Recommended Contacts"

        # Get or create the worksheet
        try:
            worksheet = spreadsheet.worksheet(tab_name)
        except Exception:
            worksheet = spreadsheet.add_worksheet(
                title=tab_name,
                rows=max(len(leads) + 1, 2),
                cols=len(HEADER_ROW),
            )

        existing = worksheet.get_all_values()

        # Build the "already present" set keyed on (first, last, company) — same
        # shape as deduplicate_leads. Source the columns by header name (not
        # position) so column reordering doesn't silently break dedup.
        existing_keys = self._existing_lead_keys(existing)
        if existing_keys:
            before = len(leads)
            leads = [l for l in leads if self._lead_key(l) not in existing_keys]
            skipped = before - len(leads)
            if skipped:
                logger.info(f"Idempotent dedup: skipped {skipped}/{before} leads already in '{tab_name}'")

        if not leads:
            logger.info(f"No new leads to append to '{tab_name}' (all already present)")
            return

        if not existing:
            # Completely empty sheet — write header + leads
            start_row = 1
            rows = [HEADER_ROW] + [self._lead_to_row(lead) for lead in leads]
        else:
            # Append at the next free row after the last non-empty row
            start_row = len(existing) + 1
            rows = [self._lead_to_row(lead) for lead in leads]

        worksheet.update(f"A{start_row}", rows, value_input_option="USER_ENTERED")

        # Apply formatting and data validation to the new rows only
        self._format_recommended_contacts(worksheet, start_row, len(leads))

        logger.info(f"Appended {len(leads)} leads to '{tab_name}' starting at row {start_row}")

    @staticmethod
    def _lead_key(lead: ScoutLead) -> str:
        """Normalized identity key used for idempotent dedup against the sheet."""
        return "|".join([
            (lead.first_name or "").strip().lower(),
            (lead.last_name or "").strip().lower(),
            (lead.company or "").strip().lower(),
        ])

    @staticmethod
    def _existing_lead_keys(existing: list) -> set:
        """Extract identity keys from a 2D worksheet snapshot. Looks up
        First Name / Last Name / Company Name by header position so it survives
        column additions (as long as headers match HEADER_ROW)."""
        if not existing or len(existing) < 2:
            return set()
        header = existing[0]
        try:
            i_first = header.index("First Name")
            i_last = header.index("Last Name")
            i_co = header.index("Company Name")
        except ValueError:
            # Header row exists but doesn't match — can't dedup safely. Better
            # to skip dedup than to silently mis-key and produce duplicates.
            return set()
        keys = set()
        for row in existing[1:]:
            if len(row) <= max(i_first, i_last, i_co):
                continue
            key = "|".join([
                (row[i_first] or "").strip().lower(),
                (row[i_last] or "").strip().lower(),
                (row[i_co] or "").strip().lower(),
            ])
            if key.strip("|"):  # skip totally-empty rows
                keys.add(key)
        return keys

    def _lead_to_row(self, lead: ScoutLead) -> list:
        """Convert a ScoutLead to a row for the Recommended Contacts tab."""
        return [
            # Auto-filled columns
            lead.first_name or "",
            lead.last_name or "",
            lead.company or "",
            lead.title or "",
            lead.linkedin_url or "",
            lead.email or "",
            round(lead.fit_score, 2) if lead.fit_score is not None else "",
            self._format_blurb_with_context(lead),
            lead.country or "",
            ", ".join(lead.disciplines) if lead.disciplines else "",
            ", ".join(lead.keywords) if lead.keywords else "",
            lead.company_description or "",
            lead.discovery_source or "",
            # HITL columns (blank — reviewer fills these)
            False,      # Perfect Fit? (checkbox, default unchecked)
            "",         # Reviewer Decision
            "",         # Reviewer Notes
        ]

    @staticmethod
    def _format_blurb_with_context(lead: ScoutLead) -> str:
        """Append raw discovery context to the scorer's blurb for human review."""
        blurb = lead.fit_blurb or ""
        context_parts = []
        if lead.specific_expertise:
            context_parts.append(f"Expertise: {', '.join(lead.specific_expertise)}")
        if lead.evidence_snippets:
            context_parts.append(f"Evidence: {' | '.join(lead.evidence_snippets)}")
        if lead.org_type:
            context_parts.append(f"Org type: {lead.org_type}")
        # Expansion lineage — lets reviewers trace why this lead surfaced
        if lead.seed_url:
            context_parts.append(f"Seed URL (find_similar): {lead.seed_url}")
        if lead.seed_query:
            context_parts.append(f"Refined query: {lead.seed_query}")
        if not context_parts:
            return blurb
        return blurb + "\n\n--- Discovery Context ---\n" + "\n".join(context_parts)

    def _format_recommended_contacts(self, worksheet, start_row: int, num_new_leads: int):
        """Apply formatting and data validation to newly appended rows."""
        try:
            import gspread
            from gspread_formatting import (
                format_cell_range, CellFormat, TextFormat, Color,
                set_frozen, DataValidationRule, BooleanCondition,
                set_data_validation_for_cell_range,
            )

            # Freeze header row (idempotent)
            set_frozen(worksheet, rows=1)

            # Bold header (idempotent)
            header_fmt = CellFormat(
                textFormat=TextFormat(bold=True),
                backgroundColor=Color(0.9, 0.93, 0.98),  # Light blue
            )
            format_cell_range(worksheet, "1:1", header_fmt)

            if num_new_leads == 0:
                return

            # Calculate row range for new leads
            # If start_row == 1, leads start at row 2 (after header)
            first_data_row = start_row + 1 if start_row == 1 else start_row
            last_data_row = first_data_row + num_new_leads - 1

            # Data validation: Perfect Fit? checkbox
            checkbox_rule = DataValidationRule(
                BooleanCondition("BOOLEAN", []),
                showCustomUi=True,
            )
            checkbox_col = chr(ord('A') + len(AUTO_COLUMNS))  # First HITL column
            set_data_validation_for_cell_range(
                worksheet,
                f"{checkbox_col}{first_data_row}:{checkbox_col}{last_data_row}",
                checkbox_rule,
            )

            # Data validation: Reviewer Decision dropdown
            decision_col = chr(ord('A') + len(AUTO_COLUMNS) + 1)  # Second HITL column
            decision_rule = DataValidationRule(
                BooleanCondition("ONE_OF_LIST", REVIEWER_DECISION_VALUES),
                showCustomUi=True,
            )
            set_data_validation_for_cell_range(
                worksheet,
                f"{decision_col}{first_data_row}:{decision_col}{last_data_row}",
                decision_rule,
            )

            logger.info("Applied formatting and data validation to Recommended Contacts")

        except ImportError:
            logger.warning(
                "gspread-formatting not installed — skipping formatting. "
                "Install with: pip install gspread-formatting"
            )
        except Exception as e:
            logger.warning(f"Formatting failed (non-fatal): {e}")

    # =========================================================================
    # Write "Below Threshold" tab
    # =========================================================================

    def _write_below_threshold_tab(self, spreadsheet, leads: List[ScoutLead]):
        """Write below-threshold leads to a separate tab."""
        tab_name = "Below Threshold"

        try:
            worksheet = spreadsheet.worksheet(tab_name)
            worksheet.clear()
        except Exception:
            worksheet = spreadsheet.add_worksheet(
                title=tab_name,
                rows=len(leads) + 1,
                cols=len(HEADER_ROW),
            )

        rows = [HEADER_ROW]
        for lead in leads:
            rows.append(self._lead_to_row(lead))

        if rows:
            worksheet.update(rows, value_input_option="USER_ENTERED")

        logger.info(f"Wrote {len(leads)} leads to tab '{tab_name}'")

    # =========================================================================
    # Write to "Recommendation Run Metadata" tab (append row per run)
    # =========================================================================

    METADATA_HEADERS = [
        "Run ID", "Timestamp", "Input Type", "Request ID", "Request Title",
        "Total Leads", "Scored Leads", "Scoring Errors", "Above Threshold", "Threshold",
        "Scoring Model", "Avg Fit Score", "Max Fit Score",
        "Removed (already on Halo)",
    ]

    def _write_metadata_tab(self, spreadsheet, leads: List[ScoutLead], run_id: str, metrics: Optional[dict] = None):
        """Append a metadata row to the 'Recommendation Run Metadata' tab."""
        tab_name = "Recommendation Run Metadata"

        try:
            worksheet = spreadsheet.worksheet(tab_name)
        except Exception:
            # Create the tab with headers if it doesn't exist
            worksheet = spreadsheet.add_worksheet(title=tab_name, rows=100, cols=len(self.METADATA_HEADERS))
            worksheet.update([self.METADATA_HEADERS], value_input_option="RAW")
            logger.info(f"Created '{tab_name}' tab with headers")

        # Ensure headers exist (in case tab was created empty)
        existing_headers = worksheet.row_values(1)
        if not existing_headers:
            worksheet.update([self.METADATA_HEADERS], value_input_option="RAW")

        # Compute stats — exclude scoring errors from "scored" so averages aren't
        # contaminated by leads we never actually evaluated.
        scored = [l for l in leads if l.fit_score is not None and not l.scoring_error]
        above = [l for l in scored if l.fit_score >= self.config.min_fit_score]
        metrics = metrics or {}
        scoring_errors = sum(1 for l in leads if l.scoring_error)
        removed_halo = metrics.get("halo_removed", 0)

        row = [
            run_id,
            datetime.now().isoformat(),
            self.config.input_type.value,
            str(self.config.request_id or "N/A"),
            self.config.request_title or "N/A",
            len(leads),
            len(scored),
            scoring_errors,
            len(above),
            self.config.min_fit_score,
            self.config.score_model,
            f"{sum(l.fit_score for l in scored) / len(scored):.2f}" if scored else "N/A",
            f"{max(l.fit_score for l in scored):.2f}" if scored else "N/A",
            removed_halo,
        ]

        # Append to next empty row
        worksheet.append_row(row, value_input_option="RAW")
        logger.info(f"Appended run metadata to '{tab_name}'")

    # =========================================================================
    # CSV fallback
    # =========================================================================

    def write_csv(self, leads: List[ScoutLead], output_path: str) -> str:
        """
        Alternative: Write leads to a local CSV file.
        Useful for testing without Google Sheets credentials.
        """
        import csv
        from pathlib import Path

        scored_leads = sorted(
            [l for l in leads if l.fit_score is not None],
            key=lambda l: l.fit_score,
            reverse=True,
        )

        path = Path(output_path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(HEADER_ROW)

            for lead in scored_leads:
                writer.writerow(self._lead_to_row(lead))

        logger.info(f"Wrote {len(scored_leads)} leads to {path}")
        return str(path)
