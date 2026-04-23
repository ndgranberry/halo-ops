#!/usr/bin/env python3
"""
Agent Scout — Fit Scorer (Module 4)
=====================================
Uses Claude API as LLM-as-judge to score each lead's fit (0.0-1.0)
and generate a short "why they are a fit" blurb.

Pattern adapted from Proposal_Fit_Score/fit_scorer.py.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Dict, Any

from .claude_client import ClaudeClient, build_cached_user_blocks
from .models import ScoutLead, ScoutConfig, LeadStatus
from .taxonomy import DISCIPLINES, AREAS_OF_EXPERTISE
from .prompts import (
    FIT_SCORING_SYSTEM,
    FIT_SCORING_USER_PREFIX, FIT_SCORING_USER_CANDIDATE,
    FIT_SCORING_EXAMPLES_SECTION, FIT_SCORING_PATTERNS_SECTION,
)
from .prompt_store import get_active_prompt

logger = logging.getLogger(__name__)

# Tool schema for structured scoring output
SCORE_LEAD_TOOL = {
    "name": "score_lead",
    "description": "Submit the fit score and classification for a lead.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "description": "Fit score from 0.0 to 1.0",
            },
            "blurb": {
                "type": "string",
                "description": "2-3 sentence explanation of why this lead is or isn't a fit",
            },
            "country": {
                "type": "string",
                "description": "Lead's country of residence or primary work location",
            },
            "disciplines": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Halo disciplines that apply to this lead",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Halo areas of expertise keywords that apply",
            },
        },
        "required": ["score", "blurb"],
    },
}


class FitScorer:
    """Score leads using Claude API as LLM-as-judge."""

    def __init__(self, config: ScoutConfig):
        self.config = config
        self.claude = ClaudeClient(model=config.score_model, temperature=config.score_temperature)
        # Use GEPA-optimized prompt if available, otherwise fall back to baseline
        optimized = get_active_prompt()
        if optimized:
            self._system_prompt = optimized
            logger.info("Using GEPA-optimized scoring prompt")
        else:
            self._system_prompt = FIT_SCORING_SYSTEM
            logger.info("Using baseline scoring prompt (no GEPA optimization active)")

    def score_leads(self, leads: List[ScoutLead]) -> List[ScoutLead]:
        """
        Score all leads for fit against the request context.
        Updates each lead's fit_score and fit_blurb in place.
        """
        if not self.config.request_looking_for:
            logger.warning("No request context — scoring will be less accurate")

        scorable = [l for l in leads if l.bio or l.company_description or l.specific_expertise]
        logger.info(f"Scoring {len(scorable)} leads (skipping {len(leads) - len(scorable)} without data)")

        # Build the run-constant cached prefix once (request context + examples
        # + classification rules + Halo taxonomies). All per-lead calls reuse
        # this exact byte-for-byte prefix so prompt caching is effective.
        cached_prefix = self._build_cached_prefix()

        # Score in parallel — 10 concurrent API calls
        max_workers = min(10, len(scorable))

        def _score_one(lead: ScoutLead) -> None:
            try:
                score, blurb = self._score_single(lead, cached_prefix)
                # `_score_single` returns (0.0, "LLM evaluation failed") when the API
                # call returns no tool_use block — flag that as an error, not a 0 score.
                if blurb == "LLM evaluation failed":
                    lead.scoring_error = "LLM evaluation failed (no tool_use response)"
                    lead.fit_score = None
                    lead.fit_blurb = lead.scoring_error
                else:
                    lead.fit_score = score
                    lead.fit_blurb = blurb
                lead.status = LeadStatus.SCORED
                logger.info(f"Scored {lead.full_name()} @ {lead.company or 'Unknown'}: {score:.2f}")
            except Exception as e:
                logger.error(f"Scoring failed for {lead.full_name()}: {e}")
                lead.scoring_error = f"Scoring failed: {e}"
                lead.fit_score = None
                lead.fit_blurb = lead.scoring_error
                lead.errors.append(lead.scoring_error)
                lead.status = LeadStatus.SCORED

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_score_one, lead): lead for lead in scorable}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    lead = futures[future]
                    logger.error(f"Unexpected scoring thread error for {lead.full_name()}: {e}")
                    lead.scoring_error = f"Scoring thread error: {e}"
                    lead.fit_score = None
                    lead.fit_blurb = lead.scoring_error
                    lead.status = LeadStatus.SCORED

        # Leads skipped for lack of data are NOT scoring errors — they genuinely
        # had no signal to evaluate. Score them as 0 so the threshold drops them.
        for lead in leads:
            if lead.fit_score is None and not lead.scoring_error:
                lead.fit_score = 0.0
                lead.fit_blurb = "Insufficient data to evaluate fit"
                lead.status = LeadStatus.SCORED

        scored = sum(1 for l in leads if l.fit_score and l.fit_score > 0)
        usage = self.claude.usage_summary()
        logger.info(
            f"Scoring complete: {scored}/{len(leads)} with positive scores | "
            f"{usage['calls']} calls | "
            f"in={usage['input_tokens']} out={usage['output_tokens']} "
            f"cache_write={usage['cache_creation_tokens']} cache_read={usage['cache_read_tokens']}"
        )

        return leads

    def _score_single(self, lead: ScoutLead, cached_prefix: str) -> tuple[float, str]:
        """Score and classify a single lead. Returns (score, blurb). Also sets lead.country/disciplines/keywords."""
        candidate_block = FIT_SCORING_USER_CANDIDATE.format(
            first_name=lead.first_name or "",
            last_name=lead.last_name or "",
            title=lead.title or "Unknown",
            company=lead.company or "Unknown",
            bio=lead.bio or "No bio available",
            company_description=lead.company_description or "No description available",
            extra_context=self._build_extra_context(lead),
        )

        user_blocks = build_cached_user_blocks(cached_prefix, candidate_block)

        data = self.claude.call_with_tools(
            system=self._system_prompt,
            user=user_blocks,
            tools=[SCORE_LEAD_TOOL],
            max_tokens=700,
            tool_choice={"type": "tool", "name": "score_lead"},
        )

        if not data:
            return 0.0, "LLM evaluation failed"

        return self._parse_tool_response(data, lead)

    def _parse_tool_response(self, data: Dict[str, Any], lead: ScoutLead = None) -> tuple[float, str]:
        """Parse structured tool use response. Much simpler than regex-based parsing."""
        score = float(data.get("score", 0.0))
        blurb = data.get("blurb", "No explanation provided")
        score = max(0.0, min(1.0, score))

        if lead is not None:
            if data.get("country"):
                lead.country = data["country"]
            if data.get("disciplines"):
                lead.disciplines = [d for d in data["disciplines"] if d in DISCIPLINES]
            if data.get("keywords"):
                lead.keywords = [k for k in data["keywords"] if k in AREAS_OF_EXPERTISE]

        return score, blurb

    def _build_cached_prefix(self) -> str:
        """Render the run-constant prefix used for every lead (cached as a single
        ephemeral block).
        """
        return FIT_SCORING_USER_PREFIX.format(
            request_title=self.config.request_title or "Not specified",
            request_looking_for=self.config.request_looking_for or "Not specified",
            request_use_case=self.config.request_use_case or "Not specified",
            request_sois=self.config.request_sois or "Not specified",
            request_partner_types=self.config.request_partner_types or "Not specified",
            request_requirements=self.config.request_requirements or "Not specified",
            request_out_of_scope=self.config.request_out_of_scope or "Not specified",
            examples_section=self._build_examples_section(),
            disciplines_list=", ".join(DISCIPLINES),
            keywords_list=", ".join(AREAS_OF_EXPERTISE),
        )

    @staticmethod
    def _build_extra_context(lead: ScoutLead) -> str:
        """Build optional extra context block for the scoring prompt."""
        parts = []
        if lead.discovery_source:
            parts.append(f"Discovery Source: {lead.discovery_source}")
        if lead.org_type:
            parts.append(f"Organization Type: {lead.org_type}")
        if lead.specific_expertise:
            parts.append(f"Specific Expertise: {', '.join(lead.specific_expertise)}")
        if lead.evidence_snippets:
            parts.append(f"Evidence: {' | '.join(lead.evidence_snippets)}")
        return "\n".join(parts)

    def _build_examples_section(self) -> str:
        """Build the few-shot examples section for type 2 input, with rich signals."""
        if not self.config.example_innovators:
            return ""

        examples = []
        for ex in self.config.example_innovators:
            name = ex.get("name", "Unknown")
            company = ex.get("company", "Unknown")
            title = ex.get("title", "")
            user_type = ex.get("user_type", "")
            expertise = ex.get("areas_of_expertise", "")
            reason = ex.get("why_good_fit", ex.get("reason", "Confirmed good fit"))

            # Build a richer example line
            header = f"- {name}"
            if title:
                header += f", {title}"
            header += f" ({company})"
            if user_type:
                header += f" [{user_type}]"

            parts = [header]
            parts.append(f"  Fit reason: {reason}")
            if expertise:
                parts.append(f"  Expertise: {expertise}")

            examples.append("\n".join(parts))

        # Cap at 15 examples to avoid token bloat
        if len(examples) > 15:
            truncated = len(examples) - 15
            examples = examples[:15]
            examples.append(f"... and {truncated} more confirmed good fits")
            logger.info(f"Truncated examples section: showing 15 of {15 + truncated}")

        examples_text = "\n".join(examples)

        # Optionally append aggregate patterns section
        if self.config.example_patterns:
            p = self.config.example_patterns
            patterns_text = FIT_SCORING_PATTERNS_SECTION.format(
                startup_count=p.get("startup_count", 0),
                scientist_count=p.get("scientist_count", 0),
                common_titles=", ".join(p.get("common_titles", [])[:8]),
                areas_of_expertise=", ".join(p.get("areas_of_expertise", [])[:8]),
            )
            examples_text += "\n" + patterns_text

        return FIT_SCORING_EXAMPLES_SECTION.format(examples=examples_text)
