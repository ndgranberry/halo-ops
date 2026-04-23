#!/usr/bin/env python3
"""
Agent Scout — Prompt Store
============================
Version, persist, and retrieve GEPA-optimized prompts.
Each optimization run produces a versioned prompt file.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROMPT_DIR = BASE_DIR / "prompt_versions"
PROMPT_DIR.mkdir(exist_ok=True)
ACTIVE_FILE = PROMPT_DIR / "active.json"


def save_prompt(
    prompt_text: str,
    metadata: Dict[str, Any],
    metrics: Dict[str, Any],
) -> str:
    """
    Save an optimized prompt version. Returns the version ID.
    Does NOT auto-activate — call activate_version() separately.
    """
    # Determine next version number
    existing = list_versions()
    next_num = len(existing) + 1
    version_id = f"v{next_num}"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{version_id}_{timestamp}.json"

    data = {
        "version": version_id,
        "created_at": datetime.now().isoformat(),
        "prompt_text": prompt_text,
        "metadata": metadata,
        "metrics": metrics,
    }

    path = PROMPT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info(f"Saved prompt version {version_id} to {path}")
    return version_id


def activate_version(version_id: str) -> bool:
    """Set a version as the active prompt."""
    # Find the file
    for path in PROMPT_DIR.glob("*.json"):
        if path.name == "active.json":
            continue
        try:
            with open(path) as f:
                data = json.load(f)
            if data.get("version") == version_id:
                active = {"version": version_id, "path": str(path.name), "activated_at": datetime.now().isoformat()}
                with open(ACTIVE_FILE, "w") as f:
                    json.dump(active, f, indent=2)
                logger.info(f"Activated prompt version {version_id}")
                return True
        except (json.JSONDecodeError, KeyError):
            continue
    logger.error(f"Version {version_id} not found")
    return False


def get_active_prompt() -> Optional[str]:
    """
    Get the currently active optimized prompt text.
    Returns None if no optimized prompt is active (use baseline).
    """
    if not ACTIVE_FILE.exists():
        return None

    try:
        with open(ACTIVE_FILE) as f:
            active = json.load(f)
        version_file = PROMPT_DIR / active["path"]
        with open(version_file) as f:
            data = json.load(f)
        return data["prompt_text"]
    except Exception as e:
        logger.warning(f"Could not load active prompt: {e}")
        return None


def get_active_version_info() -> Optional[Dict[str, Any]]:
    """Get full info about the active version."""
    if not ACTIVE_FILE.exists():
        return None
    try:
        with open(ACTIVE_FILE) as f:
            active = json.load(f)
        version_file = PROMPT_DIR / active["path"]
        with open(version_file) as f:
            return json.load(f)
    except Exception:
        return None


def list_versions() -> List[Dict[str, Any]]:
    """List all saved prompt versions with their metrics."""
    versions = []
    for path in sorted(PROMPT_DIR.glob("v*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            versions.append({
                "version": data["version"],
                "created_at": data["created_at"],
                "file": path.name,
                "metrics": data.get("metrics", {}),
                "dataset_size": data.get("metadata", {}).get("dataset_size", "?"),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return versions


def save_baseline(prompt_text: str) -> None:
    """Save the original hand-crafted prompt as baseline for comparison."""
    path = PROMPT_DIR / "baseline.json"
    if path.exists():
        return  # Already saved
    data = {
        "version": "baseline",
        "created_at": datetime.now().isoformat(),
        "prompt_text": prompt_text,
        "metadata": {"source": "hand-crafted FIT_SCORING_SYSTEM from prompts.py"},
        "metrics": {},
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Saved baseline prompt")
