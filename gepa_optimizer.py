#!/usr/bin/env python3
"""
Agent Scout — GEPA Optimizer
===============================
Runs DSPy GEPA optimization on the fit scorer using human feedback.

GEPA (Genetic-Pareto) reflectively evolves prompt instructions using
textual feedback from the metric function. This is the core optimization loop.
"""

import json
import logging
import os
import statistics
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import dspy
from dspy import GEPA, Example

from dspy_fit_scorer import (
    DSPyFitScorer,
    format_request_context,
    format_candidate_profile,
    parse_score,
)
from prompt_store import save_prompt, activate_version, save_baseline, get_active_prompt
from prompts import FIT_SCORING_SYSTEM

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "feedback_data" / "gepa_logs"

# ── Minimum thresholds ──────────────────────────────────────────────
MIN_EXAMPLES = 20          # Minimum labeled examples to run optimization
MIN_POSITIVE = 5           # Minimum Approve decisions
MIN_NEGATIVE = 5           # Minimum Reject decisions
AUTO_ACTIVATE_MIN = 40     # Auto-activate only with this many examples


def build_dspy_examples(labeled_data: List[Dict[str, Any]]) -> List[Example]:
    """Convert labeled feedback into DSPy Example objects."""
    examples = []
    for item in labeled_data:
        decision = item.get("reviewer_decision", "")
        if decision not in ("Approve", "Reject", "Maybe", "Need More Info"):
            continue

        ex = Example(
            request_context=format_request_context(item),
            candidate_profile=format_candidate_profile(item),
            # Gold labels
            reviewer_decision=decision,
            reviewer_notes=item.get("reviewer_notes", ""),
            target_score=item.get("target_score", 0.5),
            # Metadata for feedback construction
            candidate_name=f"{item.get('first_name', '')} {item.get('last_name', '')}".strip(),
            candidate_company=item.get("company", "Unknown"),
            original_score=item.get("fit_score"),
        ).with_inputs("request_context", "candidate_profile")

        examples.append(ex)

    return examples


def fit_scoring_metric(
    gold: Example,
    pred: dspy.Prediction,
    trace=None,
    pred_name: str = None,
    pred_trace=None,
) -> Dict[str, Any]:
    """
    GEPA metric returning score + textual feedback.

    This is the most critical piece — GEPA uses the feedback strings
    to reflect on what the prompt is doing wrong and propose improvements.
    """
    # Parse predicted score
    predicted_score = parse_score(pred)
    if predicted_score is None:
        if pred_name is not None:
            return {
                "score": 0.0,
                "feedback": f"Failed to parse score from prediction. Output was: {str(pred)[:200]}",
            }
        return 0.0

    target = float(gold.target_score)
    decision = gold.reviewer_decision
    notes = gold.reviewer_notes
    name = gold.candidate_name
    company = gold.candidate_company
    original_score = gold.original_score

    # ── Binary correctness ──
    # Approve/Maybe = should score high (>= 0.70)
    # Reject = should score low (< 0.60)
    if decision == "Approve":
        correct = predicted_score >= 0.70
    elif decision == "Reject":
        correct = predicted_score < 0.60
    elif decision == "Maybe":
        correct = 0.50 <= predicted_score <= 0.80
    else:  # Need More Info
        correct = 0.40 <= predicted_score <= 0.75

    # ── Calibration ──
    calibration = 1.0 - min(abs(predicted_score - target), 1.0)

    # ── Composite score ──
    score = 0.65 * float(correct) + 0.35 * calibration

    # ── Build textual feedback (GEPA's superpower) ──
    feedback_parts = [
        f"Candidate: {name} at {company}",
        f"Predicted: {predicted_score:.2f}, Target: ~{target:.2f}",
        f"Human decision: {decision}",
    ]

    if not correct:
        if decision == "Reject" and predicted_score >= 0.60:
            feedback_parts.append(
                f"FALSE POSITIVE: Scorer gave {predicted_score:.2f} but human rejected this lead."
            )
            # Classify the type of error
            if notes:
                notes_lower = notes.lower()
                if "academic" in notes_lower or "not interested in academic" in notes_lower:
                    feedback_parts.append(
                        "PATTERN: Academic contact submitted for a request that doesn't want academics. "
                        "The scorer should check Partner Types Sought and penalize academics when not wanted."
                    )
                elif "service provider" in notes_lower or "consultant" in notes_lower:
                    feedback_parts.append(
                        "PATTERN: Service provider/consultant mismatch. The scorer should verify the "
                        "candidate's org type matches the requested partner types."
                    )
                elif "unrelated" in notes_lower or "tangential" in notes_lower:
                    feedback_parts.append(
                        "PATTERN: Domain mismatch scored too high. The scorer over-indexed on keyword "
                        "overlap without checking actual domain relevance."
                    )
                elif "not sure" in notes_lower or "early stage" in notes_lower:
                    feedback_parts.append(
                        "PATTERN: Uncertain fit scored too high. When there's insufficient evidence "
                        "of capabilities, the scorer should lower the score."
                    )
                elif "contact" in notes_lower and ("not" in notes_lower or "stale" in notes_lower):
                    feedback_parts.append(
                        "PATTERN: Stale or wrong contact. The scorer should verify contact currency "
                        "when evidence suggests the person may have moved."
                    )

        elif decision == "Approve" and predicted_score < 0.70:
            feedback_parts.append(
                f"FALSE NEGATIVE: Scorer gave {predicted_score:.2f} but human approved. "
                "The scorer is being too conservative for this lead."
            )

    if notes:
        feedback_parts.append(f"Human reviewer said: \"{notes}\"")

    if abs(predicted_score - target) > 0.25:
        feedback_parts.append(
            f"LARGE MISS: Score off by {abs(predicted_score - target):.2f}. "
            "Needs significant recalibration for this type of lead."
        )

    feedback_str = " | ".join(feedback_parts)

    # GEPA calls the metric in two modes:
    # - During reflection (pred_name is set): return dict with feedback for GEPA to reflect on
    # - During evaluation (pred_name is None): return scalar for aggregation
    if pred_name is not None:
        return {"score": score, "feedback": feedback_str}
    return score


