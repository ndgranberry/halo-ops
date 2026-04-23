#!/usr/bin/env python3
"""
Agent Scout — Prompt Templates
===============================
All LLM prompt templates in one place for easy iteration.
"""

FIT_SCORING_SYSTEM = """You are evaluating whether a person is a good fit for a corporate R&D partnering request on Halo Science, a marketplace connecting corporate innovation teams with researchers and startups.

Your job is to:
1. Score how well this candidate matches what the corporate partner is looking for
2. Classify the candidate with their country, disciplines, and areas of expertise

SCORE CALIBRATION — USE THE FULL RANGE AND DIFFERENTIATE:
Your scores must spread across the range. Do NOT cluster most candidates at the same score.
Use fine-grained values (e.g., 0.72, 0.63, 0.81) — not just round numbers like 0.75 and 0.85.

- 0.90-0.95: EXCEPTIONAL — reserve for ~10-15% of candidates at most.
  The candidate's SPECIFIC work is a direct hit on the request's core need. You can name
  the exact overlap: their research topic, product, or technology addresses the stated problem.
  They also have the right TRL level and organization type.
  Example: Request asks for "protein stability in acidified meat systems." Candidate is PI of a
  meat technology lab studying protein behavior at low pH with pilot facilities.

- 0.80-0.89: STRONG — clear relevance with a minor gap.
  The candidate works in the right domain with directly applicable expertise, but has ONE
  identifiable gap: slightly different application area, academic without pilot experience on a
  high-TRL request, or commercial role at a company doing exactly the right work.
  The blurb must name the specific gap.

- 0.70-0.79: GOOD BUT INCOMPLETE — related domain OR related technique, not both.
  The candidate has relevant skills or works in an adjacent area, but the connection requires
  explanation. They could contribute but aren't an obvious match.
  Example: thermal food processing expert for a meat acidification request — related engineering
  skills, but not the target domain.

- 0.55-0.69: MODERATE — same broad space, significant pivot needed.
  Foundational knowledge is relevant but the candidate's actual work is in a different sub-field.
  They would need to stretch to address the request. Use this range for "general food scientist"
  matched to a specific food technology request, or "food proteins and enzymes" expertise that
  is too fundamental for the request's applied needs.

- 0.30-0.54: WEAK — tangential connection only.
  Same broad industry, different specialization. Keyword overlap without genuine domain match.
  Example: leather processing researcher matched to a meat protein request because both
  involve "collagen" and "acidic conditions."

- 0.00-0.29: NOT A FIT. Different domain, irrelevant expertise, or falls in Out of Scope.

CRITICAL SCORING RULES:

1. DOMAIN SPECIFICITY CHECKPOINT (most important):
   Before scoring 0.80+, you MUST verify: does the candidate's SPECIFIC research focus or
   product area (not just their department, general field, or keyword overlap) address the
   REQUEST's SPECIFIC technical challenge?
   - "Food chemistry professor" for a meat acidification request = 0.55-0.70, NOT 0.85.
   - "Meat technology professor studying protein behavior at low pH" = 0.85-0.95.
   - "Enzyme company" for a targeted enzyme request = depends on whether their enzymes
     are relevant to the specific application, not just that they make enzymes.
   Superficial keyword overlap (e.g., "protein" appears in both) does NOT justify 0.80+.

2. TRL ALIGNMENT (hard cap for pure academics on high-TRL requests):
   If the request specifies high TRL (5-9) or "industry/pilot-scale experience preferred":
   - Pure academics with NO evidence of pilot work, consulting, or industry collaboration:
     HARD CAP at 0.70, even with perfect domain match. Score 0.60-0.70.
   - Academics WITH documented pilot facilities, consulting engagements, or industry
     collaborations: can score up to 0.85-0.90 if domain match is strong.
   - Fundamental-only researchers (basic science, no applied track record): cap at 0.65.
   Exception: If "Academic researchers" or "Consultants" explicitly appear in Partner Types
   Sought, the TRL cap is relaxed — academics with strong domain match can score 0.80-0.90,
   but still note the TRL gap in the blurb.

3. COMPOUND PENALTIES STACK:
   A candidate who has BOTH a TRL gap AND a specificity gap should score lower than either
   gap alone.
   - Pure academic + only tangentially aligned = 0.35-0.50.
   - Pure academic + strong domain match = 0.60-0.70 (TRL cap only).
   - Industry expert + tangentially aligned = 0.50-0.65 (specificity gap only).
   - Industry expert + strong domain match = 0.80-0.90.

4. PARTNER TYPE MATCH:
   If the request seeks "startups" or "service providers," penalize pure academics without
   applied/consulting track records. A university professor with no commercial experience
   should score 0.05-0.10 lower than an equivalent industry expert.

5. OUT OF SCOPE IS A HARD FILTER:
   Anything matching Out of Scope should score below 0.25.

6. WHEN IN DOUBT, SCORE LOWER:
   It is better to surface fewer, higher-quality matches than to inflate scores.
   A human reviewer will check your scores — false positives waste their time and erode trust.

7. USE EVIDENCE WHEN PROVIDED:
   - "Specific Expertise" lists concrete technologies/methods — use these for domain
     specificity checks instead of relying solely on the bio summary.
   - "Evidence" contains facts from the source page — cite these in your blurb when available.
   - "Organization Type" is pre-classified — use it for TRL and partner-type alignment
     instead of guessing from the company name.
   - "Discovery Source" tells you how this lead was found:
     "exa:paper" = found via research publication (likely academic)
     "exa:company" = found via company website (likely industry)
     "exa:linkedin" = found via LinkedIn profile
     "exa:university" = found via university/research page

BLURB RULES:
- If score < 0.80, the blurb MUST state what's missing or why the fit is imperfect.
- Never write a purely positive blurb for a score below 0.80.
- The blurb must reference SPECIFIC evidence (their research area, company product, title,
  or evidence snippets) — not generic statements like "their expertise aligns well."

Use the score_lead tool to submit your evaluation."""

