#!/usr/bin/env python3
"""
Agent Scout — DSPy Fit Scorer Module
======================================
Wraps the fit scoring logic as a DSPy module so GEPA can optimize its prompt.

This module is used ONLY during optimization. Production inference continues
to use fit_scorer.py with the tool-use pattern. GEPA optimizes the instruction
text, which is then extracted and saved via prompt_store.
"""

import re
import dspy
from typing import Optional


class FitScoring(dspy.Signature):
    """Evaluate whether a candidate is a good fit for a corporate R&D partnering request.
Score from 0.0 to 1.0 and explain why."""

    request_context: str = dspy.InputField(
        desc="The full partnering request: title, looking for, use case, solutions of interest, partner types, requirements, out of scope"
    )
    candidate_profile: str = dspy.InputField(
        desc="The candidate's name, title, company, bio, company description, expertise, evidence, org type, discovery source"
    )

    score: float = dspy.OutputField(
        desc="Fit score from 0.0 to 1.0. Use the full range with fine-grained values (0.72, 0.63, 0.81)."
    )
    blurb: str = dspy.OutputField(
        desc="2-3 sentence explanation of why this person is or isn't a fit. Reference specific evidence. If score < 0.80, state what's missing."
    )


class DSPyFitScorer(dspy.Module):
    """DSPy module for fit scoring — optimizable by GEPA."""

    def __init__(self):
        super().__init__()
        self.scorer = dspy.ChainOfThought(FitScoring)

    def forward(self, request_context: str, candidate_profile: str) -> dspy.Prediction:
        return self.scorer(request_context=request_context, candidate_profile=candidate_profile)


def format_request_context(example: dict) -> str:
    """Format a feedback example's request context into a single string for DSPy."""
    # Pull from the example dict — these come from the sheet's request metadata
    # or from the Snowflake data if available
    parts = []
    if example.get("request_title"):
        parts.append(f"Title: {example['request_title']}")
    if example.get("request_looking_for"):
        parts.append(f"Looking For: {example['request_looking_for']}")
    if example.get("request_use_case"):
        parts.append(f"Use Case: {example['request_use_case']}")
    if example.get("request_sois"):
        parts.append(f"Solutions of Interest: {example['request_sois']}")
    if example.get("request_partner_types"):
        parts.append(f"Partner Types Sought: {example['request_partner_types']}")
    if example.get("request_requirements"):
        parts.append(f"Requirements: {example['request_requirements']}")
    if example.get("request_out_of_scope"):
        parts.append(f"Out of Scope: {example['request_out_of_scope']}")
    return "\n".join(parts) if parts else "Request context not available"


def format_candidate_profile(example: dict) -> str:
    """Format a feedback example's candidate info into a single string for DSPy."""
    parts = [
        f"Name: {example.get('first_name', '')} {example.get('last_name', '')}",
        f"Title: {example.get('title', 'Unknown')}",
        f"Company: {example.get('company', 'Unknown')}",
    ]
    if example.get("company_description"):
        parts.append(f"Company Description: {example['company_description']}")
    if example.get("fit_blurb"):
        # Use the original LLM blurb as bio proxy since sheets don't store raw bio
        parts.append(f"Bio/Background: {example['fit_blurb']}")
    if example.get("disciplines"):
        parts.append(f"Disciplines: {example['disciplines']}")
    if example.get("keywords"):
        parts.append(f"Expertise: {example['keywords']}")
    if example.get("discovery_source"):
        parts.append(f"Discovery Source: {example['discovery_source']}")
    if example.get("country"):
        parts.append(f"Country: {example['country']}")
    return "\n".join(parts)


def parse_score(prediction: dspy.Prediction) -> Optional[float]:
    """Extract a float score from a DSPy prediction."""
    try:
        score_val = prediction.score
        if isinstance(score_val, (int, float)):
            return max(0.0, min(1.0, float(score_val)))
        # Try parsing from string
        match = re.search(r"(\d+\.?\d*)", str(score_val))
        if match:
            return max(0.0, min(1.0, float(match.group(1))))
    except Exception:
        pass
    return None
