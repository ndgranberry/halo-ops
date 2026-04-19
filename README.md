# RoboScout Query Generator

Automates the work human scouts do when writing Semantic Scholar search queries for RoboScout. Takes a partnering request (from Snowflake or CLI), uses Claude to generate queries, validates them live against the Semantic Scholar API, refines any that fail, and outputs the final set.

## What This Agent Does

RoboScout Query Generator is an AI agent that reads a corporate R&D partnering request and produces search queries for Semantic Scholar. Those queries are then used by the broader RoboScout system to find researchers whose publications match the request.

The agent:
1. Reads the partnering request (title, looking-for, use case, solutions of interest)
2. Analyzes the request and identifies specific technologies, compounds, and techniques
3. Generates 8-15 search queries targeting different Solutions of Interest (SOIs)
4. Validates each query against the Semantic Scholar API (checks result count and relevance)
5. Refines queries that are too broad or irrelevant (up to 2 rounds)
6. Recovers coverage for any SOIs that lost all their queries during validation
7. Outputs valid queries to Google Sheets (or CSV/JSON)

## How It Works

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌─────────────┐
│  1. LOAD         │ ──► │  2. GENERATE      │ ──► │  3. VALIDATE      │ ──► │  4. OUTPUT   │
│  Request from    │     │  Claude analyzes  │     │  Each query gets  │     │  CSV or      │
│  Snowflake or    │     │  request, expands │     │  checked against  │     │  Google      │
│  CLI args        │     │  SOIs, generates  │     │  Semantic Scholar │     │  Sheets      │
│                  │     │  8-15 queries     │     │  for count +      │     │              │
│                  │     │                   │     │  relevance        │     │              │
└─────────────────┘     └──────────────────┘     └──────────────────┘     └─────────────┘
```

## How to Use It

### Automatic Mode (Daily)

The agent runs daily via a macOS LaunchAgent. It:
1. Checks Snowflake for new partnering requests from the last 24 hours
2. Runs the query generation pipeline for each new request
3. Appends results to the Google Sheet
4. Sends a summary Slack notification

No action needed — just monitor the Google Sheet and Slack.

### Manual Mode

To run the agent for a specific request:

```bash
# From Snowflake
python3 roboscout_query_gen.py --request-id 1597 --output-csv queries.csv

# Manual input
python3 roboscout_query_gen.py \
    --looking-for "Researchers in precision fermentation of dairy proteins" \
    --use-case "Replace animal-derived casein in cheese applications" \
    --sois "Microbial strain engineering, bioprocess optimization" \
    --output-csv queries.csv

# Output to Google Sheets
python3 roboscout_query_gen.py --request-id 1597 \
    --output-sheet "https://docs.google.com/spreadsheets/d/..."
```

To run manually for multiple requests:

```bash
python3 run_daily.py --request-ids 1597 1600 1582
```

## Monitoring Your Agent

All monitoring happens through Google Sheets and Slack — no code needed.

### Google Sheets Tabs

| Tab | What It Shows | Who Writes |
|-----|--------------|-----------|
| **Queries** | All generated queries per request (query text, result count, SOI, status) | Agent |
| **Coverage** | Which SOIs are covered per request, best query per SOI | Agent |
| **Run Metadata** | When each run happened, model used, success/failure stats | Agent |
| **Performance Trends** | Quality metrics over time: valid rate, coverage rate, specificity mix | Agent |
| **Feedback** | Your quality ratings for individual queries (dropdowns + notes) | You |

### Performance Trends Tab

This tab auto-populates after every run. Key columns:

- **Valid Rate**: What percentage of generated queries passed validation. Target: above 60%.
- **Coverage Rate**: What percentage of SOIs have at least one valid query. Target: above 70%.
- **Specificity Distribution**: How many queries fall into each range (highly specific, specific, moderate, general). A healthy mix is best.
- **Refinement Rate**: How many queries needed refinement. High refinement rates may indicate the generation prompt needs improvement.
- **Prompt Version**: Which prompt version was used (baseline or optimized).

### Slack Notifications

| Notification | When | What It Means |
|-------------|------|--------------|
| Daily summary | After each daily run | How many requests were processed, success/fail counts |
| Quality alert | Valid rate <60% or coverage <70% over 7 days | Agent quality has degraded — review recent runs |
| Health alert | API key invalid, S2 unreachable, etc. | Something is broken — may need technical help |
| Optimization complete | After weekly GEPA run | New prompt candidate available for your approval |

## Improving Your Agent

The agent improves over time through your feedback. Here's how the cycle works:

### Step 1: Give Feedback

Open the **Feedback** tab in the Google Sheet. For each query, use the dropdown in the **Rating** column:
- **good** — This query is well-targeted and would find relevant researchers
- **bad** — This query is too broad, too narrow, or off-topic
- **wrong SOI** — The query doesn't match the SOI it claims to target

Add notes in the **Notes** column explaining what's wrong (e.g., "this query is about polymer chemistry, not food science").

### Step 2: Automatic Optimization

Every week, an automated script:
1. Reads your feedback from the Feedback tab
2. Uses it to evolve the agent's prompts using GEPA (a prompt optimization algorithm)
3. Saves a candidate prompt and notifies you via Slack
4. The candidate prompt is NOT used automatically — you must approve it first

### Step 3: Approve or Reject

When you get a Slack notification about a new prompt candidate:
1. Open the Feedback tab
2. Find the row with `__PROMPT_CANDIDATE__` in the Query column
3. Type `approved` or `rejected` in the Notes column
4. The next daily run will use the approved prompt

### What Happens If You Don't Give Feedback

The agent still works fine with its default prompts. Feedback just makes it better over time. There's no penalty for not reviewing every query — even occasional feedback helps.

## Troubleshooting

| Problem | Likely Cause | What to Do |
|---------|-------------|-----------|
| "No queries generated" | Anthropic API key expired or invalid | Check `config/.env` has a valid `ANTHROPIC_API_KEY` |
| All queries too broad | Request is very vague or generic | The agent may need more specific SOIs — consider adding to the request |
| SOI not covered | Recovery queries tried but Semantic Scholar may not have relevant papers | Some niche SOIs just don't have academic coverage |
| "S2 API unreachable" | Semantic Scholar rate limits | Get a free API key at semanticscholar.org/product/api |
| Slack notifications stopped | Webhook URL expired | Check `SLACK_WEBHOOK_URL` in config/.env |
| Quality degraded over time | Prompt drift or changing request types | Give feedback in the Feedback tab to trigger optimization |

For technical issues, contact the engineering team.

## Configuration

All secrets go in `config/.env`:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Snowflake (required for --request-id)
SNOWFLAKE_ACCOUNT=HCXAKRI-TJB53055
SNOWFLAKE_USER=NEIL
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=FIVETRAN_WAREHOUSE
SNOWFLAKE_DATABASE=FIVETRAN_DATABASE
SNOWFLAKE_SCHEMA=HEROKU_POSTGRES_PUBLIC

# Optional but strongly recommended — 10x faster validation
SEMANTIC_SCHOLAR_API_KEY=your_key

# Optional — for Google Sheets output
GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/creds.json

# Optional — for Slack notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Optional — Google Sheet URL for daily runner
ROBOSCOUT_SHEET_URL=https://docs.google.com/spreadsheets/d/...
```

