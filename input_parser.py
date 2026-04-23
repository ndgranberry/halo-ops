#!/usr/bin/env python3
"""
Agent Scout — Input Parser (Module 1)
======================================
Normalizes all 4 input types into ScoutConfig + List[ScoutLead].

Phase 1 MVP: Only type 3 (scraped list) is implemented.
Phase 2 will add types 1, 2, 4 via Snowflake and company discovery.
"""

import csv
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from models import ScoutLead, ScoutConfig, InputType, LeadStatus

logger = logging.getLogger(__name__)


class InputParser:
    """Parse and normalize inputs into the scouting pipeline format."""

    def __init__(self, config: ScoutConfig):
        self.config = config

    def parse(self) -> Tuple[ScoutConfig, List[ScoutLead]]:
        """
        Parse input based on config.input_type.
        Returns updated config and initial lead list.
        """
        parsers = {
            InputType.SCRAPED_LIST: self._parse_scraped_list,
            InputType.COMPANY_LIST: self._parse_company_list,
            InputType.PARTNERING_REQUEST: self._parse_partnering_request,
            InputType.REQUEST_WITH_EXAMPLES: self._parse_request_with_examples,
        }

        parser_fn = parsers.get(self.config.input_type)
        if not parser_fn:
            raise ValueError(f"Unknown input type: {self.config.input_type}")

        return parser_fn()

    # =========================================================================
    # Type 3: Scraped List (name + company)
    # =========================================================================
    def _parse_scraped_list(self) -> Tuple[ScoutConfig, List[ScoutLead]]:
        """Parse a scraped list of name + company from CSV or Google Sheet."""
        leads = []

        if self.config.input_csv_path:
            leads = self._read_csv(self.config.input_csv_path)
        elif self.config.input_sheet_url:
            leads = self._read_google_sheet(self.config.input_sheet_url)
        else:
            raise ValueError("Type 3 requires input_csv_path or input_sheet_url")

        # Load request context if request_id provided (for scoring later)
        if self.config.request_id and not self.config.request_looking_for:
            self._load_request_from_snowflake()

        logger.info(f"Parsed {len(leads)} leads from scraped list")
        return self.config, leads

    def _read_csv(self, csv_path: str) -> List[ScoutLead]:
        """Read leads from a local CSV file."""
        leads = []
        path = Path(csv_path)

        if not path.exists():
            raise FileNotFoundError(f"CSV not found: {csv_path}")

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Normalize column names to lowercase
            for row in reader:
                normalized = {k.lower().strip(): v.strip() for k, v in row.items() if v}
                lead = self._row_to_lead(normalized)
                if lead:
                    leads.append(lead)

        return leads

    def _read_google_sheet_raw(self, sheet_url: str, tab_name: Optional[str] = None) -> List[dict]:
        """Read raw row dicts from a Google Sheet tab. Normalizes column names."""
        try:
            import gspread
        except ImportError:
            raise ImportError("gspread is required for Google Sheets. pip install gspread google-auth")

        creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if creds_path:
            gc = gspread.service_account(filename=creds_path)
        else:
            gc = gspread.oauth()

        spreadsheet = gc.open_by_url(sheet_url)

        if tab_name:
            worksheet = spreadsheet.worksheet(tab_name)
        else:
            worksheet = spreadsheet.sheet1

        records = worksheet.get_all_records()

        # Normalize column names: lowercase, strip whitespace, remove [bracketed] suffixes
        normalized_rows = []
        for row in records:
            normalized = {}
            for k, v in row.items():
                if v == "" or v is None:
                    continue
                clean_key = re.sub(r'\s*\[.*?\]\s*', '', str(k)).lower().strip()
                normalized[clean_key] = str(v).strip()
            normalized_rows.append(normalized)

        return normalized_rows

    def _read_google_sheet(self, sheet_url: str) -> List[ScoutLead]:
        """Read leads from a Google Sheet (Type 3 wrapper)."""
        raw_rows = self._read_google_sheet_raw(sheet_url)

        leads = []
        for row in raw_rows:
            lead = self._row_to_lead(row)
            if lead:
                leads.append(lead)

        return leads

    def _row_to_lead(self, row: dict) -> Optional[ScoutLead]:
        """Convert a normalized row dict to a ScoutLead."""
        # Try common column name variations
        first_name = (
            row.get("first_name") or row.get("first name") or
            row.get("firstname") or row.get("first") or ""
        )
        last_name = (
            row.get("last_name") or row.get("last name") or
            row.get("lastname") or row.get("last") or ""
        )

        # Handle "name" as a single field
        full_name = row.get("name") or row.get("full_name") or row.get("full name") or ""
        if full_name and not first_name:
            parts = full_name.split(None, 1)
            first_name = parts[0] if parts else ""
            last_name = parts[1] if len(parts) > 1 else ""

        company = (
            row.get("company") or row.get("organization") or
            row.get("company_name") or row.get("company name") or ""
        )

        # Skip rows with no useful data
        if not first_name and not company:
            return None

        return ScoutLead(
            first_name=first_name or None,
            last_name=last_name or None,
            company=company or None,
            title=row.get("title") or row.get("job_title") or row.get("job title") or None,
            email=row.get("email") or row.get("email_address") or None,
            linkedin_url=row.get("linkedin") or row.get("linkedin_url") or row.get("linkedin url") or None,
            bio=row.get("bio") or row.get("description") or row.get("about") or None,
            discovery_source="input",
            status=LeadStatus.PARSED,
            raw_input=row,
        )

    # =========================================================================
    # Type 4: Company List (Phase 2)
    # =========================================================================
    def _parse_company_list(self) -> Tuple[ScoutConfig, List[ScoutLead]]:
        """Parse a list of companies. Person discovery will find people."""
        if not self.config.companies:
            raise ValueError("Type 4 requires a list of companies in config.companies")

        leads = [
            ScoutLead(company=company.strip(), discovery_source="input", status=LeadStatus.PARSED)
            for company in self.config.companies
            if company.strip()
        ]

        if self.config.request_id and not self.config.request_looking_for:
            self._load_request_from_snowflake()

        logger.info(f"Parsed {len(leads)} companies for discovery")
        return self.config, leads

    # =========================================================================
    # Type 1: Partnering Request (Phase 2)
    # =========================================================================
    def _parse_partnering_request(self) -> Tuple[ScoutConfig, List[ScoutLead]]:
        """Load request from Snowflake. Person discovery will find leads."""
        if not self.config.request_id and not self.config.request_looking_for:
            raise ValueError("Type 1 requires request_id or request_looking_for text")

        if self.config.request_id and not self.config.request_looking_for:
            self._load_request_from_snowflake()

        logger.info(f"Loaded request: {self.config.request_title}")
        return self.config, []  # Empty leads — discovery will populate

    # =========================================================================
    # Type 2: Request + Examples
    # =========================================================================
    def _parse_request_with_examples(self) -> Tuple[ScoutConfig, List[ScoutLead]]:
        """Load request context + example innovators from Google Sheet or config."""

        # If example_innovators already provided (e.g., from JSON config), use them
        if self.config.example_innovators:
            config, leads = self._parse_partnering_request()
            logger.info(f"Using {len(self.config.example_innovators)} pre-loaded example innovators")
            return config, leads

        # Otherwise, read from Google Sheet
        if not self.config.input_sheet_url:
            raise ValueError("Type 2 requires example_innovators or input_sheet_url")

        # 1. Read all rows from the specified tab
        raw_rows = self._read_google_sheet_raw(
            self.config.input_sheet_url,
            tab_name=self.config.input_sheet_tab,
        )
        logger.info(f"Read {len(raw_rows)} rows from Google Sheet")

        # 2. Filter to "Perfect fit?" = TRUE
        examples = self._filter_perfect_fit_examples(raw_rows)
        logger.info(f"Found {len(examples)} 'Perfect fit' examples out of {len(raw_rows)} total rows")

        if not examples:
            raise ValueError("No rows with 'Perfect fit?' = TRUE found in the sheet")

        # 3. Convert to rich example_innovators format
        self.config.example_innovators = [self._row_to_example(row) for row in examples]

        # 4. Extract aggregate patterns for discovery enhancement
        self.config.example_patterns = self._extract_example_patterns(examples)

        # 5. Load request context from Snowflake
        config, leads = self._parse_partnering_request()

        logger.info(
            f"Loaded {len(self.config.example_innovators)} example innovators | "
            f"Patterns: {self.config.example_patterns.get('example_count', 0)} examples, "
            f"{len(self.config.example_patterns.get('common_titles', []))} title patterns, "
            f"{len(self.config.example_patterns.get('areas_of_expertise', []))} expertise areas"
        )
        return config, leads

    def _filter_perfect_fit_examples(self, raw_rows: List[dict]) -> List[dict]:
        """Filter rows where 'Perfect fit?' is truthy."""
        examples = []
        for row in raw_rows:
            perfect_fit = row.get("perfect fit?", "").upper()
            if perfect_fit in ("TRUE", "YES", "1", "X"):
                examples.append(row)
        return examples

    def _row_to_example(self, row: dict) -> Dict[str, Any]:
        """Convert a normalized sheet row to a rich example innovator dict."""
        first = row.get("first name", "")
        last = row.get("last name", "")
        name = f"{first} {last}".strip()
        company = row.get("company name", "")
        title = row.get("job title", "")
        email = row.get("email", "") or row.get("verified email", "")
        domain = row.get("company domain", "")
        user_type = row.get("user type", "")

        # Extract domain from email if company domain not provided
        if not domain and email and "@" in email:
            domain = email.split("@")[1]

        # Build a synthesized "why_good_fit" from available signals
        expertise = row.get("areas of expertise - added by halo", "") or row.get("areas of expertise", "")
        disciplines = row.get("disciplines - added by halo", "") or row.get("disciplines", "")
        lead_source = row.get("scientist - lead source", "")
        lead_category = row.get("scientist - lead source - category", "")

        reason_parts = []
        if title and company:
            reason_parts.append(f"{title} at {company}")
        if expertise:
            reason_parts.append(f"Expertise: {expertise}")
        if disciplines:
            reason_parts.append(f"Disciplines: {disciplines}")
        if lead_source:
            reason_parts.append(f"Source: {lead_source}")

        return {
            "name": name,
            "company": company,
            "title": title,
            "email": email,
            "domain": domain,
            "user_type": user_type,
            "linkedin": row.get("linkedin (optional)", "") or row.get("linkedin", ""),
            "country": row.get("country", ""),
            "areas_of_expertise": expertise,
            "disciplines": disciplines,
            "lead_source": lead_source,
            "lead_source_category": lead_category,
            "department_head": row.get("department head", ""),
            "halo_user_role": row.get("halo user role", ""),
            "why_good_fit": ". ".join(reason_parts) if reason_parts else "Confirmed good fit",
        }

    def _extract_example_patterns(self, examples: List[dict]) -> Dict[str, Any]:
        """Extract aggregate patterns from perfect-fit examples for discovery."""
        titles: Counter = Counter()
        user_types: Counter = Counter()
        domains: Counter = Counter()
        countries: Counter = Counter()
        expertise_terms: Counter = Counter()
        disciplines: Counter = Counter()
        lead_categories: Counter = Counter()

        for row in examples:
            if row.get("job title"):
                titles[row["job title"]] += 1
            if row.get("user type"):
                user_types[row["user type"]] += 1

            # Domain extraction
            domain = row.get("company domain", "")
            email = row.get("email", "") or row.get("verified email", "")
            if not domain and email and "@" in email:
                domain = email.split("@")[1]
            if domain:
                domains[domain] += 1

            if row.get("country"):
                countries[row["country"]] += 1

            # Split comma-separated expertise/discipline fields
            expertise_raw = (
                row.get("areas of expertise - added by halo", "")
                or row.get("areas of expertise", "")
                or ""
            )
            for term in expertise_raw.split(","):
                term = term.strip()
                if term:
                    expertise_terms[term] += 1

            disciplines_raw = (
                row.get("disciplines - added by halo", "")
                or row.get("disciplines", "")
                or ""
            )
            for disc in disciplines_raw.split(","):
                disc = disc.strip()
                if disc:
                    disciplines[disc] += 1

            if row.get("scientist - lead source - category"):
                lead_categories[row["scientist - lead source - category"]] += 1

        return {
            "common_titles": [t for t, _ in titles.most_common(15)],
            "common_user_types": [t for t, _ in user_types.most_common()],
            "common_domains": [d for d, _ in domains.most_common(20)],
            "common_countries": [c for c, _ in countries.most_common(10)],
            "areas_of_expertise": [e for e, _ in expertise_terms.most_common(20)],
            "disciplines": [d for d, _ in disciplines.most_common(15)],
            "lead_source_categories": [c for c, _ in lead_categories.most_common()],
            "example_count": len(examples),
            "startup_count": user_types.get("Startup", 0),
            "scientist_count": user_types.get("Scientist", 0),
        }

    # =========================================================================
    # Snowflake Integration (Phase 2)
    # =========================================================================
    def _load_request_from_snowflake(self):
        """Load request context from Snowflake OPS_REQUESTS_DATA."""
        try:
            from snowflake_client import get_request_data
            request = get_request_data(self.config.request_id)
            self.config.request_title = request.get("TITLE", "")
            self.config.request_looking_for = request.get("LOOKING_FOR", "")
            self.config.request_use_case = request.get("USE_CASE", "")
            self.config.request_sois = request.get("SOLUTIONS_OF_INTEREST", "")
            self.config.request_partner_types = request.get("PARTNER_TYPES", "")
            self.config.request_trl_range = request.get("TRL_RANGE", "")
            self.config.request_requirements = request.get("REQUIREMENTS", "")
            self.config.request_out_of_scope = request.get("OUT_OF_SCOPE", "")
            logger.info(f"Loaded request {self.config.request_id} from Snowflake: {self.config.request_title}")
        except ImportError:
            logger.warning("snowflake_client not available. Provide request context manually.")
        except Exception as e:
            logger.error(f"Failed to load request from Snowflake: {e}")
