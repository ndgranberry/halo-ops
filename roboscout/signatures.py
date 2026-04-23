#!/usr/bin/env python3
"""
RoboScout Query Generator — DSPy Signatures & Pydantic Models
================================================================
Defines the data contracts (Pydantic output models) and DSPy Signatures
that replace the Anthropic tool_use schemas and prompt templates.

Replaces:
  - query_generator.py: GENERATE_QUERIES_TOOL, REFINE_QUERY_TOOL, RELEVANCE_CHECK_TOOL
  - prompts.py: All system/user prompt templates
"""

from typing import List, Literal

import dspy
from pydantic import BaseModel, Field

# =============================================================================
# Pydantic output models (replace tool_use JSON schemas)
# =============================================================================


class AnalysisOutput(BaseModel):
    """Analysis of the R&D partnering request."""

    core_problem: str = Field(description="1-2 sentence summary of the R&D challenge")
    key_technologies: List[str] = Field(description="Key technologies identified")
    domain: str = Field(description="Primary scientific domain")
    out_of_scope_items: List[str] = Field(
        default_factory=list, description="Items explicitly out of scope"
    )


class ExpandedSOI(BaseModel):
    """A Solution of Interest from the request. Only include SOIs explicitly listed in the request's solutions_of_interest field. Do NOT invent or add new SOIs."""

    soi: str = Field(description="Name of the solution of interest, exactly as stated in the request")
    from_rfp: bool = Field(description="Must be True — only include SOIs from the request")
    specific_terms: List[str] = Field(
        description="Specific technical terms for this SOI"
    )
    reasoning: str = Field(default="", description="Why this SOI was identified")


class CandidateQuery(BaseModel):
    """A single candidate Semantic Scholar query."""

    query: str = Field(description="The Semantic Scholar query text")
    target_soi: str = Field(description="Which SOI this query covers")
    expected_specificity: Literal[
        "general", "moderate", "specific"
    ] = Field(description="Expected result range")
    rationale: str = Field(description="Why this query was generated")


class GenerateQueriesOutput(BaseModel):
    """Complete output from query generation."""

    analysis: AnalysisOutput
    expanded_sois: List[ExpandedSOI]
    candidate_queries: List[CandidateQuery] = Field(min_length=8, max_length=20)


class RefineQueryOutput(BaseModel):
    """Output from query refinement."""

    refined_query: str = Field(description="The improved query text")
    changes_made: str = Field(description="What was changed and why")
    expected_specificity: Literal[
        "general", "moderate", "specific"
    ]


class RegenerateQueryOutput(BaseModel):
    """Output from query regeneration — a completely new query for the same SOI."""

    new_query: str = Field(description="A completely new Semantic Scholar query text")
    approach: str = Field(description="What angle/approach this new query takes")
    expected_specificity: Literal[
        "general", "moderate", "specific"
    ] = Field(description="Expected result range")


class PaperEvaluation(BaseModel):
    """Relevance evaluation of a single paper."""

    paper_index: int
    title: str = Field(default="")
    relevant: bool
    reason: str


class RelevanceCheckOutput(BaseModel):
    """Output from relevance checking."""

    evaluations: List[PaperEvaluation]
    relevant_count: int
    total_checked: int
    relevance_ratio: float = Field(ge=0.0, le=1.0)
    summary: str = Field(description="1 sentence on overall result quality")


# =============================================================================
# DSPy Signatures (replace prompt templates)
# =============================================================================