def check_readiness(dataset: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Check if dataset meets minimum thresholds for optimization."""
    if len(dataset) < MIN_EXAMPLES:
        return False, f"Need {MIN_EXAMPLES} examples, have {len(dataset)}"

    approve = sum(1 for d in dataset if d.get("reviewer_decision") == "Approve")
    reject = sum(1 for d in dataset if d.get("reviewer_decision") == "Reject")

    if approve < MIN_POSITIVE:
        return False, f"Need {MIN_POSITIVE} Approve examples, have {approve}"
    if reject < MIN_NEGATIVE:
        return False, f"Need {MIN_NEGATIVE} Reject examples, have {reject}"

    return True, f"Ready: {len(dataset)} examples ({approve} approve, {reject} reject)"


def train_val_split(
    examples: List[Example],
    val_ratio: float = 0.2,
    seed: int = 42,
) -> Tuple[List[Example], List[Example]]:
    """Split examples into train/val, stratified by decision."""
    import random
    rng = random.Random(seed)

    # Group by decision
    by_decision = {}
    for ex in examples:
        d = ex.reviewer_decision
        by_decision.setdefault(d, []).append(ex)

    train, val = [], []
    for decision, group in by_decision.items():
        rng.shuffle(group)
        n_val = max(1, int(len(group) * val_ratio))
        val.extend(group[:n_val])
        train.extend(group[n_val:])

    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


def run_optimization(
    labeled_data: List[Dict[str, Any]],
    model: str = "anthropic/claude-sonnet-4-20250514",
    reflection_model: str = "anthropic/claude-sonnet-4-20250514",
    max_metric_calls: int = 300,
    auto_activate: bool = False,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Run GEPA optimization on the fit scorer.

    Returns a results dict with version_id, metrics, and comparison to baseline.
    """
    # ── 1. Check readiness ──
    ready, msg = check_readiness(labeled_data)
    if not ready:
        logger.warning(f"Dataset not ready: {msg}")
        return {"status": "not_ready", "message": msg}

    logger.info(f"Starting GEPA optimization with {len(labeled_data)} examples")

    # ── 2. Save baseline prompt if not already saved ──
    save_baseline(FIT_SCORING_SYSTEM)

    # ── 3. Build DSPy examples and split ──
    examples = build_dspy_examples(labeled_data)
    trainset, valset = train_val_split(examples, val_ratio=0.2, seed=seed)
    logger.info(f"Split: {len(trainset)} train, {len(valset)} val")

    # ── 4. Configure DSPy ──
    lm = dspy.LM(model, temperature=0.1, max_tokens=800)
    reflection_lm = dspy.LM(reflection_model, temperature=1.0, max_tokens=4000)
    dspy.configure(lm=lm)

    # ── 5. Evaluate baseline ──
    logger.info("Evaluating baseline prompt...")
    base_module = DSPyFitScorer()
    baseline_metrics = _evaluate(base_module, valset)
    logger.info(f"Baseline: {_fmt_metrics(baseline_metrics)}")

    # ── 6. Run GEPA ──
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info(f"Running GEPA (max_metric_calls={max_metric_calls})...")
    optimizer = GEPA(
        metric=fit_scoring_metric,
        reflection_lm=reflection_lm,
        max_metric_calls=max_metric_calls,
        reflection_minibatch_size=min(8, len(trainset)),
        skip_perfect_score=True,
        log_dir=str(LOG_DIR / f"gepa_{timestamp}"),
        track_stats=True,
        seed=seed,
    )

    optimized_module = optimizer.compile(
        DSPyFitScorer(),
        trainset=trainset,
        valset=valset,
    )

    # ── 7. Evaluate optimized ──
    logger.info("Evaluating optimized prompt...")
    optimized_metrics = _evaluate(optimized_module, valset)
    logger.info(f"Optimized: {_fmt_metrics(optimized_metrics)}")

    # ── 8. Extract optimized instruction ──
    optimized_instruction = _extract_instruction(optimized_module)

    # ── 9. Compare and save ──
    improved = optimized_metrics["composite"] > baseline_metrics["composite"]
    improvement = optimized_metrics["composite"] - baseline_metrics["composite"]

    metadata = {
        "dataset_size": len(labeled_data),
        "train_size": len(trainset),
        "val_size": len(valset),
        "model": model,
        "reflection_model": reflection_model,
        "max_metric_calls": max_metric_calls,
        "optimized_at": datetime.now().isoformat(),
        "seed": seed,
    }

    metrics = {
        "baseline": baseline_metrics,
        "optimized": optimized_metrics,
        "improvement": improvement,
        "improved": improved,
    }

    version_id = save_prompt(optimized_instruction, metadata, metrics)

    # Auto-activate if improved AND we have enough data
    should_activate = improved and auto_activate and len(labeled_data) >= AUTO_ACTIVATE_MIN
    if should_activate:
        activate_version(version_id)
        logger.info(f"Auto-activated {version_id} (improvement: {improvement:+.3f})")
    elif improved:
        logger.info(
            f"Improved by {improvement:+.3f} but NOT auto-activated "
            f"(need {AUTO_ACTIVATE_MIN} examples or manual activation, have {len(labeled_data)})"
        )
    else:
        logger.info(f"No improvement ({improvement:+.3f}). Saved for reference but not activated.")

    return {
        "status": "completed",
        "version_id": version_id,
        "improved": improved,
        "improvement": improvement,
        "baseline_metrics": baseline_metrics,
        "optimized_metrics": optimized_metrics,
        "should_activate": should_activate,
        "dataset_size": len(labeled_data),
    }


def _evaluate(module: dspy.Module, valset: List[Example]) -> Dict[str, float]:
    """Evaluate a module on a validation set. Returns metric dict."""
    results = []
    correct_count = 0
    calibration_sum = 0.0
    fp_count = 0  # False positives (reject scored high)
    fn_count = 0  # False negatives (approve scored low)

    for ex in valset:
        try:
            pred = module(
                request_context=ex.request_context,
                candidate_profile=ex.candidate_profile,
            )
            metric_result = fit_scoring_metric(ex, pred, trace=None, pred_name=None, pred_trace=None)
            score = metric_result if isinstance(metric_result, (int, float)) else metric_result.get("score", 0.0)
            results.append(score)

            predicted = parse_score(pred)
            if predicted is not None:
                target = float(ex.target_score)
                calibration_sum += abs(predicted - target)

                if ex.reviewer_decision == "Approve" and predicted < 0.70:
                    fn_count += 1
                elif ex.reviewer_decision == "Reject" and predicted >= 0.60:
                    fp_count += 1

                # Binary correct
                if ex.reviewer_decision == "Approve" and predicted >= 0.70:
                    correct_count += 1
                elif ex.reviewer_decision == "Reject" and predicted < 0.60:
                    correct_count += 1
                elif ex.reviewer_decision == "Maybe" and 0.50 <= predicted <= 0.80:
                    correct_count += 1
        except Exception as e:
            logger.warning(f"Eval error: {e}")
            results.append(0.0)

    n = len(valset)
    return {
        "composite": statistics.mean(results) if results else 0.0,
        "accuracy": correct_count / n if n > 0 else 0.0,
        "mean_calibration_error": calibration_sum / n if n > 0 else 1.0,
        "false_positives": fp_count,
        "false_negatives": fn_count,
        "n": n,
    }


def _extract_instruction(module: dspy.Module) -> str:
    """Extract the optimized instruction text from a compiled DSPy module."""
    try:
        # Navigate to the scorer predict's signature instruction
        sig = module.scorer.signature
        return sig.instructions
    except AttributeError:
        try:
            # Alternative path for different DSPy versions
            for name, param in module.named_parameters():
                if hasattr(param, "signature"):
                    return param.signature.instructions
        except Exception:
            pass
    logger.warning("Could not extract optimized instruction, using baseline")
    return FIT_SCORING_SYSTEM


def _fmt_metrics(m: Dict[str, float]) -> str:
    return (
        f"composite={m['composite']:.3f} accuracy={m['accuracy']:.1%} "
        f"cal_error={m['mean_calibration_error']:.3f} "
        f"FP={m['false_positives']} FN={m['false_negatives']}"
    )
