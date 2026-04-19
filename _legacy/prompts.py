#!/usr/bin/env python3
"""
RoboScout Query Generator — Prompt Templates
=============================================
All LLM prompt templates in one place for easy iteration.
"""

from pathlib import Path

# Load the scout query guide for injection into prompts
_GUIDE_PATH = Path(__file__).parent / "context" / "query_generation_guide.md"


def _load_guide() -> str:
    """Load the query generation guide, or return empty string if missing."""
    if _GUIDE_PATH.exists():
        return _GUIDE_PATH.read_text()
    return ""


# =============================================================================
# Step 1+2+3: Analyze request, expand vocabulary, generate queries
# =============================================================================

QUERY_GENERATION_SYSTEM = """You are an expert at crafting search queries for Semantic Scholar, a scientific publication database. Your queries are used by RoboScout, an AI system that finds relevant researchers based on their publications.

Your goal: generate high-quality search queries that will surface publications by researchers who could fulfill a corporate R&D partnering request.

CRITICAL RULES FOR SEMANTIC SCHOLAR QUERIES:
1. NO quotation marks — they cause poor results
2. NO boolean operators (AND, OR, NOT) — they don't work properly with RoboScout
3. NO negation prefixes (non-, un-) — they pull in the thing you're excluding
4. NO "alternatives to X" phrasing — this returns papers about X, not alternatives
5. AVOID generic words: "novel", "new", "method", "data", "significant", "treatment" — unless they narrow results
6. USE specific compounds, materials, technologies, organisms, techniques
7. USE conjunctions/prepositions strategically ("for", "in", "or") to narrow context
8. ADD field-specific context (e.g., "in food packaging") to avoid cross-domain noise
9. Focus on WHAT the solution IS, not what it replaces

QUERY SPECIFICITY TARGETS:
- General (1,001–3,000 results): Submit 1-2 queries max in this range
- Moderate (500–1,000 results): Submit 2-4 queries
- Specific (100–500 results): Submit as many as needed
- Highly Specific (<100 results): Submit as many as needed
- NEVER submit queries likely to return >3,000 results

SPECIFICITY BALANCE PER SOI:
Each SOI should have AT LEAST one Specific query (100-500 results) AND one Highly Specific query (<100 results). Together, the queries for a given SOI must contribute a minimum of 100 total results.
- The Highly Specific query finds the most targeted researchers (closest matches)
- The Specific query casts a wider net to catch relevant work outside the narrow framing
- If you can only generate one query per SOI, aim for the Specific range (100-500)
- For each SOI, think: "What's the precise technical term?" (→ highly specific) AND "What's the broader research area?" (→ specific)

{guide_section}

Use the provided tool to submit your response."""


QUERY_GENERATION_USER = """PARTNERING REQUEST:
Title: {title}
Looking For: {looking_for}
Use Case: {use_case}
Solutions of Interest: {solutions_of_interest}
Requirements: {requirements}
Out of Scope: {out_of_scope}

Analyze this request and generate Semantic Scholar search queries. Follow this process:

1. ANALYZE: Identify the core technical problem, key technologies, and domain
2. EXPAND: Beyond the listed SOIs, identify specific compounds, materials, methods, organisms, or techniques that could fulfill this request. Think about adjacent fields and cross-industry solutions.
3. GENERATE: Create 8-15 candidate queries following all the rules above

For each query, ensure:
- At least 2 queries per listed Solution of Interest: one aimed at Specific range (100-500 results) and one at Highly Specific range (<100 results)
- Additional queries for SOIs you identified in step 2 (same 2-query minimum)
- Each SOI's queries should contribute at least 100 total results combined
- Each query focuses on a specific technology/approach, not a vague topic
- Avoid semantic-equivalent queries (e.g., "botanical remedies X" and "herbal remedies X" retrieve the same papers). Instead vary by mechanism, material, or use context.

Use the submit_queries tool to provide your analysis, expanded SOIs, and candidate queries."""


# =============================================================================
# Refinement: Fix queries that are too broad or irrelevant
# =============================================================================

QUERY_REFINEMENT_SYSTEM = """You are refining a Semantic Scholar search query that didn't meet quality criteria.

RULES (same as before):
- NO quotation marks, boolean operators, or negation prefixes
- USE specific materials/compounds/technologies
- ADD field context to narrow results
- Use conjunctions/prepositions strategically

Use the provided tool to submit your response."""