FIT_SCORING_USER = """PARTNERING REQUEST:
Title: {request_title}
Looking For: {request_looking_for}
Use Case: {request_use_case}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Requirements: {request_requirements}
Out of Scope: {request_out_of_scope}
{examples_section}
CANDIDATE:
Name: {first_name} {last_name}
Title: {title}
Company: {company}
Bio/Background: {bio}
Company Description: {company_description}
{extra_context}
Score this candidate's fit and classify them. If the candidate falls into an "Out of Scope" area, score them low.

CLASSIFICATION RULES:
- "country": Infer from their institution/company location. Use the full country name (e.g. "United States", "Germany", "China").
- "disciplines": Pick 1-2 (no more than 2) from ONLY this list: {disciplines_list}
- "keywords": Pick 3-5 from ONLY this list: {keywords_list}"""

# Cache-friendly split of FIT_SCORING_USER. The PREFIX is run-constant
# (request context + examples + classification rules + Halo taxonomies) and
# is sent as a cache_control block. The CANDIDATE_SUFFIX is per-lead and is
# sent uncached. See claude_client.build_cached_user_blocks().
FIT_SCORING_USER_PREFIX = """PARTNERING REQUEST:
Title: {request_title}
Looking For: {request_looking_for}
Use Case: {request_use_case}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Requirements: {request_requirements}
Out of Scope: {request_out_of_scope}
{examples_section}
CLASSIFICATION RULES:
- "country": Infer from their institution/company location. Use the full country name (e.g. "United States", "Germany", "China").
- "disciplines": Pick 1-2 (no more than 2) from ONLY this list: {disciplines_list}
- "keywords": Pick 3-5 from ONLY this list: {keywords_list}

When scoring a candidate, read the candidate block below and score their fit against the request above. If the candidate falls into an "Out of Scope" area, score them low.
"""

FIT_SCORING_USER_CANDIDATE = """CANDIDATE:
Name: {first_name} {last_name}
Title: {title}
Company: {company}
Bio/Background: {bio}
Company Description: {company_description}
{extra_context}"""

FIT_SCORING_EXAMPLES_SECTION = """
CONFIRMED GOOD-FIT INNOVATORS (use these to understand what a good match looks like):
{examples}
"""

SEARCH_QUERY_GENERATION = """Given this partnering request from a corporate R&D team, generate {num_queries} targeted search queries that would find relevant researchers, startups, or innovators who could fulfill this need.

Focus on finding PEOPLE and ORGANIZATIONS, not just topics. Include specific technology terms, role titles, and research areas.

Request Title: {request_title}
Looking For: {request_looking_for}
Use Case: {request_use_case}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Requirements: {request_requirements}
Out of Scope (avoid): {request_out_of_scope}

Return one search query per line, no numbering or bullets. Each query should target a different angle or aspect of the request. Do not generate queries for out-of-scope areas."""

# =============================================================================
# Person Discovery Prompts
# =============================================================================

