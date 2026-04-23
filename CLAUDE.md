# Halo Ops — Claude Code Guide

Two tools running on a Hetzner server, both triggered via HTTP from Claude Code.

- **Agent Scout** — finds, scores, and enriches contacts for partnering requests
- **RoboScout Query Gen** — generates Semantic Scholar search queries for RoboScout

## Server

**Base URL:** `http://46.224.159.126:8000`

Check health: `curl http://46.224.159.126:8000/health`

### Deployment endpoints

```bash
# Check what commit the server is running
curl http://46.224.159.126:8000/version

# Force a git pull (after pushing to GitHub)
curl -X POST http://46.224.159.126:8000/pull
```

The server also auto-pulls every 5 minutes via cron, so `/pull` is only needed for urgent changes.

### Operational endpoints

```bash
# Inspect server config — shows git commit, which models are configured,
# which API keys are present (true/false, not values)
curl -s http://46.224.159.126:8000/config

# List running scout/roboscout processes
curl -s http://46.224.159.126:8000/processes

# Check if a specific PID is still running
curl -s http://46.224.159.126:8000/status/3247

# Kill a specific process
curl -X POST http://46.224.159.126:8000/kill/3247

# Kill all running scout/roboscout jobs
curl -X POST http://46.224.159.126:8000/kill-all

# Restart the FastAPI service (picks up server.py changes)
curl -X POST http://46.224.159.126:8000/restart

# Clear all old log files
curl -X DELETE http://46.224.159.126:8000/logs
```

### Output files

```bash
# List recent output CSVs
curl -s http://46.224.159.126:8000/output

# Download a specific output file
curl -s http://46.224.159.126:8000/output/metallic_flake_test.csv -o result.csv
```

---

## RoboScout Query Gen

### Trigger a run

```bash
curl -s -X POST http://46.224.159.126:8000/run-roboscout \
  -H "Content-Type: application/json" \
  -d '{"request_id": 1664, "output_csv": "/tmp/roboscout_1664.csv"}'
```

### Manual input (no Snowflake ID)

```bash
curl -s -X POST http://46.224.159.126:8000/run-roboscout \
  -H "Content-Type: application/json" \
  -d '{
    "looking_for": "Researchers in precision fermentation of dairy proteins",
    "use_case": "Replace animal-derived casein in cheese applications",
    "sois": "Microbial strain engineering, bioprocess optimization",
    "output_csv": "/tmp/test.csv"
  }'
```

### Check progress

```bash
# List all runs
curl -s http://46.224.159.126:8000/logs

# Tail last 50 lines of a specific run
curl -s http://46.224.159.126:8000/logs/roboscout_1664.log

# Tail last 100 lines
curl -s "http://46.224.159.126:8000/logs/roboscout_1664.log?lines=100"
```

---

## Agent Scout

### Triggering a Run

Send a POST to `/run`. The server starts the job in the background and returns a `pid` immediately — the job keeps running after the curl exits.

### By Snowflake request ID (most common)

```bash
curl -s -X POST http://46.224.159.126:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "type": "request_with_examples",
    "request_id": 1582,
    "input_sheet": "https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID",
    "sheet_tab": "Scientists and Startups",
    "output_sheet": "https://docs.google.com/spreadsheets/d/YOUR_OUTPUT_SHEET_ID"
  }'
```

### By description (no Snowflake ID)

```bash
curl -s -X POST http://46.224.159.126:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "type": "partnering_request",
    "request_looking_for": "Researchers in precision fermentation of dairy proteins",
    "request_use_case": "Replace animal-derived casein in cheese applications",
    "request_sois": "Microbial strain engineering, bioprocess optimization",
    "output_sheet": "https://docs.google.com/spreadsheets/d/YOUR_OUTPUT_SHEET_ID"
  }'
```

### By company list

```bash
curl -s -X POST http://46.224.159.126:8000/run \
  -H "Content-Type: application/json" \
  -d '{
    "type": "company_list",
    "companies": "Novozymes,Ginkgo Bioworks,Zymergen",
    "request_looking_for": "Bioprocess engineers",
    "output_sheet": "https://docs.google.com/spreadsheets/d/YOUR_OUTPUT_SHEET_ID"
  }'
```

### Resume a failed run

```bash
curl -s -X POST http://46.224.159.126:8000/run \
  -H "Content-Type: application/json" \
  -d '{"type": "partnering_request", "resume": "scout_20260421_093022"}'
```

## Input Types

| type | When to use |
|------|-------------|
| `partnering_request` | You have a request description but no example leads |
| `request_with_examples` | Google Sheet with example leads + request context |
| `scraped_list` | CSV of pre-scraped leads to score and enrich |
| `company_list` | List of companies — agent discovers contacts at each |

## Key Parameters

| Parameter | Description |
|-----------|-------------|
| `request_id` | Snowflake request ID — pulls all context automatically |
| `request_looking_for` | Plain text description of who they want |
| `request_use_case` | What the partner wants to do |
| `request_sois` | Solutions of interest (comma-separated) |
| `input_sheet` | Google Sheet URL with example leads |
| `sheet_tab` | Tab name in the input sheet |
| `output_sheet` | Google Sheet URL to write results to |
| `min_score` | Minimum fit score to include (default 0.3, range 0-1) |

## Output

Results are written to the specified Google Sheet with columns: name, title, company, email, LinkedIn, fit score, score rationale, enrichment status.

Run state is saved in `.scout_state/` on the server — if a run fails partway through, resume it with the `run_id` from the response.

### Check Agent Scout progress

```bash
curl -s http://46.224.159.126:8000/logs/scout_1582.log
```
