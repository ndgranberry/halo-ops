#!/usr/bin/env python3
"""
RoboScout Query Generator — DSPy Configuration
=================================================
Configures the DSPy language model and manages optimized prompt versions.
"""

import logging
from pathlib import Path

import dspy

logger = logging.getLogger("roboscout_query_gen.dspy_config")

PROMPT_DIR = Path(__file__).parent / "optimization" / "prompts"


def configure_lm(
    model: str = "anthropic/claude-sonnet-4-20250514",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dspy.LM:
    """Configure the DSPy language model for the pipeline.

    Args:
        model: LiteLLM model identifier (e.g., "anthropic/claude-sonnet-4-20250514").
        temperature: Sampling temperature (0.3 = deterministic but creative enough).
        max_tokens: Maximum tokens per response.

    Returns:
        The configured LM instance.
    """
    lm = dspy.LM(model, temperature=temperature, max_tokens=max_tokens)
    dspy.configure(lm=lm)
    logger.info(f"DSPy configured: model={model}, temperature={temperature}")
    return lm


def load_active_prompt(module: dspy.Module) -> str:
    """Load the currently approved optimized prompt, or use baseline.

    Looks for optimization/prompts/active.json. If it exists, loads the
    GEPA-evolved prompts into the module. Otherwise, uses the default
    signature docstrings (baseline).

    Args:
        module: The DSPy module to load prompts into.

    Returns:
        Prompt version identifier ("baseline" or the prompt file stem).
    """
    active_path = PROMPT_DIR / "active.json"
    if active_path.exists():
        try:
            module.load(str(active_path))
            logger.info(f"Loaded optimized prompts from {active_path}")
            return "optimized"
        except Exception as e:
            logger.warning(f"Failed to load optimized prompts: {e}. Using baseline.")
            return "baseline"
    return "baseline"
