#!/usr/bin/env python3
"""
Agent Scout — Person Discovery (Module 2)
===========================================
Given a company name (and optionally a partnering request), finds the
right people to contact.

Two-step approach:
1. Claude generates a "person spec" — target titles/roles based on
   company type + request context
2. n8n webhook queries Apollo/Amplemarket for people matching
   company + titles

Handles the key insight that different company types need different
title targeting:
  - Startup (5-50 people): CEO, CTO, Co-Founder, CSO
  - Scale-up (50-500): VP R&D, Head of Research, Director of Innovation
  - Large corp (500+): Open Innovation, Tech Scouting, External R&D
  - University/research lab: PI, Professor, Department Head, Lab Director
  - CRO/service provider: VP Business Dev, Head of Partnerships
"""

import json
import logging
import os
import time
from typing import List, Dict, Any, Optional

import requests

from .claude_client import ClaudeClient
from .models import ScoutLead, ScoutConfig, InputType, LeadStatus, deduplicate_leads
from .prompts import (
    PERSON_SPEC_SYSTEM, PERSON_SPEC_USER,
    EXAMPLE_PATTERNS_CONTEXT, SEARCH_CRITERIA_FROM_REQUEST,
    CONTACT_RESOLUTION_SYSTEM, CONTACT_RESOLUTION_USER,
)

logger = logging.getLogger(__name__)

# Default timeout for n8n webhook
WEBHOOK_TIMEOUT_SECONDS = 120

# -- Tool schemas for structured output --

GENERATE_PERSON_SPEC_TOOL = {
    "name": "generate_person_spec",
    "description": "Specify what titles/roles to target at a company.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_type": {
                "type": "string",
                "description": "startup, scaleup, large_corp, university, research_institute, cro, government, unknown",
            },
            "titles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Target job titles to search for at this company",
            },
            "seniority": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Seniority levels: director, vp, c_suite, founder, manager, etc.",
            },
            "departments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Target departments: engineering, research, executive, etc.",
            },
            "company_description": {
                "type": "string",
                "description": "Brief company description if known",
            },
            "reasoning": {
                "type": "string",
                "description": "Why these titles were chosen for this company type",
            },
        },
        "required": ["titles"],
    },
}

RESOLVE_CONTACTS_TOOL = {
    "name": "resolve_contacts",
    "description": "Identify key contacts at a company from search results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "contacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "title": {"type": "string"},
                        "linkedin_url": {"type": "string"},
                    },
                    "required": ["first_name", "last_name"],
                },
            },
            "estimated_employees": {
                "type": "integer",
                "description": "Estimated number of employees",
            },
            "large_org": {
                "type": "boolean",
                "description": "True if this is a large organization (1000+ employees)",
            },
            "reasoning": {
                "type": "string",
                "description": "How contacts were identified",
            },
        },
        "required": ["contacts", "estimated_employees"],
    },
}

GENERATE_SEARCH_CRITERIA_TOOL = {
    "name": "generate_search_criteria",
    "description": "Generate broad search criteria from a partnering request.",
    "input_schema": {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Key search terms and topics",
            },
            "titles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Target job titles to search for",
            },
            "search_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Full search query strings for people databases",
            },
            "company_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Types of companies to target",
            },
        },
        "required": ["keywords", "titles", "search_queries"],
    },
}


