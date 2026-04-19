#!/usr/bin/env python3
"""
RoboScout Query Generator — Semantic Scholar API Client
========================================================
Thin wrapper around the Semantic Scholar Academic Graph API.
Used for query validation: get result counts and sample papers.

Returns structured S2Result objects so callers can distinguish
"zero hits" from "API unreachable" — previously both looked like -1/0
and corrupted downstream category classification.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

import requests

logger = logging.getLogger("roboscout_query_gen.semantic_scholar")

BASE_URL = "https://api.semanticscholar.org/graph/v1"


class S2Status(str, Enum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"  # gave up after retries
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"


@dataclass
class S2Result:
    """Result from an S2 search call.

    Invariants:
    - status == OK  =>  `total` and `papers` are trustworthy
    - status != OK  =>  caller must NOT treat the query as validated
    """

    status: S2Status
    total: int = 0
    papers: List[dict] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == S2Status.OK


class SemanticScholarClient:
    """Client for Semantic Scholar paper search API."""

    MAX_RETRIES = 3

    def __init__(
        self,
        api_key: Optional[str] = None,
        rate_limit_delay: Optional[float] = None,
    ):
        self.api_key = api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
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
            logger.info(
                "No S2 API key — using unauthenticated access (slower rate limit)"
            )

    # --- Public API --------------------------------------------------------

    def search_relevance(self, query: str, limit: int = 5) -> S2Result:
        """Relevance-ranked search via /paper/search. Returns S2Result."""
        return self._request(
            f"{BASE_URL}/paper/search",
            {
                "query": query,
                "limit": limit,
                "fields": "title,abstract,year,citationCount",
            },
        )

    def search_bulk(self, query: str, limit: int = 1) -> S2Result:
        """Bulk search via /paper/search/bulk. Good for total counts only."""
        return self._request(
            f"{BASE_URL}/paper/search/bulk",
            {"query": query, "limit": limit, "fields": "title"},
        )

    # --- Back-compat shims (preserve the old (int, list) tuple API) --------

    def get_result_count(self, query: str) -> int:
        """Return total count. Returns -1 on error (legacy behavior).

        Prefer `search_bulk()` which returns an explicit S2Result.
        """
        res = self.search_bulk(query, limit=1)
        return res.total if res.ok else -1

    def get_top_papers(
        self, query: str, limit: int = 5
    ) -> Tuple[int, List[dict]]:
        """Return (count, papers). Returns (-1, []) on error (legacy).

        Prefer `search_relevance()` which returns an explicit S2Result.
        """
        res = self.search_relevance(query, limit=limit)
        if not res.ok:
            return -1, []
        return res.total, res.papers

    # --- Internals ---------------------------------------------------------

    def _request(self, url: str, params: dict) -> S2Result:
        """Perform a GET with backoff; return S2Result with explicit status."""
        for attempt in range(self.MAX_RETRIES):
            self._respect_rate_limit()
            try:
                resp = self.session.get(url, params=params, timeout=30)
            except requests.exceptions.Timeout:
                logger.warning("S2 request timeout (attempt %d)", attempt + 1)
                if attempt + 1 < self.MAX_RETRIES:
                    time.sleep(5 * (attempt + 1))
                    continue
                return S2Result(status=S2Status.TIMEOUT, error="timeout")
            except requests.exceptions.RequestException as e:
                logger.error("S2 network error: %s", e)
                return S2Result(status=S2Status.NETWORK_ERROR, error=str(e))

            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning(
                    "S2 rate limited. Waiting %ds (retry %d/%d)",
                    wait,
                    attempt + 1,
                    self.MAX_RETRIES,
                )
                if attempt + 1 < self.MAX_RETRIES:
                    time.sleep(wait)
                    continue
                return S2Result(
                    status=S2Status.RATE_LIMITED, error="429 after retries"
                )

            if not resp.ok:
                logger.error(
                    "S2 HTTP %d: %s", resp.status_code, resp.text[:200]
                )
                return S2Result(
                    status=S2Status.HTTP_ERROR,
                    error=f"HTTP {resp.status_code}",
                )

            try:
                data = resp.json()
            except ValueError as e:
                logger.error("S2 returned non-JSON: %s", e)
                return S2Result(status=S2Status.HTTP_ERROR, error="bad json")

            return S2Result(
                status=S2Status.OK,
                total=int(data.get("total", 0)),
                papers=list(data.get("data", []) or []),
            )

        # Should not be reachable — retry loop always returns — but be safe.
        return S2Result(status=S2Status.HTTP_ERROR, error="exhausted retries")

    def _respect_rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()