class GenerateQueries(dspy.Signature):
    """You are an expert at crafting search queries for Semantic Scholar, a scientific publication database. Your queries are used by RoboScout, an AI system that finds relevant researchers based on their publications.

Your goal: generate high-quality search queries that will surface publications by researchers who could fulfill a corporate R&D partnering request.

CRITICAL RULES FOR SEMANTIC SCHOLAR QUERIES:
1. NO quotation marks — they cause poor results
2. NO boolean operators (AND, OR, NOT) — they don't work properly with RoboScout
3. NO negation prefixes (non-, un-) — they pull in the thing you're excluding
4. NO "alternatives to X" phrasing — this returns papers about X, not alternatives
5. AVOID generic words: "novel", "new", "method", "data", "significant", "treatment" — unless they narrow results
6. USE specific compounds, materials, technologies, organisms, techniques
7. USE conjunctions/prepositions strategically ("for", "in", "through", "or") to establish the relationship between technique and application (e.g., "monitoring hydrolysis through viscosity" is much better than "viscosity measurement hydrolysis monitoring")
8. ADD field-specific context (e.g., "in food packaging") to avoid cross-domain noise
9. Focus on WHAT the solution IS, not what it replaces
10. NEVER generate queries targeting out-of-scope items — if the request lists topics, materials, or approaches as out of scope, do not create queries that would primarily surface those results
11. KEEP queries to 4-7 meaningful terms — DO NOT keyword-stuff. Long queries with 8+ terms return poor results on Semantic Scholar. Each query should name ONE specific technique or approach plus its domain context.

REDUNDANCY AVOIDANCE:
- Before generating a specific query (20-499 results), ask: "Would these results already be captured by a broader query I'm also generating?" If yes, DON'T generate the specific query unless it surfaces a genuinely different set of papers (different technique, different angle, different research community).
- BAD: generating both "dermal papilla cells compound screening hair growth" (1605 results) AND "high throughput screening cell based assays hair follicle dermal papilla proliferation signaling" (subset of the same papers with more keywords)
- GOOD: generating both "dermal papilla cells compound screening hair growth" (broad coverage) AND "hair follicle-on-chip dermal papilla keratinocyte co-culture" (different technique — organ-on-chip — surfaces different researchers)
- Drop unnecessary qualifier words that don't meaningfully filter results (e.g., "multiplexed immunoassay X" vs "immunoassay X" — if both return similar papers, use the simpler form)

THINK BEYOND THE RFP:
- The request describes a specific problem. Your queries should also explore ADJACENT methodologies and techniques not explicitly mentioned that could solve the same problem.
- Consider: related analytical techniques, coupled/hybrid methods, upstream/downstream technologies, cross-industry applications
- Example: If the request mentions hydrolysis monitoring, also consider condensation (a related reaction), coupled techniques (FT-NIR, 2D-IR, IR + light scattering), and process analytical technology from adjacent industries
- Researchers who developed methodology for RELATED systems using the same technique could have applicable expertise

APPLICATION vs PRODUCTION FOCUS:
- Read the request carefully to understand whether it seeks APPLICATION of a technology or PRODUCTION/SYNTHESIS of it. These surface very different researchers.
- If the request is about APPLYING a technology (e.g., using sweeteners in food formulations), focus queries on APPLICATION contexts — not on optimizing fermentation or synthesis processes.
- BAD: "metabolic engineering erythritol production yarrowia lipolytica food grade" (targets production researchers, not application researchers)
- GOOD: "erythritol as sugar replacement" or "sugar reduction in food formulation sweetener" (targets people who work on applying it)
- Production-focused queries may capture a few relevant researchers incidentally, but the majority will be irrelevant to an application-focused request.

KEEP THE KEY CONCEPT PROMINENT:
- When a query has too many terms, the most important concept (e.g., "assay", "screening", "sensor") gets buried and results drift off-topic.
- BAD: "matrix metalloproteinase assay collagen synthesis extracellular matrix remodeling tissue repair screening" — "assay" is buried, so results are about studying ECM rather than screening systems
- GOOD: "matrix metalloproteinase expression assay screening in tissue repair" — "assay screening" is prominent, anchoring results to actual screening platforms

BAD QUERIES (keyword-stuffed, too many generic terms crammed together):
- "high throughput screening cell based assays hair follicle dermal papilla proliferation signaling" (11 terms, vague)
- "multiplex assay hair follicle keratinocyte proliferation dermal papilla cell screening platform" (10 terms, vague)
- "hair follicle screening assays dermatology cell culture platforms" (generic platform terms)
- "viscosity measurement organosilane hydrolysis solution concentration monitoring" (too many measurement concepts, no connecting logic)

GOOD QUERIES (specific technique + focused domain context):
- "wnt beta-catenin signaling pathway multiplex analysis hair follicle" (specific pathway + technique)
- "robotic screening platform dermal papilla cell assays" (specific platform type + cell type)
- "biomimetic hair follicle culture system compound screening" (specific model type + application)
- "monitoring organosilane hydrolysis through viscosity" (connecting word "through" establishes technique-application relationship)
- "real-time reaction monitoring industrial solution ftir raman spectroscopy" (specific techniques + application context)
- "Raman spectroscopy silane hydrolysis monitoring" (broad enough to capture related chemistries)

QUERY SPECIFICITY TARGETS:
- General (1,001-3,000 results): Submit 1-2 queries max in this range
- Moderate (500-1,000 results): Submit 2-4 queries
- Specific (20-499 results): Primary target range — submit as many as needed, but ONLY if they add differentiation over broader queries
- Queries returning fewer than 20 results will be REJECTED as too narrow
- NEVER submit queries likely to return >3,000 results

COVERAGE REQUIREMENTS PER SOI:
Each SOI MUST have AT LEAST one query in the Specific range (20-499 results). Together, the queries for a given SOI must contribute a minimum of 100 total results. SOIs that don't meet both requirements will trigger recovery.

Analyze the partnering request and generate 8-15 candidate Semantic Scholar queries. Follow this process:
1. ANALYZE: Identify the core technical problem, key technologies, and domain
2. THINK BROADLY: Consider adjacent techniques, related phenomena, and cross-discipline approaches that could solve the problem — don't limit yourself to only what's explicitly mentioned in the request
3. GENERATE: Create 8-15 candidate queries ONLY targeting the SOIs listed in the request. Do NOT invent new SOIs.

For each query, ensure:
- At least 2 queries per listed Solution of Interest, with at least one aimed at the Specific range (20-499 results)
- Each SOI's queries should contribute at least 100 total results combined
- Each query names ONE specific technique, platform, or approach — not a laundry list of keywords
- Each query should be 4-7 meaningful terms (not more)
- Each query should target at least 20 results (avoid ultra-narrow queries)
- Prefer BROADER terminology when the niche term is a subset (e.g., "silane" instead of "trialkoxysilane" — researchers working on related silane compounds may have transferable expertise)
- Each specific query must add genuine differentiation — it should surface different researchers or techniques than your broader queries already cover
- Avoid semantic-equivalent queries (e.g., "botanical remedies X" and "herbal remedies X" retrieve the same papers). Instead vary by mechanism, material, or use context."""

    title: str = dspy.InputField(desc="Request title")
    looking_for: str = dspy.InputField(desc="What the partner is looking for")
    use_case: str = dspy.InputField(desc="Use case description")
    solutions_of_interest: str = dspy.InputField(
        desc="Solutions of interest from the request"
    )
    requirements: str = dspy.InputField(desc="Technical requirements")
    out_of_scope: str = dspy.InputField(desc="Items explicitly out of scope")
    reference_guide: str = dspy.InputField(
        desc="Domain-specific query generation guide with examples and rules"
    )

    output: GenerateQueriesOutput = dspy.OutputField(
        desc="Analysis, expanded SOIs, and candidate queries"
    )


