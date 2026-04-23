#!/usr/bin/env python3
"""
Agent Scout — Academic Email Enrichment
=========================================
Finds email addresses for academic researchers using ORCID, OpenAlex,
and Exa faculty page search. Designed as a complement to n8n webhook
enrichment which has weak coverage for university contacts.

Sources (in order of reliability):
1. ORCID — self-reported researcher email (highest reliability)
2. OpenAlex paper affiliations — email from paper metadata (needs name validation)
3. Exa faculty page search — email from university profile pages (highest yield)
"""

import logging
import os
import re
import time
from typing import List, Optional, Dict, Any

import requests

from models_scout import ScoutLead

logger = logging.getLogger(__name__)

# OpenAlex polite pool — include mailto for faster rate limits
OPENALEX_MAILTO = "neil@halo.science"
OPENALEX_DELAY = 0.2  # seconds between requests
ORCID_DELAY = 0.2

# Email regex for extraction from text
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')


class AcademicEnricher:
    """Find emails for academic leads via ORCID, OpenAlex, and Exa faculty search."""

    def __init__(self, mailto: str = OPENALEX_MAILTO):
        self.mailto = mailto
        self._exa = None  # Lazy-loaded

    @property
    def exa(self):
        """Lazy-load Exa client (only needed for Step 4)."""
        if self._exa is None:
            api_key = os.getenv("EXA_API_KEY")
            if api_key:
                from exa_py import Exa
                self._exa = Exa(api_key=api_key)
            else:
                logger.warning("EXA_API_KEY not set — faculty page search disabled")
        return self._exa

    def enrich(self, leads: List[ScoutLead]) -> List[ScoutLead]:
        """
        Run academic enrichment on leads missing emails.
        Modifies leads in place and returns the list.
        """
        needs_email = [l for l in leads if not l.email and l.first_name and l.last_name]
        if not needs_email:
            logger.info("No leads need academic enrichment")
            return leads

        logger.info(f"Academic enrichment: {len(needs_email)} leads without emails")

        # Step 1: OpenAlex author search → get ORCID IDs and works URLs
        author_data = self._openalex_author_search(needs_email)

        # Step 2: ORCID email lookup
        still_needs = [l for l in needs_email if not l.email]
        if still_needs:
            self._orcid_email_lookup(still_needs, author_data)

        # Step 3: OpenAlex paper affiliation emails
        still_needs = [l for l in needs_email if not l.email]
        if still_needs:
            self._openalex_paper_emails(still_needs, author_data)

        # Step 4: Exa faculty page search
        still_needs = [l for l in needs_email if not l.email]
        if still_needs and self.exa:
            self._exa_faculty_search(still_needs)

        # Report results
        found = sum(1 for l in needs_email if l.email)
        logger.info(
            f"Academic enrichment complete: {found}/{len(needs_email)} emails found"
        )
        by_source = {}
        for l in needs_email:
            if l.email and l.email_source:
                by_source[l.email_source] = by_source.get(l.email_source, 0) + 1
        for source, count in sorted(by_source.items()):
            logger.info(f"  {source}: {count}")

        return leads

    # =========================================================================
    # Step 1: OpenAlex Author Search
    # =========================================================================

    def _openalex_author_search(
        self, leads: List[ScoutLead]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Search OpenAlex for each lead. Returns a dict keyed by lead key
        with ORCID IDs and works URLs.
        """
        author_data: Dict[str, Dict[str, Any]] = {}

        for lead in leads:
            key = self._lead_key(lead)
            name = f"{lead.first_name} {lead.last_name}"

            try:
                resp = requests.get(
                    "https://api.openalex.org/authors",
                    params={
                        "search": name,
                        "per_page": 5,
                        "mailto": self.mailto,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])

                match = self._match_author(results, lead)
                if match:
                    orcid = (match.get("ids") or {}).get("orcid", "")
                    if orcid:
                        # Normalize: "https://orcid.org/0000-..." → "0000-..."
                        orcid_id = orcid.replace("https://orcid.org/", "")
                        lead.orcid_id = orcid_id

                    author_data[key] = {
                        "orcid_id": lead.orcid_id,
                        "works_api_url": match.get("works_api_url", ""),
                        "display_name": match.get("display_name", ""),
                    }

            except Exception as e:
                logger.debug(f"OpenAlex search failed for {name}: {e}")

            time.sleep(OPENALEX_DELAY)

        found_orcid = sum(1 for d in author_data.values() if d.get("orcid_id"))
        logger.info(
            f"  OpenAlex: matched {len(author_data)}/{len(leads)} authors, "
            f"{found_orcid} with ORCID IDs"
        )
        return author_data

    def _match_author(
        self, results: List[Dict], lead: ScoutLead
    ) -> Optional[Dict]:
        """Find the best matching author from OpenAlex results."""
        first = (lead.first_name or "").lower()
        last = (lead.last_name or "").lower()
        institution = (lead.company or "").lower()

        for author in results:
            display = (author.get("display_name") or "").lower()
            if first in display and last in display:
                # If we have institution info, prefer matches at the right one
                if institution:
                    affiliations = author.get("last_known_institutions") or []
                    for aff in affiliations:
                        aff_name = (aff.get("display_name") or "").lower()
                        if any(w in aff_name for w in institution.split()[:2] if len(w) > 3):
                            return author
                # Accept name match even without institution confirmation
                return author
        return None

    # =========================================================================
    # Step 2: ORCID Email Lookup
    # =========================================================================

    def _orcid_email_lookup(
        self, leads: List[ScoutLead], author_data: Dict[str, Dict]
    ):
        """Check ORCID for self-reported public emails."""
        leads_with_orcid = [l for l in leads if l.orcid_id]
        if not leads_with_orcid:
            return

        found = 0
        for lead in leads_with_orcid:
            try:
                resp = requests.get(
                    f"https://pub.orcid.org/v3.0/{lead.orcid_id}/email",
                    headers={"Accept": "application/json"},
                    timeout=10,
                )
                resp.raise_for_status()
                emails = resp.json().get("email", [])

                for entry in emails:
                    email = entry.get("email")
                    if email:
                        lead.email = email
                        lead.email_source = "orcid"
                        found += 1
                        break

            except Exception as e:
                logger.debug(f"ORCID lookup failed for {lead.orcid_id}: {e}")

            time.sleep(ORCID_DELAY)

        logger.info(f"  ORCID: {found} emails found from {len(leads_with_orcid)} lookups")

    # =========================================================================
    # Step 3: OpenAlex Paper Affiliation Emails
    # =========================================================================

    def _openalex_paper_emails(
        self, leads: List[ScoutLead], author_data: Dict[str, Dict]
    ):
        """Extract emails from paper affiliation strings with strict name validation."""
        found = 0

        for lead in leads:
            key = self._lead_key(lead)
            data = author_data.get(key)
            if not data or not data.get("works_api_url"):
                continue

            try:
                works_url = data["works_api_url"]
                resp = requests.get(
                    works_url,
                    params={
                        "sort": "publication_date:desc",
                        "per_page": 10,
                        "mailto": self.mailto,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                works = resp.json().get("results", [])

                email = self._extract_validated_email(works, lead)
                if email:
                    lead.email = email
                    lead.email_source = "openalex"
                    found += 1

            except Exception as e:
                logger.debug(f"OpenAlex works fetch failed for {lead.full_name()}: {e}")

            time.sleep(OPENALEX_DELAY)

        logger.info(f"  OpenAlex papers: {found} validated emails")

    def _extract_validated_email(
        self, works: List[Dict], lead: ScoutLead
    ) -> Optional[str]:
        """
        Extract email from paper affiliation strings.
        CRITICAL: validates that the email belongs to the actual contact,
        not a co-author.
        """
        first = (lead.first_name or "").lower()
        last = (lead.last_name or "").lower()
        institution_domain = self._guess_domain(lead.company)

        for work in works:
            for authorship in work.get("authorships", []):
                for aff_str in authorship.get("raw_affiliation_strings", []):
                    emails = EMAIL_RE.findall(aff_str)
                    for email in emails:
                        email_lower = email.lower()
                        username = email_lower.split("@")[0]
                        domain = email_lower.split("@")[1]

                        # Name validation: username must contain part of the name
                        name_match = (
                            last in username or
                            first in username or
                            (len(last) > 3 and last[:4] in username)
                        )

                        # Domain validation (optional, strengthens match)
                        domain_match = True
                        if institution_domain:
                            domain_match = institution_domain in domain

                        if name_match and domain_match:
                            return email

        return None

    # =========================================================================
    # Step 4: Exa Faculty Page Search
    # =========================================================================

    def _exa_faculty_search(self, leads: List[ScoutLead]):
        """Search for faculty profile pages and extract emails."""
        found = 0

        for lead in leads:
            name = f"{lead.first_name} {lead.last_name}"
            institution = lead.company or ""
            query = f'"{name}" {institution} email faculty profile'

            try:
                response = self.exa.search_and_contents(
                    query=query,
                    num_results=3,
                    type="keyword",
                    text={"max_characters": 3000},
                )

                for result in response.results:
                    text = getattr(result, "text", "") or ""
                    title = getattr(result, "title", "") or ""

                    # Look for email in page content
                    emails = EMAIL_RE.findall(text)
                    email = self._pick_best_email(emails, lead)
                    if email:
                        lead.email = email
                        lead.email_source = "faculty_page"
                        found += 1
                        break

            except Exception as e:
                logger.debug(f"Exa faculty search failed for {name}: {e}")

            time.sleep(1.0)  # Exa rate limit

        logger.info(f"  Exa faculty pages: {found} emails found")

    def _pick_best_email(
        self, emails: List[str], lead: ScoutLead
    ) -> Optional[str]:
        """Pick the email most likely to belong to this lead."""
        first = (lead.first_name or "").lower()
        last = (lead.last_name or "").lower()
        institution_domain = self._guess_domain(lead.company)

        for email in emails:
            email_lower = email.lower()
            username = email_lower.split("@")[0]
            domain = email_lower.split("@")[1]

            # Skip generic emails
            if username in ("info", "contact", "admin", "webmaster", "office"):
                continue

            # Name must appear in username
            name_match = last in username or first in username
            if not name_match:
                continue

            # Prefer institutional domain
            if institution_domain and institution_domain in domain:
                return email

            # Accept if name matches even without domain match
            return email

        return None

    # =========================================================================
    # Utilities
    # =========================================================================

    @staticmethod
    def _lead_key(lead: ScoutLead) -> str:
        """Create a lookup key for a lead."""
        return f"{(lead.first_name or '').lower()}|{(lead.last_name or '').lower()}"

    @staticmethod
    def _guess_domain(company: Optional[str]) -> Optional[str]:
        """
        Guess the email domain root from an institution name.
        E.g. "Wageningen University" → "wur" won't work, but
        "University of Leeds" → "leeds" can help validate.
        """
        if not company:
            return None

        company_lower = company.lower()

        # Extract likely domain keyword from institution name
        # Remove common prefixes/suffixes
        for prefix in ["university of ", "the ", "universität ", "université "]:
            if company_lower.startswith(prefix):
                return company_lower[len(prefix):].split()[0]

        # For "X University", use first word
        for suffix in [" university", " institute", " college"]:
            if company_lower.endswith(suffix):
                return company_lower[:company_lower.index(suffix)].split()[-1]

        return None
