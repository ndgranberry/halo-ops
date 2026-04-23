#!/usr/bin/env python3
"""
RoboScout Query Generator — Main Orchestrator
===============================================
Generates, validates, and outputs Semantic Scholar queries for RoboScout.

Usage:
    # From Snowflake request
    python roboscout_query_gen.py --request-id 456 --output-csv queries.csv

    # Manual input
    python roboscout_query_gen.py \
        --looking-for "Researchers in precision fermentation of dairy proteins" \
        --use-case "Replace animal-derived casein in cheese applications" \
        --sois "Microbial strain engineering, bioprocess optimization" \
        --output-csv queries.csv

    # Output to Google Sheet
    python roboscout_query_gen.py --request-id 456 \
        --output-sheet "https://docs.google.com/spreadsheets/d/..."
"""

import argparse
import logging
import sys
from datetime import datetime

# Centralized env loading + settings (see config.py).
from config import ConfigError, load_env, settings, validate_for

load_env()

from dspy_config import configure_lm, load_active_prompt
from logging_setup import (
    configure_logging,
    current_run_id,
    inherit_run_id_from_env,
    new_run_id,
    set_run_id,
)
from models_roboscout import QueryRequest, QueryRun
from modules import RoboScoutPipeline
from output_formatter import OutputFormatter
from request_loader import RequestLoader
from semantic_scholar import SemanticScholarClient

configure_logging()
inherit_run_id_from_env()
logger = logging.getLogger("roboscout_query_gen")


