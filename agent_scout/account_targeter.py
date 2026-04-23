#!/usr/bin/env python3
"""
Agent Scout — Smart Account Targeter
======================================
Data-driven targeting rules derived from analysis of 2,060 activated Halo users.

Key finding: the right contact title depends on org size and type.
- Startups: CEO/Founder/CSO activate (59% C-suite)
- Scale-ups: VP/Director/Head of R&D
- Large corps: DO NOT target C-suite — Sales/BD/Account Managers activate
- Universities: PIs (86% of academic activators)
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class OrgType(Enum):
    STARTUP = "startup"           # <50 employees
    SCALEUP = "scaleup"           # 50-500 employees
    LARGE_CORP = "large_corp"     # 500+ employees
    UNIVERSITY = "university"
    RESEARCH_INSTITUTE = "research_institute"
    SUPPLIER = "supplier"
    UNKNOWN = "unknown"


@dataclass
class TargetingResult:
    """Recommendation for who to contact at an organization."""
    org_type: OrgType
    estimated_size: Optional[int]
    target_titles: List[str]
    flag: Optional[str]  # e.g., "Large org, need to determine contact"
    reasoning: str


# =========================================================================
# Targeting Rules (validated against 2,060 activated users)
# =========================================================================

TARGETING_RULES = {
    OrgType.STARTUP: {
        "titles": [
            "CEO", "Founder", "Co-founder", "CTO",
            "Chief Scientific Officer", "CSO",
        ],
        "seniority": ["founder", "c_suite"],
        "flag": None,
        "note": "59% of startup activators are C-suite. They are the decision-maker AND domain expert.",
    },
    OrgType.SCALEUP: {
        "titles": [
            "VP R&D", "VP Research", "Director of Innovation",
            "Director of R&D", "Head of Research", "CTO",
            "Director of Technology", "VP Technology",
        ],
        "seniority": ["vp", "director"],
        "flag": None,
        "note": "Scale-ups send mid-level R&D leadership, not C-suite.",
    },
    OrgType.LARGE_CORP: {
        "titles": [
            "Open Innovation Manager", "Technology Scouting",
            "External R&D", "Business Development",
            "Head of Open Innovation", "Director of External Innovation",
        ],
        "seniority": ["director", "manager", "vp"],
        "flag": "Large org, need to determine contact",
        "note": "Large corp activators are Sales/BD/Marketing — NOT C-suite. Flag for human review.",
    },
    OrgType.UNIVERSITY: {
        "titles": [
            "Principal Investigator", "Lab Director",
            "Department Head", "Professor", "Associate Professor",
        ],
        "seniority": ["senior", "director"],
        "flag": None,
        "note": "86% of university activators are PIs.",
    },
    OrgType.RESEARCH_INSTITUTE: {
        "titles": [
            "Principal Investigator", "Group Leader",
            "Senior Scientist", "Program Director", "Research Director",
        ],
        "seniority": ["senior", "director"],
        "flag": None,
        "note": "Research institutes: target lab/group leaders.",
    },
    OrgType.SUPPLIER: {
        "titles": [
            "Technical Manager", "Head of Applications",
            "CTO", "R&D Manager", "Product Manager",
        ],
        "seniority": ["manager", "director"],
        "flag": None,
        "note": "Suppliers send operational/technical managers.",
    },
}


# =========================================================================
# Known Large Companies (avoid unnecessary API calls)
# =========================================================================

KNOWN_LARGE_CORPS = {
    # Food & Ingredients
    "iff", "international flavors & fragrances", "cargill", "symrise",
    "givaudan", "kerry", "ingredion", "tate & lyle", "cp kelco",
    "adm", "archer daniels midland", "dsm", "dsm-firmenich",
    "novozymes", "chr. hansen", "dupont", "danisco", "firmenich",
    "corbion", "ajinomoto", "roquette", "kemin", "sensient",
    "lonza", "lallemand", "lesaffre", "puratos", "brenntag",
    # Consumer goods / F&B
    "unilever", "nestle", "nestlé", "danone", "pepsico", "pepsi",
    "coca-cola", "mars", "mondelez", "kraft heinz", "general mills",
    "kellogg", "conagra", "campbell", "mccormick", "hershey",
    "tyson", "jbs", "mccain", "bunge",
    # Ag & Crop Science
    "bayer", "syngenta", "corteva", "basf", "fmc", "upl",
    "sumitomo chemical", "nufarm",
    # Pharma & Biotech (large)
    "roche", "pfizer", "novartis", "merck", "johnson & johnson",
    "abbvie", "amgen", "genentech", "gilead", "biogen",
    "astrazeneca", "eli lilly", "gsk", "glaxosmithkline",
    "boehringer ingelheim", "takeda", "sanofi",
    # Chemicals
    "dow", "dupont", "3m", "henkel", "evonik", "clariant",
    "arkema", "eastman", "celanese", "sabic",
    # Other large
    "procter & gamble", "p&g", "l'oreal", "loreal",
    "colgate-palmolive", "reckitt", "church & dwight",
}

KNOWN_UNIVERSITIES = {
    "university", "universität", "université", "universidad",
    "universidade", "università", "college", "institute of technology",
    "polytechnic", "école", "eth ", "mit ", "caltech",
    "school of", "faculty of", "department of",
}

KNOWN_RESEARCH_INSTITUTES = {
    "research institute", "research center", "research centre",
    "national lab", "national laboratory", "fraunhofer",
    "max planck", "cnrs", "csic", "csiro", "inrae", "embrapa",
    "agricultural research", "food research", "usda", "ars ",
    "southwest research institute",
}


class AccountTargeter:
    """Smart targeting engine for agent scout."""

    def classify_org(self, org_name: str, estimated_size: Optional[int] = None) -> OrgType:
        """
        Classify an organization by type based on name and/or size.
        Uses heuristics first, falls back to size-based classification.
        """
        if not org_name:
            return OrgType.UNKNOWN

        name_lower = org_name.lower().strip()

        # Check known large corps
        for corp in KNOWN_LARGE_CORPS:
            if corp in name_lower:
                return OrgType.LARGE_CORP

        # Check universities
        for kw in KNOWN_UNIVERSITIES:
            if kw in name_lower:
                return OrgType.UNIVERSITY

        # Check research institutes
        for kw in KNOWN_RESEARCH_INSTITUTES:
            if kw in name_lower:
                return OrgType.RESEARCH_INSTITUTE

        # Size-based classification
        if estimated_size is not None:
            if estimated_size > 500:
                return OrgType.LARGE_CORP
            elif estimated_size > 50:
                return OrgType.SCALEUP
            else:
                return OrgType.STARTUP

        # Default: assume startup (most common on Halo)
        return OrgType.STARTUP

    def get_targeting(
        self, org_name: str, estimated_size: Optional[int] = None
    ) -> TargetingResult:
        """
        Get targeting recommendation for an organization.

        Returns target titles, seniority levels, and any flags.
        """
        org_type = self.classify_org(org_name, estimated_size)
        rules = TARGETING_RULES.get(org_type, TARGETING_RULES[OrgType.STARTUP])

        return TargetingResult(
            org_type=org_type,
            estimated_size=estimated_size,
            target_titles=rules["titles"],
            flag=rules.get("flag"),
            reasoning=rules["note"],
        )

    def should_flag_large_org(self, org_name: str, estimated_size: Optional[int] = None) -> bool:
        """Quick check: should this org be flagged instead of auto-targeted?"""
        org_type = self.classify_org(org_name, estimated_size)
        return org_type == OrgType.LARGE_CORP
