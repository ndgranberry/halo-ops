#!/usr/bin/env python3
"""
Agent Scout — Contact Enrichment (Module 3)
=============================================
Thin Python wrapper around existing n8n enrichment waterfall.
Sends batches of leads to n8n webhook (Amplemarket → Findymail → Apollo),
polls for results, maps enriched data back to ScoutLead objects.
"""

import json
import logging
import os
import re
import time
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

import requests

from .models import ScoutLead, LeadStatus
from .domain_resolver import DomainResolver

logger = logging.getLogger(__name__)

# Default timeout for n8n webhook responses
WEBHOOK_TIMEOUT_SECONDS = 300  # 5 minutes — n8n waterfall can be slow
POLL_INTERVAL_SECONDS = 10
MAX_POLL_RETRIES = 30  # 30 * 10s = 5 minutes max wait


class ContactEnricher:
    """Enrich leads via n8n waterfall webhook (Amplemarket → Findymail → Apollo)."""

    def __init__(self, webhook_url: Optional[str] = None, batch_size: int = 3):
        self.webhook_url = webhook_url or os.getenv("N8N_ENRICHMENT_WEBHOOK_URL")
        self.batch_size = batch_size
        self.domain_resolver = DomainResolver()

        if not self.webhook_url:
            logger.warning(
                "No n8n enrichment webhook URL configured. "
                "Set N8N_ENRICHMENT_WEBHOOK_URL or pass webhook_url. "
                "Enrichment will be skipped."
            )

    def enrich(self, leads: List[ScoutLead], run_id: str = "") -> List[ScoutLead]:
        """
        Enrich a list of leads via the n8n waterfall webhook.

        Sends leads in batches, collects results, updates lead objects.
        Leads that fail enrichment are marked but don't block the pipeline.
        """
        if not self.webhook_url:
            logger.info("Skipping enrichment (no webhook URL configured)")
            for lead in leads:
                lead.status = LeadStatus.ENRICHMENT_COMPLETE
            return leads

        # Split into batches
        batches = [leads[i:i + self.batch_size] for i in range(0, len(leads), self.batch_size)]
        logger.info(f"Enriching {len(leads)} leads in {len(batches)} batch(es)")

        for batch_idx, batch in enumerate(batches):
            logger.info(f"Sending batch {batch_idx + 1}/{len(batches)} ({len(batch)} leads)")

            # Mark as pending
            for lead in batch:
                lead.status = LeadStatus.ENRICHMENT_PENDING

            try:
                enriched_data = self._send_batch(batch, run_id, batch_idx)
                self._apply_enrichment(batch, enriched_data)
            except Exception as e:
                logger.error(f"Batch {batch_idx + 1} failed: {e}")
                for lead in batch:
                    lead.status = LeadStatus.ENRICHMENT_FAILED
                    lead.errors.append(f"Enrichment failed: {str(e)}")

            # Rate limiting between batches
            if batch_idx < len(batches) - 1:
                time.sleep(2)

        enriched = sum(1 for l in leads if l.status == LeadStatus.ENRICHMENT_COMPLETE)
        failed = sum(1 for l in leads if l.status == LeadStatus.ENRICHMENT_FAILED)
        logger.info(f"Enrichment complete: {enriched} enriched, {failed} failed")

        return leads

    def _send_batch(
        self, batch: List[ScoutLead], run_id: str, batch_idx: int
    ) -> List[Dict[str, Any]]:
        """Send a batch to the n8n webhook and get enriched results."""
        payload = {
            "run_id": run_id,
            "batch_id": batch_idx,
            "leads": [
                {
                    "first_name": lead.first_name or "",
                    "last_name": lead.last_name or "",
                    "company": lead.company or "",
                    "company_domain": self.domain_resolver.resolve(
                        lead.company or "",
                        email=lead.email or "",
                        company_description=lead.company_description or "",
                    ),
                    "linkedin_url": lead.linkedin_url or "",
                    "email": lead.email or "",
                    "title": lead.title or "",
                }
                for lead in batch
            ],
        }

        response = requests.post(
            self.webhook_url,
            json=payload,
            timeout=WEBHOOK_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        result = response.json()

        # The n8n webhook can return results directly (synchronous)
        # or return a polling URL (asynchronous)
        if "leads" in result:
            return result["leads"]
        elif "poll_url" in result:
            return self._poll_for_results(result["poll_url"])
        else:
            # Assume the response itself is the list of enriched leads
            if isinstance(result, list):
                return result
            logger.warning(f"Unexpected webhook response format: {list(result.keys())}")
            return []

    def _poll_for_results(self, poll_url: str) -> List[Dict[str, Any]]:
        """Poll n8n for async enrichment results."""
        for attempt in range(MAX_POLL_RETRIES):
            time.sleep(POLL_INTERVAL_SECONDS)
            logger.info(f"Polling for results (attempt {attempt + 1}/{MAX_POLL_RETRIES})")

            response = requests.get(poll_url, timeout=30)
            response.raise_for_status()
            result = response.json()

            status = result.get("status", "")
            if status == "complete":
                return result.get("leads", [])
            elif status == "failed":
                raise RuntimeError(f"Enrichment failed: {result.get('error', 'unknown')}")
            # else: still processing, continue polling

        raise TimeoutError(f"Enrichment polling timed out after {MAX_POLL_RETRIES * POLL_INTERVAL_SECONDS}s")

    def _apply_enrichment(
        self, leads: List[ScoutLead], enriched_data: List[Dict[str, Any]]
    ):
        """Map enriched data back to ScoutLead objects.

        n8n returns fields with capitalized keys (First Name, Email, LinkedIn, etc.)
        so we normalize both formats.
        """
        # Build lookup by name+company for matching
        enriched_lookup = {}
        for item in enriched_data:
            fn = item.get("first_name") or item.get("First Name") or ""
            ln = item.get("last_name") or item.get("Last Name") or ""
            co = item.get("company") or item.get("Company Name") or ""
            key = self._match_key(fn, ln, co)
            enriched_lookup[key] = item

        for lead in leads:
            key = self._match_key(
                lead.first_name or "", lead.last_name or "", lead.company or ""
            )
            enriched = enriched_lookup.get(key)

            if enriched:
                # Normalize field access (n8n uses capitalized keys)
                email = enriched.get("email") or enriched.get("Email") or ""
                title = enriched.get("title") or enriched.get("Title") or ""
                linkedin = enriched.get("linkedin_url") or enriched.get("LinkedIn") or ""
                source = enriched.get("Source") or "n8n"

                if email:
                    lead.email = email
                    lead.email_source = f"n8n:{source}"
                if title and not lead.title:
                    lead.title = title
                if linkedin and not lead.linkedin_url:
                    lead.linkedin_url = linkedin

                lead.status = LeadStatus.ENRICHMENT_COMPLETE
            else:
                # No match found — keep existing data, mark as complete anyway
                lead.status = LeadStatus.ENRICHMENT_COMPLETE
                if not lead.email:
                    lead.errors.append("No enrichment data returned for this lead")

    @staticmethod
    def _match_key(first_name: str, last_name: str, company: str) -> str:
        """Create a normalized matching key."""
        return f"{first_name.lower().strip()}|{last_name.lower().strip()}|{company.lower().strip()}"