class RoboScoutQueryGen:
    """Main pipeline orchestrator."""

    def __init__(self, model: str = None):
        self.model = model or settings.default_model
        configure_lm(model=f"anthropic/{self.model}")
        self.s2_client = SemanticScholarClient()
        self.pipeline = RoboScoutPipeline(self.s2_client)
        self.prompt_version = load_active_prompt(self.pipeline)
        self.formatter = OutputFormatter()

    def run(
        self,
        request: QueryRequest,
        output_csv: str = None,
        output_sheet: str = None,
        output_json: str = None,
    ) -> QueryRun:
        """Run the full pipeline."""
        # Reuse an inherited run_id (from run_daily batch) or mint one.
        rid = current_run_id()
        if not rid:
            rid = new_run_id()
            set_run_id(rid)
        run = QueryRun(
            run_id=rid,
            request=request,
            started_at=datetime.now().isoformat(),
            model_used=self.model,
        )

        logger.info(f"Prompt version: {self.prompt_version}")

        # Run the DSPy pipeline (generate → validate → coverage → recovery)
        queries, expanded_sois, soi_coverage = self.pipeline(request)

        run.queries = queries
        run.soi_coverage = soi_coverage

        if not queries:
            logger.error("No queries generated. Check API key and request content.")
            return run

        # Stage 4: Output
        logger.info("\n=== Stage 4: Writing output ===")
        run.completed_at = datetime.now().isoformat()

        wrote_any = False
        if output_json:
            self.formatter.write_json(run, output_json)
            wrote_any = True
        if output_csv:
            self.formatter.write_csv(run, output_csv)
            wrote_any = True
        if output_sheet:
            self.formatter.write_sheets(run, output_sheet)
            wrote_any = True
        if not wrote_any:
            default_path = f"{run.run_id}_queries.csv"
            self.formatter.write_csv(run, default_path)

        # Summary
        stats = run.stats
        logger.info(f"\n{'='*50}")
        logger.info("Pipeline complete!")
        logger.info(f"  Valid queries: {stats['valid']}/{stats['total_generated']}")
        if stats['unvalidated']:
            logger.info(f"  Unvalidated (S2 timeout): {stats['unvalidated']}")
        logger.info(f"  Rejected (too broad): {stats['rejected_too_broad']}")
        logger.info(f"  Rejected (irrelevant): {stats['rejected_irrelevant']}")
        logger.info(f"  SOIs covered: {stats['sois_covered']}/{stats['sois_total']}")
        logger.info(f"  Categories: {stats['by_category']}")

        return run


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="RoboScout Query Generator — Generate Semantic Scholar queries for partnering requests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Input: Snowflake
    parser.add_argument("--request-id", type=int, help="Snowflake request ID")
    parser.add_argument("--sso", action="store_true", help="Use SSO for Snowflake auth")
    parser.add_argument(
        "--find-new",
        action="store_true",
        help="Query Snowflake for requests launched in last 24h (enabled + complete). "
             "Outputs JSON list of request IDs. Use with --output-json.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Lookback window for --find-new (default: 24 hours)",
    )

    # Input: Manual
    parser.add_argument("--looking-for", help="What the partner is looking for")
    parser.add_argument("--use-case", help="Use case description")
    parser.add_argument("--sois", help="Solutions of interest (comma-separated)")
    parser.add_argument("--title", help="Request title (optional for manual input)")
    parser.add_argument("--requirements", help="Requirements text")
    parser.add_argument("--out-of-scope", help="Out of scope items")

    # Output
    parser.add_argument("--output-csv", help="Path for output CSV")
    parser.add_argument("--output-sheet", help="Google Sheet URL for output")
    parser.add_argument(
        "--output-json",
        nargs="?",
        const="-",
        help="Output JSON (to file path or stdout if no path given). "
             "When outputting to stdout, logs go to stderr.",
    )

    # Settings
    parser.add_argument(
        "--model",
        default=settings.default_model,
        help=f"Claude model to use (default: {settings.default_model})",
    )

    args = parser.parse_args()

    # If JSON output goes to stdout, redirect all logging to stderr
    # so n8n Execute Command gets clean JSON on stdout
    if args.output_json and (args.output_json == "-" or not args.output_json):
        configure_logging(to_stderr=True, force=True)

    # Fast-fail on missing env *before* we do any expensive work.
    needed = []
    if args.find_new or args.request_id:
        needed.append("snowflake")
    if not args.find_new:
        needed.append("llm")
    if args.output_sheet:
        needed.append("sheets")
    try:
        if needed:
            validate_for(needed)
    except ConfigError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(2)

    # --find-new mode: just query Snowflake and output IDs, then exit
    if args.find_new:
        import json as _json
        loader = RequestLoader(use_sso=args.sso)
        try:
            new_requests = loader.find_new_requests(hours=args.hours)
            result = {
                "request_ids": [r["id"] for r in new_requests],
                "requests": new_requests,
                "count": len(new_requests),
                "hours_lookback": args.hours,
            }
            if args.output_json:
                _json.dump(result, sys.stdout, indent=2, default=str)
                sys.stdout.write("\n")
            else:
                for r in new_requests:
                    logger.info(f"  #{r['id']}: {r['title']} ({r['company']})")
                logger.info(f"Total: {len(new_requests)} new requests")
        finally:
            loader.close()
        return

    # Validate input
    if not args.request_id and not args.looking_for:
        parser.error("Either --request-id or --looking-for is required")

    # Load request
    loader = RequestLoader(use_sso=args.sso)
    try:
        if args.request_id:
            request = loader.load_from_snowflake(args.request_id)
        else:
            request = loader.load_from_args(
                looking_for=args.looking_for or "",
                use_case=args.use_case or "",
                sois=args.sois or "",
                title=args.title or "",
                requirements=args.requirements or "",
                out_of_scope=args.out_of_scope or "",
            )
    finally:
        loader.close()

    # Run pipeline
    pipeline = RoboScoutQueryGen(model=args.model)

    try:
        pipeline.run(
            request=request,
            output_csv=args.output_csv,
            output_sheet=args.output_sheet,
            output_json=args.output_json,
        )
    except KeyboardInterrupt:
        logger.info("\nInterrupted.")
        sys.exit(1)
    except Exception:
        logger.exception("Pipeline failed")
        raise


if __name__ == "__main__":
    main()
