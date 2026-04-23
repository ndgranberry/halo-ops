#!/usr/bin/env python3
"""
RoboScout Optimization — GEPA Feedback Metric
================================================
Defines the metric function used by dspy.GEPA for prompt optimization.

Returns {"score": float, "feedback": str} where:
  - score: 0-1 quality rating
  - feedback: textual description of what went wrong (GEPA uses this for
    reflective prompt evolution)
"""



def query_generation_metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> dict:
    """GEPA feedback metric for query generation quality.

    Evaluates the predicted GenerateQueriesOutput against the gold example
    using four axes:
      1. SOI Coverage (40%): Are all input SOIs addressed?
      2. Query Count (20%): In the 8-20 target range?
      3. Specificity Diversity (20%): Mix of specific + broader queries?
      4. Rule Compliance (20%): No quotation marks, boolean operators, etc?

    Args:
        gold: dspy.Example with input fields
        pred: Prediction with .output (GenerateQueriesOutput)

    Returns:
        {"score": float, "feedback": str}
    """
    output = pred.output
    feedback_parts = []
    score = 0.0

    # --- 1. SOI Coverage (40% weight) ---
    input_sois = set(
        s.lower().strip()
        for s in gold.solutions_of_interest.split(",")
        if s.strip()
    )
    covered_sois = set(
        cq.target_soi.lower().strip()
        for cq in output.candidate_queries
        if cq.target_soi.strip()
    )

    if input_sois:
        coverage_ratio = len(input_sois & covered_sois) / len(input_sois)
    else:
        coverage_ratio = 1.0  # No SOIs specified = vacuously covered

    score += 0.4 * coverage_ratio
    uncovered = input_sois - covered_sois
    if uncovered:
        feedback_parts.append(
            f"Missing queries for SOIs: {', '.join(sorted(uncovered))}"
        )

    # --- 2. Query Count (20% weight) ---
    count = len(output.candidate_queries)
    if 8 <= count <= 20:
        score += 0.2
    elif count > 0:
        score += 0.1
        if count < 8:
            feedback_parts.append(f"Only {count} queries generated (minimum: 8)")
        else:
            feedback_parts.append(f"Generated {count} queries (maximum: 20)")
    else:
        feedback_parts.append("No queries generated")

    # --- 3. Specificity Diversity (20% weight) ---
    specs = [cq.expected_specificity for cq in output.candidate_queries]
    has_specific = any(s in ("specific", "highly_specific") for s in specs)
    has_broader = any(s in ("moderate", "general") for s in specs)
    if has_specific and has_broader:
        score += 0.2
    elif has_specific or has_broader:
        score += 0.1
        feedback_parts.append(
            "Queries lack specificity diversity — need both specific "
            "and broader queries per SOI"
        )
    else:
        feedback_parts.append("No valid specificity labels on queries")

    # --- 4. Rule Compliance (20% weight) ---
    violations = []
    for cq in output.candidate_queries:
        q = cq.query
        if '"' in q or "'" in q:
            violations.append(f"'{q[:40]}...' contains quotation marks")
        if any(op in q.upper() for op in [" AND ", " OR ", " NOT "]):
            violations.append(f"'{q[:40]}...' uses boolean operators")
        if any(q.lower().startswith(prefix) for prefix in ["non-", "un-"]):
            violations.append(f"'{q[:40]}...' starts with negation prefix")
        if "alternatives to" in q.lower():
            violations.append(f"'{q[:40]}...' uses 'alternatives to' phrasing")

    if output.candidate_queries:
        violation_ratio = len(violations) / len(output.candidate_queries)
    else:
        violation_ratio = 0.0
    score += 0.2 * (1.0 - min(violation_ratio, 1.0))
    if violations:
        # Show at most 3 violations
        feedback_parts.append(
            f"Rule violations ({len(violations)} total): "
            + "; ".join(violations[:3])
        )

    feedback = " | ".join(feedback_parts) if feedback_parts else "All checks passed"
    return {"score": round(score, 4), "feedback": feedback}


def live_validation_metric(gold, pred, s2_client=None,
                           trace=None, pred_name=None, pred_trace=None) -> dict:
    """Run actual Semantic Scholar validation on generated queries.

    This is an advanced metric that makes real API calls. Use sparingly
    (e.g., during final validation of optimized prompts).

    Args:
        gold: dspy.Example with input fields
        pred: Prediction with .output (GenerateQueriesOutput)
        s2_client: SemanticScholarClient instance (required)

    Returns:
        {"score": float, "feedback": str}
    """
    if s2_client is None:
        return {"score": 0.0, "feedback": "No S2 client provided for live validation"}

    feedback_parts = []
    valid_count = 0
    checked = min(len(pred.output.candidate_queries), 5)  # Spot-check first 5

    for cq in pred.output.candidate_queries[:checked]:
        total, papers = s2_client.get_top_papers(cq.query, limit=5)
        if total < 0:
            feedback_parts.append(f"S2 API error for '{cq.query[:40]}'")
            continue
        if total > 3000:
            feedback_parts.append(f"'{cq.query[:40]}' returned {total} results (too broad)")
        elif total == 0:
            feedback_parts.append(f"'{cq.query[:40]}' returned 0 results (too narrow)")
        else:
            valid_count += 1

    score = valid_count / max(checked, 1)
    feedback = " | ".join(feedback_parts) if feedback_parts else "All spot-checked queries valid"
    return {"score": round(score, 4), "feedback": feedback}
