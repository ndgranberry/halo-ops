#!/usr/bin/env python3
"""
RoboScout Query Generator — Query Validation & Refinement
===========================================================
Validates generated queries against Semantic Scholar:
1. Check result counts → classify
2. Spot-check relevance of top results
3. Refine queries that fail (too broad or irrelevant)
"""

import logging
import time
from typing import List

from models import QueryRequest, GeneratedQuery, QueryCategory
from semantic_scholar import SemanticScholarClient
from query_generator import QueryGenerator

logger = logging.getLogger("roboscout_query_gen.query_validator")

MAX_REFINEMENT_ROUNDS = 2
RELEVANCE_THRESHOLD = 0.6  # 60% of top results must be relevant
PAPERS_TO_CHECK = 20  # matches the 2-page manual review workflow
RETRY_WAIT_SECONDS = 30
MAX_RETRY_PASSES = 2


class QueryValidator:
    """Validate and refine generated queries against Semantic Scholar."""

    def __init__(
        self,
        s2_client: SemanticScholarClient,
        generator: QueryGenerator,
    ):
        self.s2 = s2_client
        self.generator = generator

    def validate_all(
        self,
        queries: List[GeneratedQuery],
        request: QueryRequest,
    ) -> List[GeneratedQuery]:
        """
        Validate all queries with retry for S2 failures.
        Returns the same list with validation fields populated.
        """
        validated = []

        for i, query in enumerate(queries):
            logger.info(f"[{i+1}/{len(queries)}] Validating: '{query.query}'")
            result = self._validate_single(query, request)
            validated.append(result)

        # Retry pass: re-attempt any queries that failed due to S2 errors
        for retry_pass in range(MAX_RETRY_PASSES):
            unvalidated = [(i, q) for i, q in enumerate(validated) if q.is_unvalidated]
            if not unvalidated:
                break

            logger.info(
                f"\n--- Retry pass {retry_pass + 1}: {len(unvalidated)} queries "
                f"need validation. Waiting {RETRY_WAIT_SECONDS}s for S2 rate limits... ---"
            )
            time.sleep(RETRY_WAIT_SECONDS)

            for idx, query in unvalidated:
                logger.info(f"  Retrying [{idx+1}]: '{query.query}'")
                result = self._validate_single(query, request)
                validated[idx] = result

        # Summary
        valid = [q for q in validated if q.is_valid]
        unvalidated_final = [q for q in validated if q.is_unvalidated]
        rejected = [q for q in validated if not q.is_valid and not q.is_unvalidated]

        parts = [f"{len(valid)} valid"]
        if rejected:
            parts.append(f"{len(rejected)} rejected")
        if unvalidated_final:
            parts.append(f"{len(unvalidated_final)} unvalidated (S2 unreachable)")
        logger.info(f"\nValidation complete: {', '.join(parts)} out of {len(validated)} total")

        return validated

    def _validate_single(
        self,
        query: GeneratedQuery,
        request: QueryRequest,
    ) -> GeneratedQuery:
        """Validate a single query, with refinement loop."""
        current = query

        for round_num in range(MAX_REFINEMENT_ROUNDS + 1):
            # Step 1: Get result count
            total, papers = self.s2.get_top_papers(current.query, limit=PAPERS_TO_CHECK)

            if total < 0:
                logger.warning(f"  S2 API error for '{current.query}', skipping validation")
                current.result_count = None
                return current

            current.result_count = total
            current.category = QueryCategory.from_count(total)
            current.sample_titles = [p.get("title", "") for p in papers]

            logger.info(f"  Results: {total} → {current.category.value}")

            # Step 2a: Check if zero results — query is too narrow / useless
            if total == 0:
                if round_num < MAX_REFINEMENT_ROUNDS:
                    logger.info(f"  Zero results. Refining to broaden...")
                    current = self.generator.refine_query(
                        current,
                        request,
                        problem="Query returns 0 results. Broaden by using fewer terms or more general vocabulary while staying on-topic.",
                        target_range="10-500 results",
                    )
                    continue
                else:
                    logger.warning(f"  Still zero results after {MAX_REFINEMENT_ROUNDS} refinements. Rejecting.")
                    current.relevance_passed = False
                    current.relevance_details = "Zero results — query too narrow"
                    return current

            # Step 2b: Check if too broad
            if current.category == QueryCategory.TOO_BROAD:
                if round_num < MAX_REFINEMENT_ROUNDS:
                    logger.info(f"  Too broad ({total} > 3000). Refining...")
                    current = self.generator.refine_query(
                        current,
                        request,
                        problem=f"Query returns {total} results, which exceeds the 3,000 limit. Add more specific terms or field context to narrow results.",
                        target_range="under 3,000 (ideally 500-1,000)",
                    )
                    continue
                else:
                    logger.warning(f"  Still too broad after {MAX_REFINEMENT_ROUNDS} refinements. Rejecting.")
                    return current

            # Step 3: Relevance spot-check (only if we got papers)
            if papers:
                relevance = self.generator.check_relevance(current, request, papers)

                ratio = relevance.get("relevance_ratio", 1.0)
                summary = relevance.get("summary", "")

                # Enforce threshold deterministically — don't trust LLM's verdict
                current.relevance_passed = ratio >= RELEVANCE_THRESHOLD
                current.relevance_details = summary
                verdict = "pass" if current.relevance_passed else "fail"

                logger.info(f"  Relevance: {ratio:.0%} — {verdict} — {summary}")

                if not current.relevance_passed and round_num < MAX_REFINEMENT_ROUNDS:
                    logger.info(f"  Relevance too low ({ratio:.0%} < {RELEVANCE_THRESHOLD:.0%}). Refining...")
                    current = self.generator.refine_query(
                        current,
                        request,
                        problem=f"Only {ratio:.0%} of top results are relevant. {summary}",
                        target_range=f"under {current.result_count} with better relevance",
                    )
                    continue
            else:
                # No papers returned but count > 0 — skip relevance check
                current.relevance_passed = True
                current.relevance_details = "No papers available for relevance check"

            # Passed all checks
            return current

        return current
