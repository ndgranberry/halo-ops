#!/usr/bin/env python3
"""
Agent Scout — Main Orchestrator
=================================
Runs the full scouting pipeline: parse → discover → score → filter → enrich → output.

Usage:
    # Type 2: Request + examples from Google Sheet
    python agent_scout.py --type request_with_examples \
        --input-sheet "https://docs.google.com/spreadsheets/d/..." \
        --sheet-tab "Scientists and Startups" \
        --request-id 1582 \
        --output-sheet "https://docs.google.com/spreadsheets/d/..."

    # Type 3: Scraped list (CSV)
    python agent_scout.py --type scraped_list --input-csv leads.csv \
        --request-looking-for "Researchers in precision fermentation" \
        --output-csv results.csv

    # Type 3: Scraped list (Google Sheet)
    python agent_scout.py --type scraped_list \
        --input-sheet "https://docs.google.com/spreadsheets/d/..." \
        --request-id 456 \
        --output-sheet "https://docs.google.com/spreadsheets/d/..."

    # Type 4: Company list
    python agent_scout.py --type company_list \
        --companies "Acme Corp,Initech,Globex" \
        --request-id 456

    # Resume a failed run
    python agent_scout.py --resume scout_20260304_143022
"""

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from models_scout import ScoutLead, ScoutConfig, ScoutRun, InputType, LeadStatus, deduplicate_leads
from input_parser import InputParser
from person_discovery import PersonDiscovery
from exa_discovery import ExaDiscovery
from enrichment import ContactEnricher
from academic_enrichment import AcademicEnricher
from fit_scorer import FitScorer
from output_formatter import OutputFormatter

# Load .env if present
load_dotenv(override=True)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agent_scout")

# State directory for resume capability
STATE_DIR = Path(__file__).parent / ".scout_state"
STATE_DIR.mkdir(exist_ok=True)


