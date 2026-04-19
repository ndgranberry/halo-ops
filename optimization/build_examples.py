#!/usr/bin/env python3
"""
RoboScout Optimization — Build Training Examples
===================================================
Converts worked examples from context/query_generation_guide.md and
human feedback data into dspy.Example training data for GEPA.

Usage:
    python -m optimization.build_examples
"""

import json
import logging
from pathlib import Path
from typing import List

import dspy

logger = logging.getLogger("roboscout_optimization.build_examples")

PROJECT_DIR = Path(__file__).parent.parent
GUIDE_PATH = PROJECT_DIR / "context" / "query_generation_guide.md"
FEEDBACK_PATH = PROJECT_DIR / "optimization" / "training_data" / "feedback_data.json"
EXAMPLES_PATH = PROJECT_DIR / "optimization" / "training_data" / "training_examples.json"

# Input field names that match the GenerateQueries signature
INPUT_FIELDS = [
    "title", "looking_for", "use_case", "solutions_of_interest",
    "requirements", "out_of_scope", "reference_guide",
]


def build_from_feedback() -> List[dspy.Example]:
    """Build training examples from human feedback data.

    Reads positive feedback (queries rated "good") and suggested queries
    (from "bad" entries where the manager provided an alternative phrasing).
    Both become positive training examples for GEPA.
    """
    if not FEEDBACK_PATH.exists():
        logger.info("No feedback data found — skipping feedback examples")
        return []

    data = json.loads(FEEDBACK_PATH.read_text())
    positive = data.get("positive", [])
    negative = data.get("negative", [])

    if not positive and not negative:
        logger.info("No feedback entries — skipping")
        return []

    # Group by request_id: good queries + suggested alternatives
    by_request = {}
    for entry in positive:
        rid = entry.get("request_id", "unknown")
        by_request.setdefault(rid, {"good": [], "suggested": []})
        by_request[rid]["good"].append(entry)

    # Negative entries with a suggested_query become positive examples
    for entry in negative:
        suggested = (entry.get("suggested_query") or "").strip()
        if suggested:
            rid = entry.get("request_id", "unknown")
            by_request.setdefault(rid, {"good": [], "suggested": []})
            by_request[rid]["suggested"].append({
                "query": suggested,
                "soi": entry.get("soi", ""),
                "source": "human_suggested",
                "original_bad_query": entry.get("query", ""),
            })

    examples = []
    for rid, entries in by_request.items():
        good_queries = [e.get("query", "") for e in entries["good"]]
        good_queries += [e.get("query", "") for e in entries["suggested"]]

        if not good_queries:
            continue

        all_entries = entries["good"] + entries["suggested"]
        example = dspy.Example(
            title=f"Request #{rid}",
            looking_for="",  # We don't have full request data in feedback
            use_case="",
            solutions_of_interest=", ".join(
                set(e.get("soi", "") for e in all_entries if e.get("soi"))
            ),
            requirements="",
            out_of_scope="",
            reference_guide="",
            # Store good queries as metadata for the metric to reference
            _good_queries=good_queries,
        ).with_inputs(*INPUT_FIELDS)
        examples.append(example)

    suggested_count = sum(len(v["suggested"]) for v in by_request.values())
    logger.info(
        f"Built {len(examples)} training examples from feedback data "
        f"({len(positive)} good queries, {suggested_count} suggested alternatives)"
    )
    return examples


def build_manual_examples() -> List[dspy.Example]:
    """Build manual training examples from curated request/query pairs.

    These are stored in optimization/training_data/training_examples.json.
    Each entry has:
      - inputs: {title, looking_for, use_case, solutions_of_interest, ...}
      - good_queries: [{query, target_soi, expected_specificity, rationale}]
    """
    if not EXAMPLES_PATH.exists():
        logger.info(
            "No training_examples.json found. Create it with curated examples."
        )
        _create_example_template()
        return []

    data = json.loads(EXAMPLES_PATH.read_text())
    examples_data = data.get("examples", [])

    examples = []
    guide_text = GUIDE_PATH.read_text() if GUIDE_PATH.exists() else ""

    for entry in examples_data:
        inputs = entry.get("inputs", {})
        example = dspy.Example(
            title=inputs.get("title", ""),
            looking_for=inputs.get("looking_for", ""),
            use_case=inputs.get("use_case", ""),
            solutions_of_interest=inputs.get("solutions_of_interest", ""),
            requirements=inputs.get("requirements", ""),
            out_of_scope=inputs.get("out_of_scope", ""),
            reference_guide=guide_text,
            _good_queries=[q.get("query", "") for q in entry.get("good_queries", [])],
        ).with_inputs(*INPUT_FIELDS)
        examples.append(example)

    logger.info(f"Loaded {len(examples)} manual training examples")
    return examples


def build_all_examples() -> List[dspy.Example]:
    """Build complete training set from all sources."""
    examples = []
    examples.extend(build_manual_examples())
    examples.extend(build_from_feedback())

    logger.info(f"Total training examples: {len(examples)}")
    return examples


def _create_example_template():
    """Create a template training_examples.json with one sample entry."""
    EXAMPLES_PATH.parent.mkdir(parents=True, exist_ok=True)

    template = {
        "description": (
            "Curated training examples for GEPA optimization. "
            "Each example has input fields matching the GenerateQueries signature "
            "and a list of known-good queries."
        ),
        "examples": [
            {
                "inputs": {
                    "title": "Precision Fermentation for Dairy Proteins",
                    "looking_for": (
                        "Researchers with expertise in precision fermentation "
                        "of dairy proteins, particularly casein and whey."
                    ),
                    "use_case": (
                        "Replace animal-derived dairy proteins in cheese and "
                        "yogurt applications using microbial fermentation."
                    ),
                    "solutions_of_interest": (
                        "Microbial strain engineering, Bioprocess optimization, "
                        "Recombinant protein expression"
                    ),
                    "requirements": "TRL 4+ preferred, food-grade organisms",
                    "out_of_scope": "Plant-based protein alternatives",
                },
                "good_queries": [
                    {
                        "query": "precision fermentation casein production microbial",
                        "target_soi": "Microbial strain engineering",
                        "expected_specificity": "specific",
                        "rationale": (
                            "Targets researchers working on microbial production "
                            "of casein specifically"
                        ),
                    },
                    {
                        "query": "recombinant whey protein expression food grade",
                        "target_soi": "Recombinant protein expression",
                        "expected_specificity": "highly_specific",
                        "rationale": (
                            "Narrows to food-grade recombinant whey protein work"
                        ),
                    },
                ],
            },
        ],
    }

    EXAMPLES_PATH.write_text(json.dumps(template, indent=2))
    logger.info(f"Created template training_examples.json at {EXAMPLES_PATH}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    examples = build_all_examples()
    print(f"Built {len(examples)} total training examples")