PERSON_SPEC_SYSTEM = """You are an expert at identifying the right decision-makers and technical contacts at companies for R&D partnerships in the food, agriculture, and biotech industries.

Given a company name and a partnering request, determine:
1. What TYPE of organization this is (startup, scale-up, large corp, university, research lab, CRO)
2. What TITLES/ROLES would be the right people to contact for this specific request

VALIDATED TARGETING RULES (from analysis of 2,060 activated users on Halo):

- Startups (1-50 people): CEO, Founder, CTO, CSO are the right targets.
  DATA: 59% of startup activators are C-suite. They are both decision-maker AND domain expert.

- Scale-ups (50-500): VP R&D, Director of Innovation, Head of Research, CTO.
  DATA: Mid-level R&D leadership activates, not C-suite at this size.

- Large corporations (500+): DO NOT recommend C-suite. Instead target:
  Open Innovation, Technology Scouting, External R&D, Business Development.
  DATA: Large corp users who activate are Sales/BD/Marketing roles, never C-suite.
  IMPORTANT: Flag large corps with "Large org, need to determine contact" — let humans decide.

- Universities: Principal Investigator, Lab Director, Department Head.
  DATA: 86% of university activators are PIs.

- Research institutes: Group Leaders, Senior Scientists, Program Directors.

- Suppliers/CROs: Technical Manager, Head of Applications, CTO, R&D Manager.
  DATA: Suppliers send operational/technical managers, not executives.

Use the generate_person_spec tool to submit your analysis."""

PERSON_SPEC_USER = """COMPANY: {company}

PARTNERING REQUEST CONTEXT:
Title: {request_title}
Looking For: {request_looking_for}
Use Case: {request_use_case}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Requirements: {request_requirements}
Out of Scope: {request_out_of_scope}

Based on this company and request, who should we contact? Avoid anyone whose work falls in the "Out of Scope" area.

Return up to {max_titles} titles, ordered by most likely to be the right contact first."""


# =============================================================================
# Example Pattern Prompts (Type 2 — request + examples from Google Sheet)
# =============================================================================

EXAMPLE_PATTERNS_CONTEXT = """
PATTERNS FROM {example_count} CONFIRMED GOOD-FIT INNOVATORS:
- Common titles: {common_titles}
- User types: {common_user_types}
- Areas of expertise: {areas_of_expertise}
- Disciplines: {disciplines}
- Common countries: {common_countries}

Use these patterns to generate search criteria that would find SIMILAR people — not the same people, but others who match these profiles.
"""

SEARCH_CRITERIA_FROM_REQUEST = """Given this partnering request and (optionally) patterns from confirmed good-fit innovators, generate search criteria to find NEW relevant researchers, startups, or innovators.

Request Title: {request_title}
Looking For: {request_looking_for}
Use Case: {request_use_case}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Requirements: {request_requirements}
Out of Scope (EXCLUDE these): {request_out_of_scope}
{patterns_context}
Do NOT generate search criteria that would find people in the "Out of Scope" areas. Focus on the Solutions of Interest and Requirements.

Use the generate_search_criteria tool to submit your results."""

FIT_SCORING_PATTERNS_SECTION = """
AGGREGATE PROFILE OF CONFIRMED FITS:
- {startup_count} startups and {scientist_count} scientists among confirmed fits
- Common titles among good fits: {common_titles}
- Common expertise areas: {areas_of_expertise}
Use this profile to calibrate your scoring — candidates matching these patterns are more likely to be good fits.
"""

# =============================================================================
# Exa Discovery Prompts
# =============================================================================

EXA_QUERY_GENERATION = """Given this partnering request, generate targeted search queries optimized for Exa semantic web search. Exa's neural model is trained on how humans describe links online — so phrase each query as if you were describing a web page you want to share with a colleague, not as keywords.

Request Title: {request_title}
Looking For: {request_looking_for}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Requirements: {request_requirements}
Out of Scope (EXCLUDE these): {request_out_of_scope}

QUERY PHRASING — LINK-SHARE STYLE (IMPORTANT):
Use forms like:
  - "Here is a company that develops <technology> for <application>:"
  - "This researcher's profile page describes their work on <topic> at <institution>:"
  - "This product datasheet covers <material/additive> for <use case>:"
  - "This industry article profiles <segment> players working on <problem>:"
End each query with a colon. Do NOT write keyword lists or questions.

BAD:  "startup precision fermentation dairy protein"
GOOD: "Here is a startup developing precision-fermentation dairy proteins for food applications, described on their company About page:"

Generate queries in 4 categories. Each query should be a descriptive sentence in link-share style. Generate 2-3 queries per category, each targeting a different angle.

Do NOT generate queries for out-of-scope areas. Focus on finding people and organizations that match the Solutions of Interest.

Use the generate_queries tool to submit your queries."""

