#!/usr/bin/env python3
"""
Agent Scout — Data Models
=========================
Central dataclass definitions for the scouting pipeline.
A ScoutLead accumulates fields as it passes through each stage.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class LeadStatus(Enum):
    """Tracks where a lead is in the pipeline."""
    PARSED = "parsed"
    DISCOVERED = "discovered"
    ENRICHMENT_PENDING = "enrichment_pending"
    ENRICHMENT_COMPLETE = "enrichment_complete"
    ENRICHMENT_FAILED = "enrichment_failed"
    SCORED = "scored"
    OUTPUT = "output"


class InputType(Enum):
    """The 4 input types for Agent Scout."""
    PARTNERING_REQUEST = "partnering_request"
    REQUEST_WITH_EXAMPLES = "request_with_examples"
    SCRAPED_LIST = "scraped_list"
    COMPANY_LIST = "company_list"


@dataclass
class ScoutLead:
    """A lead as it flows through the Agent Scout pipeline."""
    # Identity (populated at different stages)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None

    # Discovery context
    bio: Optional[str] = None
    company_description: Optional[str] = None
    discovery_source: Optional[str] = None  # "exa", "apollo", "input"
    source_url: Optional[str] = None        # The page this lead was extracted from
    seed_url: Optional[str] = None          # For find_similar: the upstream seed lead's source_url that triggered the search
    seed_query: Optional[str] = None        # For blurb-refined / query-driven discovery: the refined Exa query that surfaced this lead
    specific_expertise: Optional[List[str]] = field(default=None)  # Technologies/methods/research areas from Exa
    evidence_snippets: Optional[List[str]] = field(default=None)   # Direct facts from source page
    org_type: Optional[str] = None                                  # startup|university|research_institute|etc.

    # Scoring
    fit_score: Optional[float] = None       # 0.0 - 1.0
    fit_blurb: Optional[str] = None         # "Why they are a fit"
    scoring_error: Optional[str] = None     # Set when the scoring API call failed; distinguishes
                                            # "scored 0 because bad fit" from "we never got a real score"

    # Halo taxonomy classification
    country: Optional[str] = None           # Country name (e.g. "United States")
    disciplines: Optional[List[str]] = field(default=None)  # From Halo discipline list
    keywords: Optional[List[str]] = field(default=None)     # From Halo areas of expertise list

    # Enrichment metadata
    email_source: Optional[str] = None      # "n8n", "orcid", "openalex", "faculty_page"
    orcid_id: Optional[str] = None          # ORCID identifier (e.g. "0000-0002-1234-5678")

    # Halo platform check
    already_on_halo: Optional[bool] = None  # True if email found in USERS table
    halo_domain_count: Optional[int] = None  # Number of Halo users at this company's email domain

    # Pipeline metadata
    status: LeadStatus = LeadStatus.PARSED
    errors: List[str] = field(default_factory=list)
    raw_input: Optional[Dict[str, Any]] = None

    def full_name(self) -> str:
        parts = [p for p in [self.first_name, self.last_name] if p]
        return " ".join(parts) if parts else "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "first_name": self.first_name or "",
            "last_name": self.last_name or "",
            "email": self.email or "",
            "company": self.company or "",
            "title": self.title or "",
            "linkedin_url": self.linkedin_url or "",
            "bio": self.bio or "",
            "company_description": self.company_description or "",
            "discovery_source": self.discovery_source or "",
            "source_url": self.source_url or "",
            "seed_url": self.seed_url or "",
            "seed_query": self.seed_query or "",
            "specific_expertise": self.specific_expertise or [],
            "evidence_snippets": self.evidence_snippets or [],
            "org_type": self.org_type or "",
            "email_source": self.email_source or "",
            "orcid_id": self.orcid_id or "",
            "fit_score": self.fit_score,
            "fit_blurb": self.fit_blurb or "",
            "scoring_error": self.scoring_error or "",
            "country": self.country or "",
            "disciplines": self.disciplines or [],
            "keywords": self.keywords or [],
            "already_on_halo": self.already_on_halo,
            "halo_domain_count": self.halo_domain_count,
            "status": self.status.value,
            "errors": self.errors,
        }

    def richness(self) -> int:
        """Count how many useful fields are populated on this lead."""
        return sum(1 for v in [
            self.first_name, self.last_name, self.email, self.company,
            self.title, self.linkedin_url, self.bio, self.company_description,
            self.specific_expertise, self.evidence_snippets,
        ] if v)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScoutLead":
        status_val = data.pop("status", "parsed")
        status = LeadStatus(status_val) if isinstance(status_val, str) else status_val
        return cls(status=status, **{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ScoutConfig:
    """Configuration for a scouting run."""
    input_type: InputType

    # Request context (loaded from Snowflake or provided directly)
    request_id: Optional[int] = None
    request_title: Optional[str] = None
    request_looking_for: Optional[str] = None
    request_use_case: Optional[str] = None
    request_sois: Optional[str] = None
    request_partner_types: Optional[str] = None
    request_trl_range: Optional[str] = None
    request_requirements: Optional[str] = None
    request_out_of_scope: Optional[str] = None

    # Input sources
    input_sheet_url: Optional[str] = None   # For type 3 (scraped list) or type 2 (examples)
    input_sheet_tab: Optional[str] = None   # Specific tab/worksheet name in Google Sheet
    input_csv_path: Optional[str] = None    # Alternative: local CSV
    companies: Optional[List[str]] = None   # For type 4

    # Example innovators (for type 2)
    example_innovators: Optional[List[Dict[str, Any]]] = None
    example_patterns: Optional[Dict[str, Any]] = None  # Aggregate patterns from examples

    # Output
    output_sheet_url: Optional[str] = None
    output_sheet_name: Optional[str] = None  # New sheet name if creating

    # Discovery settings
    max_leads_per_company: int = 5
    discovery_sources: List[str] = field(default_factory=lambda: ["exa", "apollo"])

    # Exa discovery settings
    exa_num_results_per_query: int = 10
    exa_search_type: str = "neural"    # "neural" or "keyword"
    exa_use_contents: bool = True       # fetch page contents for extraction

    # Solve-planner / comprehensive discovery settings (Phase 1 upgrade)
    enable_solve_planner: bool = True       # produce a structured solve plan before queries
    num_solve_angles: int = 25              # target number of distinct solve angles
    solve_plan_model: str = "claude-sonnet-4-6"  # model for the planner call
    solve_plan_max_tokens: int = 16000      # room for 25 rich angles

    # Enrichment settings
    n8n_enrichment_webhook_url: Optional[str] = None
    enrichment_batch_size: int = 3

    # Scoring settings
    score_model: str = "claude-sonnet-4-6"
    score_temperature: float = 0.1
    min_fit_score: float = 0.3

    # Expansion loop settings
    enable_expansion: bool = True
    expansion_similar_threshold: float = 0.80   # min score for find_similar seeds
    expansion_blurb_threshold: float = 0.55     # min score for blurb synthesis inclusion
    expansion_max_similar_seeds: int = 10       # cap find_similar URLs per round
    expansion_min_qualified: int = 50           # target: stop when this many leads score >= min_fit_score
    expansion_max_rounds: int = 3               # hard cap on expansion loops
    expansion_min_new_per_round: int = 5        # early exit if round yields fewer qualified


@dataclass
class ScoutRun:
    """Tracks the state of a complete scouting run for resume capability."""
    run_id: str
    config: ScoutConfig
    leads: List[ScoutLead] = field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    stage: str = "init"
    stages_completed: List[str] = field(default_factory=list)
    # Per-stage results and counters surface here instead of being mutated onto
    # config (which is a smell — config is *input*, metrics are *output*).
    metrics: Dict[str, Any] = field(default_factory=dict)
    # Solve plan produced by the SolvePlanner stage (upstream of discover).
    # None when enable_solve_planner=False or planner has not yet run.
    solve_plan: Optional[Dict[str, Any]] = None


def deduplicate_leads(leads: List[ScoutLead]) -> List[ScoutLead]:
    """Deduplicate leads by (name + company), keeping the richest version."""
    seen: Dict[str, ScoutLead] = {}
    for lead in leads:
        if lead.first_name and lead.last_name:
            key = (
                f"{lead.first_name.lower().strip()}|"
                f"{lead.last_name.lower().strip()}|"
                f"{(lead.company or '').lower().strip()}"
            )
        elif lead.company:
            key = f"company_only|{lead.company.lower().strip()}"
        else:
            key = f"unknown_{id(lead)}"

        existing = seen.get(key)
        if not existing or lead.richness() > existing.richness():
            seen[key] = lead

    return list(seen.values())
