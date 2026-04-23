#!/usr/bin/env python3
"""
Agent Scout — Solve Planner (Phase 1 comprehensive-discovery upgrade)
=====================================================================

Upstream of Exa query generation, the SolvePlanner runs a single Claude call
that decomposes a partnering request into 20-30 distinct "angles." Each angle
is a self-contained search pack for one mechanism, actor type, or adjacent
industry in the solution landscape.

Output (a dict conforming to GENERATE_SOLVE_PLAN_TOOL's input_schema) is
persisted on ScoutRun.solve_plan and consumed by
ExaDiscovery.discover_from_solve_plan.

This is the one place the LLM *thinks about the problem shape* before
querying. Keeping it separate from query generation keeps the planning
prompt focused on reasoning and the query-generation prompt focused on
phrasing.
"""

import logging
from typing import Any, Dict, Optional

from .claude_client import ClaudeClient
from .models import ScoutConfig
from .prompts import (
    SOLVE_PLAN_SYSTEM,
    SOLVE_PLAN_USER,
    GENERATE_SOLVE_PLAN_TOOL,
)

logger = logging.getLogger(__name__)


class SolvePlanner:
    """Produces a structured solve plan for a partnering request."""

    def __init__(self, config: ScoutConfig):
        self.config = config
        # Use a higher-temperature, creative call — we want breadth.
        self.claude = ClaudeClient(
            model=config.solve_plan_model,
            temperature=0.6,
        )

    def plan(self) -> Optional[Dict[str, Any]]:
        """Run the solve-planning call. Returns a dict with 'summary' + 'angles'
        matching GENERATE_SOLVE_PLAN_TOOL, or None on failure.
        """
        user = SOLVE_PLAN_USER.format(
            request_title=self.config.request_title or "Not specified",
            request_looking_for=self.config.request_looking_for or "Not specified",
            request_use_case=self.config.request_use_case or "Not specified",
            request_sois=self.config.request_sois or "Not specified",
            request_partner_types=self.config.request_partner_types or "Not specified",
            request_trl_range=self.config.request_trl_range or "Not specified",
            request_requirements=self.config.request_requirements or "Not specified",
            request_out_of_scope=self.config.request_out_of_scope or "Not specified",
            num_angles=self.config.num_solve_angles,
        )

        logger.info(
            f"SolvePlanner: requesting {self.config.num_solve_angles} angles "
            f"for '{self.config.request_title}'"
        )

        plan = self.claude.call_with_tools(
            system=SOLVE_PLAN_SYSTEM,
            user=user,
            tools=[GENERATE_SOLVE_PLAN_TOOL],
            tool_choice={"type": "tool", "name": "generate_solve_plan"},
            max_tokens=self.config.solve_plan_max_tokens,
        )

        if not plan or "angles" not in plan:
            logger.error("SolvePlanner: Claude did not return a valid plan")
            return None

        angles = plan.get("angles", [])
        logger.info(
            f"SolvePlanner: got {len(angles)} angles. "
            f"actor_type mix: {_actor_type_counts(angles)}"
        )
        return plan


def _actor_type_counts(angles) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for a in angles:
        at = a.get("actor_type") or "unknown"
        counts[at] = counts.get(at, 0) + 1
    return counts
