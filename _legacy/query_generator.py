#!/usr/bin/env python3
"""
RoboScout Query Generator — Query Generation via Claude API
=============================================================
Uses Claude tool_use for guaranteed structured JSON output.
Multi-step: analyze request → expand vocabulary → generate queries.
"""

import logging
import time
from typing import List

import anthropic

from models import QueryRequest, GeneratedQuery
from prompts import get_query_generation_system, QUERY_GENERATION_USER

logger = logging.getLogger("roboscout_query_gen.query_generator")


# =============================================================================
# Tool definitions — these force Claude to return structured JSON
# =============================================================================

GENERATE_QUERIES_TOOL = {
    "name": "submit_queries",
    "description": "Submit the analysis and generated Semantic Scholar queries.",
    "input_schema": {
        "type": "object",
        "properties": {
            "analysis": {
                "type": "object",
                "properties": {
                    "core_problem": {"type": "string", "description": "1-2 sentence summary of the R&D challenge"},
                    "key_technologies": {"type": "array", "items": {"type": "string"}},
                    "domain": {"type": "string", "description": "Primary scientific domain"},
                    "out_of_scope_items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["core_problem", "key_technologies", "domain"],
            },
            "expanded_sois": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "soi": {"type": "string"},
                        "from_rfp": {"type": "boolean"},
                        "specific_terms": {"type": "array", "items": {"type": "string"}},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["soi", "from_rfp", "specific_terms"],
                },
            },
            "candidate_queries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The Semantic Scholar query text"},
                        "target_soi": {"type": "string", "description": "Which SOI this covers"},
                        "expected_specificity": {
                            "type": "string",
                            "enum": ["general", "moderate", "specific", "highly_specific"],
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["query", "target_soi", "expected_specificity", "rationale"],
                },
                "minItems": 8,
                "maxItems": 20,
            },
        },
        "required": ["analysis", "expanded_sois", "candidate_queries"],
    },
}

REFINE_QUERY_TOOL = {
    "name": "submit_refined_query",
    "description": "Submit the refined Semantic Scholar query.",
    "input_schema": {
        "type": "object",
        "properties": {
            "refined_query": {"type": "string", "description": "The improved query text"},
            "changes_made": {"type": "string", "description": "What was changed and why"},
            "expected_specificity": {
                "type": "string",
                "enum": ["general", "moderate", "specific", "highly_specific"],
            },
        },
        "required": ["refined_query", "changes_made", "expected_specificity"],
    },
}

RELEVANCE_CHECK_TOOL = {
    "name": "submit_relevance_check",
    "description": "Submit the relevance evaluation of search results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "evaluations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "paper_index": {"type": "integer"},
                        "title": {"type": "string"},
                        "relevant": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["paper_index", "relevant", "reason"],
                },
            },
            "relevant_count": {"type": "integer"},
            "total_checked": {"type": "integer"},
            "relevance_ratio": {"type": "number", "minimum": 0, "maximum": 1},
            "summary": {"type": "string", "description": "1 sentence on overall result quality"},
        },
        "required": ["evaluations", "relevant_count", "total_checked", "relevance_ratio", "summary"],
    },
}