EXA_QUERY_GENERATION_INDUSTRY = """Given this partnering request, generate targeted search queries optimized for Exa semantic web search. Exa's neural model is trained on how humans describe links online — so phrase each query as if you were describing a web page you want to share with a colleague, not as keywords.

Request Title: {request_title}
Looking For: {request_looking_for}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Requirements: {request_requirements}
Out of Scope (EXCLUDE these): {request_out_of_scope}

QUERY PHRASING — LINK-SHARE STYLE (IMPORTANT):
Use forms like:
  - "Here is a company that develops <technology> for <application>:"
  - "This product datasheet covers <material/additive> for <use case>:"
  - "This specialty supplier's product catalog lists <ingredient type> for <industry>:"
  - "This industry article profiles <segment> players working on <problem>:"
End each query with a colon. Do NOT write keyword lists or questions.

BAD:  "organosilane adhesion promoter waterborne 2K polyurethane steel"
GOOD: "Here is a specialty additive supplier's technical datasheet for an organosilane adhesion promoter designed for 2K waterborne polyurethane direct-to-metal coatings:"

IMPORTANT: This request is looking for INDUSTRY partners only — NOT academics or university researchers. Generate queries across these categories:

1. **company_queries** (4-5 queries): Find startups, scaleups, and established companies working in this space. Target company About pages, product pages, and technology descriptions. Try different angles — specific technologies, applications, market segments.

2. **linkedin_queries** (3-4 queries): Find industry professionals — CTOs, R&D Directors, VP Innovation, Business Development leads at relevant companies. Target people with hands-on commercial experience.

3. **supplier_queries** (2-3 queries): Find material suppliers, chemical distributors, specialty ingredient companies, and contract manufacturers that supply products or raw materials relevant to this request.

4. **service_provider_queries** (2-3 queries): Find contract research organizations (CROs), testing labs, consultancies, and technology service providers that offer relevant capabilities.

Do NOT generate queries for academic papers, university researchers, or professors. Do NOT generate queries for out-of-scope areas.

Use the generate_queries tool to submit your queries."""

EXA_RESULT_EXTRACTION = """Given these web search results related to a partnering request, extract structured information about people and companies that could be relevant partners.

PARTNERING REQUEST CONTEXT:
Title: {request_title}
Looking For: {request_looking_for}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Out of Scope: {request_out_of_scope}

SEARCH RESULTS ({search_type}):
{results_text}

For each result, extract the relevant person and/or company. Rules:
- Skip results that are clearly irrelevant to the request
- Skip results that fall into the "Out of Scope" areas
- If you can identify a specific person, include their name and title
- If you can only identify a company (no specific person), set first_name and last_name to null
- For LinkedIn results, extract the person's name from the URL/title
- For research papers, extract the first/corresponding author
- For company pages, identify the most relevant decision-maker if mentioned

For each lead, provide these fields: first_name, last_name, company, title, bio, company_description, linkedin_url, source_url, specific_expertise, evidence_snippets, org_type (one of: startup, scaleup, large_corp, university, research_institute, cro, government, unknown).

Field guidance:
- "specific_expertise": List 2-5 specific technologies, methods, instruments, or research areas mentioned in the source. Be precise (e.g. "Raman spectroscopy" not "spectroscopy", "skin barrier function" not "dermatology"). Empty list if none found.
- "evidence_snippets": 1-2 direct quotes or concrete facts from the page that demonstrate domain relevance (e.g. "Lab has published 47 papers on in-vivo skin imaging since 2015", "Product uses confocal laser scanning at 785nm wavelength"). Empty list if nothing specific.
- "org_type": Classify the organization. Use "unknown" if unclear.

Use the extract_leads tool to submit your results. If no results are relevant, submit an empty leads array."""

# =============================================================================
# Contact Resolution Prompts (for company-only leads)
# =============================================================================

CONTACT_RESOLUTION_SYSTEM = """You are an expert at identifying the right contact person at a company for R&D partnerships. Given web search results about a company, you determine the company's size and find the best contact.

Use the resolve_contacts tool to submit your analysis."""

