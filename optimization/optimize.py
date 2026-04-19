#!/usr/bin/env python3
"""
RoboScout Optimization — GEPA Optimizer Runner
=================================================
Runs dspy.GEPA to evolve prompts using textual feedback.

Usage:
    python -m optimization.optimize [--budget light|medium|heavy]

The optimizer:
1. Loads training examples from build_examples.py
2. Splits 80/20 train/val
3. Runs GEPA with a reflection LM (Opus) and task LM (Sonnet)
4. Saves the optimized program to optimization/prompts/
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

import dspy

logger = logging.getLogger("roboscout_optimization.optimize")

PROJECT_DIR = Path(__file__).parent.parent
PROMPTS_DIR = PROJECT_DIR / "optimization" / "prompts"
LOGS_DIR = PROJECT_DIR / "optimization" / "logs"


def run_optimization(
    budget: str = "medium",
    task_model: str = "anthropic/claude-sonnet-4-20250514",
    reflection_model: str = "anthropic/claude-opus-4-20250514",
    save_as_candidate: bool = True,
) -> dict:
    """Run GEPA optimization on the query generation pipeline.

    Args:
        budget: GEPA auto budget ("light", "medium", "heavy").
        task_model: LiteLLM model ID for the task LM.
        reflection_model: LiteLLM model ID for the reflection LM.
        save_as_candidate: If True, save as candidate.json (not active).

    Returns:
        Dict with optimization results and file paths.
    """
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Configure LMs
    task_lm = dspy.LM(task_model, temperature=0.3, max_tokens=4096)
    reflection_lm = dspy.LM(reflection_model, temperature=1.0)
    dspy.configure(lm=task_lm)

    # Build training data
    from optimization.build_examples import build_all_examples
    from optimization.metrics import query_generation_metric

    examples = build_all_examples()
    if len(examples) < 3:
        logger.warning(
            f"Only {len(examples)} training examples available. "
            f"Need at least 3 for meaningful optimization."
        )
        return {"status": "insufficient_data", "example_count": len(examples)}

    # Split 80/20
    split_idx = max(int(len(examples) * 0.8), 1)
    train = examples[:split_idx]
    val = examples[split_idx:] if split_idx < len(examples) else examples[:1]

    logger.info(
        f"Training examples: {len(train)}, Validation examples: {len(val)}"
    )

    # Create module
    from modules import QueryGenerationModule
    module = QueryGenerationModule()

    # Run GEPA
    logger.info(f"Starting GEPA optimization (budget={budget})...")
    optimizer = dspy.GEPA(
        metric=query_generation_metric,
        reflection_lm=reflection_lm,
        auto=budget,
        log_dir=str(LOGS_DIR),
        track_stats=True,
    )

    optimized = optimizer.compile(
        module,
        trainset=train,
        valset=val,
    )

    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if save_as_candidate:
        output_path = PROMPTS_DIR / "candidate.json"
    else:
        output_path = PROMPTS_DIR / f"optimized_{timestamp}.json"

    optimized.save(str(output_path))
    logger.info(f"Saved optimized program to {output_path}")

    # Get results summary
    results = {
        "status": "success",
        "timestamp": timestamp,
        "budget": budget,
        "train_size": len(train),
        "val_size": len(val),
        "output_path": str(output_path),
        "task_model": task_model,
        "reflection_model": reflection_model,
    }

    # Try to extract scores from GEPA results
    try:
        if hasattr(optimized, "detailed_results"):
            dr = optimized.detailed_results
            if hasattr(dr, "val_aggregate_scores"):
                results["best_val_score"] = max(dr.val_aggregate_scores)
    except Exception:
        pass

    results_path = LOGS_DIR / f"optimization_{timestamp}.json"
    results_path.write_text(json.dumps(results, indent=2, default=str))
    logger.info(f"Optimization results: {results}")

    return results


def promote_candidate():
    """Promote candidate.json to active.json (used by production pipeline)."""
    candidate = PROMPTS_DIR / "candidate.json"
    active = PROMPTS_DIR / "active.json"

    if not candidate.exists():
        logger.error("No candidate.json found to promote")
        return False

    # Back up current active if it exists
    if active.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = PROMPTS_DIR / f"active_backup_{timestamp}.json"
        active.rename(backup)
        logger.info(f"Backed up active prompt to {backup}")

    candidate.rename(active)
    logger.info("Promoted candidate.json to active.json")
    return True


def rollback_prompt():
    """Roll back to baseline (remove active.json)."""
    active = PROMPTS_DIR / "active.json"
    if active.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = PROMPTS_DIR / f"rolled_back_{timestamp}.json"
        active.rename(backup)
        logger.info(f"Rolled back to baseline (saved old active as {backup})")
        return True
    else:
        logger.info("Already using baseline (no active.json)")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Run GEPA prompt optimization")
    parser.add_argument(
        "--budget", choices=["light", "medium", "heavy"],
        default="medium", help="GEPA optimization budget"
    )
    parser.add_argument(
        "--promote", action="store_true",
        help="Promote candidate.json to active.json"
    )
    parser.add_argument(
        "--rollback", action="store_true",
        help="Roll back to baseline prompts"
    )
    args = parser.parse_args()

    if args.promote:
        promote_candidate()
    elif args.rollback:
        rollback_prompt()
    else:
        run_optimization(budget=args.budget)