class AgentScout:
    """Main pipeline orchestrator."""

    def __init__(self, config: ScoutConfig):
        self.config = config
        self.run = ScoutRun(
            run_id=f"scout_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            config=config,
            started_at=datetime.now().isoformat(),
        )

    def _preflight_checks(self) -> None:
        """Validate API keys and connections before running the pipeline."""
        errors = []

        # Claude API key
        if not os.getenv("ANTHROPIC_API_KEY"):
            errors.append("ANTHROPIC_API_KEY not set")

        # Exa API key (needed for discovery in types 1, 2, 4)
        if self.config.input_type != InputType.SCRAPED_LIST and not os.getenv("EXA_API_KEY"):
            errors.append("EXA_API_KEY not set (required for discovery)")

        # n8n enrichment webhook
        if not self.config.n8n_enrichment_webhook_url:
            logger.warning("Preflight: No n8n enrichment webhook configured — enrichment will be limited to academic sources")

        # Snowflake (needed for request lookup and Halo dedup)
        if self.config.request_id:
            required_sf = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"]
            missing_sf = [k for k in required_sf if not os.getenv(k)]
            if missing_sf:
                errors.append(f"Snowflake credentials missing: {', '.join(missing_sf)}")

        if errors:
            for e in errors:
                logger.error(f"Preflight FAIL: {e}")
            raise EnvironmentError(f"Preflight checks failed: {'; '.join(errors)}")

        logger.info("Preflight checks passed")

    def execute(self) -> str:
        """
        Run the full pipeline. Returns output URL/path.

        Stages:
        0. preflight — Validate API keys and connections
        1. parse — Normalize inputs
        2. discover — Find people (types 1, 2, 4 only)
        2b. requester_screen — Drop leads from the company that posted the request
        3. score — LLM-as-judge fit scoring (before enrichment to avoid wasting API calls)
        4. filter — Drop leads below threshold before expensive enrichment
        5. enrich — Contact enrichment via n8n (only above-threshold leads)
        6. halo_dedup — Remove leads already on Halo
        7. output — Write to Google Sheets or CSV
        """
        self._preflight_checks()

        stages = [
            ("parse", self._run_parse),
            ("solve_plan", self._run_solve_plan),
            ("discover", self._run_discover),
            ("requester_screen", self._run_requester_screen),
            ("score", self._run_score),
            ("expand", self._run_expand_loop),
            ("filter", self._run_filter),
            ("enrich", self._run_enrich),
            ("halo_dedup", self._run_halo_dedup),
            ("output", self._run_output),
        ]

        for stage_name, stage_fn in stages:
            if stage_name in self.run.stages_completed:
                logger.info(f"Skipping stage '{stage_name}' (already completed)")
                continue

            logger.info(f"=== Stage: {stage_name} ===")
            self.run.stage = stage_name
            self._save_state()

            try:
                result = stage_fn()
                self.run.stages_completed.append(stage_name)
                self._save_state()
            except Exception as e:
                logger.error(f"Stage '{stage_name}' failed: {e}")
                self._save_state()
                raise

        self.run.completed_at = datetime.now().isoformat()
        self._save_state()

        total = len(self.run.leads)
        above = sum(1 for l in self.run.leads if l.fit_score and l.fit_score >= self.config.min_fit_score)
        errored = sum(1 for l in self.run.leads if l.scoring_error)
        msg = f"\nPipeline complete! {above}/{total} leads above threshold ({self.config.min_fit_score})"
        if errored:
            msg += f" | {errored} scoring errors require review"
        logger.info(msg)

        return result

    # =========================================================================
    # Pipeline Stages
    # =========================================================================

    def _run_parse(self) -> None:
        """Stage 1: Parse and normalize inputs."""
        parser = InputParser(self.config)
        self.config, self.run.leads = parser.parse()
        self.run.config = self.config
        logger.info(f"Parsed {len(self.run.leads)} initial leads")

    def _run_solve_plan(self) -> None:
        """Stage 1b (new): Produce a structured solve plan before queries.

        Decomposes the request into 20-30 distinct angles so downstream Exa
        discovery can run an angle-based multi-track pack instead of a
        single narrow query generation. Skipped for scraped/company-list
        inputs or when disabled in config.
        """
        if not self.config.enable_solve_planner:
            logger.info("Solve planner: disabled in config — skipping")
            return
        if self.config.input_type in (InputType.SCRAPED_LIST, InputType.COMPANY_LIST):
            logger.info(f"Solve planner: skipped for input_type={self.config.input_type.value}")
            return
        if not self.config.request_title and not self.config.request_looking_for:
            logger.info("Solve planner: no request context to plan from — skipping")
            return

        from solve_planner import SolvePlanner
        planner = SolvePlanner(self.config)
        plan = planner.plan()
        if not plan:
            logger.warning("Solve planner returned nothing — pipeline will fall back to query-generation path")
            return
        self.run.solve_plan = plan
        angles = plan.get("angles") or []
        self.run.metrics["solve_plan_angle_count"] = len(angles)

    def _run_discover(self) -> None:
        """Stage 2: Find people at companies (skipped for scraped lists).

        When a solve plan is active, we checkpoint per-angle by appending
        discovered leads onto self.run.leads incrementally and persisting
        state — so a crash mid-discovery only loses the in-flight angle
        rather than every prior angle's work.
        """
        discovery = PersonDiscovery(self.config)

        checkpoint_cache: Dict[str, List[ScoutLead]] = {}

        def _checkpoint(angle_idx, angle_name, angle_leads):
            # Accumulate into a local cache keyed by angle to avoid duplicate
            # appends if discover() itself also returns the same leads at the
            # end. Then persist a snapshot.
            key = f"{angle_idx}:{angle_name}"
            checkpoint_cache[key] = angle_leads
            # Merge all checkpointed leads into the run state for a durable
            # snapshot. `deduplicate_leads` keeps the richest record per key.
            merged = []
            for v in checkpoint_cache.values():
                merged.extend(v)
            self.run.leads = deduplicate_leads(self.run.leads + merged)
            # Sync checkpoint progress to state file.
            try:
                self._save_state()
                logger.info(
                    f"  [checkpoint] saved after angle '{angle_name}' "
                    f"(total leads so far: {len(self.run.leads)})"
                )
            except Exception as e:
                logger.warning(f"  [checkpoint] save failed: {e}")

        self.run.leads = discovery.discover(
            self.run.leads,
            solve_plan=self.run.solve_plan,
            on_angle_progress=_checkpoint,
        )
        logger.info(f"{len(self.run.leads)} leads after discovery")

    def _run_filter(self) -> None:
        """Stage 4: Drop leads below threshold before expensive enrichment.

        Keep:
        - leads above threshold
        - leads whose scoring errored (we never got a real signal — don't
          silently drop them; they need re-scoring or human review)
        - unscored leads (safety net)
        """
        before = len(self.run.leads)
        kept = []
        errored = 0
        for l in self.run.leads:
            if l.scoring_error:
                kept.append(l)
                errored += 1
            elif l.fit_score is None or l.fit_score >= self.config.min_fit_score:
                kept.append(l)
        self.run.leads = kept
        dropped = before - len(self.run.leads)
        # Surface the errored count so the run summary makes the failure mode visible.
        self.run.metrics["scoring_errors"] = errored
        logger.info(
            f"Pre-enrichment filter: dropped {dropped}/{before} leads below "
            f"{self.config.min_fit_score} threshold ({len(self.run.leads)} remaining, "
            f"{errored} kept as scoring errors for review)"
        )

    def _run_enrich(self) -> None:
        """Stage 5: Contact enrichment (n8n webhook + academic sources)."""
        # Phase 1: n8n webhook enrichment (Amplemarket → Findymail → Apollo)
        # After score-before-enrich reorder, leads are SCORED status and lack emails
        needs_enrichment = [
            l for l in self.run.leads
            if not l.email
        ]

        if needs_enrichment:
            enricher = ContactEnricher(
                webhook_url=self.config.n8n_enrichment_webhook_url,
                batch_size=self.config.enrichment_batch_size,
            )
            enricher.enrich(needs_enrichment, run_id=self.run.run_id)
        else:
            logger.info("No leads need n8n enrichment")

        # Phase 2: Academic enrichment for leads still missing emails
        still_needs_email = [l for l in self.run.leads if not l.email]
        if still_needs_email:
            logger.info(f"Running academic enrichment for {len(still_needs_email)} leads without emails")
            academic = AcademicEnricher()
            academic.enrich(still_needs_email)

    def _run_halo_dedup(self) -> None:
        """Stage 6: Remove leads that already have Halo accounts."""
        leads_with_email = [l for l in self.run.leads if l.email]
        if not leads_with_email:
            logger.info("No emails to check against Halo")
            return

        try:
            from snowflake_client import check_emails_on_halo
            results = check_emails_on_halo([l.email for l in leads_with_email])

            on_halo_emails = {
                email for email, is_on_halo in results.items() if is_on_halo
            }
            before_count = len(self.run.leads)
            self.run.leads = [
                l for l in self.run.leads
                if not (l.email and l.email.strip().lower() in on_halo_emails)
            ]
            removed = before_count - len(self.run.leads)
            self.run.metrics["halo_removed"] = removed

            logger.info(f"Halo dedup: removed {removed}/{len(leads_with_email)} leads already on Halo ({len(self.run.leads)} remaining)")

        except Exception as e:
            logger.warning(f"Halo dedup check failed (non-fatal): {e}")

    def _run_requester_screen(self) -> None:
        """Screen out leads from the company that posted the request.

        Matches on email domain and company-name substring. Runs before
        scoring so we don't waste LLM calls on ineligible candidates.
        """
        if not self.config.request_id:
            logger.info("Requester screen: no request_id — skipping")
            return

        try:
            from snowflake_client import get_request_company
            req_co = get_request_company(self.config.request_id)
        except Exception as e:
            logger.warning(f"Requester screen: lookup failed ({e}) — skipping")
            return

        if not req_co:
            logger.info("Requester screen: no requesting company on record — skipping")
            return

        req_name = (req_co.get("company_name") or "").strip().lower()
        req_domains = set(req_co.get("domains") or [])
        if not req_name and not req_domains:
            logger.info("Requester screen: no name/domains to match — skipping")
            return

        def _is_requester(lead) -> bool:
            email = (lead.email or "").strip().lower()
            if email and "@" in email:
                dom = email.split("@", 1)[1]
                if dom in req_domains:
                    return True
                # also match subdomains (e.g. research.leprinofoods.com)
                if any(dom == d or dom.endswith("." + d) for d in req_domains):
                    return True
            company = (lead.company or "").strip().lower()
            if req_name and company and (req_name in company or company in req_name):
                return True
            return False

        before = len(self.run.leads)
        kept = [l for l in self.run.leads if not _is_requester(l)]
        removed = before - len(kept)
        self.run.leads = kept
        self.run.metrics["requester_filtered"] = removed
        logger.info(
            f"Requester screen: removed {removed}/{before} leads from "
            f"'{req_co.get('company_name')}' (domains={sorted(req_domains)})"
        )

    def _run_score(self) -> None:
        """Stage 3: LLM-as-judge fit scoring (runs before enrichment)."""
        scorer = FitScorer(self.config)
        scorer.score_leads(self.run.leads)

    def _run_expand_loop(self) -> None:
        """Stage 3b: Expansion loop — use scorer output to find more good leads.

        Two parallel tracks per round:
        - Track A: find_similar on high-scoring lead URLs (≥0.80)
        - Track B: Synthesize blurb patterns → refined Exa queries

        Exits when: target qualified count met, max rounds hit, or diminishing returns.
        """
        # Skip for scraped lists (no Exa discovery) or if expansion disabled
        if self.config.input_type == InputType.SCRAPED_LIST:
            logger.info("Expansion: skipped for scraped_list input type")
            return
        if not self.config.enable_expansion:
            logger.info("Expansion: disabled in config")
            return
        if not os.getenv("EXA_API_KEY"):
            logger.info("Expansion: skipped — no EXA_API_KEY")
            return

        exa_disc = ExaDiscovery(self.config)
        scorer = FitScorer(self.config)
        total_new = 0

        for round_num in range(self.config.expansion_max_rounds):
            # Check if we already have enough qualified leads
            qualified = sum(
                1 for l in self.run.leads
                if l.fit_score is not None and l.fit_score >= self.config.min_fit_score
            )
            if qualified >= self.config.expansion_min_qualified:
                logger.info(
                    f"Expansion: target met ({qualified} >= {self.config.expansion_min_qualified} qualified) — stopping"
                )
                break

            logger.info(
                f"Expansion round {round_num + 1}: "
                f"{qualified}/{self.config.expansion_min_qualified} qualified, running tracks..."
            )

            # Run Track A + Track B in parallel
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_similar = executor.submit(exa_disc.expand_from_similar, self.run.leads)
                future_blurb = executor.submit(exa_disc.expand_from_blurb_synthesis, self.run.leads)

                new_similar = future_similar.result()
                new_blurb = future_blurb.result()

            # Deduplicate new leads against existing
            all_new = deduplicate_leads(new_similar + new_blurb)

            # Remove leads that match existing leads by name+company
            existing_keys = set()
            for l in self.run.leads:
                if l.first_name and l.last_name:
                    key = f"{l.first_name.lower().strip()}|{l.last_name.lower().strip()}|{(l.company or '').lower().strip()}"
                    existing_keys.add(key)
            all_new = [
                l for l in all_new
                if not (
                    l.first_name and l.last_name
                    and f"{l.first_name.lower().strip()}|{l.last_name.lower().strip()}|{(l.company or '').lower().strip()}" in existing_keys
                )
            ]

            if not all_new:
                logger.info(f"Expansion round {round_num + 1}: no new unique leads — stopping")
                break

            # Score new leads
            logger.info(f"Expansion round {round_num + 1}: scoring {len(all_new)} new leads")
            scorer.score_leads(all_new)

            # Count new qualified
            new_qualified = sum(
                1 for l in all_new
                if l.fit_score is not None and l.fit_score >= self.config.min_fit_score
            )

            # Add to pipeline
            self.run.leads.extend(all_new)
            total_new += len(all_new)

            similar_count = len(new_similar)
            blurb_count = len(new_blurb)
            logger.info(
                f"Expansion round {round_num + 1}: "
                f"{similar_count} from find_similar, {blurb_count} from blurb synthesis, "
                f"{len(all_new)} net new, {new_qualified} qualified"
            )

            # Diminishing returns check
            if new_qualified < self.config.expansion_min_new_per_round:
                logger.info(
                    f"Expansion: diminishing returns "
                    f"({new_qualified} < {self.config.expansion_min_new_per_round} new qualified) — stopping"
                )
                break

        final_qualified = sum(
            1 for l in self.run.leads
            if l.fit_score is not None and l.fit_score >= self.config.min_fit_score
        )
        logger.info(
            f"Expansion complete: {total_new} total new leads added, "
            f"{final_qualified} total qualified leads"
        )

    def _run_output(self) -> str:
        """Stage 7: Write results to Google Sheets or CSV."""
        formatter = OutputFormatter(self.config)

        # Use CSV output if specified or if no Google Sheets config
        if self.config.output_sheet_url or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
            return formatter.write(self.run.leads, run_id=self.run.run_id, metrics=self.run.metrics)
        else:
            # Fallback to CSV
            csv_path = str(STATE_DIR / f"{self.run.run_id}_results.csv")
            return formatter.write_csv(self.run.leads, csv_path)

    # =========================================================================
    # State Management (for resume)
    # =========================================================================

    def _save_state(self):
        """Save pipeline state for resume capability."""
        state = {
            "run_id": self.run.run_id,
            "started_at": self.run.started_at,
            "completed_at": self.run.completed_at,
            "stage": self.run.stage,
            "stages_completed": self.run.stages_completed,
            "config": {
                "input_type": self.config.input_type.value,
                "request_id": self.config.request_id,
                "request_title": self.config.request_title,
                "request_looking_for": self.config.request_looking_for,
                "request_use_case": self.config.request_use_case,
                "request_sois": self.config.request_sois,
                "request_partner_types": self.config.request_partner_types,
                "request_requirements": self.config.request_requirements,
                "request_out_of_scope": self.config.request_out_of_scope,
                "min_fit_score": self.config.min_fit_score,
                "score_model": self.config.score_model,
                "input_sheet_url": self.config.input_sheet_url,
                "input_sheet_tab": self.config.input_sheet_tab,
                "example_patterns": self.config.example_patterns,
                "enable_solve_planner": self.config.enable_solve_planner,
                "num_solve_angles": self.config.num_solve_angles,
                "solve_plan_model": self.config.solve_plan_model,
            },
            "leads": [lead.to_dict() for lead in self.run.leads],
            "metrics": dict(self.run.metrics),
            "solve_plan": self.run.solve_plan,
            "stats": {
                "total_leads": len(self.run.leads),
                "scored": sum(1 for l in self.run.leads if l.fit_score is not None),
                "above_threshold": sum(
                    1 for l in self.run.leads
                    if l.fit_score and l.fit_score >= self.config.min_fit_score
                ),
            },
        }

        state_path = STATE_DIR / f"{self.run.run_id}.json"
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2, default=str)

    @classmethod
    def resume(cls, run_id: str) -> "AgentScout":
        """Resume a failed run from its last completed stage."""
        state_path = STATE_DIR / f"{run_id}.json"
        if not state_path.exists():
            raise FileNotFoundError(f"No saved state for run: {run_id}")

        with open(state_path) as f:
            state = json.load(f)

        config = ScoutConfig(
            input_type=InputType(state["config"]["input_type"]),
            request_id=state["config"].get("request_id"),
            request_title=state["config"].get("request_title"),
            request_looking_for=state["config"].get("request_looking_for"),
            request_use_case=state["config"].get("request_use_case"),
            request_sois=state["config"].get("request_sois"),
            request_partner_types=state["config"].get("request_partner_types"),
            request_requirements=state["config"].get("request_requirements"),
            request_out_of_scope=state["config"].get("request_out_of_scope"),
            min_fit_score=state["config"].get("min_fit_score", 0.3),
            score_model=state["config"].get("score_model", "claude-sonnet-4-6"),
            input_sheet_url=state["config"].get("input_sheet_url"),
            input_sheet_tab=state["config"].get("input_sheet_tab"),
            example_patterns=state["config"].get("example_patterns"),
            enable_solve_planner=state["config"].get("enable_solve_planner", True),
            num_solve_angles=state["config"].get("num_solve_angles", 25),
            solve_plan_model=state["config"].get("solve_plan_model", "claude-sonnet-4-6"),
        )

        agent = cls(config)
        agent.run.run_id = state["run_id"]
        agent.run.started_at = state["started_at"]
        agent.run.stages_completed = state["stages_completed"]
        agent.run.leads = [ScoutLead.from_dict(ld) for ld in state["leads"]]
        agent.run.metrics = dict(state.get("metrics") or {})
        agent.run.solve_plan = state.get("solve_plan")

        logger.info(f"Resumed run {run_id}. Completed stages: {agent.run.stages_completed}")
        return agent


