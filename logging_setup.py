#!/usr/bin/env python3
"""
RoboScout — Logging setup

Provides:
- Consistent formatting across all entry points (run_daily, single request).
- Optional JSON structured logging (set ROBOSCOUT_LOG_JSON=1).
- Run-ID correlation via a contextvar — every log line emitted during a
  pipeline run is tagged with the same run_id, so you can trace one
  request through multiple stages (and subprocess boundaries if the id
  is propagated via env/CLI).

Usage:
    from logging_setup import configure_logging, set_run_id, new_run_id

    configure_logging()              # idempotent
    rid = new_run_id()               # uuid4-based
    set_run_id(rid)                  # all subsequent logs include it
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import settings

_run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "roboscout_run_id", default=""
)

_configured = False


def new_run_id(prefix: str = "rsqg") -> str:
    """Generate a new run id. Not thread-bound — caller must set_run_id()."""
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def set_run_id(run_id: str) -> None:
    """Stamp subsequent log lines with this run_id."""
    _run_id_var.set(run_id)


def current_run_id() -> str:
    return _run_id_var.get()


class _RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get() or "-"
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "run_id": getattr(record, "run_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    *,
    to_stderr: bool = False,
    log_file: Optional[Path] = None,
    force: bool = False,
) -> None:
    """Configure the root logger.

    - Honors ROBOSCOUT_LOG_JSON / ROBOSCOUT_LOG_LEVEL via settings.
    - If ``to_stderr`` is True, log stream is stderr (used when JSON
      output goes to stdout, e.g. n8n integration).
    - If ``log_file`` is given, adds a FileHandler alongside the stream.
    - Idempotent unless force=True.
    """
    global _configured
    if _configured and not force:
        return

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    level = getattr(logging, settings.log_level, logging.INFO)
    root.setLevel(level)

    text_fmt = (
        "%(asctime)s [%(run_id)s] %(levelname)s %(name)s: %(message)s"
    )
    formatter: logging.Formatter = (
        _JsonFormatter()
        if settings.log_json
        else logging.Formatter(text_fmt, datefmt="%Y-%m-%d %H:%M:%S")
    )

    stream = sys.stderr if to_stderr else sys.stdout
    sh = logging.StreamHandler(stream)
    sh.setFormatter(formatter)
    sh.addFilter(_RunIdFilter())
    root.addHandler(sh)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(formatter)
        fh.addFilter(_RunIdFilter())
        root.addHandler(fh)

    _configured = True


class timed_stage:
    """Context manager that logs elapsed time for a pipeline stage."""

    def __init__(self, name: str, logger: Optional[logging.Logger] = None):
        self.name = name
        self.logger = logger or logging.getLogger("roboscout_query_gen.timing")
        self._start = 0.0

    def __enter__(self) -> timed_stage:
        import time

        self._start = time.monotonic()
        self.logger.info("▶ stage start: %s", self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        import time

        elapsed = time.monotonic() - self._start
        status = "ok" if exc_type is None else f"error ({exc_type.__name__})"
        self.logger.info("■ stage done: %s (%.2fs, %s)", self.name, elapsed, status)
        return False


# Propagate run_id across subprocess boundaries via env var.
RUN_ID_ENV = "ROBOSCOUT_RUN_ID"


def inherit_run_id_from_env() -> None:
    """If ROBOSCOUT_RUN_ID is set in env, adopt it as the current run_id."""
    inherited = os.getenv(RUN_ID_ENV, "").strip()
    if inherited:
        set_run_id(inherited)


def export_run_id_to_env(env: dict) -> dict:
    """Return a copy of env with ROBOSCOUT_RUN_ID set to the current run_id."""
    env = dict(env)
    rid = current_run_id()
    if rid:
        env[RUN_ID_ENV] = rid
    return env
