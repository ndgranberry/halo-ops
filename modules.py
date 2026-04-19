#!/usr/bin/env python3
"""
RoboScout Query Generator — DSPy Modules
==========================================
Contains the DSPy Module implementations that replace:
  - query_generator.py  → QueryGenerationModule
  - query_validator.py   → QueryValidationModule
  - orchestration logic  → RoboScoutPipeline

Each module uses dspy.Predict with typed Signatures for structured output
via Pydantic models (DSPy 2.6+ handles Pydantic OutputFields natively).
"""

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import dspy

from config import settings
from logging_setup import timed_stage
from models import GeneratedQuery, QueryCategory, QueryRequest, SOICoverage
from signatures import (
    CheckRelevance,
    GenerateQueries,
    GenerateQueriesOutput,
    GenerateRecoveryQueries,
    RefineQuery,
    RegenerateQuery,
)

logger = logging.getLogger("roboscout_query_gen.modules")

# Load the scout guide once at module level
_GUIDE_PATH = Path(__file__).parent / "context" / "query_generation_guide.md"
_GUIDE_TEXT = _GUIDE_PATH.read_text() if _GUIDE_PATH.exists() else ""


# =============================================================================
# QueryGenerationModule — replaces QueryGenerator class
# =============================================================================


class QueryGenerationModule(dspy.Module):
    """Generate Semantic Scholar queries from a partnering request.

    Replaces query_generator.py QueryGenerator class. Uses dspy.Predict
    with typed Signatures for structured output (DSPy 2.6+ natively
    handles Pydantic OutputFields).
    """

    def __init__(self):
        super().__init__()
        self.generate = dspy.Predict(GenerateQueries)
        self.refine = dspy.Predict(RefineQuery)
        self.check_relevance = dspy.Predict(CheckRelevance)
        self.generate_recovery = dspy.Predict(GenerateRecoveryQueries)
        self.regenerate = dspy.Predict(RegenerateQuery)

    def forward(
        self, request: QueryRequest
    ) -> Tuple[List[GeneratedQuery], List[str]]:
        """Generate candidate queries for a partnering request.

        Returns:
            (queries, expanded_sois) — list of GeneratedQuery and list of SOI names.
        """
        try:
            result = self.generate(
                title=request.title,
                looking_for=request.looking_for,
                use_case=request.use_case,
                solutions_of_interest=request.solutions_of_interest,
                requirements=request.requirements,
                out_of_scope=request.out_of_scope,
                reference_guide=_GUIDE_TEXT,
            )
            return self._parse_output(result.output)
        except Exception:
            # Broad except is intentional: DSPy/LiteLLM raise a wide variety
            # of exceptions (network, JSON, Pydantic validation). Log with
            # traceback so we can diagnose root causes from the log file.
            logger.exception("Query generation failed — returning empty list")
            return [], []

    def forward_refine(
        self,
        query: GeneratedQuery,
        request: QueryRequest,
        problem: str,
        target_range: str,
    ) -> GeneratedQuery:
        """Refine a query that failed validation.

        Port of QueryGenerator.refine_query() (query_generator.py:236-291).
        """
        sample_titles_section = ""
        if query.sample_titles:
            titles_str = "\n".join(f"  - {t}" for t in query.sample_titles[:5])
            sample_titles_section = f"Sample titles from results:\n{titles_str}"

        request_context = (
            f"Title: {request.title}\n"
            f"Looking For: {request.looking_for}\n"
            f"SOIs: {request.solutions_of_interest}"
        )

        try:
            result = self.refine(
                original_query=query.query,
                target_soi=query.target_soi,
                problem=problem,
                result_count=str(query.result_count or "unknown"),
                target_range=target_range,
                sample_titles=sample_titles_section,
                request_context=request_context,
            )

            output = result.output
            refined = GeneratedQuery(
                query=output.refined_query,
                target_soi=query.target_soi,
                rationale=output.changes_made,
                expected_specificity=output.expected_specificity,
                refinement_round=query.refinement_round + 1,
                original_query=query.original_query or query.query,
                refinement_reason=problem,
                is_recovery=query.is_recovery,
            )

            logger.info(
                f"Refined: '{query.query}' → '{refined.query}' "
                f"(round {refined.refinement_round})"
            )
            return refined

        except Exception:
            logger.warning(
                "Refinement failed for query %r — keeping original",
                query.query,
                exc_info=True,
            )
            return query

    def forward_relevance(
        self,
        query: GeneratedQuery,
        request: QueryRequest,
        papers: List[dict],
    ) -> dict:
        """Check if top search results are relevant to the request.

        Port of QueryGenerator.check_relevance() (query_generator.py:293-324).
        """
        papers_section = ""
        for i, paper in enumerate(papers):
            title = paper.get("title", "Unknown")
            abstract = (paper.get("abstract") or "No abstract available")[:300]
            papers_section += f"\n[{i}] Title: {title}\n    Abstract: {abstract}\n"

        try:
            result = self.check_relevance(
                title=request.title,
                looking_for=request.looking_for,
                solutions_of_interest=request.solutions_of_interest,
                query=query.query,
                papers_section=papers_section,
            )

            output = result.output
            return {
                "relevance_ratio": output.relevance_ratio,
                "summary": output.summary,
                "evaluations": [e.model_dump() for e in output.evaluations],
                "relevant_count": output.relevant_count,
                "total_checked": output.total_checked,
            }

        except Exception:
            # Graceful degrade: if relevance LLM call fails, we assume
            # pass rather than block the whole query. Traceback is logged
            # so we can tune the LLM call if this fires often.
            logger.warning(
                "Relevance check failed for query %r — assuming pass",
                query.query,
                exc_info=True,
            )
            return {"relevance_ratio": 1.0, "summary": "Check failed, assuming pass"}

    def forward_recovery(
        self,
        request: QueryRequest,
        uncovered_sois: List[SOICoverage],
        failed_by_soi: Dict[str, list],
    ) -> Tuple[List[GeneratedQuery], List[str]]:
        """Generate recovery queries for uncovered SOIs.

        Port of QueryGenerator.generate_recovery() (query_generator.py:166-234).
        """
        uncovered_list = "\n".join(f"- {cov.soi}" for cov in uncovered_sois)

        failed_section_parts = []
        for cov in uncovered_sois:
            failed = failed_by_soi.get(cov.soi.lower(), [])
            part = f"\nSOI: {cov.soi}"
            if failed:
                for fq in failed:
                    part += (
                        f'\n  Tried: "{fq["query"]}" '
                        f'→ {fq["result_count"]} results — {fq["reason"]}'
                    )
            else:
                part += "\n  No queries were attempted for this SOI."
            failed_section_parts.append(part)

        failed_queries_section = "\n".join(failed_section_parts)

        # Try up to 2 times (matching original behavior)
        queries = []
        for attempt in range(2):
            try:
                result = self.generate_recovery(
                    title=request.title,
                    looking_for=request.looking_for,
                    solutions_of_interest=request.solutions_of_interest,
                    uncovered_sois=uncovered_list,
                    failed_queries_section=failed_queries_section,
                    reference_guide=_GUIDE_TEXT,
                )

                queries, _ = self._parse_output(result.output)
                if queries:
                    break
                logger.warning(
                    f"Recovery attempt {attempt + 1} returned 0 queries, retrying..."
                )

            except Exception as e:
                logger.error(f"Recovery generation failed (attempt {attempt + 1}): {e}")

        for q in queries:
            q.is_recovery = True

        logger.info(
            f"Generated {len(queries)} recovery queries "
            f"for {len(uncovered_sois)} uncovered SOIs"
        )
        return queries, []

    def forward_regenerate(
        self,
        target_soi: str,
        failed_queries: List[dict],
        request: "QueryRequest",
    ) -> Optional[GeneratedQuery]:
        """Generate a completely new query for an SOI after all refinements failed.

        Unlike refinement (which tweaks an existing query), this starts fresh
        with a fundamentally different approach.

        Args:
            target_soi: The SOI that needs a new query.
            failed_queries: List of dicts with query/result_count/reason for
                            all queries tried for this SOI.
            request: The partnering request for context.

        Returns:
            A fresh GeneratedQuery, or None if generation fails.
        """
        failed_section = f"SOI: {target_soi}\n"
        for fq in failed_queries:
            failed_section += (
                f'  Tried: "{fq["query"]}" '
                f'→ {fq["result_count"]} results — {fq["reason"]}\n'
            )

        request_context = (
            f"Title: {request.title}\n"
            f"Looking For: {request.looking_for}\n"
            f"SOIs: {request.solutions_of_interest}"
        )

        try:
            result = self.regenerate(
                target_soi=target_soi,
                failed_queries=failed_section,
                request_context=request_context,
                reference_guide=_GUIDE_TEXT,
            )

            output = result.output
            new_query = GeneratedQuery(
                query=output.new_query,
                target_soi=target_soi,
                rationale=f"Regenerated: {output.approach}",
                expected_specificity=output.expected_specificity,
                refinement_round=0,
                is_regeneration=True,
            )

            logger.info(
                f"Regenerated query for '{target_soi}': '{new_query.query}' "
                f"(approach: {output.approach})"
            )
            return new_query

        except Exception as e:
            logger.warning(
                f"Regeneration failed for SOI '{target_soi}': {e}"
            )
            return None

    def _parse_output(
        self, output: GenerateQueriesOutput
    ) -> Tuple[List[GeneratedQuery], List[str]]:
        """Convert Pydantic output to existing GeneratedQuery dataclasses.

        Port of QueryGenerator._parse_response() (query_generator.py:364-432).
        """
        queries = []
        for cq in output.candidate_queries:
            q = GeneratedQuery(
                query=cq.query,
                target_soi=cq.target_soi,
                rationale=cq.rationale,
                expected_specificity=cq.expected_specificity,
            )
            if q.query:
                queries.append(q)

        # Log analysis summary
        logger.info(f"Domain: {output.analysis.domain}")
        logger.info(f"Key technologies: {output.analysis.key_technologies}")

        rfp_sois = [s.soi for s in output.expanded_sois if s.from_rfp]
        new_sois = [s.soi for s in output.expanded_sois if not s.from_rfp]
        if new_sois:
            logger.warning(
                f"Discarding {len(new_sois)} LLM-invented SOIs not in the original request: {new_sois}"
            )
        logger.info(
            f"SOIs from RFP: {len(rfp_sois)}"
        )
        logger.info(f"Generated {len(queries)} candidate queries")

        # Only use SOIs that come from the actual request — never LLM-invented ones
        soi_names = [s.soi for s in output.expanded_sois if s.from_rfp and s.soi]

        # Drop any queries targeting invented SOIs
        valid_soi_set = set(soi_names)
        before = len(queries)
        queries = [q for q in queries if q.target_soi in valid_soi_set]
        dropped = before - len(queries)
        if dropped:
            logger.warning(
                f"Dropped {dropped} queries targeting invented SOIs"
            )

        return queries, soi_names


