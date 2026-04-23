# Halo Ops — AI Scouting Tools

Two tools for finding and scoring innovation partners for Halo Science partnering requests.

| Tool | File | What it does |
|------|------|-------------|
| **Agent Scout** | `agent_scout.py` | Finds, scores, enriches, and deduplicates contacts for a partnering request |
| **RoboScout Query Gen** | `roboscout_query_gen.py` | Generates and validates Semantic Scholar search queries for RoboScout |

Both tools share the same credentials, Snowflake connection, and Google Sheets auth. See [CLAUDE.md](CLAUDE.md) for how to trigger runs from Claude Code.

---

## Using with Claude Code

The fastest way to work with these tools is via Claude Code. Once set up, you can trigger runs, check progress, and get results in plain English — no CLI knowledge needed.

### Setup (one time)

1. **Clone the repo**
   ```bash
   git clone https://github.com/ndgranberry/halo-ops.git
   cd halo-ops
   ```

2. **Open in Claude Code**
   ```bash
   claude .
   ```
   Claude Code will automatically load `CLAUDE.md`, giving it full context about both tools and how to trigger runs.

3. **Get credentials from Neil** — you need a `.env` file and `google_service_account.json` placed in the repo root. Ask Neil to share these via 1Password.

### Triggering runs

Just describe what you want in Claude Code:

> *"Run Agent Scout for request 1664 and output to this Google Sheet: [url]"*

> *"Generate RoboScout queries for request 1655"*

> *"Check the progress of the last RoboScout run"*

Claude Code will translate your request into the right curl command using the server at `46.224.159.126:8000`. You don't need to know the API — just describe the task.

---

# Agent Scout

AI-powered lead discovery and scoring pipeline for Halo Science. Automatically finds, enriches, scores, and deduplicates potential innovation partners for corporate partnering requests.

---

## Table of Contents