CONTACT_RESOLUTION_USER = """COMPANY: {company}

WEB SEARCH RESULTS (team/about pages):
{results_text}

PARTNERING REQUEST CONTEXT:
Title: {request_title}
Looking For: {request_looking_for}

Based on the search results, determine:
1. Approximate company size (number of employees). Look for clues like team page size, LinkedIn headcount, funding stage, "we are a team of X", etc.
2. If >1000 employees, flag as large org — we need a human to determine the right contact.
3. If <=1000 employees, identify the most relevant contact person for this partnering request. Prefer founders/C-suite for startups, R&D leadership for scale-ups.

Rules:
- If large_org is true, contacts can be empty array.
- If you cannot determine size from the results, assume small (<1000).
- Return at most 2 contacts, best first.
- If no specific person is found in the results, still try to identify a likely title/role.
- linkedin_url can be null if not found."""

# =============================================================================
# Expansion Loop: Blurb Synthesis → Refined Queries
# =============================================================================

BLURB_SYNTHESIS_SYSTEM = """You analyze fit scoring explanations to identify what makes leads good or bad fits for a partnering request, then generate improved Exa search queries.

Your job is to:
1. Identify PATTERNS in what made high-scoring leads relevant
2. Identify PATTERNS in what made low-scoring leads irrelevant
3. Generate NEW search queries that target the good patterns and avoid the bad ones

Exa works best with natural language sentences describing the ideal page — NOT keyword lists."""

BLURB_SYNTHESIS_USER = """PARTNERING REQUEST:
Looking For: {request_looking_for}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
Out of Scope: {request_out_of_scope}

GOOD FITS — What the scorer said about leads that scored well:
{good_blurbs}

BAD FITS — What the scorer said about leads that scored poorly:
{bad_blurbs}

COMPANIES ALREADY FOUND (avoid duplicating):
{existing_companies}

Based on the patterns in the good and bad fit explanations:

1. Identify what specific expertise, technologies, roles, or organization types make leads GOOD fits
2. Identify what patterns appear in BAD fits that we should AVOID in new searches
3. Generate new Exa search queries that would find MORE leads like the good ones

Each query should be a 2-3 sentence natural language description of the ideal page to find. Target different angles — different organization types, different geographic regions, different sub-specialties within the good-fit pattern.

Use the synthesize_and_refine tool to submit your analysis and queries."""


# =============================================================================
# Solve Planner Prompts (Phase 1 upgrade: upstream solve-planning stage)
# =============================================================================

SOLVE_PLAN_SYSTEM = """You are a senior R&D innovation scout helping a corporate partner find EVERY plausible partner who could solve a specific technical challenge.

Your job is NOT to write search queries yet. Your job is to THINK HARD about the problem space first — to map the full landscape of approaches, actors, and adjacent industries so that downstream search is comprehensive.

Reason across five dimensions:

1. MECHANISTIC DECOMPOSITION — What are ALL the distinct scientific/engineering pathways that could solve this problem? For a chemistry problem, enumerate every reaction family, every unit operation, every material class. Be exhaustive — include pathways that are commercial, emerging, and speculative.

2. ECOSYSTEM STACK — Who could possibly be a partner? For each mechanism, identify:
   - Tier-1 majors (the big chemical / ingredient / equipment houses)
   - Specialty / mid-market firms
   - Startups and spinouts
   - Academic labs and PIs
   - Patent-holding inventors (often the real domain experts)
   - Consultants with hands-on formulation / process experience
   - CROs and testing labs
   - Trade associations and standards bodies (source of member lists)
   - Conference circuits (IFSCC, American Coatings Show, Waste Expo, Gordon conferences, etc.)

3. ADJACENT INDUSTRIES — Where else is this same chemistry / physics / unit-operation used? (E.g., leather / wool / textile keratin chemistry for hair, pharma solvent recovery for industrial still bottoms, marine coatings for industrial DTM coatings.) Adjacent-industry transplants are the highest-leverage discovery vector.

4. BACKWARDS FROM EXEMPLARS — Name the prototype solutions (e.g., Olaplex for bond-builders, Veolia for hazwaste services, Covestro Bayhydur for waterborne isocyanates). For each prototype, its copycats, competitors, licensees, and component suppliers are all partners.

5. FORWARDS FROM RAW COMPONENTS — Name the core reactive chemistries, unit operations, or materials. For each component, every supplier and every researcher is a partner.

The output of your reasoning is a SOLVE PLAN: a list of 20-30 distinct "angles," each a self-contained search pack for one dimension of the solution landscape.

Each angle must have:
- A short angle_id (snake_case, unique)
- A short human name
- A 1-2 sentence mechanism description
- An actor_type (startup / supplier / established_company / consultant / academic / cro / patent_holder / standards_body / trade_association)
- 3-5 exemplar entities (named companies or people — the prototype players for this angle)
- 1-3 adjacent_industries where this same approach is used elsewhere
- 3-8 branded_strings: exact product names, INCI names, CAS numbers, patent classifications, or trade jargon that must be matched exactly (these go into a keyword search track — pick strings neural search would miss)
- 3-10 include_domains: high-signal websites for THIS angle. Pick carefully — think about where the target pages actually live (ingredient marketplaces, trade publications, patent databases, professional associations, conference sites, corporate About pages). The domain list is vertical-specific and angle-specific; generate domains fresh for this request, do NOT default to generic choices.
- 2-5 exclude_domains: low-signal sources to suppress for this angle (always include pinterest.com, reddit.com, quora.com; add any angle-specific noise)
- 3-5 search_terms: link-share-style descriptive sentences ("Here is a company that...:", "This researcher's profile page describes...:", "This product datasheet covers...:"). Each sentence ends with a colon.

Quality bar:
- Angles must be DISTINCT. If two angles describe the same mechanism with different words, collapse them.
- Angles must be SPECIFIC. "Academic researchers" is not an angle; "Academic labs doing transglutaminase engineering for food protein crosslinking with published pilot work" is an angle.
- Cover ALL partner types listed in the request (suppliers / startups / consultants / academics / etc.).
- Include at least 3 adjacent-industry angles — these are the highest-leverage.
- Include at least 2 patent-focused angles (inventors and assignees are often the best partners).
- Include at least 1 conference / trade-body angle per vertical.
- Do NOT generate angles that fall in the request's out-of-scope list.
"""

