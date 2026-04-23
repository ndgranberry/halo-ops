#!/usr/bin/env python3
"""
Agent Scout — Feedback Collector
==================================
Reads human-reviewed Google Sheets, extracts labeled examples,
deduplicates, and persists as a structured JSON dataset for GEPA optimization.
"""

import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
SA_PATH = BASE_DIR / "google_service_account.json"
FEEDBACK_DIR = BASE_DIR / "feedback_data"
FEEDBACK_DIR.mkdir(exist_ok=True)
DATASET_PATH = FEEDBACK_DIR / "labeled_examples.json"
CONFIG_PATH = BASE_DIR / "feedback_config.json"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Map reviewer decisions to target scores for the metric
DECISION_TARGET_SCORES = {
    "Approve": 0.88,
    "Maybe": 0.62,
    "Need More Info": 0.55,
    "Reject": 0.25,
}


def _dedup_key(example: Dict[str, Any]) -> str:
    """Composite key for deduplication: (sheet, first_name, last_name, company)."""
    parts = [
        str(example.get("sheet_key", "")),
        str(example.get("first_name", "")).strip().lower(),
        str(example.get("last_name", "")).strip().lower(),
        str(example.get("company", "")).strip().lower(),
    ]
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def _connect_sheets() -> gspread.Client:
    creds = Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    return gspread.authorize(creds)


def _find_recommended_contacts_tab(sheet: gspread.Spreadsheet) -> Optional[gspread.Worksheet]:
    """Find the Recommended Contacts tab (the one with reviewer feedback)."""
    for ws in sheet.worksheets():
        if "recommended" in ws.title.lower():
            return ws
    # Fallback: look for any tab with Reviewer Decision column
    for ws in sheet.worksheets():
        headers = ws.row_values(1)
        if any("reviewer" in h.lower() for h in headers):
            return ws
    return None


def _extract_examples_from_sheet(
    gc: gspread.Client,
    sheet_key: str,
    label: str,
    request_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Extract labeled examples from a single sheet."""
    try:
        sheet = gc.open_by_key(sheet_key)
    except Exception as e:
        logger.error(f"Cannot open sheet {sheet_key}: {e}")
        return []

    ws = _find_recommended_contacts_tab(sheet)
    if not ws:
        logger.warning(f"No Recommended Contacts tab in {label}")
        return []

    header = ws.row_values(1)
    if not any(h.strip() for h in header):
        # Header row missing — use fixed positional mapping
        POSITIONAL_HEADERS = [
            "First Name", "Last Name", "Company Name", "Title",
            "LinkedIn", "Email", "Fit Score", "Why They're a Fit",
            "Country", "Disciplines", "Keywords (Areas of Expertise)",
            "Company Description", "Discovery Source", "Perfect Fit?",
            "Reviewer Decision", "Reviewer Notes",
        ]
        values = ws.get_all_values()
        all_data = [
            {POSITIONAL_HEADERS[i]: (row[i] if i < len(row) else "")
             for i in range(len(POSITIONAL_HEADERS))}
            for row in values if any(c.strip() for c in row)
        ]
    else:
        all_data = ws.get_all_records()
    examples = []

    for row in all_data:
        # Skip empty rows and rows without a reviewer decision
        first_name = str(row.get("First Name", "")).strip()
        decision = str(row.get("Reviewer Decision", "")).strip()
        if not decision or decision not in DECISION_TARGET_SCORES:
            continue

        example = {
            "sheet_key": sheet_key,
            "sheet_label": label,
            "request_id": request_id,
            # Lead data
            "first_name": first_name,
            "last_name": str(row.get("Last Name", "")).strip(),
            "company": str(row.get("Company Name", "")).strip(),
            "title": str(row.get("Title", "")).strip(),
            "email": str(row.get("Email", "")).strip(),
            "linkedin": str(row.get("LinkedIn", "")).strip(),
            "country": str(row.get("Country", "")).strip(),
            "disciplines": str(row.get("Disciplines", "")).strip(),
            "keywords": str(row.get("Keywords (Areas of Expertise)", "")).strip(),
            "company_description": str(row.get("Company Description", "")).strip(),
            "discovery_source": str(row.get("Discovery Source", "")).strip(),
            # LLM predictions
            "fit_score": _safe_float(row.get("Fit Score", "")),
            "fit_blurb": str(row.get("Why They're a Fit", "")).strip(),
            # Human labels (the gold standard)
            "reviewer_decision": decision,
            "reviewer_notes": str(row.get("Reviewer Notes", "")).strip(),
            "perfect_fit": str(row.get("Perfect Fit?", "")).strip().upper() == "TRUE",
            # Derived
            "target_score": DECISION_TARGET_SCORES.get(decision, 0.5),
            "collected_at": datetime.now().isoformat(),
        }
        examples.append(example)

    logger.info(f"  {label}: {len(examples)} labeled examples extracted")
    return examples


def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def collect_all_feedback() -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Read all configured feedback sheets. Returns (full_dataset, stats).
    Deduplicates against existing data.
    """
    # Load existing dataset
    existing = _load_existing()
    existing_keys = {_dedup_key(e) for e in existing}
    logger.info(f"Existing dataset: {len(existing)} examples")

    # Load sheet config
    if not CONFIG_PATH.exists():
        logger.error(f"No feedback_config.json found at {CONFIG_PATH}")
        return existing, {"new": 0, "total": len(existing)}

    with open(CONFIG_PATH) as f:
        sheets = json.load(f)

    gc = _connect_sheets()
    new_count = 0

    for sheet_cfg in sheets:
        sheet_key = sheet_cfg["sheet_key"]
        label = sheet_cfg.get("label", sheet_key[:20])
        request_id = sheet_cfg.get("request_id")

        logger.info(f"Collecting feedback from: {label}")
        examples = _extract_examples_from_sheet(gc, sheet_key, label, request_id)

        for ex in examples:
            key = _dedup_key(ex)
            if key not in existing_keys:
                existing.append(ex)
                existing_keys.add(key)
                new_count += 1

    # Save
    _save_dataset(existing)

    stats = {
        "new": new_count,
        "total": len(existing),
        "approve": sum(1 for e in existing if e["reviewer_decision"] == "Approve"),
        "reject": sum(1 for e in existing if e["reviewer_decision"] == "Reject"),
        "maybe": sum(1 for e in existing if e["reviewer_decision"] == "Maybe"),
        "need_more_info": sum(1 for e in existing if e["reviewer_decision"] == "Need More Info"),
        "sheets": len(sheets),
    }
    logger.info(f"Dataset updated: {new_count} new, {len(existing)} total")
    return existing, stats


def load_dataset() -> List[Dict[str, Any]]:
    """Load the persisted labeled dataset."""
    return _load_existing()


def _load_existing() -> List[Dict[str, Any]]:
    if DATASET_PATH.exists():
        with open(DATASET_PATH) as f:
            return json.load(f)
    return []


def _save_dataset(data: List[Dict[str, Any]]) -> None:
    with open(DATASET_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"  Saved {len(data)} examples to {DATASET_PATH}")


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    dataset, stats = collect_all_feedback()
    print(f"\n{'='*60}")
    print(f"Feedback Collection Complete")
    print(f"{'='*60}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