1. [The Problem](#the-problem)
2. [How It Works — The Pipeline](#how-it-works--the-pipeline)
3. [Architecture Deep Dive](#architecture-deep-dive)
4. [The LLM-as-Judge Scoring System](#the-llm-as-judge-scoring-system)
5. [Evaluation & Analysis](#evaluation--analysis)
6. [The Path to DSPy](#the-path-to-dspy)
7. [Risks & Failure Modes](#risks--failure-modes)
8. [Setup](#setup)
9. [Usage](#usage)
10. [Upgrade Roadmap](#upgrade-roadmap)

---

## The Problem

Halo Science is a marketplace connecting corporate R&D teams with researchers and startups. When a corporation posts a partnering request ("we need someone working on enzymatic upcycling of food waste into protein"), a human has to find, vet, and reach out to the right innovators. This is slow, expensive, and inconsistent.

Agent Scout automates the full funnel:

```
Corporate request → Find people → Score relevance → Get contact info → Output for human review
```

The hard part isn't any one step — it's that every step involves judgment. "Is this company relevant?" "Is this the right person at the company?" "Is a leather processing researcher who works with 'acidic protease' a good match for a meat protein request?" (No — that's a keyword trap.)

This system encodes those judgments as LLM prompts calibrated against human feedback, then chains them into a pipeline that runs in minutes instead of days.

---

## How It Works — The Pipeline

Agent Scout runs a 7-stage pipeline. The stages execute sequentially, but within each stage there's parallelism (concurrent API calls, batch processing).

```
parse → discover → score → filter → enrich → halo_dedup → output
```

### Stage 1: Parse

Normalizes one of 4 input types into a common `ScoutLead` format. If a `request_id` is provided, loads full request context from Snowflake — the `REQUESTS`, `REQUEST_SOLUTIONS`, `REQUEST_REQUIREMENTS`, and `REQUEST_FOR_PROPOSALS` tables give us everything the corporate partner specified: what they're looking for, use case, solutions of interest, partner types, TRL level, requirements, and what's out of scope.

**Why this matters:** The richer the request context, the more precise the scoring. A request that says "startups doing precision fermentation for dairy proteins, TRL 5-9, not interested in plant-based alternatives" gives the scorer enough signal to differentiate a 0.92 from a 0.58. A vague request like "food innovation partners" forces the scorer to guess.

| Input Type | Description | When to Use |
|------------|-------------|-------------|
| `partnering_request` | Discovery from request ID alone | Greenfield scouting — no leads yet |
| `request_with_examples` | Request + example leads from Google Sheet | Have some known good fits to learn from |
| `scraped_list` | Pre-collected names/companies (CSV or Sheet) | Got a list from a conference, LinkedIn scrape, etc. |
| `company_list` | Target specific companies | Know the companies, need the right people |

### Stage 2: Discover

Finds people using a multi-strategy approach. This is the most complex stage because it chains two LLM calls (query generation + result extraction) around an external API (Exa).

**How Exa semantic search works:** Unlike keyword search, Exa takes a natural language description and finds web pages that match semantically. So "startup developing microbial crop protection products for row crops" finds relevant company About pages even if they don't use those exact words.

Four search strategies run in sequence:

1. **Company search** — finds startups/companies. Uses Exa's `company` category filter.
2. **People search** — finds researchers/executives. Uses Exa's `people` category (LinkedIn blocks scraping, so we get public profiles and bios).
3. **Research papers** — finds researchers via publications. Uses `research paper` category.
4. **Academic/university search** — domain-filtered search across ResearchGate, Google Scholar, ORCID.

**Adaptive refinement:** After the initial search, Claude evaluates whether the results have gaps (e.g., "we found lots of academics but no startups"). If gaps exist, it generates refined queries and runs up to 2 more rounds. This is a simple agentic loop — evaluate → identify gaps → refine → retry.

**Title targeting** (`account_targeter.py`): When we find a company, we need the right *person* at that company. This module uses patterns validated against 2,060 activated Halo users:

| Org Type | Target Titles | Why |
|----------|---------------|-----|
| Startup (1-50) | CEO, Founder, CTO, CSO | 59% of startup activators are C-suite — they are both decision-maker AND domain expert |
| Scale-up (50-500) | VP R&D, Director of Innovation | Mid-level R&D leadership activates, not C-suite |
| Large corp (500+) | Open Innovation, Tech Scouting, BD | Never C-suite — large corp users who activate are Sales/BD roles |
| University | PI, Professor, Lab Director | 86% of academic activators are PIs |

### Stage 3: Score (LLM-as-Judge)

This is the core intellectual work. Each lead gets a Claude API call with:
- Full request context (looking_for, use_case, SOIs, partner types, TRL, requirements, out_of_scope)
- Lead context (name, title, company, bio, company description, specific expertise, evidence snippets)
- A detailed rubric with calibration instructions and anti-patterns
- (Optional) Few-shot examples from confirmed good fits

Claude returns structured output via tool use:
```json
{
  "score": 0.72,
  "blurb": "Prof. Okafor's bioprocess optimization in yeast is relevant but not dairy-specific...",
  "country": "United States",
  "disciplines": ["Food Science & Technology"],
  "keywords": ["Fermentation", "Bioprocessing"]
}
```

Scoring runs with 10 concurrent threads. A typical batch of 80 leads takes ~45 seconds.

**Why score before enrich:** Enrichment is expensive — each lead costs an n8n webhook call that fans out to Amplemarket, Findymail, and Apollo. Scoring first lets us drop low-fit leads (Stage 4: Filter) before burning those credits. The tradeoff: scoring happens without email, but fit scoring depends on role/expertise/company, not contact info.

### Stage 4: Filter

Drops leads below `min_fit_score` threshold (default 0.3). Simple but high-leverage: on a typical run, this cuts 30-50% of leads before the expensive enrichment step.

### Stage 5: Enrich

Two-phase contact enrichment for leads that survived scoring:

1. **n8n webhook waterfall** — Sends `{first_name, last_name, company, company_domain}` to an n8n workflow that tries Amplemarket first, falls back to Findymail, then Apollo. First tool to find an email wins. Timeout: 5 minutes per batch.

2. **Academic enrichment** — For leads still missing emails (usually researchers), queries ORCID and OpenAlex APIs using the person's name + institution. Free and surprisingly effective for academics.

**Domain resolution** (`domain_resolver.py`): Before sending to n8n, we need the company's email domain. Three-tier resolution: local cache → Exa search for the company → heuristic from company name.

### Stage 6: Halo Dedup

Checks enriched emails against Snowflake's 511K+ Halo user table. Removes anyone already on the platform — no point scouting someone who's already a Halo user.

### Stage 7: Output

Writes to Google Sheets with:
- **Auto-filled columns:** name, email, title, company, LinkedIn, fit score, fit blurb, company description, discovery source, country, disciplines, keywords
- **HITL review columns:** reviewer decision (Approve/Reject/Maybe dropdown), notes, outreach status, perfect fit checkbox

The sheet is formatted with frozen headers and data validation dropdowns for the review columns.

---

## Architecture Deep Dive

### Module Map

```
agent_scout.py          Main orchestrator — pipeline stages, state management, CLI
models.py               Core dataclasses: ScoutLead, ScoutConfig, ScoutRun, LeadStatus
input_parser.py         Input normalization — reads Google Sheets, CSV, Snowflake
snowflake_client.py     SQL queries for request context + Halo user dedup
person_discovery.py     Orchestrates title targeting + n8n Apollo webhook discovery
exa_discovery.py        Multi-strategy Exa semantic search + Claude extraction
enrichment.py           n8n webhook wrapper — batching, polling, field mapping
academic_enrichment.py  ORCID + OpenAlex email lookup
fit_scorer.py           LLM-as-judge scoring — 10-thread parallel, tool-use output
output_formatter.py     Google Sheets/CSV writer with formatting
domain_resolver.py      Company → email domain resolution (cache → Exa → heuristic)
account_targeter.py     Data-driven title targeting by org size
prompts.py              All LLM prompt templates — single file for easy iteration
taxonomy.py             Halo discipline + expertise lists (250+ controlled terms)
claude_client.py        Shared Claude API wrapper with retry logic
```

### Key Design Decision: ScoutLead as Accumulating Record

A `ScoutLead` starts nearly empty and accumulates data at each stage:

```
Parse:    {first_name, last_name, company}
Discover: + {bio, company_description, specific_expertise, evidence_snippets, org_type}
Score:    + {fit_score, fit_blurb, country, disciplines, keywords}
Enrich:   + {email, linkedin_url, email_source}
```

This is why the pipeline order matters — each stage depends on fields populated by earlier stages. Discovery provides the bio/expertise context that scoring needs. Scoring provides the threshold that filtering uses. Enrichment needs name+company from discovery.

### State Management and Resumability

Every stage completion serializes the full pipeline state (all leads + config) to `.scout_state/<run_id>.json`. If a run fails mid-pipeline, `--resume <run_id>` picks up from the last completed stage. This is critical because:

- Exa discovery takes 2-5 minutes and costs API credits
- Scoring 100 leads burns ~100 Claude API calls
- Enrichment costs money per lead in the n8n waterfall

You don't want to redo completed stages because Stage 5 hit an n8n timeout.

---

## The LLM-as-Judge Scoring System

This is the most important and most fragile part of the system. Understanding how it works, where it fails, and how to improve it is the key to the whole project.

### The Prompt Architecture

The scoring prompt (`prompts.py` → `FIT_SCORING_SYSTEM`) is ~120 lines of carefully calibrated instructions. It's structured as:

1. **Role framing** — "You are evaluating whether a person is a good fit for a corporate R&D partnering request on Halo Science"
2. **Score band definitions** — Six bands from 0.90-0.95 (Exceptional) to 0.00-0.29 (Not a Fit), each with characteristics and concrete examples
3. **Critical scoring rules** — Seven numbered rules addressing specific failure patterns observed in production:
   - Domain specificity checkpoint
   - TRL alignment (hard caps for pure academics)
   - Compound penalty stacking
   - Partner type matching
   - Out of scope as hard filter
   - "When in doubt, score lower"
   - Evidence usage instructions
4. **Blurb rules** — Below 0.80, the blurb must state what's missing. No generic praise allowed.

**Why so prescriptive:** Without these rules, the LLM defaults to polite, compressed scoring. In early runs, 70% of leads got the exact same score (0.85), making the score useless for prioritization. Every rule in the prompt addresses a specific failure pattern observed in a real production run.

### Calibration from Human Review

The current rubric was calibrated against human review of 57 scored leads from a "protein stability in acidified meat systems" request (March 2026). Three failure patterns drove most of the prompt revisions:

**1. TRL penalty was too soft**

Original: Academic at a Chinese university scored 0.85.
Human: Rejected — "does not satisfy the high TRL requirement, fundamental research, not industrial problem solving."
Fix: Hard cap at 0.70 for pure academics on high-TRL requests. Cap at 0.65 for fundamental-only researchers.

**2. Adjacent domain scored the same as exact domain**

Original: Thermal food processing expert scored 0.85. Meat technology PI also scored 0.85.
Human: Thermal expert was "Maybe — this is a stretch." Meat PI was "Perfect Fit."
Fix: Added the 0.70-0.79 band specifically for "related domain OR related technique, not both." The thermal expert drops to ~0.68, the meat PI stays at ~0.95.

**3. Superficial keyword matches inflated scores**

Original: Leather processing researcher scored 0.85 because bio mentioned "acidic protease" and "protein processing under acidic conditions."
Human: Rejected — leather tanning is not meat science.
Fix: Added "Domain Specificity Checkpoint" as Rule #1 — before scoring 0.80+, must verify the candidate's SPECIFIC research focus, not just keyword overlap.

The corrected examples live in `context/scored_lead_examples.md` and are available as few-shot references.

### Few-Shot Learning (Type 2 Inputs)

For `request_with_examples` runs, the scorer injects up to 15 confirmed good-fit innovators into the prompt. This gives the model concrete examples of what "good" looks like for this specific request.

Additionally, aggregate patterns are extracted from the example set:
- How many are startups vs. scientists
- Common titles among good fits
- Shared areas of expertise

This is primitive but effective — it narrows the scoring distribution around the examples rather than relying solely on the rubric.

### Structured Output via Tool Use

Scoring uses Claude's tool-use feature (not free-text parsing). The `score_lead` tool schema enforces that the model returns a structured JSON object with score, blurb, country, disciplines, and keywords. This eliminates regex parsing failures and ensures type safety.

---

## Evaluation & Analysis

The `analysis/` directory contains scripts that evaluate scoring quality against ground truth data from Halo's matching confusion matrix — a Snowflake export of all predictions ever made, with human labels.

### What We Measure

**Actual Positive Rate by Score Bin** (`score_tp_analysis.py`): For each 0.05-wide score bin, what percentage of predictions are actually relevant? This is the most important chart — it shows whether higher scores actually correlate with better leads.

**Precision-Recall Tradeoff** (`score_tp_analysis.py`): If we set the cutoff at score X, how many true positives do we capture (recall) and what fraction of what we send is relevant (precision)? The current threshold of 0.55 was picked by inspecting this curve.

**LLM vs. Keyword Scoring** (`llm_vs_keyword.py`, `full_writeup_data.py`): Head-to-head comparison of LLM-based scoring against a keyword/embedding baseline. At comparable volume, which approach finds more true positives? At comparable recall, which has better precision? This is the core justification for using LLM scoring.

**False Positive Analysis** (`fp_analysis.py`): Among leads that scored above threshold, how many resulted in "Not Relevant" responses? Broken down by score bin — the FP rate should decrease as scores increase.

**Engagement Funnel** (`full_writeup_data.py`): Of all predictions above threshold, how many led to proposals, relevant responses, not-relevant responses, or no response? This connects scoring quality to business outcomes.

### Key Evaluation Concepts

**Strong Positive vs. Soft Positive:** Ground truth has two tiers. A "Strong Positive" means the innovator submitted a proposal (hardest evidence of fit). A "Soft Positive" means they were marked as relevant even without a proposal. The analysis tracks both.

**Predictions per Proposal:** The conversion metric — how many predictions do you need to send to get one proposal? Lower is better. This varies dramatically by score bin, which is what makes the threshold decision so consequential.

**Score Agreement:** When both LLM and keyword scoring agree a lead is positive, the true positive rate is highest. Disagreements (LLM positive but keyword negative, or vice versa) have measurably different TP rates, which reveals where each approach has blind spots.

---

## The Path to DSPy

Agent Scout is a hand-tuned prompt pipeline. Every prompt was written manually, calibrated by eyeballing human review feedback, and revised through trial-and-error. This works, but it doesn't scale. DSPy is the framework that could systematize what we're doing manually.

### What DSPy Would Replace

DSPy (Declarative Self-improving Language Programs) treats LLM prompts as optimizable programs. Instead of hand-writing instructions like "when the request specifies TRL 5-9, cap pure academics at 0.70," you define:

1. **Signatures** — input/output contracts (what Agent Scout calls the `score_lead` tool schema)
2. **Modules** — composable LLM operations (what Agent Scout calls pipeline stages)
3. **Metrics** — how to measure quality (what Agent Scout's `analysis/` scripts compute)
4. **Optimizers** — algorithms that rewrite prompts/few-shot examples to maximize the metric

Agent Scout already has the raw ingredients:

| Agent Scout Today | DSPy Equivalent |
|-------------------|-----------------|
| `prompts.py` system prompts | DSPy `Signature` definitions |
| `fit_scorer.py` with tool-use schema | DSPy `Module` with typed outputs |
| `context/scored_lead_examples.md` | DSPy training examples (labeled demonstrations) |
| `analysis/score_tp_analysis.py` | DSPy `Metric` functions |
| Human review → manual prompt revision | DSPy `Optimizer` (MIPROv2, BootstrapFewShot) |

### What the Migration Looks Like

**Step 1: Define the Metric**

The metric already exists in `score_tp_analysis.py`. At its simplest:

```python
def scoring_metric(example, prediction):
    """Does the model's score agree with human judgment?"""
    human_label = example.human_decision  # Approve, Reject, Maybe
    model_score = prediction.score

    if human_label == "Approve" and model_score >= 0.70:
        return 1.0  # Correct
    if human_label == "Reject" and model_score < 0.55:
        return 1.0  # Correct
    return 0.0  # Wrong
```

In practice you'd want something softer — penalizing score distance from the ideal, weighting false positives more than false negatives (because FPs waste human reviewer time and erode trust).

**Step 2: Build the Training Set**

The confusion matrix export + human-reviewed Google Sheets give us labeled examples: (request_context, lead_context) → human_decision. The `context/scored_lead_examples.md` file already has ~10 gold-standard examples with human-corrected scores and explanations for why the original score was wrong.

To build a DSPy-ready training set, we'd structure these as:

```python
trainset = [
    dspy.Example(
        request_context="Protein stability in acidified meat systems, TRL 5-9...",
        lead_context="Ilse Fraeye, Associate Professor, Meat Technology, KU Leuven...",
        score=0.95,
        blurb="Exceptional match — leads a meat technology research group...",
    ).with_inputs("request_context", "lead_context"),
    # ... more examples
]
```

**Step 3: Let the Optimizer Rewrite the Prompt**

Instead of manually writing 120 lines of scoring instructions, DSPy's MIPROv2 optimizer would:
1. Generate candidate instructions from the training examples
2. Select the best few-shot examples to include
3. Test each candidate against the metric
4. Iterate to find the best-performing combination

The current hand-tuned prompt has ~7 explicit scoring rules. An optimizer might discover that different rules, different example orderings, or different phrasing of the same rules produce better metric scores.

**Step 4: Compose the Full Pipeline**

DSPy modules compose like functions. The full Agent Scout pipeline becomes:

```python
class AgentScoutPipeline(dspy.Module):
    def __init__(self):
        self.generate_queries = dspy.ChainOfThought("request_context -> search_queries")
        self.extract_leads = dspy.ChainOfThought("search_results, request_context -> leads")
        self.score_lead = dspy.ChainOfThought("request_context, lead_context -> score, blurb")

    def forward(self, request_context):
        queries = self.generate_queries(request_context=request_context)
        results = exa_search(queries.search_queries)  # external API call
        leads = self.extract_leads(search_results=results, request_context=request_context)
        scored = [self.score_lead(request_context=request_context, lead_context=l) for l in leads]
        return scored
```

Each `ChainOfThought` module gets its own optimized prompt. The whole pipeline can be optimized end-to-end (maximizing proposal conversion) or stage-by-stage (maximizing extraction recall, scoring precision, etc.).

### Why We Haven't Done This Yet

1. **Training set size.** DSPy optimizers need ~50-200 labeled examples to work well. We have ~57 human-reviewed leads from one request. That's enough for one request type but not enough for a general-purpose scorer. We need more human reviews across diverse request types.

2. **Metric definition is hard.** "Precision at threshold 0.55 for strong positives" is one metric, but we actually care about a blend of precision, recall, score calibration, blurb quality, and engagement conversion. Defining a single composite metric that captures "good scoring" is a research problem.

3. **Prompt complexity.** The current prompt has domain-specific rules (TRL caps, compound penalties, org-type matching) that encode genuine domain expertise. An optimizer might discover these rules, or it might find shortcuts that work on the training set but fail on new request types. Human-readable, manually maintained rules are more debuggable.

4. **The eval infrastructure needs work.** DSPy optimizers assume fast, repeatable evaluation. Our current eval requires loading a CSV from Snowflake, computing confusion matrices, and inspecting charts. This needs to be wrapped into a callable `metric()` function.

### Practical First Steps

The lowest-risk entry point for DSPy:

1. **Few-shot selection** — Use DSPy's `BootstrapFewShot` to automatically select which examples from `scored_lead_examples.md` to inject into the prompt. Currently we use all 15; an optimizer might find that 5 specific examples outperform 15 random ones.

2. **Prompt instruction optimization** — Use `MIPROv2` to rewrite the scoring system prompt while holding the examples fixed. Compare the optimized prompt against the hand-tuned one using the existing confusion matrix metric.

3. **Multi-stage optimization** — Optimize query generation (Stage 2) and scoring (Stage 3) independently, then compose. Measure whether optimized queries find more true positives before scoring even runs.

---

## Risks & Failure Modes

### Scoring Risks

**Score compression / clustering.** The single most common failure. Without aggressive calibration instructions, the LLM gives 70%+ of leads the same score (typically 0.85), making the score useless for prioritization. The current prompt addresses this with explicit band definitions and "use fine-grained values" instructions, but it regresses whenever the prompt is modified.

*Detection:* Check standard deviation of scores in each run. If stdev < 0.08, scores are compressed. The analysis scripts flag this.

*Mitigation:* The rubric includes "Common Scoring Mistakes to Avoid" and the system prompt explicitly says "Do NOT cluster most candidates at the same score."

**Keyword-match inflation.** The leather-researcher-as-meat-protein-expert problem. LLMs are susceptible to surface-level keyword overlap — both domains involve "collagen," "acidic conditions," and "protease," so the model assigns high relevance despite completely different application domains.

*Detection:* Human review catches these. The FP analysis (`analysis/fp_analysis.py`) tracks "not relevant" rates by score bin — if high-scoring leads have unexpectedly high FP rates, keyword inflation is likely.

*Mitigation:* "Domain Specificity Checkpoint" as Rule #1 in the scoring prompt. Also, `evidence_snippets` from the discovery stage provide concrete facts from the source page, making it harder for the model to hallucinate relevance.

**TRL-domain interaction.** A lead can be a domain expert but a bad fit because the request needs TRL 5-9 and the lead is a pure academic. Or vice versa: a great commercializer with no domain expertise. The interaction between these two dimensions is the subtlest scoring challenge.

*Mitigation:* "Compound penalties stack" rule in the prompt. Pure academic + tangential domain = 0.35-0.50. Industry expert + exact domain = 0.80-0.90.

**Sparse data scoring.** Many leads have minimal context — just a name, title, and company. The scorer has to decide: score conservatively (penalize unknowns) or optimistically (assume relevance). Currently we score conservatively: "company seems relevant but we don't know much about the person" = 0.40-0.60.

*Risk:* This penalizes under-documented but genuinely excellent leads. A postdoc at a tiny lab with no web presence might be the world's expert on the exact technology, but with no bio to score from, they'll get a 0.45.

### Discovery Risks

**Exa search quality.** Semantic search is powerful but opaque. If the generated queries are subtly off, the results will be systematically biased. For example, "startup developing microbial crop protection" might miss companies that describe their work as "biological seed treatment" — same domain, different vocabulary.

*Mitigation:* Multiple search strategies (company, people, papers, academic). Adaptive refinement — if results have gaps, refine queries. But there's no ground truth for discovery recall (how would you know what you didn't find?).

**LinkedIn blocking.** Exa can't scrape LinkedIn content, so people search returns titles and URLs but no bios. This means LinkedIn-sourced leads have sparse context for scoring. The pipeline works around this by using the `people` category instead of trying to scrape profiles, but it's still a data quality gap.

**Query generation hallucination.** Claude generates the Exa queries. If the model misunderstands the request context, it generates queries for the wrong domain. For example, a request for "food-grade adhesive systems" might generate queries about medical adhesives or construction adhesives.

*Mitigation:* The query generation prompt includes "Out of Scope" context to steer away from known wrong directions. But novel misunderstandings aren't covered.

### Enrichment Risks

**n8n webhook reliability.** The enrichment waterfall depends on three external APIs (Amplemarket, Findymail, Apollo) orchestrated through n8n. Any one of them can timeout, rate-limit, or return stale data. The webhook has a 5-minute timeout per batch.

*Mitigation:* Leads that fail enrichment are marked `ENRICHMENT_FAILED` but don't block the pipeline. The output includes them without email. Academic enrichment runs as a fallback for researchers.

**Email accuracy.** The n8n waterfall returns the "best" email it can find, but this may be a general company inbox, an old address, or a wrong person at the same company. There's no verification step beyond the waterfall itself (Findymail does some validation).

**Domain resolution errors.** Mapping "FermentaBio" to "fermentabio.com" is straightforward. Mapping "University of Wisconsin-Madison" to the right email domain is harder. The domain resolver uses a 3-tier approach (cache → Exa search → heuristic), but heuristic-generated domains (e.g., company-name.com) are frequently wrong.

### Pipeline Risks

**Stage ordering lock-in.** The pipeline stages must run in a specific order because each stage depends on fields from earlier stages. This makes it hard to experiment with alternative orderings (e.g., "what if we enriched first, then scored with email context?").

**Single-run calibration.** The prompt was heavily calibrated on one request type (acidified meat protein systems, March 2026). Other request types — crop biologicals, sustainable packaging, AI/ML for food — may have different failure patterns that the current rubric doesn't address.

*Risk:* The rubric over-indexes on patterns specific to food/meat science and under-indexes on other verticals. A leather researcher is correctly filtered for meat requests, but analogous cross-domain traps in other verticals aren't anticipated.

**Dedup timing.** Halo dedup runs after enrichment (needs email to match). This means we spend enrichment credits on leads who turn out to already be Halo users. An early domain-level check during discovery could catch some of these, but email-level dedup is the only reliable method.

### Cost Risks

**Claude API costs scale with lead volume.** Every lead gets at least one Claude call for scoring. Discovery adds more Claude calls for query generation and result extraction. A run with 200+ leads can burn $5-15 in API credits.

**n8n enrichment costs are opaque.** The n8n waterfall fans out to Amplemarket, Findymail, and Apollo — each has its own pricing. A batch of 50 enrichments might cost $10-30 depending on which tools resolve the email.

**Exa API costs.** Exa charges per search call. The adaptive refinement loop can amplify costs — a run that triggers 2 refinement rounds makes 3x the Exa calls.

---

## Setup

### Environment Variables

Create a `.env` file:

```env
ANTHROPIC_API_KEY=sk-ant-...
EXA_API_KEY=...
N8N_ENRICHMENT_WEBHOOK_URL=https://haloscience.app.n8n.cloud/webhook/...
GOOGLE_SERVICE_ACCOUNT_JSON=google_service_account.json

SNOWFLAKE_ACCOUNT=...
SNOWFLAKE_USER=...
SNOWFLAKE_PASSWORD=...
SNOWFLAKE_WAREHOUSE=FIVETRAN_WAREHOUSE
SNOWFLAKE_DATABASE=FIVETRAN_DATABASE
SNOWFLAKE_SCHEMA=HEROKU_POSTGRES_PUBLIC
```

### Install

**Option A — Docker (recommended)**

```bash
cp config/.env.example .env  # fill in your values
cp /path/to/google_service_account.json .

docker build -t agent-scout .
docker run --env-file .env \
  -v $(pwd)/output:/app/output \
  -v $(pwd)/.scout_state:/app/.scout_state \
  -v $(pwd)/google_service_account.json:/app/google_service_account.json \
  agent-scout --help
```

**Option B — Local Python**

```bash
pip install -r requirements.txt
```

### Google Sheets Auth

Place a service account JSON file at the path specified by `GOOGLE_SERVICE_ACCOUNT_JSON`. The service account must have editor access to the input/output Google Sheets.

---

## Usage

```bash
# Type 2: Request + example leads from Google Sheet
python agent_scout.py --type request_with_examples \
    --input-sheet "https://docs.google.com/spreadsheets/d/..." \
    --sheet-tab "Scientists and Startups" \
    --request-id 1582 \
    --output-sheet "https://docs.google.com/spreadsheets/d/..."

# Type 3: Scraped list from CSV
python agent_scout.py --type scraped_list \
    --input-csv leads.csv \
    --request-looking-for "Researchers in precision fermentation" \
    --output-csv results.csv

# Type 4: Company list
python agent_scout.py --type company_list \
    --companies "Acme Corp,Initech,Globex" \
    --request-id 456

# Resume a failed run
python agent_scout.py --resume scout_20260304_143022
```

Pre-built runner scripts for specific requests are in `runners/`.

### Testing

```bash
pytest
```

Tests cover models, Claude client, fit scorer, and pipeline integration (`tests/`).

---

## Upgrade Roadmap

### Near-Term (Low Risk, High Impact)

**Close the feedback loop.** Human reviewer decisions (Approve/Reject/Maybe) from Google Sheets should be read back into the system to (a) expand the training set for DSPy, (b) flag scoring calibration drift, and (c) auto-generate new `scored_lead_examples.md` entries. This is the single highest-leverage improvement.

**Parallel Exa discovery.** The four search strategies currently run sequentially with 1-second rate-limit delays between calls. Running them in parallel (4 concurrent threads, each rate-limited independently) would cut discovery time by ~4x. This is a straightforward `ThreadPoolExecutor` change.

**Batch enrichment.** Current enrichment sends leads one-at-a-time through the n8n webhook. True batch support (send 10 leads, get 10 results) would reduce HTTP overhead and n8n execution time.

### Medium-Term (Moderate Risk, High Impact)

**DSPy few-shot optimization.** Use `BootstrapFewShot` to select the optimal subset of examples for few-shot injection. Requires wrapping the existing confusion matrix analysis into a callable metric function and building a training set from human-reviewed Google Sheets.

**Per-vertical rubric tuning.** The current rubric was calibrated on food/meat science. Different request verticals (crop science, packaging, AI/ML) likely have different failure patterns. Either maintain separate rubrics per vertical or use DSPy to learn vertical-specific scoring instructions.

**Score confidence / uncertainty.** The scorer returns a point estimate (0.72). Adding a confidence signal ("I'm confident this is a 0.72" vs. "I'm guessing, sparse data") would help human reviewers prioritize their review time. Could implement via multiple scoring runs + variance, or by asking the model to self-assess confidence.

**Discovery recall measurement.** We currently have no way to measure what we're missing. Building a holdout set (leads found by humans but not by Agent Scout) would let us measure discovery recall and identify systematic blind spots in the Exa query generation.

### Long-Term (Higher Risk, Transformative)

**End-to-end DSPy optimization.** Optimize the full pipeline — query generation, result extraction, and scoring — against proposal conversion as the end metric. This requires significantly more labeled data and careful metric design, but could produce a system that self-improves with each batch of human reviews.

**Active learning loop.** Instead of scoring all leads and having humans review the top N, use uncertainty sampling: score all leads, identify the ones where the scorer is least confident, route those specifically for human review, and use the labels to improve the model. This maximizes the information value of each human review.

**Multi-model scoring ensemble.** Use multiple LLMs (Claude, GPT-4, Gemini) as independent judges and aggregate their scores. Disagreements between models flag leads that need human attention. More expensive per lead but potentially more robust than any single model.

**Real-time request monitoring.** Instead of running Agent Scout as a batch job per request, run it continuously: monitor new requests as they're created, auto-discover leads, and present pre-scored candidates to the Halo team before they start manual scouting.