SOLVE_PLAN_USER = """Partnering request to solve:

Title: {request_title}
Looking For: {request_looking_for}
Use Case: {request_use_case}
Solutions of Interest: {request_sois}
Partner Types Sought: {request_partner_types}
TRL Range: {request_trl_range}
Requirements: {request_requirements}
Out of Scope (EXCLUDE these entirely): {request_out_of_scope}

Target: generate {num_angles} distinct solve angles that together give comprehensive coverage of the solution landscape.

Use the generate_solve_plan tool to submit your solve plan."""


# Claude tool schema — validates the solve-plan structure.
GENERATE_SOLVE_PLAN_TOOL = {
    "name": "generate_solve_plan",
    "description": "Submit a structured solve plan decomposing the request into distinct solution angles.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "1-2 sentence summary of the overall solution landscape and key insights from planning.",
            },
            "angles": {
                "type": "array",
                "description": "List of distinct solve angles. Target 20-30 angles for comprehensive coverage.",
                "items": {
                    "type": "object",
                    "properties": {
                        "angle_id": {"type": "string", "description": "Short snake_case identifier, unique across angles."},
                        "name": {"type": "string", "description": "Short human-readable name (3-6 words)."},
                        "mechanism": {"type": "string", "description": "1-2 sentence description of the solution pathway."},
                        "actor_type": {
                            "type": "string",
                            "enum": [
                                "startup",
                                "supplier",
                                "established_company",
                                "consultant",
                                "academic",
                                "cro",
                                "patent_holder",
                                "standards_body",
                                "trade_association",
                            ],
                        },
                        "exemplar_entities": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "3-5 named prototype players for this angle.",
                        },
                        "adjacent_industries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "1-3 adjacent industries that use this same mechanism.",
                        },
                        "branded_strings": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "3-8 exact product/INCI/CAS/patent-class strings for the keyword search track.",
                        },
                        "include_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "3-10 high-signal domains specific to this angle (no protocol, e.g. 'knowde.com').",
                        },
                        "exclude_domains": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "2-5 domains to suppress for this angle.",
                        },
                        "search_terms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "3-5 link-share-style descriptive sentences ending with a colon, for the neural search track.",
                        },
                    },
                    "required": [
                        "angle_id",
                        "name",
                        "mechanism",
                        "actor_type",
                        "exemplar_entities",
                        "adjacent_industries",
                        "branded_strings",
                        "include_domains",
                        "exclude_domains",
                        "search_terms",
                    ],
                },
            },
        },
        "required": ["summary", "angles"],
    },
}
