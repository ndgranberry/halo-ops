#!/usr/bin/env python3
"""
Agent Scout — Shared Claude API Client
========================================
Single wrapper around the Anthropic Messages API with retry logic
and prompt caching support.

Used by fit_scorer, person_discovery, and exa_discovery.

Caching:
- The `system` prompt is automatically wrapped with cache_control. For
  Sonnet 4.6 the minimum cacheable prefix is 2048 tokens; smaller prompts
  silently won't cache (no error).
- Callers may pass `user` as either a plain string or a pre-built list of
  content blocks. Use `build_cached_user_blocks(prefix, suffix)` to opt
  into caching the run-constant prefix of a per-call user prompt.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Union

import anthropic

logger = logging.getLogger(__name__)


def build_cached_user_blocks(cached_prefix: str, variable_suffix: str) -> List[Dict[str, Any]]:
    """Build a user-content list that caches `cached_prefix` and leaves the
    `variable_suffix` (per-call data) uncached.

    Use when the run-constant portion of a per-call prompt is large enough
    to benefit from caching (>= the model's min cacheable prefix).
    """
    return [
        {"type": "text", "text": cached_prefix, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": variable_suffix},
    ]


class ClaudeClient:
    """Thin wrapper around the Anthropic Messages API with exponential backoff."""

    def __init__(self, model: str, temperature: float = 0.3):
        self.client = anthropic.Anthropic()
        self.model = model
        self.temperature = temperature
        # Counters are read/written from worker threads (FitScorer, ExaDiscovery
        # extraction). The GIL makes individual `+= 1` mostly-safe, but
        # accumulating a multi-field usage record from a response object is not
        # atomic — guard with a lock to keep totals truthful.
        self._counter_lock = threading.Lock()
        self.call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_tokens = 0
        self.cache_read_tokens = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _system_blocks(system: str) -> List[Dict[str, Any]]:
        """Wrap a system string as a single cache-controlled text block."""
        return [{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }]

    @staticmethod
    def _user_content(user: Union[str, List[Dict[str, Any]]]) -> Union[str, List[Dict[str, Any]]]:
        """Pass through pre-built block lists; leave plain strings as-is."""
        return user

    def _record_call(self, response) -> None:
        """Atomically increment call_count and accumulate usage metrics.

        Tolerates SDK shape changes — counts are best-effort, but the
        increment of `call_count` is guaranteed even if usage parsing fails.
        """
        # Compute deltas outside the lock (response.usage is local), then
        # commit under the lock to keep cross-thread totals truthful.
        delta = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
        }
        try:
            usage = response.usage
            delta["input_tokens"] = getattr(usage, "input_tokens", 0) or 0
            delta["output_tokens"] = getattr(usage, "output_tokens", 0) or 0
            delta["cache_creation_tokens"] = getattr(usage, "cache_creation_input_tokens", 0) or 0
            delta["cache_read_tokens"] = getattr(usage, "cache_read_input_tokens", 0) or 0
        except Exception:
            pass

        with self._counter_lock:
            self.call_count += 1
            self.input_tokens += delta["input_tokens"]
            self.output_tokens += delta["output_tokens"]
            self.cache_creation_tokens += delta["cache_creation_tokens"]
            self.cache_read_tokens += delta["cache_read_tokens"]

    def usage_summary(self) -> Dict[str, int]:
        """Snapshot of token usage so the orchestrator can log/cost-estimate."""
        with self._counter_lock:
            return {
                "calls": self.call_count,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_creation_tokens": self.cache_creation_tokens,
                "cache_read_tokens": self.cache_read_tokens,
            }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(
        self,
        system: str,
        user: Union[str, List[Dict[str, Any]]],
        max_tokens: int = 500,
        temperature: Optional[float] = None,
        retry_count: int = 0,
    ) -> Optional[str]:
        """Call Claude API. Returns response text or None on failure."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature if temperature is not None else self.temperature,
                system=self._system_blocks(system),
                messages=[{"role": "user", "content": self._user_content(user)}],
            )
            self._record_call(response)

            if response.content and response.content[0].text:
                return response.content[0].text
            return None

        except anthropic.RateLimitError:
            if retry_count < 3:
                wait_time = (2 ** retry_count) + 1
                logger.warning(f"Rate limit hit. Retry {retry_count + 1}/3 after {wait_time:.0f}s")
                time.sleep(wait_time)
                return self.call(system, user, max_tokens, temperature, retry_count + 1)
            logger.error("Rate limit exceeded after 3 retries")
            return None

        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            if retry_count < 3:
                wait_time = (2 ** retry_count) + 1
                logger.warning(f"Transient API error ({type(e).__name__}). Retry {retry_count + 1}/3 after {wait_time:.0f}s")
                time.sleep(wait_time)
                return self.call(system, user, max_tokens, temperature, retry_count + 1)
            logger.error(f"Transient API error after 3 retries: {e}")
            return None

        except anthropic.APIStatusError as e:
            if e.status_code >= 500 and retry_count < 3:
                wait_time = (2 ** retry_count) + 1
                logger.warning(f"Server error {e.status_code}. Retry {retry_count + 1}/3 after {wait_time:.0f}s")
                time.sleep(wait_time)
                return self.call(system, user, max_tokens, temperature, retry_count + 1)
            logger.error(f"Claude API call failed: {e}")
            return None

        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            return None

    def call_with_tools(
        self,
        system: str,
        user: Union[str, List[Dict[str, Any]]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 500,
        temperature: Optional[float] = None,
        tool_choice: Optional[Dict[str, str]] = None,
        retry_count: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Call Claude with tool use. Returns the tool input dict or None on failure.

        When tool_choice forces a specific tool, Claude is guaranteed to return
        structured output matching the tool's input_schema — no regex parsing needed.
        """
        try:
            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature if temperature is not None else self.temperature,
                "system": self._system_blocks(system),
                "messages": [{"role": "user", "content": self._user_content(user)}],
                "tools": tools,
            }
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

            response = self.client.messages.create(**kwargs)
            self._record_call(response)

            # Extract the tool_use block from the response
            for block in response.content:
                if block.type == "tool_use":
                    return block.input

            # No tool_use block found — Claude responded with text instead
            logger.warning("Claude returned text instead of tool call")
            return None

        except anthropic.RateLimitError:
            if retry_count < 3:
                wait_time = (2 ** retry_count) + 1
                logger.warning(f"Rate limit hit. Retry {retry_count + 1}/3 after {wait_time:.0f}s")
                time.sleep(wait_time)
                return self.call_with_tools(
                    system, user, tools, max_tokens, temperature, tool_choice, retry_count + 1
                )
            logger.error("Rate limit exceeded after 3 retries")
            return None

        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
            if retry_count < 3:
                wait_time = (2 ** retry_count) + 1
                logger.warning(f"Transient API error ({type(e).__name__}). Retry {retry_count + 1}/3 after {wait_time:.0f}s")
                time.sleep(wait_time)
                return self.call_with_tools(
                    system, user, tools, max_tokens, temperature, tool_choice, retry_count + 1
                )
            logger.error(f"Transient API error after 3 retries: {e}")
            return None

        except anthropic.APIStatusError as e:
            if e.status_code >= 500 and retry_count < 3:
                wait_time = (2 ** retry_count) + 1
                logger.warning(f"Server error {e.status_code}. Retry {retry_count + 1}/3 after {wait_time:.0f}s")
                time.sleep(wait_time)
                return self.call_with_tools(
                    system, user, tools, max_tokens, temperature, tool_choice, retry_count + 1
                )
            logger.error(f"Claude API call (tool use) failed: {e}")
            return None

        except Exception as e:
            logger.error(f"Claude API call (tool use) failed: {e}")
            return None