class RefineQuery(dspy.Signature):
    """You are refining a Semantic Scholar search query that didn't meet quality criteria.

RULES:
- NO quotation marks, boolean operators, or negation prefixes
- USE specific materials/compounds/technologies
- ADD field context to narrow results
- Use conjunctions/prepositions strategically ("through", "for", "in") to establish technique-application relationships
- KEEP queries to 4-7 meaningful terms — do NOT keyword-stuff
- Each query should name ONE specific technique or approach, not a laundry list
- Prefer BROADER terminology when the niche term limits results too much (e.g., "silane" instead of "trialkoxysilane")

Refine this query to fix the problem:
- If too broad: add more specific terms or field context
- If results are irrelevant: shift the terminology toward the actual target domain
- If too many terms (8+): simplify — pick the most distinctive terms and drop generic ones
- If too narrow: BROADEN by using more general vocabulary. Researchers who work on RELATED systems with the same technique may have transferable expertise. Do NOT just add more keywords — that makes it narrower, not broader."""

    original_query: str = dspy.InputField(desc="The query that needs refinement")
    target_soi: str = dspy.InputField(desc="Which SOI this query targets")
    problem: str = dspy.InputField(
        desc="Why the query failed (too broad, irrelevant, zero results)"
    )
    result_count: str = dspy.InputField(desc="Current result count")
    target_range: str = dspy.InputField(desc="Target result count range")
    sample_titles: str = dspy.InputField(
        desc="Sample paper titles from current results"
    )
    request_context: str = dspy.InputField(
        desc="Title, Looking For, and SOIs from the partnering request"
    )

    output: RefineQueryOutput = dspy.OutputField(
        desc="Refined query with explanation of changes"
    )