class PersonDiscovery:
    """Find the right people at companies based on request context."""

    def __init__(self, config: ScoutConfig):
        self.config = config
        self.claude = ClaudeClient(model=config.score_model, temperature=0.3)
        self.discovery_webhook_url = os.getenv(
            "N8N_DISCOVERY_WEBHOOK_URL",
            config.n8n_enrichment_webhook_url,  # fallback to enrichment webhook
        )

        # Initialize Exa discovery if configured
        self.exa_discovery = None
        if "exa" in config.discovery_sources:
            try:
                from .exa_discovery import ExaDiscovery
                self.exa_discovery = ExaDiscovery(config)
                logger.info("Exa discovery initialized")
            except Exception as e:
                logger.warning(f"Exa discovery unavailable: {e}")

    def discover(
        self,
        leads: List[ScoutLead],
        solve_plan: Optional[Dict[str, Any]] = None,
        on_angle_progress=None,
    ) -> List[ScoutLead]:
        """
        Main discovery orchestration. Behavior depends on input type:

        - SCRAPED_LIST: Skip (leads already have people)
        - COMPANY_LIST: Find people at each company
        - PARTNERING_REQUEST: Generate search criteria, find people
        - REQUEST_WITH_EXAMPLES: Same as above, enhanced by examples

        If a solve_plan dict is provided (from the upstream SolvePlanner stage),
        Exa discovery uses the angle-based multi-track entry point instead of
        the one-shot query-generation path. `on_angle_progress` is an optional
        callback(angle_idx, angle_name, angle_leads) invoked after each angle
        so the caller can persist per-angle checkpoints.
        """
        if self.config.input_type == InputType.SCRAPED_LIST:
            logger.info("Skipping person discovery (scraped list already has people)")
            for lead in leads:
                lead.status = LeadStatus.DISCOVERED
            return leads

        if self.config.input_type == InputType.COMPANY_LIST:
            return self._discover_at_companies(leads)

        if self.config.input_type in (InputType.PARTNERING_REQUEST, InputType.REQUEST_WITH_EXAMPLES):
            return self._discover_from_request(
                solve_plan=solve_plan, on_angle_progress=on_angle_progress
            )

        return leads

    # =========================================================================
    # Company List Discovery (Type 4)
    # =========================================================================

    def _discover_at_companies(self, leads: List[ScoutLead]) -> List[ScoutLead]:
        """Find the right people at each company in the list."""
        discovered_leads = []

        for i, lead in enumerate(leads):
            company = lead.company
            if not company:
                continue

            logger.info(f"[{i+1}/{len(leads)}] Discovering people at {company}")

            try:
                # Step 1: Claude generates person spec for this company
                person_spec = self._generate_person_spec(company)
                if not person_spec or "titles" not in person_spec:
                    person_spec = self._default_person_spec(company)
                logger.info(f"  Target titles: {person_spec['titles'][:3]}...")

                # Step 2: Search for people matching the spec (Apollo + Exa)
                people = self._search_people(company, person_spec)

                # Supplement with Exa results if available
                if self.exa_discovery:
                    try:
                        exa_people = self.exa_discovery.discover_at_company(company, person_spec)
                        for ep in exa_people:
                            people.append(ep.to_dict())
                    except Exception as e:
                        logger.warning(f"  Exa company search failed for {company}: {e}")

                if people:
                    for person in people:
                        new_lead = ScoutLead(
                            first_name=person.get("first_name"),
                            last_name=person.get("last_name"),
                            company=company,
                            title=person.get("title"),
                            email=person.get("email"),
                            linkedin_url=person.get("linkedin_url"),
                            bio=person.get("bio"),
                            company_description=person.get("company_description")
                                or person_spec.get("company_description"),
                            discovery_source="apollo",
                            status=LeadStatus.DISCOVERED,
                        )
                        discovered_leads.append(new_lead)
                    logger.info(f"  Found {len(people)} people")
                else:
                    # Keep the company lead so it still flows through the pipeline
                    lead.status = LeadStatus.DISCOVERED
                    lead.errors.append("No people found at this company")
                    discovered_leads.append(lead)
                    logger.info(f"  No people found")

                # Rate limit between companies
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"  Discovery failed for {company}: {e}")
                lead.status = LeadStatus.DISCOVERED
                lead.errors.append(f"Discovery failed: {str(e)}")
                discovered_leads.append(lead)

        logger.info(f"Discovery complete: {len(discovered_leads)} leads from {len(leads)} companies")
        return discovered_leads

    # =========================================================================
    # Request-Based Discovery (Types 1, 2)
    # =========================================================================

    def _discover_from_request(
        self,
        solve_plan: Optional[Dict[str, Any]] = None,
        on_angle_progress=None,
    ) -> List[ScoutLead]:
        """
        Discover people based on a partnering request (no company list).
        When example patterns are available (Type 2), merges pattern-derived
        titles and expertise into the search criteria for better results.

        When a solve_plan is provided, Exa discovery uses the angle-based
        multi-track path (discover_from_solve_plan) instead of the one-shot
        query-generation path. `on_angle_progress` is forwarded to the
        angle-based path for per-angle checkpointing.
        """
        logger.info("Request-based discovery (generating search criteria)")

        # Generate broad search criteria from the request
        search_criteria = self._generate_search_criteria()
        if not search_criteria:
            logger.warning("Failed to generate search criteria — using minimal fallback")
            search_criteria = {
                "keywords": [s.strip() for s in (self.config.request_sois or "").split(",") if s.strip()],
                "titles": ["CTO", "VP Research", "Head of R&D"],
                "search_queries": [self.config.request_looking_for or self.config.request_title or ""],
            }

        # Merge example-derived signals if available (Type 2)
        if self.config.example_patterns:
            pattern_titles = self.config.example_patterns.get("common_titles", [])
            existing_titles = search_criteria.get("titles", [])
            # Deduplicate, prioritizing pattern-derived titles
            merged_titles = list(dict.fromkeys(pattern_titles + existing_titles))
            search_criteria["titles"] = merged_titles[:15]

            # Add expertise as additional keywords
            expertise = self.config.example_patterns.get("areas_of_expertise", [])
            existing_kw = search_criteria.get("keywords", [])
            if isinstance(existing_kw, str):
                import json as _json
                try:
                    existing_kw = _json.loads(existing_kw)
                except Exception:
                    existing_kw = [k.strip() for k in existing_kw.split(",") if k.strip()]
            if not isinstance(existing_kw, list):
                existing_kw = []
            if not isinstance(expertise, list):
                expertise = []
            search_criteria["keywords"] = list(dict.fromkeys(existing_kw + expertise))[:20]

            logger.info(
                f"Enhanced search with example patterns: "
                f"{len(search_criteria['titles'])} titles, "
                f"{len(search_criteria['keywords'])} keywords"
            )

        # --- n8n/Apollo search ---
        apollo_leads = []
        if self.discovery_webhook_url:
            people = self._search_broad(search_criteria)
            for person in people:
                lead = ScoutLead(
                    first_name=person.get("first_name"),
                    last_name=person.get("last_name"),
                    company=person.get("company"),
                    title=person.get("title"),
                    email=person.get("email"),
                    linkedin_url=person.get("linkedin_url"),
                    bio=person.get("bio"),
                    company_description=person.get("company_description"),
                    discovery_source="apollo",
                    status=LeadStatus.DISCOVERED,
                )
                apollo_leads.append(lead)
            logger.info(f"Apollo discovery found {len(apollo_leads)} leads")
        else:
            logger.info("No n8n webhook — skipping Apollo search")

        # --- Exa search ---
        exa_leads = []
        if self.exa_discovery:
            try:
                if solve_plan and solve_plan.get("angles"):
                    logger.info(
                        f"Using solve-plan-driven Exa discovery "
                        f"({len(solve_plan['angles'])} angles)"
                    )
                    exa_leads = self.exa_discovery.discover_from_solve_plan(
                        solve_plan, on_progress=on_angle_progress
                    )
                else:
                    exa_leads = self.exa_discovery.discover_from_request(search_criteria)
                logger.info(f"Exa discovery found {len(exa_leads)} leads")
            except Exception as e:
                logger.error(f"Exa discovery failed: {e}")

        # --- Merge and deduplicate ---
        all_leads = apollo_leads + exa_leads

        if not all_leads:
            logger.warning("No leads found from any discovery source")
            logger.info(f"Search criteria: {json.dumps(search_criteria, indent=2)}")
            return []

        # --- Early Halo domain check ---
        # When we've identified companies but may not have emails yet,
        # check if anyone from those company domains is already on Halo
        self._check_halo_domains(all_leads)

        # Separate company-only leads (Exa found company but no person)
        company_only = [l for l in all_leads if not l.first_name and l.company]
        people_leads = [l for l in all_leads if l.first_name]

        # Optionally route company-only leads through Apollo for people lookup
        if company_only and self.discovery_webhook_url:
            logger.info(f"Looking up people at {len(company_only)} company-only leads via Apollo")
            company_people = self._discover_at_companies(company_only)
            people_leads.extend(company_people)

        # Fallback: resolve remaining company-only leads via Exa + Claude
        still_company_only = [l for l in company_only if not l.first_name and l.company]
        if still_company_only:
            logger.info(f"Resolving contacts for {len(still_company_only)} remaining company-only leads")
            self.resolve_company_contacts(still_company_only)

        deduped = deduplicate_leads(people_leads + company_only)
        logger.info(f"Request discovery total: {len(deduped)} unique leads")
        return deduped

    # =========================================================================
    # Contact Resolution (for company-only leads)
    # =========================================================================

    def resolve_company_contacts(self, leads: List[ScoutLead]) -> List[ScoutLead]:
        """
        For company-only leads (no person name), use smart targeting rules
        + Exa + Claude to find the right contact person or flag as large org.

        Uses AccountTargeter for fast classification of known companies,
        then Exa + Claude for unknown companies.

        Modifies leads in place and returns them.
        """
        from .account_targeter import AccountTargeter

        targeter = AccountTargeter()

        if not self.exa_discovery:
            logger.warning("Exa not configured — cannot resolve company contacts")
            return leads

        company_leads = [l for l in leads if not l.first_name and l.company]
        if not company_leads:
            return leads

        logger.info(f"Resolving contacts for {len(company_leads)} company-only leads")
        resolved = 0

        for lead in company_leads:
            company = lead.company.strip()
            logger.info(f"  Resolving: {company}")

            # Fast path: check if targeter already knows this org type
            targeting = targeter.get_targeting(company)

            if targeting.flag:
                # Known large org — flag without API call
                lead.first_name = "[Large Org]"
                lead.last_name = ""
                lead.title = targeting.flag
                lead.bio = f"{targeting.org_type.value}. {targeting.reasoning}"
                lead.discovery_source = "targeter:known_large"
                logger.info(f"    Known large org — flagged ({targeting.org_type.value})")
                resolved += 1
                continue

            try:
                # Search for company team/about pages via Exa
                results = self.exa_discovery._exa_search(
                    f"{company} team leadership founders about",
                    num_results=3,
                    search_type="neural",
                    use_contents=True,
                )

                if not results:
                    logger.warning(f"    No Exa results for {company}")
                    lead.errors.append("No Exa results for contact resolution")
                    continue

                # Format results text for Claude
                results_text = ""
                for r in results[:3]:
                    title = r.get("title", "")
                    url = r.get("url", "")
                    text = r.get("text", "")[:1500]
                    results_text += f"--- {title} ({url}) ---\n{text}\n\n"

                # Include targeting context in prompt
                target_titles_hint = ", ".join(targeting.target_titles[:4])

                # Ask Claude to identify contacts and company size
                user_prompt = CONTACT_RESOLUTION_USER.format(
                    company=company,
                    results_text=results_text,
                    request_title=self.config.request_title or "Not specified",
                    request_looking_for=self.config.request_looking_for or "Not specified",
                )

                data = self.claude.call_with_tools(
                    system=CONTACT_RESOLUTION_SYSTEM,
                    user=user_prompt,
                    tools=[RESOLVE_CONTACTS_TOOL],
                    max_tokens=600,
                    tool_choice={"type": "tool", "name": "resolve_contacts"},
                )

                if not data:
                    logger.warning(f"    Claude call failed for {company}")
                    lead.errors.append("Claude contact resolution failed")
                    continue

                contacts = data.get("contacts", [])
                estimated = data.get("estimated_employees", 0)
                reasoning = data.get("reasoning", "")

                # Re-classify with size info from Claude
                if isinstance(estimated, int) and estimated > 0:
                    targeting = targeter.get_targeting(company, estimated)

                if targeting.flag or (isinstance(estimated, int) and estimated > 1000):
                    # Flag as large org
                    lead.first_name = "[Large Org]"
                    lead.last_name = ""
                    lead.title = "Large org, need to determine contact"
                    lead.bio = f"~{estimated} employees. {reasoning}"
                    lead.discovery_source = "exa:contact_resolution"
                    logger.info(f"    Flagged as large org (~{estimated} employees)")
                    resolved += 1

                elif contacts:
                    # Use best contact
                    best = contacts[0]
                    lead.first_name = best.get("first_name", "")
                    lead.last_name = best.get("last_name", "")
                    lead.title = best.get("title", "")
                    lead.linkedin_url = best.get("linkedin_url") or lead.linkedin_url
                    lead.bio = f"Contact at {company}. {reasoning}"
                    lead.discovery_source = "exa:contact_resolution"
                    logger.info(f"    Found: {lead.first_name} {lead.last_name}, {lead.title}")
                    resolved += 1

                else:
                    logger.warning(f"    No contacts found for {company}")
                    lead.errors.append("Contact resolution returned no contacts")

            except Exception as e:
                logger.error(f"    Contact resolution failed for {company}: {e}")
                lead.errors.append(f"Contact resolution error: {e}")

            # Rate limit
            time.sleep(1.5)

        logger.info(f"Contact resolution complete: {resolved}/{len(company_leads)} resolved")
        return leads

    # =========================================================================
    # Claude: Generate Person Spec
    # =========================================================================

    def _generate_person_spec(self, company: str) -> Dict[str, Any]:
        """
        Use Claude to determine what titles/roles to search for at a company,
        given the partnering request context.

        Returns:
            {
                "company_type": "startup",
                "titles": ["CTO", "Co-Founder", "VP Research"],
                "seniority": ["director", "vp", "c_suite"],
                "departments": ["engineering", "research"],
                "company_description": "Brief description if Claude infers it",
                "reasoning": "Why these titles were chosen"
            }
        """
        user_prompt = PERSON_SPEC_USER.format(
            company=company,
            request_title=self.config.request_title or "Not specified",
            request_looking_for=self.config.request_looking_for or "Not specified",
            request_use_case=self.config.request_use_case or "Not specified",
            request_sois=self.config.request_sois or "Not specified",
            request_partner_types=self.config.request_partner_types or "Not specified",
            request_requirements=self.config.request_requirements or "Not specified",
            request_out_of_scope=self.config.request_out_of_scope or "Not specified",
            max_titles=self.config.max_leads_per_company,
        )

        spec = self.claude.call_with_tools(
            system=PERSON_SPEC_SYSTEM,
            user=user_prompt,
            tools=[GENERATE_PERSON_SPEC_TOOL],
            max_tokens=500,
            tool_choice={"type": "tool", "name": "generate_person_spec"},
        )

        if not spec or "titles" not in spec or not spec["titles"]:
            return self._default_person_spec(company)

        return spec

    def _default_person_spec(self, company: str = "") -> Dict[str, Any]:
        """Fallback person spec using smart targeting rules."""
        from .account_targeter import AccountTargeter
        targeter = AccountTargeter()
        targeting = targeter.get_targeting(company)

        return {
            "company_type": targeting.org_type.value,
            "titles": targeting.target_titles,
            "seniority": ["director", "vp", "c_suite", "founder"],
            "departments": ["research", "engineering", "executive"],
            "reasoning": f"Smart targeting: {targeting.reasoning}",
        }

    def _generate_search_criteria(self) -> Dict[str, Any]:
        """Generate broad search criteria from a partnering request, enhanced by example patterns."""

        # Build the patterns context section if we have example patterns
        patterns_context = ""
        if self.config.example_patterns:
            p = self.config.example_patterns
            patterns_context = EXAMPLE_PATTERNS_CONTEXT.format(
                example_count=p.get("example_count", 0),
                common_titles=", ".join(p.get("common_titles", [])[:10]),
                common_user_types=", ".join(p.get("common_user_types", [])),
                areas_of_expertise=", ".join(p.get("areas_of_expertise", [])[:10]),
                disciplines=", ".join(p.get("disciplines", [])[:10]),
                common_countries=", ".join(p.get("common_countries", [])[:5]),
            )

        prompt = SEARCH_CRITERIA_FROM_REQUEST.format(
            request_title=self.config.request_title or "Not specified",
            request_looking_for=self.config.request_looking_for or "Not specified",
            request_use_case=self.config.request_use_case or "Not specified",
            request_sois=self.config.request_sois or "Not specified",
            request_partner_types=self.config.request_partner_types or "Not specified",
            request_requirements=self.config.request_requirements or "Not specified",
            request_out_of_scope=self.config.request_out_of_scope or "Not specified",
            patterns_context=patterns_context,
        )

        result = self.claude.call_with_tools(
            system=PERSON_SPEC_SYSTEM,
            user=prompt,
            tools=[GENERATE_SEARCH_CRITERIA_TOOL],
            max_tokens=800,
            tool_choice={"type": "tool", "name": "generate_search_criteria"},
        )

        if result and result.get("keywords"):
            # Normalize any fields Claude returned as strings instead of lists
            import json as _json
            for field in ("keywords", "titles", "search_queries", "company_types"):
                val = result.get(field)
                if isinstance(val, str):
                    try:
                        val = _json.loads(val)
                    except Exception:
                        val = [v.strip() for v in val.split(",") if v.strip()]
                    result[field] = val if isinstance(val, list) else []
            return result

        # Fallback: extract keywords from request text
        keywords = []
        for text in [self.config.request_looking_for, self.config.request_sois]:
            if text:
                keywords.extend(text.split()[:10])

        return {
            "keywords": keywords,
            "titles": self._default_person_spec()["titles"],
            "search_queries": [self.config.request_looking_for or ""],
        }

    # =========================================================================
    # Halo Domain Check (early-stage, before enrichment)
    # =========================================================================

    def _check_halo_domains(self, leads: List[ScoutLead]):
        """
        Early-stage domain lookup: for each discovered company, check if
        anyone from that company's email domain is already on Halo.

        This runs before enrichment (when we may not have emails yet).
        Uses email domains when available, otherwise guesses from company name.
        Tags leads with halo_domain_count and can pre-set already_on_halo.
        """
        try:
            from shared.snowflake_client import find_halo_users_by_domain
        except ImportError:
            logger.warning("snowflake_client not available — skipping domain check")
            return

        # Collect unique domains to check
        domain_map = {}  # domain → list of leads at that domain
        for lead in leads:
            domain = self._extract_domain(lead)
            if domain:
                domain_map.setdefault(domain, []).append(lead)

        if not domain_map:
            logger.info("No company domains to check against Halo")
            return

        logger.info(f"Checking {len(domain_map)} company domains against Halo USERS table")

        for domain, domain_leads in domain_map.items():
            try:
                users = find_halo_users_by_domain(domain)
                count = len(users)

                # Tag all leads at this domain
                for lead in domain_leads:
                    lead.halo_domain_count = count

                    # If lead has an email, check for exact match
                    if lead.email:
                        lead_email = lead.email.strip().lower()
                        if any(u.get("EMAIL", "").lower() == lead_email for u in users):
                            lead.already_on_halo = True

                if count > 0:
                    logger.info(f"  {domain}: {count} Halo users found")

            except Exception as e:
                logger.warning(f"  Domain check failed for {domain}: {e}")

    @staticmethod
    def _extract_domain(lead: ScoutLead) -> Optional[str]:
        """
        Extract email domain from a lead. Uses email if available,
        otherwise guesses from company name + linkedin URL.
        """
        # Best: use existing email domain
        if lead.email and "@" in lead.email:
            return lead.email.split("@")[1].lower().strip()

        # Try to extract from LinkedIn URL (company pages sometimes reveal domain)
        if lead.linkedin_url and "linkedin.com/company/" in (lead.linkedin_url or ""):
            # Can't reliably get domain from LinkedIn URL, skip
            pass

        # Guess from company name — common patterns
        if lead.company:
            company = lead.company.strip().lower()
            # Remove common suffixes
            for suffix in [" inc", " inc.", " llc", " ltd", " ltd.",
                          " corp", " corp.", " gmbh", " ag", " sa",
                          " co.", " company", " technologies", " technology"]:
                if company.endswith(suffix):
                    company = company[:-len(suffix)].strip()

            # Simple heuristic: company name → domain
            # e.g. "Wageningen University" → "wageningen.nl" won't work
            # but "Bühler" → "buhlergroup.com" also won't work
            # So we only use this for obvious cases
            # Better to skip and let enrichment fill in the email
            pass

        return None

    # =========================================================================
    # n8n People Search
    # =========================================================================

    def _search_people(
        self, company: str, person_spec: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Search for people at a specific company via n8n webhook."""
        if not self.discovery_webhook_url:
            logger.info(f"  No webhook — would search: {company} + {person_spec['titles']}")
            return []

        payload = {
            "action": "people_search",
            "company": company,
            "titles": person_spec.get("titles", []),
            "seniority": person_spec.get("seniority", []),
            "departments": person_spec.get("departments", []),
            "max_results": self.config.max_leads_per_company,
        }

        try:
            response = requests.post(
                self.discovery_webhook_url,
                json=payload,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            result = response.json()

            # Expect either {"people": [...]} or a direct array
            if isinstance(result, list):
                return result[:self.config.max_leads_per_company]
            return result.get("people", result.get("leads", []))[:self.config.max_leads_per_company]

        except requests.Timeout:
            logger.error(f"  People search timed out for {company}")
            return []
        except Exception as e:
            logger.error(f"  People search failed for {company}: {e}")
            return []

    def _search_broad(self, criteria: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Broad people search (not scoped to a specific company)."""
        if not self.discovery_webhook_url:
            logger.info(f"  No webhook — would search with criteria: {criteria}")
            return []

        payload = {
            "action": "broad_search",
            **criteria,
            "max_results": 50,
        }

        try:
            response = requests.post(
                self.discovery_webhook_url,
                json=payload,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            result = response.json()

            if isinstance(result, list):
                return result
            return result.get("people", result.get("leads", []))

        except Exception as e:
            logger.error(f"  Broad search failed: {e}")
            return []

