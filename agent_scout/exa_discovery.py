#!/usr/bin/env python3
"""
Agent Scout — Exa Discovery
==============================
Uses Exa.ai semantic search to find relevant companies, researchers, and
startups for a partnering request. Results are processed through Claude
to extract structured lead data.

Search strategies:
1. Company search — find relevant startups/companies
2. LinkedIn profiles — find relevant people
3. Research papers — find relevant researchers via publications
4. University/academic — find researchers at institutions
5. Similar companies — find companies similar to known good fits
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import List, Dict, Any, Optional

from exa_py import Exa

from .claude_client import ClaudeClient
from .models import ScoutLead, ScoutConfig, LeadStatus, deduplicate_leads
from .prompts import (
    EXA_QUERY_GENERATION, EXA_QUERY_GENERATION_INDUSTRY, EXA_RESULT_EXTRACTION,
    PERSON_SPEC_SYSTEM, BLURB_SYNTHESIS_SYSTEM, BLURB_SYNTHESIS_USER,
)

logger = logging.getLogger(__name__)

# Rate limit: 1 second between Exa API calls
EXA_RATE_LIMIT_SECONDS = 1.0

# Domain-include lists used by the solve-plan-driven angle tracks.
# Patents are a strong commercial-intent signal for industrial / chemical
# requests — inventors and assignees are often the best partners.
PATENT_DOMAINS = [
    "patents.google.com",
    "lens.org",
    "freepatentsonline.com",
    "espacenet.com",
    "patentscope.wipo.int",
]
# Universal low-signal domains we always want to exclude from discovery.
UNIVERSAL_EXCLUDE_DOMAINS = [
    "pinterest.com",
    "reddit.com",
    "quora.com",
]

# -- Tool schemas for structured output --

GENERATE_QUERIES_TOOL = {
    "name": "generate_queries",
    "description": "Generate Exa-optimized search queries for each discovery strategy.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-3 sentence semantic queries to find relevant companies/startups",
            },
            "linkedin_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-3 sentence queries to find relevant people on LinkedIn",
            },
            "paper_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Queries to find relevant research papers",
            },
            "university_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Queries to find academic researchers",
            },
        },
        "required": ["company_queries", "linkedin_queries"],
    },
}

GENERATE_QUERIES_TOOL_INDUSTRY = {
    "name": "generate_queries",
    "description": "Generate Exa-optimized search queries for industry-only discovery (no academic searches).",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "4-5 sentence semantic queries to find relevant companies, startups, and scaleups",
            },
            "linkedin_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-4 sentence queries to find relevant industry professionals on LinkedIn",
            },
            "supplier_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-3 queries to find material/chemical suppliers and distributors",
            },
            "service_provider_queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-3 queries to find CROs, consultancies, and technology service providers",
            },
        },
        "required": ["company_queries", "linkedin_queries"],
    },
}

EXTRACT_LEADS_TOOL = {
    "name": "extract_leads",
    "description": "Extract structured lead data from search results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "leads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "company": {"type": "string"},
                        "title": {"type": "string"},
                        "bio": {"type": "string"},
                        "company_description": {"type": "string"},
                        "linkedin_url": {"type": "string"},
                        "specific_expertise": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "evidence_snippets": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "org_type": {"type": "string"},
                        "source_url": {
                            "type": "string",
                            "description": "The URL from the search result this lead was extracted from",
                        },
                    },
                },
            },
        },
        "required": ["leads"],
    },
}

SYNTHESIZE_AND_QUERY_TOOL = {
    "name": "synthesize_and_refine",
    "description": "Synthesize patterns from scorer blurbs and generate refined search queries.",
    "input_schema": {
        "type": "object",
        "properties": {
            "good_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-5 patterns that make leads good fits (specific expertise, org types, roles)",
            },
            "bad_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-5 patterns that make leads bad fits (to avoid in new searches)",
            },
            "refined_queries": {
                "type": "object",
                "properties": {
                    "company_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-3 sentence semantic queries to find relevant companies",
                    },
                    "linkedin_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-3 sentence queries to find relevant people on LinkedIn",
                    },
                    "paper_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Queries to find relevant research papers",
                    },
                    "university_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Queries to find academic researchers",
                    },
                },
            },
            "exclude_terms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Terms or domains to exclude from new searches",
            },
        },
        "required": ["good_patterns", "bad_patterns", "refined_queries"],
    },
}

EVALUATE_RESULTS_TOOL = {
    "name": "evaluate_results",
    "description": "Evaluate whether current discovery results sufficiently cover the request.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sufficient": {
                "type": "boolean",
                "description": "True if the leads adequately cover the request's needs",
            },
            "coverage_score": {
                "type": "number",
                "description": "0.0-1.0 estimate of how well results cover the request",
            },
            "gaps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific areas, partner types, or expertise missing from results",
            },
            "refinement_suggestions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Suggested search query adjustments to fill gaps",
            },
        },
        "required": ["sufficient", "gaps"],
    },
}


class ExaDiscovery:
    """Find relevant companies and people using Exa semantic web search."""

    def __init__(self, config: ScoutConfig):
        self.config = config
        api_key = os.getenv("EXA_API_KEY")
        if not api_key:
            raise ValueError("EXA_API_KEY environment variable not set")

        self.exa = Exa(api_key=api_key)
        self.claude = ClaudeClient(model=config.score_model, temperature=0.3)
        self.exa_call_count = 0

    # =========================================================================
    # Public API
    # =========================================================================

    def discover_from_request(self, search_criteria: Dict[str, Any]) -> List[ScoutLead]:
        """
        Run multi-strategy Exa discovery based on search criteria.
        Includes adaptive refinement: if initial results are sparse or have gaps,
        generates refined queries and searches again (max 2 rounds).

        Returns a deduplicated list of ScoutLeads from all search strategies.
        """
        # Step 1: Generate Exa-optimized queries via Claude
        queries = self._generate_exa_queries()

        if not queries:
            logger.warning("Failed to generate Exa queries — using search_criteria fallback")
            queries = self._fallback_queries(search_criteria)

        # Step 2: Run initial search + extraction
        all_leads = self._run_search_and_extract(queries)
        deduped = deduplicate_leads(all_leads)
        logger.info(f"Initial discovery: {len(deduped)} unique leads")

        # Step 3: Adaptive refinement (max 2 rounds)
        min_leads_threshold = self.config.exa_num_results_per_query * 3  # e.g. 30
        for round_num in range(2):
            if len(deduped) >= min_leads_threshold:
                logger.info(f"Sufficient leads ({len(deduped)} >= {min_leads_threshold}), skipping refinement")
                break

            evaluation = self._evaluate_result_quality(deduped)
            if not evaluation or evaluation.get("sufficient", True):
                logger.info("Results evaluated as sufficient, skipping refinement")
                break

            gaps = evaluation.get("gaps", [])
            logger.info(f"Refinement round {round_num + 1}: gaps identified: {gaps}")

            refined_queries = self._refine_queries(queries, deduped, gaps)
            if not refined_queries:
                break

            new_leads = self._run_search_and_extract(refined_queries)
            deduped = deduplicate_leads(deduped + new_leads)
            logger.info(
                f"Refinement round {round_num + 1}: {len(new_leads)} new → {len(deduped)} total"
            )

        logger.info(
            f"Exa discovery complete: {len(deduped)} unique leads "
            f"({self.exa_call_count} Exa calls, {self.claude.call_count} Claude calls)"
        )
        return deduped

    # =========================================================================
    # Solve-plan-driven discovery (Phase 1 comprehensive upgrade)
    # =========================================================================

    def discover_from_solve_plan(
        self,
        solve_plan: Dict[str, Any],
        include_academic_tracks: Optional[bool] = None,
        max_parallel_angles: int = 4,
        on_progress=None,
    ) -> List[ScoutLead]:
        """Drive discovery from a pre-computed solve plan (25+ angles).

        For each angle, runs up to 7 Exa tracks in parallel. Angles themselves
        are also processed in parallel (max_parallel_angles at once), which
        cuts wall time roughly linearly vs sequential angle execution.

        `on_progress(angle_idx, angle_name, angle_leads)` is called after each
        angle completes — allows the caller to persist per-angle checkpoints.
        """
        if not solve_plan or "angles" not in solve_plan:
            logger.warning("discover_from_solve_plan: no plan — falling back to discover_from_request")
            return self.discover_from_request({})

        if include_academic_tracks is None:
            include_academic_tracks = not self._is_industry_only()

        angles = solve_plan.get("angles", [])
        logger.info(
            f"Solve-plan discovery: {len(angles)} angles, "
            f"max_parallel_angles={max_parallel_angles}, "
            f"academic_tracks={'on' if include_academic_tracks else 'off'}"
        )

        all_leads: List[ScoutLead] = []

        def _run_one_angle(i: int, angle: Dict[str, Any]) -> List[ScoutLead]:
            angle_id = angle.get("angle_id") or f"angle_{i}"
            angle_name = angle.get("name") or angle_id
            logger.info(f"[{i}/{len(angles)}] Angle '{angle_name}' — running tracks...")
            try:
                leads = self._run_angle_pack(angle, include_academic_tracks)
            except Exception as e:
                logger.error(f"Angle '{angle_id}' failed: {e}")
                return []
            for ld in leads:
                ld.seed_query = f"angle:{angle_id}"
            logger.info(f"[{i}/{len(angles)}] Angle '{angle_name}': {len(leads)} leads")
            return leads

        with ThreadPoolExecutor(max_workers=max_parallel_angles) as exe:
            futures = {
                exe.submit(_run_one_angle, i, angle): (i, angle)
                for i, angle in enumerate(angles, start=1)
            }
            for fut in futures:
                i, angle = futures[fut]
                try:
                    angle_leads = fut.result(timeout=1200)  # 20 min per angle ceiling
                except Exception as e:
                    logger.error(f"Angle future {i} crashed: {e}")
                    angle_leads = []
                all_leads.extend(angle_leads)
                if on_progress:
                    try:
                        on_progress(i, angle.get("name") or angle.get("angle_id"), angle_leads)
                    except Exception as e:
                        logger.warning(f"on_progress callback failed: {e}")

        deduped = deduplicate_leads(all_leads)
        logger.info(
            f"Solve-plan discovery complete: {len(deduped)} unique leads from {len(all_leads)} raw "
            f"({self.exa_call_count} Exa calls, {self.claude.call_count} Claude calls)"
        )
        return deduped

    def _run_angle_pack(
        self, angle: Dict[str, Any], include_academic_tracks: bool
    ) -> List[ScoutLead]:
        """Run all tracks for a single angle in parallel. Returns extracted leads."""
        include = list(angle.get("include_domains") or [])
        exclude = list(dict.fromkeys(
            (angle.get("exclude_domains") or []) + UNIVERSAL_EXCLUDE_DOMAINS
        ))
        search_terms: List[str] = list(angle.get("search_terms") or [])
        branded: List[str] = list(angle.get("branded_strings") or [])

        # Each track is a callable returning a list of raw Exa result dicts.
        tracks: List[Any] = []

        # 1. Neural company (angle-domain-filtered)
        if search_terms:
            tracks.append(("neural_company", lambda: self._multi_query_search(
                queries=search_terms[:3],
                search_type="neural",
                category="company",
                include_domains=include or None,
                exclude_domains=exclude or None,
                use_contents=True,
            )))
            # 2. Neural people (no domain filter — profiles span hosts)
            tracks.append(("neural_people", lambda: self._multi_query_search(
                queries=search_terms[:3],
                search_type="neural",
                category="people",
                exclude_domains=exclude or None,
                use_contents=True,
            )))
            # 3. Neural trade — angle domains, no category (catches articles/profiles)
            if include:
                tracks.append(("neural_trade", lambda: self._multi_query_search(
                    queries=search_terms[:3],
                    search_type="neural",
                    category=None,
                    include_domains=include,
                    exclude_domains=exclude or None,
                    use_contents=True,
                )))

        # 4. Keyword branded — exact-string pass for product/INCI/CAS
        if branded:
            tracks.append(("keyword_branded", lambda: self._multi_query_search(
                queries=branded[:6],
                search_type="keyword",
                category=None,
                num_results_per=5,
                exclude_domains=exclude or None,
                use_contents=True,
            )))

        # 5. Patent — PATENT_DOMAINS, neural/auto
        if search_terms:
            tracks.append(("patent", lambda: self._multi_query_search(
                queries=search_terms[:2],
                search_type="neural",
                category=None,
                include_domains=PATENT_DOMAINS,
                use_contents=True,
            )))

        # 6. News / funding — last year
        if search_terms:
            from datetime import datetime, timedelta
            one_year_ago = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
            tracks.append(("news", lambda: self._multi_query_search(
                queries=search_terms[:2],
                search_type="neural",
                category="news",
                start_published_date=one_year_ago,
                exclude_domains=exclude or None,
                use_contents=True,
            )))

        # 7. Research paper — only when academic tracks are active
        if include_academic_tracks and search_terms:
            tracks.append(("research_paper", lambda: self._multi_query_search(
                queries=search_terms[:2],
                search_type="neural",
                category="research paper",
                use_contents=True,
            )))

        # Execute tracks in parallel.
        raw_by_track: Dict[str, List[Dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=min(len(tracks), 7)) as executor:
            futures = {executor.submit(fn): name for name, fn in tracks}
            for fut in futures:
                name = futures[fut]
                try:
                    raw_by_track[name] = fut.result(timeout=180)
                except Exception as e:
                    logger.warning(f"  track {name} failed: {e}")
                    raw_by_track[name] = []

        # Extract leads from each track's results, tagging the discovery source.
        angle_leads: List[ScoutLead] = []
        for track_name, results in raw_by_track.items():
            if not results:
                continue
            leads = self._extract_leads_from_results(results, f"angle_{track_name}")
            angle_leads.extend(leads)

        return angle_leads

    def _multi_query_search(
        self,
        queries: List[str],
        search_type: str,
        category: Optional[str],
        num_results_per: Optional[int] = None,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        start_published_date: Optional[str] = None,
        use_contents: bool = True,
    ) -> List[Dict[str, Any]]:
        """Run a list of queries with shared params; concatenate raw results."""
        if not queries:
            return []
        num = num_results_per if num_results_per is not None else self.config.exa_num_results_per_query
        results: List[Dict[str, Any]] = []
        for q in queries:
            r = self._exa_search(
                q,
                num_results=num,
                search_type=search_type,
                category=category,
                use_contents=use_contents,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                start_published_date=start_published_date,
            )
            results.extend(r)
        return results

    # =========================================================================
    # Search Strategies (data-driven dispatch)
    # =========================================================================
    # Each strategy is a tuple of:
    #   (query_bucket_key, exa_category_or_None, contents_spec)
    # where contents_spec is True/False, or "config" to defer to
    # self.config.exa_use_contents at call time.
    STRATEGIES: Dict[str, Dict[str, Any]] = {
        "company":           {"bucket": "company_queries",          "category": "company",        "contents": "config"},
        "linkedin":          {"bucket": "linkedin_queries",         "category": "people",         "contents": True},
        "paper":             {"bucket": "paper_queries",            "category": "research paper", "contents": True},
        "university":        {"bucket": "university_queries",       "category": None,             "contents": True},
        "supplier":          {"bucket": "supplier_queries",         "category": "company",        "contents": "config"},
        "service_provider":  {"bucket": "service_provider_queries", "category": "company",        "contents": "config"},
    }
    INDUSTRY_STRATEGIES = ["company", "linkedin", "supplier", "service_provider"]
    ACADEMIC_STRATEGIES = ["company", "linkedin", "paper", "university"]

    def _run_strategy(self, name: str, queries: List[str]) -> List[Dict[str, Any]]:
        """Run a named search strategy across a list of queries."""
        spec = self.STRATEGIES[name]
        contents_spec = spec["contents"]
        use_contents = self.config.exa_use_contents if contents_spec == "config" else bool(contents_spec)
        all_results: List[Dict[str, Any]] = []
        for query in queries:
            results = self._exa_search(
                query,
                num_results=self.config.exa_num_results_per_query,
                search_type=self.config.exa_search_type,
                category=spec["category"],
                use_contents=use_contents,
            )
            all_results.extend(results)
        return all_results

    def _run_search_and_extract(self, queries: Dict[str, List[str]]) -> List[ScoutLead]:
        """Run all active search strategies for a set of queries and extract leads."""
        if self._is_industry_only():
            logger.info("Industry-only mode: skipping paper and university searches")
            active = self.INDUSTRY_STRATEGIES
        else:
            active = self.ACADEMIC_STRATEGIES

        all_leads: List[ScoutLead] = []
        for name in active:
            spec = self.STRATEGIES[name]
            bucket_queries = queries.get(spec["bucket"], [])
            if not bucket_queries:
                continue

            logger.info(f"Exa {name} search: {len(bucket_queries)} queries")
            try:
                raw = self._run_strategy(name, bucket_queries)
            except Exception as e:
                logger.error(f"  Exa {name} search failed: {e}")
                continue
            if not raw:
                continue
            logger.info(f"  → {len(raw)} raw results")
            leads = self._extract_leads_from_results(raw, name)
            all_leads.extend(leads)
            logger.info(f"  Extracted {len(leads)} leads from {name} results")

        return all_leads

    def discover_at_company(
        self, company: str, person_spec: Dict[str, Any]
    ) -> List[ScoutLead]:
        """Search Exa for people at a specific company."""
        titles = person_spec.get("titles", [])[:3]
        title_str = " OR ".join(titles) if titles else "leadership team"

        query = f"{company} {title_str}"
        results = self._exa_search(
            query,
            num_results=self.config.max_leads_per_company,
            search_type=self.config.exa_search_type,
            use_contents=False,  # Just need URLs/titles for company-specific
        )

        if not results:
            return []

        return self._extract_leads_from_results(results, f"company:{company}")

    def find_similar_companies(self, seed_urls: List[str]) -> List[ScoutLead]:
        """Find companies similar to known good-fit companies."""
        all_results = []

        for url in seed_urls[:5]:  # Cap at 5 seed URLs
            try:
                response = self.exa.find_similar(
                    url,
                    num_results=self.config.exa_num_results_per_query,
                    category="company",
                )
                self.exa_call_count += 1
                results = self._parse_exa_response(response)
                all_results.extend(results)
                time.sleep(EXA_RATE_LIMIT_SECONDS)
            except Exception as e:
                logger.error(f"find_similar failed for {url}: {e}")

        if not all_results:
            return []

        return self._extract_leads_from_results(all_results, "similar_company")

    def _is_industry_only(self) -> bool:
        """Check if request partner types exclude academics/researchers."""
        pt = (self.config.request_partner_types or "").lower()
        if not pt:
            return False  # Unknown — use all strategies
        academic_terms = ["academic", "researcher", "university", "professor", "research institute"]
        has_academic = any(term in pt for term in academic_terms)
        return not has_academic

    # =========================================================================
    # Exa API Wrapper
    # =========================================================================

    def _exa_search(
        self,
        query: str,
        num_results: int = 10,
        search_type: str = "neural",
        category: Optional[str] = None,
        use_contents: bool = False,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        start_published_date: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> List[Dict[str, Any]]:
        """Wrapper around Exa search with rate limiting, timeout, and error handling.

        Uses ThreadPoolExecutor for portable timeout (works on all platforms,
        unlike the previous signal.SIGALRM approach).

        start_published_date: ISO date (YYYY-MM-DD) — only return results
          published on/after this date. Used by the news track to filter to
          recent funding/product announcements.
        """
        # Exa's dedicated indexes (`people`, `research paper`) do not support
        # include_domains / exclude_domains filters. Silently drop them for
        # those categories instead of erroring.
        if category in ("people", "research paper"):
            include_domains = None
            exclude_domains = None

        try:
            kwargs = {
                "query": query,
                "num_results": num_results,
                "type": search_type,
            }
            if category:
                kwargs["category"] = category
            if include_domains:
                kwargs["include_domains"] = include_domains
            if exclude_domains:
                kwargs["exclude_domains"] = exclude_domains
            if start_published_date:
                kwargs["start_published_date"] = start_published_date

            def _do_search():
                if use_contents:
                    kwargs["highlights"] = {"num_sentences": 5}
                    return self.exa.search_and_contents(**kwargs)
                else:
                    return self.exa.search(**kwargs)

            # Run search in a thread with timeout
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_search)
                response = future.result(timeout=timeout_seconds)

            self.exa_call_count += 1
            time.sleep(EXA_RATE_LIMIT_SECONDS)

            return self._parse_exa_response(response)

        except (FuturesTimeoutError, TimeoutError):
            logger.error(f"Exa search timed out for '{query[:60]}...' ({timeout_seconds}s)")
            return []
        except Exception as e:
            logger.error(f"Exa search failed for '{query[:60]}...': {e}")
            return []

    def _parse_exa_response(self, response) -> List[Dict[str, Any]]:
        """Convert Exa response to a list of result dicts."""
        results = []
        for result in response.results:
            item = {
                "title": getattr(result, "title", "") or "",
                "url": getattr(result, "url", "") or "",
                "score": getattr(result, "score", 0.0),
            }
            # Prefer highlights (targeted excerpts) over raw text
            highlights = getattr(result, "highlights", None)
            if highlights:
                item["text"] = " ... ".join(highlights)[:2000]
            else:
                text = getattr(result, "text", None)
                if text:
                    item["text"] = text[:2000]

            # Add published date if available
            published_date = getattr(result, "published_date", None)
            if published_date:
                item["published_date"] = str(published_date)

            results.append(item)

        return results

    # =========================================================================
    # Claude: Generate Exa Queries
    # =========================================================================

    def _generate_exa_queries(self) -> Optional[Dict[str, List[str]]]:
        """Use Claude to generate Exa-optimized queries from request context."""
        industry_only = self._is_industry_only()

        template = EXA_QUERY_GENERATION_INDUSTRY if industry_only else EXA_QUERY_GENERATION
        tool_schema = GENERATE_QUERIES_TOOL_INDUSTRY if industry_only else GENERATE_QUERIES_TOOL

        if industry_only:
            logger.info("Using industry-only query generation (no paper/university queries)")

        prompt = template.format(
            request_title=self.config.request_title or "Not specified",
            request_looking_for=self.config.request_looking_for or "Not specified",
            request_sois=self.config.request_sois or "Not specified",
            request_partner_types=self.config.request_partner_types or "Not specified",
            request_requirements=self.config.request_requirements or "Not specified",
            request_out_of_scope=self.config.request_out_of_scope or "Not specified",
        )

        return self.claude.call_with_tools(
            system=PERSON_SPEC_SYSTEM,
            user=prompt,
            tools=[tool_schema],
            max_tokens=800,
            tool_choice={"type": "tool", "name": "generate_queries"},
        )

    def _fallback_queries(self, search_criteria: Dict[str, Any]) -> Dict[str, List[str]]:
        """Build Exa queries from existing search criteria when Claude call fails."""
        search_queries = search_criteria.get("search_queries", [])
        keywords = search_criteria.get("keywords", [])

        # Build basic queries from available data
        base_query = " ".join(keywords[:5]) if keywords else (
            self.config.request_looking_for or self.config.request_title or ""
        )

        if self._is_industry_only():
            return {
                "company_queries": search_queries[:3] if search_queries else [
                    f"startup company {base_query}",
                    f"supplier manufacturer {base_query}",
                ],
                "linkedin_queries": [f"CTO director {base_query}"],
                "supplier_queries": [f"supplier distributor {base_query}"],
                "service_provider_queries": [f"contract research consulting {base_query}"],
            }

        return {
            "company_queries": search_queries[:2] if search_queries else [
                f"startup company {base_query}"
            ],
            "linkedin_queries": [f"researcher {base_query}"],
            "paper_queries": [base_query] if base_query else [],
            "university_queries": [f"professor {base_query}"] if base_query else [],
        }

    # =========================================================================
    # Claude: Extract Leads from Exa Results
    # =========================================================================

    def _extract_leads_from_results(
        self, results: List[Dict[str, Any]], search_type: str
    ) -> List[ScoutLead]:
        """Use Claude to extract structured lead data from Exa search results."""
        if not results:
            return []

        all_leads = []

        # Process in batches of 8
        batch_size = 8
        for i in range(0, len(results), batch_size):
            batch = results[i:i + batch_size]

            # Format results for the prompt
            results_text = self._format_results_for_extraction(batch)

            prompt = EXA_RESULT_EXTRACTION.format(
                request_title=self.config.request_title or "Not specified",
                request_looking_for=self.config.request_looking_for or "Not specified",
                request_sois=self.config.request_sois or "Not specified",
                request_partner_types=self.config.request_partner_types or "Not specified",
                request_out_of_scope=self.config.request_out_of_scope or "Not specified",
                search_type=search_type,
                results_text=results_text,
            )

            data = self.claude.call_with_tools(
                system=PERSON_SPEC_SYSTEM,
                user=prompt,
                tools=[EXTRACT_LEADS_TOOL],
                max_tokens=1500,
                tool_choice={"type": "tool", "name": "extract_leads"},
            )
            if not data:
                continue

            extracted = data.get("leads", [])
            for item in extracted:
                lead = ScoutLead(
                    first_name=item.get("first_name") or None,
                    last_name=item.get("last_name") or None,
                    company=item.get("company") or None,
                    title=item.get("title") or None,
                    bio=item.get("bio") or None,
                    company_description=item.get("company_description") or None,
                    linkedin_url=item.get("linkedin_url") or None,
                    specific_expertise=item.get("specific_expertise") or None,
                    evidence_snippets=item.get("evidence_snippets") or None,
                    org_type=item.get("org_type") or None,
                    source_url=item.get("source_url") or None,
                    discovery_source=f"exa:{search_type}",
                    status=LeadStatus.DISCOVERED,
                )
                # Only keep leads with at least a company or a name
                if lead.company or lead.first_name:
                    all_leads.append(lead)

        return all_leads

    def _format_results_for_extraction(self, results: List[Dict[str, Any]]) -> str:
        """Format Exa results into text for the Claude extraction prompt."""
        parts = []
        for i, result in enumerate(results, 1):
            entry = f"--- Result {i} ---\n"
            entry += f"Title: {result.get('title', 'N/A')}\n"
            entry += f"URL: {result.get('url', 'N/A')}\n"

            if result.get("published_date"):
                entry += f"Published: {result['published_date']}\n"

            text = result.get("text", "")
            if text:
                # Truncate long content
                entry += f"Content:\n{text[:1500]}\n"

            parts.append(entry)

        return "\n".join(parts)

    # =========================================================================
    # Adaptive Refinement
    # =========================================================================

    def _evaluate_result_quality(self, leads: List[ScoutLead]) -> Optional[Dict[str, Any]]:
        """Use Claude to evaluate whether current leads sufficiently cover the request."""
        if not leads:
            return {"sufficient": False, "gaps": ["No leads found"], "coverage_score": 0.0}

        lead_summary = "\n".join(
            f"- {l.full_name()} @ {l.company or 'Unknown'} ({l.title or 'Unknown title'})"
            for l in leads[:40]  # Cap summary to avoid token bloat
        )

        prompt = (
            f"Evaluate whether these {len(leads)} leads adequately cover this request.\n\n"
            f"REQUEST:\n"
            f"Looking for: {self.config.request_looking_for or 'Not specified'}\n"
            f"Partner types: {self.config.request_partner_types or 'Not specified'}\n"
            f"Requirements: {self.config.request_requirements or 'Not specified'}\n"
            f"SOIs: {self.config.request_sois or 'Not specified'}\n\n"
            f"CURRENT LEADS ({len(leads)} total):\n{lead_summary}\n\n"
            f"Are there major gaps in coverage? What partner types, expertise areas, "
            f"or geographies are missing?"
        )

        return self.claude.call_with_tools(
            system="You evaluate lead discovery coverage for partnering requests.",
            user=prompt,
            tools=[EVALUATE_RESULTS_TOOL],
            max_tokens=500,
            tool_choice={"type": "tool", "name": "evaluate_results"},
        )

    def _refine_queries(
        self,
        original_queries: Dict[str, List[str]],
        current_leads: List[ScoutLead],
        gaps: List[str],
    ) -> Optional[Dict[str, List[str]]]:
        """Generate refined queries targeting identified gaps."""
        if not gaps:
            return None

        existing_companies = {l.company for l in current_leads if l.company}
        gaps_text = "\n".join(f"- {g}" for g in gaps)
        existing_text = ", ".join(list(existing_companies)[:20])

        prompt = (
            f"Generate NEW search queries to fill these gaps in our discovery results.\n\n"
            f"REQUEST:\n"
            f"Looking for: {self.config.request_looking_for or 'Not specified'}\n"
            f"Partner types: {self.config.request_partner_types or 'Not specified'}\n"
            f"SOIs: {self.config.request_sois or 'Not specified'}\n\n"
            f"GAPS TO FILL:\n{gaps_text}\n\n"
            f"ALREADY FOUND (avoid duplicating): {existing_text}\n\n"
            f"Generate queries specifically targeting the gaps above. "
            f"Each query should be 2-3 sentences describing the ideal page to find."
        )

        return self.claude.call_with_tools(
            system=PERSON_SPEC_SYSTEM,
            user=prompt,
            tools=[GENERATE_QUERIES_TOOL],
            max_tokens=800,
            tool_choice={"type": "tool", "name": "generate_queries"},
        )

    # =========================================================================
    # Expansion Loop: Track A (find_similar) + Track B (blurb synthesis)
    # =========================================================================

    def expand_from_similar(self, leads: List[ScoutLead]) -> List[ScoutLead]:
        """
        Track A: Use find_similar on high-scoring leads to discover similar companies/researchers.
        Returns new leads (caller handles dedup and scoring).
        """
        threshold = self.config.expansion_similar_threshold
        max_seeds = self.config.expansion_max_similar_seeds

        # Get high-scoring leads with valid source URLs
        seeds = [
            l for l in leads
            if l.fit_score is not None
            and l.fit_score >= threshold
            and l.source_url
        ]
        # Sort by score descending, cap at max_seeds
        seeds.sort(key=lambda l: l.fit_score or 0, reverse=True)
        seeds = seeds[:max_seeds]

        if not seeds:
            logger.info(f"Track A: no leads with score >= {threshold} and source_url — skipping find_similar")
            return []

        logger.info(f"Track A: running find_similar on {len(seeds)} seed URLs")

        # Extract per-seed so we can tag downstream leads with which seed
        # produced them. Lumping all results together loses lineage.
        all_new_leads: List[ScoutLead] = []
        for seed in seeds:
            try:
                response = self.exa.find_similar(
                    seed.source_url,
                    num_results=5,
                    highlights={"num_sentences": 5},
                )
                self.exa_call_count += 1
                results = self._parse_exa_response(response)
                time.sleep(EXA_RATE_LIMIT_SECONDS)
            except Exception as e:
                logger.error(f"find_similar failed for {seed.source_url}: {e}")
                continue

            if not results:
                continue

            seed_leads = self._extract_leads_from_results(results, "find_similar")
            for nl in seed_leads:
                nl.seed_url = seed.source_url
            all_new_leads.extend(seed_leads)

        if not all_new_leads:
            logger.info("Track A: no results from find_similar")
            return []

        logger.info(f"Track A: extracted {len(all_new_leads)} leads from find_similar")
        return all_new_leads

    def expand_from_blurb_synthesis(self, leads: List[ScoutLead]) -> List[ScoutLead]:
        """
        Track B: Synthesize patterns from scorer blurbs, generate refined queries,
        run searches, and extract new leads. Returns new leads (caller handles dedup).
        """
        blurb_threshold = self.config.expansion_blurb_threshold

        # Bucket leads by score
        good_leads = [
            l for l in leads
            if l.fit_score is not None and l.fit_score >= blurb_threshold and l.fit_blurb
        ]
        bad_leads = [
            l for l in leads
            if l.fit_score is not None and l.fit_score < 0.35 and l.fit_blurb
        ]

        if not good_leads:
            logger.info("Track B: no good leads with blurbs — skipping blurb synthesis")
            return []

        # Format blurbs with scores
        good_blurbs = "\n".join(
            f"- [{l.fit_score:.2f}] {l.full_name()} @ {l.company or 'Unknown'}: {l.fit_blurb}"
            for l in sorted(good_leads, key=lambda x: x.fit_score or 0, reverse=True)[:20]
        )
        bad_blurbs = "\n".join(
            f"- [{l.fit_score:.2f}] {l.full_name()} @ {l.company or 'Unknown'}: {l.fit_blurb}"
            for l in sorted(bad_leads, key=lambda x: x.fit_score or 0)[:15]
        ) if bad_leads else "No bad fits to analyze."

        existing_companies = ", ".join(
            list({l.company for l in leads if l.company})[:30]
        )

        # Call Claude to synthesize blurbs and generate refined queries
        prompt = BLURB_SYNTHESIS_USER.format(
            request_looking_for=self.config.request_looking_for or "Not specified",
            request_sois=self.config.request_sois or "Not specified",
            request_partner_types=self.config.request_partner_types or "Not specified",
            request_out_of_scope=self.config.request_out_of_scope or "Not specified",
            good_blurbs=good_blurbs,
            bad_blurbs=bad_blurbs,
            existing_companies=existing_companies,
        )

        data = self.claude.call_with_tools(
            system=BLURB_SYNTHESIS_SYSTEM,
            user=prompt,
            tools=[SYNTHESIZE_AND_QUERY_TOOL],
            max_tokens=1200,
            tool_choice={"type": "tool", "name": "synthesize_and_refine"},
        )

        if not data:
            logger.info("Track B: blurb synthesis call failed")
            return []

        good_patterns = data.get("good_patterns", [])
        bad_patterns = data.get("bad_patterns", [])
        refined_queries = data.get("refined_queries", {})

        logger.info(
            f"Track B: synthesized {len(good_patterns)} good patterns, "
            f"{len(bad_patterns)} bad patterns, "
            f"{sum(len(v) for v in refined_queries.values() if isinstance(v, list))} refined queries"
        )

        if not refined_queries:
            logger.info("Track B: no refined queries generated")
            return []

        # Run each refined query individually so we can tag the leads with the
        # specific query that surfaced them (lineage for debugging high-score
        # leads back to the prompt that found them).
        # Look up the right strategy by query bucket using the central table.
        bucket_to_strategy = {spec["bucket"]: name for name, spec in self.STRATEGIES.items()}
        all_new_leads: List[ScoutLead] = []
        for bucket, queries in refined_queries.items():
            if not queries or not isinstance(queries, list):
                continue
            strategy_name = bucket_to_strategy.get(bucket)
            if not strategy_name:
                continue
            for query in queries:
                try:
                    raw = self._run_strategy(strategy_name, [query])
                except Exception as e:
                    logger.error(f"Track B: search for refined {strategy_name} query failed: {e}")
                    continue
                if not raw:
                    continue
                leads = self._extract_leads_from_results(raw, f"blurb_refined:{strategy_name}")
                for nl in leads:
                    nl.discovery_source = "exa:blurb_refined"
                    nl.seed_query = query
                all_new_leads.extend(leads)

        logger.info(f"Track B: extracted {len(all_new_leads)} leads from blurb-refined queries")
        return all_new_leads