class RegenerateQuery(dspy.Signature):
    """All previous queries for this Solution of Interest failed after multiple refinement attempts. You must generate a COMPLETELY NEW query using a fundamentally different approach — different terminology, different angle, adjacent fields. Do NOT tweak the failed queries; start fresh.

CRITICAL RULES FOR SEMANTIC SCHOLAR QUERIES:
1. NO quotation marks — they cause poor results
2. NO boolean operators (AND, OR, NOT) — they don't work properly
3. NO negation prefixes (non-, un-) — they pull in the thing you're excluding
4. AVOID generic words: "novel", "new", "method", "data", "significant", "treatment"
5. USE specific compounds, materials, technologies, organisms, techniques
6. ADD field-specific context to avoid cross-domain noise
7. KEEP queries to 4-7 meaningful terms — DO NOT keyword-stuff. Name ONE specific technique or approach per query.
8. NEVER generate queries targeting out-of-scope items — if the request lists topics, materials, or approaches as out of scope, do not create queries that would primarily surface those results

STRATEGY FOR REGENERATION:
- Study the failed queries and identify the PATTERN of failure (cross-domain noise? too niche? wrong vocabulary?)
- Choose a completely different entry point into the literature for this SOI
- Consider: adjacent research communities, different naming conventions, upstream/downstream technologies, related phenomena, coupled/hybrid techniques
- If failed queries were too specific, try a MUCH broader framing — use general terms for the technique class rather than the specific variant (e.g., "silane" instead of "trialkoxysilane", "spectroscopy" instead of "Raman spectroscopy")
- If failed queries had cross-domain noise, use more domain-anchored vocabulary
- Use connecting prepositions to establish relationships (e.g., "monitoring hydrolysis through viscosity" not "viscosity measurement hydrolysis monitoring")
- Target the Specific range (20-499 results) as primary goal, but a General query (1000-3000) that's relevant is better than no query at all"""

    target_soi: str = dspy.InputField(desc="The SOI this query must cover")
    failed_queries: str = dspy.InputField(
        desc="All queries tried for this SOI and why they failed"
    )
    request_context: str = dspy.InputField(
        desc="Title, Looking For, and SOIs from the partnering request"
    )
    reference_guide: str = dspy.InputField(
        desc="Domain-specific query generation guide"
    )

    output: RegenerateQueryOutput = dspy.OutputField(
        desc="A completely new query taking a different approach"
    )


class CheckRelevance(dspy.Signature):
    """You are evaluating whether Semantic Scholar search results are relevant to a corporate R&D partnering request. Be strict — a paper is only "relevant" if a researcher who published it could plausibly fulfill the partnering request.

For each paper, determine if the researcher who wrote it would be relevant to this partnering request. A paper is relevant if:
- The research topic directly or closely relates to the request
- The methods/materials studied could fulfill the stated need
- A researcher in this area would have applicable expertise"""

    title: str = dspy.InputField(desc="Partnering request title")
    looking_for: str = dspy.InputField(desc="What the partner is looking for")
    solutions_of_interest: str = dspy.InputField(desc="Solutions of interest")
    query: str = dspy.InputField(desc="The Semantic Scholar query used")
    papers_section: str = dspy.InputField(
        desc="Formatted paper titles and abstracts to evaluate"
    )

    output: RelevanceCheckOutput = dspy.OutputField(
        desc="Relevance evaluation of each paper"
    )


class GenerateRecoveryQueries(dspy.Signature):
    """You are an expert at crafting search queries for Semantic Scholar. You are generating RECOVERY queries for Solutions of Interest (SOIs) that had no surviving queries after the initial generation and validation pass.

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
9. NEVER generate queries targeting out-of-scope items — if the request lists topics, materials, or approaches as out of scope, do not create queries that would primarily surface those results

Generate 3-4 NEW candidate queries for EACH SOI that needs recovery. Take a different approach than what was tried before:
- Use different terminology, synonyms, or adjacent-field vocabulary
- Try broader or narrower framing depending on what failed
- Consider cross-industry applications, alternative research communities, coupled/hybrid techniques, and related phenomena
- If previous queries were too narrow (0 or <20 results), use MUCH more general domain terms — researchers working on related systems with the same technique may have transferable expertise
- If previous queries were irrelevant, shift the technical vocabulary closer to the target
- Use connecting prepositions ("through", "for", "in") to establish technique-application relationships rather than just listing keywords

For each query, ensure:
- It targets one of the listed SOIs specifically — NEVER invent new SOIs
- It takes a genuinely different approach from the failed queries
- It follows all the Semantic Scholar query rules above
- KEEP queries to 4-7 meaningful terms — do NOT keyword-stuff
- Each query should target at least 20 results (queries under 20 will be rejected)
- Aim for at least one query in the Specific range (20-499 results) per SOI
- Together, queries per SOI should contribute at least 100 total results"""

    title: str = dspy.InputField(desc="Request title")
    looking_for: str = dspy.InputField(desc="What the partner is looking for")
    solutions_of_interest: str = dspy.InputField(desc="All solutions of interest")
    uncovered_sois: str = dspy.InputField(desc="SOIs that need recovery queries")
    failed_queries_section: str = dspy.InputField(
        desc="Previously failed queries with reasons"
    )
    reference_guide: str = dspy.InputField(
        desc="Domain-specific query generation guide"
    )

    output: GenerateQueriesOutput = dspy.OutputField(
        desc="Recovery candidate queries"
    )