# =============================================================================
# QueryValidationModule — replaces QueryValidator class
# =============================================================================


class QueryValidationModule(dspy.Module):
    """Validate queries against Semantic Scholar with refinement loop.

    Port of query_validator.py QueryValidator class.
    Uses QueryGenerationModule for LLM calls (refine, relevance check)
    and SemanticScholarClient for external API calls.
    """

    # Class-level defaults; instance may override from config.settings in __init__.
    MAX_REFINEMENT_ROUNDS = 3
    RELEVANCE_THRESHOLD = 0.6
    PAPERS_TO_CHECK = 20
    EARLY_CHECK_SIZE = 5
    RETRY_WAIT_SECONDS = 30
    MAX_RETRY_PASSES = 2
    MAX_SOI_ATTEMPTS = 20  # Hard cap: give up on an SOI after this many S2 calls

    def __init__(self, s2_client, gen_module: QueryGenerationModule):
        super().__init__()
        self.s2 = s2_client
        self.gen = gen_module
        self._soi_attempt_counts: Dict[str, int] = {}  # Per-SOI S2 call counter
        # Pull tunables from config so they're configurable via env without code edits.
        self.MAX_REFINEMENT_ROUNDS = settings.max_refinement_rounds
        self.RELEVANCE_THRESHOLD = settings.relevance_threshold
        self.PAPERS_TO_CHECK = settings.papers_to_check

    def forward(
        self,
        queries: List[GeneratedQuery],
        request: QueryRequest,
    ) -> List[GeneratedQuery]:
        """Validate all queries with retry for S2 failures.

        Port of QueryValidator.validate_all() (query_validator.py:39-84).
        """
        # Reset per-SOI attempt counters for this validation batch
        self._soi_attempt_counts = {}

        validated = []

        for i, query in enumerate(queries):
            logger.info(f"[{i + 1}/{len(queries)}] Validating: '{query.query}'")
            result = self._validate_single(query, request)
            validated.append(result)

        # Retry pass: re-attempt queries that failed due to S2 errors
        for retry_pass in range(self.MAX_RETRY_PASSES):
            unvalidated = [
                (i, q) for i, q in enumerate(validated) if q.is_unvalidated
            ]
            if not unvalidated:
                break

            logger.info(
                f"\n--- Retry pass {retry_pass + 1}: {len(unvalidated)} queries "
                f"need validation. Waiting {self.RETRY_WAIT_SECONDS}s for S2 rate limits... ---"
            )
            time.sleep(self.RETRY_WAIT_SECONDS)

            for idx, query in unvalidated:
                logger.info(f"  Retrying [{idx + 1}]: '{query.query}'")
                result = self._validate_single(query, request)
                validated[idx] = result

        # Summary
        valid = [q for q in validated if q.is_valid]
        unvalidated_final = [q for q in validated if q.is_unvalidated]
        rejected = [
            q for q in validated if not q.is_valid and not q.is_unvalidated
        ]

        parts = [f"{len(valid)} valid"]
        if rejected:
            parts.append(f"{len(rejected)} rejected")
        if unvalidated_final:
            parts.append(
                f"{len(unvalidated_final)} unvalidated (S2 unreachable)"
            )
        logger.info(
            f"\nValidation complete: {', '.join(parts)} out of {len(validated)} total"
        )

        return validated

    def _validate_single(
        self,
        query: GeneratedQuery,
        request: QueryRequest,
        allow_regenerate: bool = True,
    ) -> GeneratedQuery:
        """Validate a single query, with refinement loop and optional regeneration.

        After exhausting MAX_REFINEMENT_ROUNDS refinements, if allow_regenerate
        is True, generates a completely new query for the same SOI and validates
        it (without further regeneration to prevent infinite loops).

        Per-SOI attempt cap (MAX_SOI_ATTEMPTS) prevents runaway behavior.
        """
        current = query
        soi_key = current.target_soi.strip().lower()
        tried_queries: List[dict] = []

        for round_num in range(self.MAX_REFINEMENT_ROUNDS + 1):
            # Guard: per-SOI attempt cap
            capped = self._enforce_soi_cap(current, soi_key)
            if capped is not None:
                return capped

            # Step 1: fetch result count + sample papers from S2
            fetched = self._fetch_s2(current, soi_key)
            if fetched is False:
                # unvalidated — caller's retry pass will pick it up
                return current
            total, papers = fetched

            # Step 2: size-based classification
            refined = self._handle_size(
                current, request, total, round_num, tried_queries
            )
            if refined is not None:
                if refined is current:
                    break  # rejected, out of refinements — fall to regeneration
                current = refined
                continue

            # Step 3: relevance spot-check (only if papers available)
            if not papers:
                current.relevance_passed = True
                current.relevance_details = "No papers available for relevance check"
                return current

            self._run_relevance_check(current, request, papers)
            if current.relevance_passed:
                return current

            # Relevance failed — either refine or break to regeneration
            tried_queries.append({
                "query": current.query,
                "result_count": total,
                "reason": f"Low relevance — {current.relevance_details}",
            })
            if round_num < self.MAX_REFINEMENT_ROUNDS:
                logger.info("  Relevance too low. Refining...")
                current = self.gen.forward_refine(
                    current,
                    request,
                    problem=f"Top results mostly irrelevant. {current.relevance_details}",
                    target_range=f"under {current.result_count} with better relevance",
                )
                continue
            break

        # === Regeneration: query exhausted all refinement rounds ===
        if allow_regenerate and not current.is_valid:
            soi_count = self._soi_attempt_counts.get(soi_key, 0)
            if soi_count >= self.MAX_SOI_ATTEMPTS:
                logger.warning(
                    f"  SOI '{current.target_soi}' at attempt cap — "
                    f"skipping regeneration."
                )
                return current

            logger.info(
                f"  Exhausted {self.MAX_REFINEMENT_ROUNDS} refinement rounds "
                f"for '{query.query}'. Regenerating a fresh query for "
                f"SOI '{current.target_soi}'..."
            )

            new_query = self.gen.forward_regenerate(
                target_soi=current.target_soi,
                failed_queries=tried_queries,
                request=request,
            )

            if new_query:
                # Validate the regenerated query (no further regeneration allowed)
                return self._validate_single(
                    new_query, request, allow_regenerate=False
                )
            else:
                logger.warning(
                    f"  Regeneration failed for SOI '{current.target_soi}'. "
                    f"Keeping rejected query."
                )

        return current

    # ------------------------------------------------------------------
    # _validate_single helpers (extracted 2026-04-17 — split of the old
    # 280-line method). Keeping them as methods on the module so they
    # share `self.gen`, `self.s2`, `self._soi_attempt_counts`, and the
    # class-level tunables.
    # ------------------------------------------------------------------

    def _enforce_soi_cap(
        self, current: GeneratedQuery, soi_key: str
    ) -> Optional[GeneratedQuery]:
        """If this SOI has hit its attempt cap, stamp the query and return it.
        Otherwise return None to let the caller proceed."""
        soi_count = self._soi_attempt_counts.get(soi_key, 0)
        if soi_count < self.MAX_SOI_ATTEMPTS:
            return None
        logger.warning(
            f"  SOI '{current.target_soi}' hit {self.MAX_SOI_ATTEMPTS}-attempt cap. "
            f"Rejecting '{current.query}' without further tries."
        )
        if current.relevance_passed is None:
            current.relevance_passed = False
            current.relevance_details = (
                f"SOI attempt cap ({self.MAX_SOI_ATTEMPTS}) reached"
            )
        return current

    def _fetch_s2(
        self, current: GeneratedQuery, soi_key: str
    ) -> "Tuple[int, List[dict]] | bool":
        """Run one S2 call and update ``current`` with count/category/titles.

        Returns (total, papers) on success, or False on any S2 error (in
        which case ``current`` is marked unvalidated with explicit
        status/error details).
        """
        self._soi_attempt_counts[soi_key] = (
            self._soi_attempt_counts.get(soi_key, 0) + 1
        )
        s2_result = self.s2.search_relevance(
            current.query, limit=self.PAPERS_TO_CHECK
        )
        if not s2_result.ok:
            logger.warning(
                "  S2 API %s for '%s' (%s) — marking unvalidated",
                s2_result.status.value,
                current.query,
                s2_result.error,
            )
            current.result_count = None
            current.relevance_details = (
                f"S2 {s2_result.status.value}: {s2_result.error or ''}".strip(": ")
            )
            return False

        total = s2_result.total
        papers = s2_result.papers
        current.result_count = total
        current.category = QueryCategory.from_count(total)
        current.sample_titles = [p.get("title", "") for p in papers]
        logger.info(
            f"  Results: {total} → {current.category.value} "
            f"(SOI attempts: {self._soi_attempt_counts[soi_key]}/{self.MAX_SOI_ATTEMPTS})"
        )
        return total, papers

    def _handle_size(
        self,
        current: GeneratedQuery,
        request: QueryRequest,
        total: int,
        round_num: int,
        tried_queries: List[dict],
    ) -> Optional[GeneratedQuery]:
        """Dispatch size-based rejection / refinement.

        Returns:
          - a *new* refined GeneratedQuery  → caller should replace `current` and continue
          - the same `current` object        → caller should break to regeneration
          - None                             → query is in-range; caller should proceed
                                               to the relevance check
        """
        if total == 0:
            return self._refine_or_break(
                current, request, round_num, tried_queries,
                reason="Zero results — query too narrow",
                problem=(
                    "Query returns 0 results. Broaden by using fewer terms or "
                    "more general vocabulary while staying on-topic."
                ),
                target_range="20-500 results",
                reject_stamp=("Zero results — query too narrow"),
            )
        if current.category == QueryCategory.TOO_NARROW:
            return self._refine_or_break(
                current, request, round_num, tried_queries,
                reason=f"Too narrow ({total} < 20)",
                problem=(
                    f"Query returns only {total} results, which is below the "
                    f"20-result minimum. Broaden by using slightly more "
                    f"general terms while staying on-topic."
                ),
                target_range="20-500 results",
            )
        if current.category == QueryCategory.TOO_BROAD:
            return self._refine_or_break(
                current, request, round_num, tried_queries,
                reason=f"Too broad ({total} > 3000)",
                problem=(
                    f"Query returns {total} results, which exceeds the 3,000 "
                    f"limit. Add more specific terms or field context to "
                    f"narrow results."
                ),
                target_range="under 3,000 (ideally 500-1,000)",
            )
        # SPECIFIC / MODERATE / GENERAL — in range, caller proceeds to relevance.
        return None

    def _refine_or_break(
        self,
        current: GeneratedQuery,
        request: QueryRequest,
        round_num: int,
        tried_queries: List[dict],
        *,
        reason: str,
        problem: str,
        target_range: str,
        reject_stamp: Optional[str] = None,
    ) -> GeneratedQuery:
        """Common pattern: log a size failure, try to refine if budget
        allows, otherwise return the same query so the caller can fall
        through to regeneration.
        """
        tried_queries.append({
            "query": current.query,
            "result_count": current.result_count or 0,
            "reason": reason,
        })
        if round_num < self.MAX_REFINEMENT_ROUNDS:
            logger.info("  %s. Refining...", reason)
            return self.gen.forward_refine(
                current, request, problem=problem, target_range=target_range
            )
        logger.warning(
            "  Still %s after %d refinements. Rejecting.",
            reason, self.MAX_REFINEMENT_ROUNDS,
        )
        if reject_stamp:
            current.relevance_passed = False
            current.relevance_details = reject_stamp
        return current  # sentinel: caller breaks to regeneration

    def _run_relevance_check(
        self,
        current: GeneratedQuery,
        request: QueryRequest,
        papers: List[dict],
    ) -> None:
        """Batched relevance check with math-impossible early exit.
        Mutates ``current`` — sets relevance_passed and relevance_details.
        """
        import math

        total_papers = len(papers)
        needed = math.ceil(total_papers * self.RELEVANCE_THRESHOLD)
        batch_size = self.EARLY_CHECK_SIZE
        cumulative_relevant = 0
        cumulative_checked = 0
        last_summary = ""

        for batch_start in range(0, total_papers, batch_size):
            batch = papers[batch_start : batch_start + batch_size]
            batch_result = self.gen.forward_relevance(current, request, batch)
            cumulative_relevant += batch_result.get("relevant_count", 0)
            cumulative_checked += batch_result.get("total_checked", len(batch))
            last_summary = batch_result.get("summary", "")

            logger.info(
                f"  Relevance batch {batch_start // batch_size + 1}: "
                f"{cumulative_relevant}/{cumulative_checked} relevant so far, "
                f"need {needed}/{total_papers}"
            )

            remaining = total_papers - cumulative_checked
            if cumulative_relevant + remaining < needed:
                current.relevance_passed = False
                current.relevance_details = (
                    f"Early exit at {cumulative_checked}/{total_papers}: "
                    f"{cumulative_relevant} relevant, need {needed} — "
                    f"impossible even if all {remaining} remaining are "
                    f"relevant — {last_summary}"
                )
                logger.info(
                    f"  Mathematically impossible to reach "
                    f"{self.RELEVANCE_THRESHOLD:.0%} — stopping"
                )
                return

        ratio = cumulative_relevant / total_papers if total_papers else 0
        current.relevance_passed = ratio >= self.RELEVANCE_THRESHOLD
        current.relevance_details = last_summary
        verdict = "pass" if current.relevance_passed else "fail"
        logger.info(f"  Relevance: {ratio:.0%} — {verdict} — {last_summary}")


