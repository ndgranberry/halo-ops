# RoboScout — Upgrade Notes

## Changelog — 2026-04-17 review pass

Refactor round driven by the in-depth review in chat. All changes are
backward-compatible at the CLI level. Summary:

### New modules
- **`config.py`** — single source of truth for env loading + tunables.
  `load_env()`, `require()`, `validate_for(["llm","snowflake","sheets"])`,
  and a frozen `settings` dataclass. Replaces the 9-line `.env` loader
  that was duplicated in two entry points.
- **`logging_setup.py`** — `configure_logging()` with optional JSON
  output (`ROBOSCOUT_LOG_JSON=1`) and run-ID correlation. `new_run_id()`
  generates a per-batch ID; `export_run_id_to_env()` propagates it to
  subprocesses so logs can be traced across process boundaries.
- **`tests/`** — pytest suite with four test files. Run `pytest` from
  repo root.

### Behavior changes
- **Silent subprocess timeout is fixed.** `run_daily.run_pipeline` now
  writes `logs/stdout_<ID>_FAILED.json` on any non-success path
  (timeout, nonzero exit, crash, bad JSON). Previously a timeout only
  left a log line and the request was invisibly dropped.
- **Top-level exception handler** in `run_daily.main()` — any unhandled
  exception now logs a traceback before exit. Investigated reason for
  the 2026-04-16 batch dying after #1682.
- **Semantic Scholar sentinel fixed.** `semantic_scholar.py` now exposes
  `S2Result` with an explicit `status` enum (`OK / RATE_LIMITED /
  HTTP_ERROR / NETWORK_ERROR / TIMEOUT`). The old `-1` sentinel is
  preserved as a back-compat shim but new callers use `search_relevance()`
  and `search_bulk()` directly. `modules.py` now records the S2 failure
  reason on the query so unvalidated queries carry context into the
  output.
- **Webhook retry** with exponential backoff for both Slack and n8n
  (`_post_with_retry` helper). Max retries configurable via
  `ROBOSCOUT_WEBHOOK_MAX_RETRIES`.
- **Sheets dedup.** Before appending rows for a request, existing rows
  with the same request_id are deleted (Queries, Coverage, Run Metadata).
  Disable with `ROBOSCOUT_SHEETS_DEDUP=false`.
- **Env validation.** `roboscout_query_gen.py` and `run_daily.py` fail
  fast at startup with a list of ALL missing required env vars, instead
  of dying deep inside an API call.

### Tunables moved to env
All defaults preserved — set only if you need to override.

| Var | Default | Effect |
|---|---|---|
| `ROBOSCOUT_MODEL` | `claude-sonnet-4-20250514` | LLM model id |
| `ROBOSCOUT_LM_TEMPERATURE` | `0.3` | DSPy LM temperature |
| `ROBOSCOUT_LM_MAX_TOKENS` | `4096` | DSPy LM max_tokens |
| `ROBOSCOUT_MAX_REFINEMENT_ROUNDS` | `2` | Query refinement rounds |
| `ROBOSCOUT_RELEVANCE_THRESHOLD` | `0.6` | Min relevance ratio |
| `ROBOSCOUT_PAPERS_TO_CHECK` | `20` | Top-N papers for relevance |
| `ROBOSCOUT_PER_REQUEST_TIMEOUT` | `1800` | Per-request timeout (s) |
| `ROBOSCOUT_FIND_NEW_TIMEOUT` | `120` | --find-new timeout (s) |
| `ROBOSCOUT_WEBHOOK_MAX_RETRIES` | `3` | Slack/n8n retries |
| `ROBOSCOUT_WEBHOOK_BACKOFF` | `2.0` | Initial backoff seconds |
| `ROBOSCOUT_EXCLUDED_COMPANY_IDS` | `2825,1669` | Companies skipped in auto-discovery (previously hardcoded) |
| `ROBOSCOUT_LOG_JSON` | unset | Emit JSON log lines |
| `ROBOSCOUT_LOG_LEVEL` | `INFO` | Root log level |
| `ROBOSCOUT_SHEETS_DEDUP` | `true` | Delete stale request rows before append |

---

## Python 3.12 migration (pending)

Current runtime is Python 3.9.6 which went EOL on 2025-10-07 and now
only receives critical security fixes. google-auth already emits a
FutureWarning about this on every run.

### Migration path

1. `brew install python@3.12` (or equivalent)
2. Recreate the venv:
   ```bash
   rm -rf .venv
   /opt/homebrew/bin/python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install pytest ruff
   ```
3. Update the launchd plist / cron / run_daily entry point to use the
   new interpreter path.
4. Run `pytest` — all existing tests should pass unchanged.
5. Run `ruff check .` and fix any new warnings (`target-version` is
   already set to `py39` so the ruleset is conservative — bump to
   `py312` after the switch to unlock the full pyupgrade ruleset).

### Compat known-good

- `dspy >= 2.6` — supports 3.9–3.13
- `anthropic >= 0.40` — supports 3.9–3.13
- `snowflake-connector-python >= 3.0` — supports 3.9–3.12
- `gspread >= 5.12` — supports 3.8–3.13

No code changes are required to import on 3.12; the EOL warnings will
simply stop.

---

## Deferred / tracked

Items from the review that were **not** addressed in this pass:

- **Concurrent batch execution.** run_daily still processes requests
  serially. Needs rate-limit awareness for S2/Anthropic before
  parallelizing.
- **Per-stage timing metrics.** `logging_setup.timed_stage` exists but
  is not yet wired through the pipeline stages. Wrap each stage in
  `modules.RoboScoutPipeline.forward` when ready.
- **Dead code in `_legacy/`.** Still present; decide whether to delete
  or keep as rollback reference.
- **Lock file.** `requirements.txt` still uses `>=` bounds. Add
  `uv pip compile` or `pip-tools` when bumping deps.
- **GEPA optimization scheduling.** Hooks exist; no cron/trigger is set.