# =============================================================================
# CLI
# =============================================================================

def build_config_from_args(args) -> ScoutConfig:
    """Build ScoutConfig from CLI arguments."""
    input_type = InputType(args.type)

    config = ScoutConfig(
        input_type=input_type,
        request_id=args.request_id,
        request_looking_for=args.request_looking_for,
        request_use_case=args.request_use_case,
        request_sois=args.request_sois,
        request_partner_types=getattr(args, 'request_partner_types', None),
        request_requirements=getattr(args, 'request_requirements', None),
        request_out_of_scope=getattr(args, 'request_out_of_scope', None),
        input_csv_path=args.input_csv,
        input_sheet_url=args.input_sheet,
        input_sheet_tab=getattr(args, 'sheet_tab', None),
        output_sheet_url=args.output_sheet,
        n8n_enrichment_webhook_url=args.enrichment_webhook or os.getenv("N8N_ENRICHMENT_WEBHOOK_URL"),
        min_fit_score=args.min_score,
    )

    if args.companies:
        config.companies = [c.strip() for c in args.companies.split(",")]

    if args.output_csv:
        # Signal to use CSV output
        config.output_sheet_url = None

    return config


def main():
    parser = argparse.ArgumentParser(
        description="Agent Scout — Find and score innovators for partnering requests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Input type
    parser.add_argument(
        "--type", required=True,
        choices=["partnering_request", "request_with_examples", "scraped_list", "company_list"],
        help="Input type",
    )

    # Input sources
    parser.add_argument("--request-id", type=int, help="Snowflake request ID")
    parser.add_argument("--request-looking-for", help="What the partner is looking for (text)")
    parser.add_argument("--request-use-case", help="Use case description")
    parser.add_argument("--request-sois", help="Solutions of interest")
    parser.add_argument("--request-partner-types", help="Partner types sought")
    parser.add_argument("--request-requirements", help="Requirements for the request")
    parser.add_argument("--request-out-of-scope", help="What is out of scope (excluded)")
    parser.add_argument("--input-csv", help="Path to input CSV (for scraped_list type)")
    parser.add_argument("--input-sheet", help="Google Sheet URL for input")
    parser.add_argument("--sheet-tab", help="Tab/worksheet name in Google Sheet (default: first sheet)")
    parser.add_argument("--companies", help="Comma-separated company names (for company_list type)")

    # Output
    parser.add_argument("--output-sheet", help="Google Sheet URL for output")
    parser.add_argument("--output-csv", help="Path for output CSV (alternative to Google Sheets)")

    # Settings
    parser.add_argument("--enrichment-webhook", help="n8n enrichment webhook URL")
    parser.add_argument("--min-score", type=float, default=0.3, help="Min fit score threshold (default: 0.3)")

    # Resume
    parser.add_argument("--resume", help="Resume a failed run by run_id")

    args = parser.parse_args()

    # Resume mode
    if args.resume:
        agent = AgentScout.resume(args.resume)
        result = agent.execute()
        print(f"\nOutput: {result}")
        return

    # Normal execution
    config = build_config_from_args(args)
    agent = AgentScout(config)

    try:
        result = agent.execute()
        print(f"\nOutput: {result}")
    except KeyboardInterrupt:
        logger.info("\nInterrupted. State saved. Resume with:")
        logger.info(f"  python agent_scout.py --resume {agent.run.run_id}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\nPipeline failed: {e}")
        logger.info(f"Resume with: python agent_scout.py --resume {agent.run.run_id}")
        raise


if __name__ == "__main__":
    main()