## File Structure

```
roboscout_query_gen/
├── roboscout_query_gen.py   # Main orchestrator + CLI entry point
├── models.py                # Data models (QueryRequest, GeneratedQuery, QueryRun)
├── signatures.py            # DSPy Signatures + Pydantic output models
├── modules.py               # DSPy Modules (generation, validation, pipeline)
├── dspy_config.py           # DSPy language model configuration
├── request_loader.py        # Snowflake + CLI input loading
├── semantic_scholar.py      # Semantic Scholar API client
├── output_formatter.py      # CSV + Google Sheets output
├── run_daily.py             # Daily runner with monitoring hooks
├── context/
│   └── query_generation_guide.md   # Scout guide injected into prompts
├── config/
│   ├── .env                 # Your secrets (git-ignored)
│   └── .env.example         # Template
├── monitoring/
│   ├── metrics_tracker.py   # Performance metrics → Google Sheet
│   ├── feedback_sheet.py    # Feedback tab management + ingestion
│   └── health_check.py      # API/service health checks
├── optimization/
│   ├── metrics.py           # GEPA feedback metric
│   ├── build_examples.py    # Training data builder
│   ├── optimize.py          # GEPA optimizer runner
│   ├── auto_optimize.py     # Automated weekly optimization
│   ├── prompts/             # Versioned prompt files
│   │   ├── active.json      # Currently active optimized prompt
│   │   └── candidate.json   # Pending approval
│   ├── training_data/       # Feedback + curated examples
│   └── logs/                # Optimization run logs
└── _legacy/                 # Pre-DSPy files (kept for rollback)
```

## Tuning the Agent (Advanced)

### The Scout Guide (`context/query_generation_guide.md`)

This is the reference document injected into every query generation call. It defines the rules for query crafting and classification thresholds. Edit this file to:

- Add new rules you discover through testing
- Change classification thresholds
- Add domain-specific guidance

Changes take effect immediately on the next run.

### Validation Parameters

Constants in `modules.py` (`QueryValidationModule`):

```python
MAX_REFINEMENT_ROUNDS = 2      # How many times to retry a failing query
RELEVANCE_THRESHOLD = 0.6      # 60% of top results must be relevant
PAPERS_TO_CHECK = 20           # How many S2 results to spot-check
RETRY_WAIT_SECONDS = 30        # Wait time between S2 retry passes
MAX_RETRY_PASSES = 2           # How many times to retry S2-failed queries
```

### Manual GEPA Optimization

To trigger prompt optimization manually:

```bash
python3 run_daily.py --optimize
```

Or with more control:

```bash
python3 -m optimization.optimize --budget medium
```

Budget options: `light` (fast, cheap), `medium` (balanced), `heavy` (thorough, expensive).

### Rolling Back Prompts

If an optimized prompt performs worse:

```bash
python3 -m optimization.optimize --rollback
```

This removes the active optimized prompt and reverts to the baseline DSPy signatures.

## Semantic Scholar API

The tool uses two S2 endpoints:
- `/paper/search/bulk` — for getting total result counts (fast, cheap)
- `/paper/search` — for getting relevance-ranked top papers with abstracts

Without an API key, S2 shares a rate limit pool across all unauthenticated users, causing frequent 429 errors. **Get a free API key** at https://www.semanticscholar.org/product/api.