# =============================================================================
# RoboScoutPipeline — top-level composition
# =============================================================================


class RoboScoutPipeline(dspy.Module):
    """Top-level pipeline composing generation, validation, and recovery.

    Replaces the orchestration logic from RoboScoutQueryGen.run()
    (roboscout_query_gen.py:73-178) and the coverage analysis methods
    (roboscout_query_gen.py:180-253).
    """

    def __init__(self, s2_client):
        super().__init__()
        self.gen = QueryGenerationModule()
        self.validator = QueryValidationModule(s2_client, self.gen)

    def forward(
        self, request: QueryRequest
    ) -> Tuple[List[GeneratedQuery], List[str], List[SOICoverage]]:
        """Run the full generation + validation + recovery pipeline.

        Returns:
            (all_queries, expanded_sois, soi_coverage)
        """
        # Stage 1: Generate candidate queries
        with timed_stage("1-generate", logger):
            queries, expanded_sois = self.gen(request)

        if not queries:
            logger.error(
                "No queries generated. Check API key and request content."
            )
            return [], expanded_sois, []

        logger.info(f"Generated {len(queries)} candidate queries")

        # Stage 2: Validate queries against Semantic Scholar
        with timed_stage("2-validate", logger):
            queries = self.validator(queries, request)

        # Stage 3: Build SOI coverage analysis
        with timed_stage("3-coverage", logger):
            soi_coverage = self._analyze_coverage(queries, expanded_sois)

        for cov in soi_coverage:
            if not cov.queries:
                status = "NOT COVERED"
            elif not cov.meets_requirements:
                parts = []
                if not cov.has_specific:
                    parts.append("no specific query")
                if cov.total_results < 100:
                    parts.append(f"only {cov.total_results} total results")
                status = f"{len(cov.queries)} queries — NEEDS MORE ({', '.join(parts)})"
            else:
                status = f"{len(cov.queries)} queries, {cov.total_results} total results"
            logger.info(f"  {cov.soi}: {status}")

        # Stage 3.5: Coverage recovery for SOIs that don't meet requirements
        uncovered = [cov for cov in soi_coverage if not cov.meets_requirements]
        if uncovered:
            logger.info(
                f"\n=== Stage 3.5: Coverage recovery "
                f"({len(uncovered)} SOIs need recovery) ==="
            )
            for uc in uncovered:
                if not uc.queries:
                    logger.info(f"  Recovering: {uc.soi} (no valid queries)")
                else:
                    logger.info(
                        f"  Recovering: {uc.soi} "
                        f"(has_specific={uc.has_specific}, "
                        f"total_results={uc.total_results})"
                    )

            failed_by_soi = self._collect_failed_for_sois(queries, uncovered)

            with timed_stage("3.5-recovery", logger):
                recovery_queries, _ = self.gen.forward_recovery(
                    request, uncovered, failed_by_soi
                )

            if recovery_queries:
                logger.info(
                    f"\n  Validating {len(recovery_queries)} recovery queries..."
                )
                recovery_validated = self.validator(recovery_queries, request)
                queries.extend(recovery_validated)

                # Deduplicate by query text (keep first occurrence)
                seen = set()
                deduped = []
                for q in queries:
                    key = q.query.strip().lower()
                    if key not in seen:
                        seen.add(key)
                        deduped.append(q)
                if len(deduped) < len(queries):
                    logger.info(
                        f"  Removed {len(queries) - len(deduped)} duplicate queries"
                    )
                    queries = deduped

                # Re-analyze coverage
                soi_coverage = self._analyze_coverage(queries, expanded_sois)

                recovered = [
                    cov
                    for cov in soi_coverage
                    if cov.meets_requirements
                    and any(
                        uc.soi.lower() == cov.soi.lower() for uc in uncovered
                    )
                ]
                still_unmet = [
                    cov for cov in soi_coverage if not cov.meets_requirements
                ]
                logger.info(
                    f"  Recovery result: {len(recovered)} SOIs recovered, "
                    f"{len(still_unmet)} still need more coverage"
                )
            else:
                logger.warning("  No recovery queries generated")
        else:
            logger.info("\n  All SOIs meet coverage requirements — no recovery needed")

        return queries, expanded_sois, soi_coverage

    def _analyze_coverage(
        self,
        queries: List[GeneratedQuery],
        expanded_sois: Optional[List[str]] = None,
    ) -> List[SOICoverage]:
        """Analyze which SOIs are covered by valid queries.

        Port of RoboScoutQueryGen._analyze_coverage()
        (roboscout_query_gen.py:180-218).
        """
        valid_queries = [q for q in queries if q.is_valid]

        soi_map: Dict[str, SOICoverage] = {}

        for q in valid_queries:
            soi = q.target_soi.strip()
            if not soi:
                continue
            key = soi.lower()
            if key not in soi_map:
                soi_map[key] = SOICoverage(soi=soi)
            soi_map[key].queries.append(q.query)
            soi_map[key].total_results += q.result_count or 0

            # Track whether this SOI has at least one SPECIFIC query (20-499)
            if q.category == QueryCategory.SPECIFIC:
                soi_map[key].has_specific = True

            if soi_map[key].best_result_count is None or (
                q.result_count
                and q.category
                in (QueryCategory.MODERATE, QueryCategory.SPECIFIC)
            ):
                soi_map[key].best_query = q.query
                soi_map[key].best_result_count = q.result_count

        # Include uncovered SOIs from Claude's parsed list
        if expanded_sois:
            for soi in expanded_sois:
                soi = soi.strip()
                key = soi.lower()
                if soi and key not in soi_map:
                    soi_map[key] = SOICoverage(soi=soi)

        return list(soi_map.values())

    def _collect_failed_for_sois(
        self,
        queries: List[GeneratedQuery],
        uncovered: List[SOICoverage],
    ) -> Dict[str, list]:
        """Collect rejected/failed queries that targeted uncovered SOIs.

        Port of RoboScoutQueryGen._collect_failed_for_sois()
        (roboscout_query_gen.py:220-253).
        """
        uncovered_keys = {cov.soi.lower() for cov in uncovered}
        failed_by_soi: Dict[str, list] = {key: [] for key in uncovered_keys}

        for q in queries:
            if q.is_valid or q.is_unvalidated:
                continue
            soi_key = q.target_soi.strip().lower()
            if soi_key not in uncovered_keys:
                continue

            if q.category == QueryCategory.TOO_BROAD:
                reason = f"Too broad ({q.result_count} > 3000)"
            elif q.result_count == 0:
                reason = "Zero results — query too narrow"
            elif q.relevance_passed is False:
                reason = f"Low relevance — {q.relevance_details}"
            else:
                reason = "Unknown rejection"

            failed_by_soi[soi_key].append(
                {
                    "query": q.query,
                    "result_count": q.result_count,
                    "reason": reason,
                }
            )

        return failed_by_soi