class QueryGenerator:
    """Generate Semantic Scholar queries using Claude with tool_use for structured output."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.3,
        max_retries: int = 3,
    ):
        self.client = anthropic.Anthropic()
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries

    def generate(self, request: QueryRequest) -> tuple:
        """Generate candidate queries for a partnering request.

        Returns:
            (queries, expanded_sois) — list of GeneratedQuery and list of SOI strings
            that Claude identified from the request (properly parsed, no naive comma-split).
        """
        system_prompt = get_query_generation_system()

        user_prompt = QUERY_GENERATION_USER.format(
            title=request.title,
            looking_for=request.looking_for,
            use_case=request.use_case,
            solutions_of_interest=request.solutions_of_interest,
            requirements=request.requirements,
            out_of_scope=request.out_of_scope,
        )

        result = self._call_claude_tool(
            system=system_prompt,
            user=user_prompt,
            tool=GENERATE_QUERIES_TOOL,
        )
        if not result:
            logger.error("Failed to get response from Claude")
            return [], []

        return self._parse_response(result)

    def generate_recovery(
        self,
        request: QueryRequest,
        uncovered_sois: list,
        failed_by_soi: dict,
    ) -> tuple:
        """Generate recovery queries for uncovered SOIs.

        Args:
            request: The partnering request.
            uncovered_sois: List of SOICoverage objects with 0 valid queries.
            failed_by_soi: Dict mapping SOI name -> list of
                {"query": str, "result_count": int, "reason": str}.

        Returns:
            (queries, []) — list of GeneratedQuery (with is_recovery=True), empty SOI list.
        """
        from prompts import COVERAGE_RECOVERY_USER, get_coverage_recovery_system

        # Format uncovered SOIs
        uncovered_list = "\n".join(f"- {cov.soi}" for cov in uncovered_sois)

        # Format failed queries section
        failed_section_parts = []
        for cov in uncovered_sois:
            failed = failed_by_soi.get(cov.soi.lower(), [])
            part = f"\nSOI: {cov.soi}"
            if failed:
                for fq in failed:
                    part += f"\n  Tried: \"{fq['query']}\" → {fq['result_count']} results — {fq['reason']}"
            else:
                part += "\n  No queries were attempted for this SOI."
            failed_section_parts.append(part)

        failed_queries_section = "\n".join(failed_section_parts)

        user_prompt = COVERAGE_RECOVERY_USER.format(
            title=request.title,
            looking_for=request.looking_for,
            solutions_of_interest=request.solutions_of_interest,
            uncovered_sois=uncovered_list,
            failed_queries_section=failed_queries_section,
        )

        system_prompt = get_coverage_recovery_system()

        # Try up to 2 times — Claude occasionally returns malformed tool_use responses
        queries = []
        for attempt in range(2):
            result = self._call_claude_tool(
                system=system_prompt,
                user=user_prompt,
                tool=GENERATE_QUERIES_TOOL,
            )
            if not result:
                logger.error("Failed to get recovery queries from Claude")
                return [], []

            queries, _ = self._parse_response(result)
            if queries:
                break
            logger.warning(f"Recovery attempt {attempt + 1} returned 0 parsed queries, retrying...")

        # Mark all recovery queries
        for q in queries:
            q.is_recovery = True

        logger.info(f"Generated {len(queries)} recovery queries for {len(uncovered_sois)} uncovered SOIs")
        return queries, []

    def refine_query(
        self,
        query: GeneratedQuery,
        request: QueryRequest,
        problem: str,
        target_range: str,
    ) -> GeneratedQuery:
        """Refine a query that failed validation."""
        from prompts import QUERY_REFINEMENT_SYSTEM, QUERY_REFINEMENT_USER

        sample_titles_section = ""
        if query.sample_titles:
            titles_str = "\n".join(f"  - {t}" for t in query.sample_titles[:5])
            sample_titles_section = f"Sample titles from results:\n{titles_str}"

        request_context = (
            f"Title: {request.title}\n"
            f"Looking For: {request.looking_for}\n"
            f"SOIs: {request.solutions_of_interest}"
        )

        user_prompt = QUERY_REFINEMENT_USER.format(
            query=query.query,
            target_soi=query.target_soi,
            problem=problem,
            result_count=query.result_count or "unknown",
            target_range=target_range,
            sample_titles_section=sample_titles_section,
            request_context=request_context,
        )

        result = self._call_claude_tool(
            system=QUERY_REFINEMENT_SYSTEM,
            user=user_prompt,
            tool=REFINE_QUERY_TOOL,
        )
        if not result:
            logger.warning(f"Refinement failed for query: {query.query}")
            return query

        refined = GeneratedQuery(
            query=result.get("refined_query", query.query),
            target_soi=query.target_soi,
            rationale=result.get("changes_made", ""),
            expected_specificity=result.get("expected_specificity", ""),
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

    def check_relevance(
        self,
        query: GeneratedQuery,
        request: QueryRequest,
        papers: List[dict],
    ) -> dict:
        """Check if top search results are relevant to the request."""
        from prompts import RELEVANCE_CHECK_SYSTEM, RELEVANCE_CHECK_USER

        papers_section = ""
        for i, paper in enumerate(papers):
            title = paper.get("title", "Unknown")
            abstract = (paper.get("abstract") or "No abstract available")[:300]
            papers_section += f"\n[{i}] Title: {title}\n    Abstract: {abstract}\n"

        user_prompt = RELEVANCE_CHECK_USER.format(
            title=request.title,
            looking_for=request.looking_for,
            solutions_of_interest=request.solutions_of_interest,
            query=query.query,
            papers_section=papers_section,
        )

        result = self._call_claude_tool(
            system=RELEVANCE_CHECK_SYSTEM,
            user=user_prompt,
            tool=RELEVANCE_CHECK_TOOL,
        )
        if not result:
            return {"relevance_ratio": 1.0, "summary": "Check failed, assuming pass"}

        return result

    def _call_claude_tool(self, system: str, user: str, tool: dict) -> dict:
        """
        Call Claude API with tool_use — guarantees structured JSON output.
        Claude is forced to call the tool (tool_choice="any"), so the response
        is always a valid tool_use block with the schema we defined.
        """
        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    temperature=self.temperature,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool["name"]},
                )

                # With tool_choice forcing the tool, the first content block
                # is always a tool_use with parsed input
                for block in response.content:
                    if block.type == "tool_use":
                        return block.input

                logger.warning("No tool_use block in response")
                return {}

            except anthropic.RateLimitError:
                wait = 2 ** (attempt + 1)
                logger.warning(f"Claude rate limited. Waiting {wait}s...")
                time.sleep(wait)
            except Exception as e:
                logger.error(f"Claude API error (attempt {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)

        return {}

    def _parse_response(self, data: dict) -> tuple:
        """Parse Claude's tool_use response into GeneratedQuery objects and SOI list.

        Returns:
            (queries, all_soi_names) — list of GeneratedQuery and list of SOI strings
        """
        queries = []

        candidates = data.get("candidate_queries", [])
        if not candidates:
            logger.warning("No candidate_queries in response")
            return queries, []

        # Guard: if Claude returned a JSON string instead of a parsed list, try to parse it
        if isinstance(candidates, str):
            logger.warning(f"candidate_queries is a string — attempting JSON parse...")
            try:
                import json
                candidates = json.loads(candidates)
                if not isinstance(candidates, list):
                    logger.error(f"Parsed candidate_queries is not a list: {type(candidates)}")
                    return queries, []
                logger.info(f"Successfully parsed {len(candidates)} candidates from string")
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse candidate_queries string: {e}")
                return queries, []

        for item in candidates:
            if isinstance(item, str):
                logger.warning(f"Skipping non-dict candidate: {item[:80]}")
                continue
            if not isinstance(item, dict):
                logger.warning(f"Skipping unexpected candidate type: {type(item)}")
                continue
            q = GeneratedQuery(
                query=item.get("query", ""),
                target_soi=item.get("target_soi", ""),
                rationale=item.get("rationale", ""),
                expected_specificity=item.get("expected_specificity", ""),
            )
            if q.query:
                queries.append(q)

        # Log analysis summary
        analysis = data.get("analysis", {})
        if analysis:
            logger.info(f"Domain: {analysis.get('domain', 'unknown')}")
            logger.info(f"Key technologies: {analysis.get('key_technologies', [])}")

        # Extract properly-parsed SOI list from Claude's analysis
        expanded = data.get("expanded_sois", [])
        # Guard: if Claude returned a JSON string, try to parse it
        if isinstance(expanded, str):
            logger.warning(f"expanded_sois is a string — attempting JSON parse...")
            try:
                import json
                expanded = json.loads(expanded)
                if not isinstance(expanded, list):
                    expanded = []
            except (json.JSONDecodeError, ValueError):
                expanded = []
        expanded = [s for s in expanded if isinstance(s, dict)]
        all_soi_names = [s["soi"] for s in expanded if s.get("soi")]
        rfp_sois = [s["soi"] for s in expanded if s.get("from_rfp")]
        new_sois = [s["soi"] for s in expanded if not s.get("from_rfp")]
        logger.info(f"SOIs from RFP: {len(rfp_sois)}, Additional SOIs identified: {len(new_sois)}")
        logger.info(f"Generated {len(queries)} candidate queries")

        return queries, all_soi_names