QUERY_REFINEMENT_USER = """The following query needs refinement:

ORIGINAL QUERY: {query}
TARGET SOI: {target_soi}

PROBLEM: {problem}
- Result count: {result_count}
- Target range: {target_range}
{sample_titles_section}

PARTNERING REQUEST CONTEXT:
{request_context}

Refine this query to fix the problem. If it's too broad, add more specific terms or field context. If results are irrelevant, shift the terminology toward the actual target domain.

Use the submit_refined_query tool to provide the refined query."""


# =============================================================================
# Relevance spot-check: Are the top results actually relevant?
# =============================================================================

RELEVANCE_CHECK_SYSTEM = """You are evaluating whether Semantic Scholar search results are relevant to a corporate R&D partnering request. Be strict — a paper is only "relevant" if a researcher who published it could plausibly fulfill the partnering request.

Use the provided tool to submit your response."""


RELEVANCE_CHECK_USER = """PARTNERING REQUEST:
Title: {title}
Looking For: {looking_for}
Solutions of Interest: {solutions_of_interest}

QUERY USED: {query}

TOP RESULTS FROM SEMANTIC SCHOLAR:
{papers_section}

For each paper, determine if the researcher who wrote it would be relevant to this partnering request. A paper is relevant if:
- The research topic directly or closely relates to the request
- The methods/materials studied could fulfill the stated need
- A researcher in this area would have applicable expertise

Use the submit_relevance_check tool to provide your evaluation."""


# =============================================================================
# Coverage recovery: Generate queries for uncovered SOIs
# =============================================================================

COVERAGE_RECOVERY_SYSTEM = """You are an expert at crafting search queries for Semantic Scholar. You are generating RECOVERY queries for Solutions of Interest (SOIs) that had no surviving queries after the initial generation and validation pass.

You know what was tried before and why it failed. Your job is to take a fundamentally different approach — different terminology, different angles, adjacent fields — to find queries that will actually return relevant results.

CRITICAL RULES FOR SEMANTIC SCHOLAR QUERIES:
1. NO quotation marks — they cause poor results
2. NO boolean operators (AND, OR, NOT) — they don't work properly
3. NO negation prefixes (non-, un-) — they pull in the thing you're excluding
4. NO "alternatives to X" phrasing — this returns papers about X, not alternatives
5. AVOID generic words: "novel", "new", "method", "data", "significant", "treatment"
6. USE specific compounds, materials, technologies, organisms, techniques
7. USE conjunctions/prepositions strategically to narrow context
8. ADD field-specific context to avoid cross-domain noise

{guide_section}

Use the provided tool to submit your response."""


COVERAGE_RECOVERY_USER = """PARTNERING REQUEST:
Title: {title}
Looking For: {looking_for}
Solutions of Interest: {solutions_of_interest}

UNCOVERED SOIs (need recovery queries):
{uncovered_sois}

PREVIOUSLY FAILED QUERIES:
{failed_queries_section}

Generate 3-4 NEW candidate queries for EACH uncovered SOI listed above. Take a different approach than what was tried before:
- Use different terminology, synonyms, or adjacent-field vocabulary
- Try broader or narrower framing depending on what failed
- Consider cross-industry applications or alternative research communities
- If previous queries were too narrow (0 results), use more general domain terms
- If previous queries were irrelevant, shift the technical vocabulary closer to the target

For each query, ensure:
- It targets one of the uncovered SOIs specifically
- It takes a genuinely different approach from the failed queries
- It follows all the Semantic Scholar query rules above
- Aim for at least one query in the Specific range (100-500 results) and one in the Highly Specific range (<100 results) per SOI
- Together, queries per SOI should contribute at least 100 total results

Use the submit_queries tool to provide your candidate queries."""


def get_query_generation_system() -> str:
    """Build the full system prompt with guide injected."""
    guide = _load_guide()
    guide_section = ""
    if guide:
        guide_section = f"\nREFERENCE — FULL SCOUT QUERY GUIDE:\n{guide}\n"
    return QUERY_GENERATION_SYSTEM.format(guide_section=guide_section)


def get_coverage_recovery_system() -> str:
    """Build the coverage recovery system prompt with guide injected."""
    guide = _load_guide()
    guide_section = ""
    if guide:
        guide_section = f"\nREFERENCE — FULL SCOUT QUERY GUIDE:\n{guide}\n"
    return COVERAGE_RECOVERY_SYSTEM.format(guide_section=guide_section)
