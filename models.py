#!/usr/bin/env python3
"""
RoboScout Query Generator — Data Models
========================================
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class QueryCategory(Enum):
    """Query classification per scout guide."""
    TOO_NARROW = "too_narrow"            # < 20 results (rejected)
    SPECIFIC = "specific"                # 20–499 results
    MODERATE = "moderate"                # 500–1,000 results
    GENERAL = "general"                  # 1,001–3,000 results
    TOO_BROAD = "too_broad"              # > 3,000 results (rejected)

    @classmethod
    def from_count(cls, count: int) -> "QueryCategory":
        if count < 20:
            return cls.TOO_NARROW
        elif count < 500:
            return cls.SPECIFIC
        elif count <= 1000:
            return cls.MODERATE
        elif count <= 3000:
            return cls.GENERAL
        else:
            return cls.TOO_BROAD


@dataclass
class QueryRequest:
    """Partnering request data used to generate queries."""
    request_id: Optional[int] = None
    title: str = ""
    looking_for: str = ""
    use_case: str = ""
    solutions_of_interest: str = ""
    requirements: str = ""
    out_of_scope: str = ""
    partner_types: str = ""
    trl_range: str = ""
    must_have_requirements: List[str] = field(default_factory=list)


@dataclass
class GeneratedQuery:
    """A single generated Semantic Scholar query."""
    query: str = ""
    target_soi: str = ""                          # Which SOI this query covers
    rationale: str = ""                            # Why this query was generated
    expected_specificity: str = ""                 # LLM's prediction: general/moderate/specific/highly_specific

    # Populated after validation
    result_count: Optional[int] = None
    category: Optional[QueryCategory] = None
    relevance_passed: Optional[bool] = None       # >60% of top results relevant?
    relevance_details: str = ""                    # What the relevance check found
    sample_titles: List[str] = field(default_factory=list)

    # Refinement tracking
    refinement_round: int = 0                      # 0 = original, 1+ = refined
    original_query: Optional[str] = None           # Before refinement
    refinement_reason: str = ""                    # Why it was refined

    # Recovery / regeneration tracking
    is_recovery: bool = False                      # True if from coverage recovery pass
    is_regeneration: bool = False                  # True if generated fresh after refinement exhaustion

    @property
    def is_valid(self) -> bool:
        """Query passes all validation checks."""
        if self.result_count is None:
            return False
        if self.category in (QueryCategory.TOO_BROAD, QueryCategory.TOO_NARROW):
            return False
        return self.relevance_passed is not False

    @property
    def is_unvalidated(self) -> bool:
        """Query was never validated (e.g., S2 API timeout)."""
        return self.result_count is None and self.relevance_passed is None

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "target_soi": self.target_soi,
            "rationale": self.rationale,
            "result_count": self.result_count,
            "category": self.category.value if self.category else None,
            "relevance_passed": self.relevance_passed,
            "relevance_details": self.relevance_details,
            "sample_titles": self.sample_titles,
            "refinement_round": self.refinement_round,
            "original_query": self.original_query,
            "is_recovery": self.is_recovery,
            "is_regeneration": self.is_regeneration,
        }


@dataclass
class SOICoverage:
    """Tracks which SOIs have queries covering them."""
    soi: str = ""
    queries: List[str] = field(default_factory=list)
    best_query: str = ""           # Best-performing query for this SOI
    best_result_count: Optional[int] = None
    total_results: int = 0         # Sum of result counts across all valid queries
    has_specific: bool = False     # At least one query in SPECIFIC (20-499) range

    @property
    def meets_requirements(self) -> bool:
        """SOI is fully covered: has queries, ≥1 specific, ≥100 total results."""
        return bool(self.queries) and self.has_specific and self.total_results >= 100


@dataclass
class QueryRun:
    """Full run metadata."""
    run_id: str = ""
    request: Optional[QueryRequest] = None
    queries: List[GeneratedQuery] = field(default_factory=list)
    soi_coverage: List[SOICoverage] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    model_used: str = "claude-sonnet-4-20250514"

    @property
    def valid_queries(self) -> List[GeneratedQuery]:
        return [q for q in self.queries if q.is_valid]

    @property
    def unvalidated_queries(self) -> List[GeneratedQuery]:
        return [q for q in self.queries if q.is_unvalidated]

    def to_full_dict(self) -> dict:
        """Serialize the full run to a dict (for JSON output)."""
        valid = self.valid_queries
        unvalidated = self.unvalidated_queries
        rejected = [
            q for q in self.queries
            if not q.is_valid and not q.is_unvalidated
        ]

        def _rejection_reason(q: GeneratedQuery) -> str:
            if q.category == QueryCategory.TOO_BROAD:
                return f"Too broad ({q.result_count} > 3000)"
            elif q.category == QueryCategory.TOO_NARROW:
                return f"Too narrow ({q.result_count} < 20)"
            elif q.result_count == 0:
                return "Zero results — query too narrow"
            else:
                return f"Low relevance ({q.relevance_details})"

        return {
            "run_id": self.run_id,
            "request_id": self.request.request_id if self.request else None,
            "request_title": self.request.title if self.request else "",
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "model_used": self.model_used,
            "stats": self.stats,
            "valid_queries": [
                {
                    "query": q.query,
                    "result_count": q.result_count,
                    "category": q.category.value if q.category else None,
                    "target_soi": q.target_soi,
                    "rationale": q.rationale,
                    "relevance_passed": q.relevance_passed,
                    "relevance_details": q.relevance_details,
                    "refinement_round": q.refinement_round,
                    "original_query": q.original_query,
                    "is_recovery": q.is_recovery,
                    "is_regeneration": q.is_regeneration,
                }
                for q in sorted(valid, key=lambda x: x.result_count or 0)
            ],
            "unvalidated_queries": [
                {
                    "query": q.query,
                    "target_soi": q.target_soi,
                    "rationale": q.rationale,
                    "refinement_round": q.refinement_round,
                    "original_query": q.original_query,
                }
                for q in unvalidated
            ],
            "rejected_queries": [
                {
                    "query": q.query,
                    "result_count": q.result_count,
                    "category": q.category.value if q.category else None,
                    "target_soi": q.target_soi,
                    "rejection_reason": _rejection_reason(q),
                }
                for q in rejected
            ],
            "soi_coverage": [
                {
                    "soi": cov.soi,
                    "num_queries": len(cov.queries),
                    "best_query": cov.best_query,
                    "best_result_count": cov.best_result_count,
                }
                for cov in self.soi_coverage
            ],
        }

    @property
    def stats(self) -> dict:
        return {
            "total_generated": len(self.queries),
            "valid": len(self.valid_queries),
            "unvalidated": len(self.unvalidated_queries),
            "rejected_too_broad": sum(1 for q in self.queries if q.category == QueryCategory.TOO_BROAD),
            "rejected_too_narrow": sum(1 for q in self.queries if q.category == QueryCategory.TOO_NARROW),
            "rejected_irrelevant": sum(
                1 for q in self.queries
                if q.relevance_passed is False and not q.is_unvalidated
                and q.category not in (QueryCategory.TOO_BROAD, QueryCategory.TOO_NARROW)
            ),
            "by_category": {
                cat.value: sum(1 for q in self.valid_queries if q.category == cat)
                for cat in QueryCategory if cat not in (QueryCategory.TOO_BROAD, QueryCategory.TOO_NARROW)
            },
            "sois_covered": len([s for s in self.soi_coverage if s.meets_requirements]),
            "sois_total": len(self.soi_coverage),
        }
