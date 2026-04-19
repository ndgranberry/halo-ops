#!/usr/bin/env python3
"""
RoboScout Query Generator — Semantic Scholar API Client
========================================================
Thin wrapper around the Semantic Scholar Academic Graph API.
Used for query validation: get result counts and sample papers.
"""

import logging
import os
import time
from typing import List, Optional, Tuple

import requests

logger = logging.getLogger("roboscout_query_gen.semantic_scholar")

BASE_URL = "https://api.semanticscholar.org/graph/v1"


class SemanticScholarClient:
    """Client for Semantic Scholar paper search API."""

    def __init__(self, api_key: Optional[str] = None, rate_limit_delay: float = None):
        self.api_key = api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
        # Use faster rate if authenticated, slower if sharing the public pool
        if rate_limit_delay is not None:
            self.rate_limit_delay = rate_limit_delay
        elif self.api_key:
            self.rate_limit_delay = 1.0
        else:
            self.rate_limit_delay = 3.0
        self._last_request_time = 0.0
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["x-api-key"] = self.api_key
            logger.info("Using authenticated S2 API access")
        else:
            logger.info("No S2 API key — using unauthenticated access (slower rate limit)")

    def get_result_count(self, query: str) -> int:
        """Get approximate result count for a query. Returns -1 on error."""
        data = self._search_bulk(query, limit=1)
        if data is None:
            return -1
        return data.get("total", 0)

    def get_top_papers(
        self, query: str, limit: int = 5
    ) -> Tuple[int, List[dict]]:
        """
        Get result count and top papers for relevance checking.

        Uses /paper/search (relevance-ranked) for better top results.
        Returns (total_count, list_of_paper_dicts).
        """
        data = self._search_relevance(query, limit=limit)
        if data is None:
            return -1, []

        total = data.get("total", 0)
        papers = data.get("data", [])
        return total, papers

    def _search_bulk(self, query: str, limit: int = 1, _retries: int = 0) -> Optional[dict]:
        """
        Call /paper/search/bulk — good for getting total counts.
        Supports up to 1000 results per call.
        """
        self._respect_rate_limit()

        params = {
            "query": query,
            "limit": limit,
            "fields": "title",
        }

        try:
            resp = self.session.get(
                f"{BASE_URL}/paper/search/bulk",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429 and _retries < 3:
                wait = 5 * (_retries + 1)
                logger.warning(f"S2 rate limited. Waiting {wait}s... (retry {_retries + 1}/3)")
                time.sleep(wait)
                return self._search_bulk(query, limit, _retries + 1)
            logger.error(f"S2 API HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"S2 API error: {e}")
            return None

    def _search_relevance(self, query: str, limit: int = 5, _retries: int = 0) -> Optional[dict]:
        """
        Call /paper/search — returns relevance-ranked results.
        Better for spot-checking top results. Max 1000 results.
        """
        self._respect_rate_limit()

        params = {
            "query": query,
            "limit": limit,
            "fields": "title,abstract,year,citationCount",
        }

        try:
            resp = self.session.get(
                f"{BASE_URL}/paper/search",
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429 and _retries < 3:
                wait = 5 * (_retries + 1)
                logger.warning(f"S2 rate limited. Waiting {wait}s... (retry {_retries + 1}/3)")
                time.sleep(wait)
                return self._search_relevance(query, limit, _retries + 1)
            logger.error(f"S2 API HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"S2 API error: {e}")
            return None

    def _respect_rate_limit(self):
        """Ensure we don't exceed rate limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()
