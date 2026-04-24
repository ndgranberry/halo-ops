#!/usr/bin/env python3
"""
Agent Scout — Unified LLM Client
==================================
Provider-agnostic wrapper around LiteLLM's completion API.

Supports Anthropic (claude-*), Google (gemini/*), OpenAI, and others.
Auto-detects provider from the model prefix.

Used by fit_scorer, person_discovery, solve_planner, and exa_discovery.
"""

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Union

import litellm

# Suppress LiteLLM's verbose INFO logging and ANSI color codes that corrupt log files
litellm.suppress_debug_info = True
# Silently drop params that a given provider doesn't support (e.g. `thinking` for Gemini)
litellm.drop_params = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def build_cached_user_blocks(cached_prefix: str, variable_suffix: str) -> str:
    """Compatibility shim — cache blocks are ignored for non-Anthropic providers.

    Returns the concatenated string. When Anthropic is used via LiteLLM,
    we lose explicit cache control; it's worth the provider portability.
    """
    return cached_prefix + "\n\n" + variable_suffix


def _normalize_model(model: str) -> str:
    """Add a provider prefix if missing. Defaults to anthropic for bare claude-*."""
    if "/" in model:
        return model
    if model.startswith("claude-"):
        return f"anthropic/{model}"
    return model


def _convert_tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Anthropic-style tool → OpenAI-style for LiteLLM."""
    if "function" in tool:
        return tool  # already OpenAI format
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        },
    }


def _convert_tool_choice(tool_choice: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Convert Anthropic-style tool_choice → OpenAI-style."""
    if tool_choice is None:
        return None
    if tool_choice.get("type") == "tool":
        return {"type": "function", "function": {"name": tool_choice["name"]}}
    return tool_choice


class ClaudeClient:
    """Provider-agnostic LLM client. Name kept for backwards compatibility."""

    def __init__(self, model: str, temperature: float = 0.3):
        self.model = _normalize_model(model)
        self.temperature = temperature
        self._counter_lock = threading.Lock()
        self.call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_tokens = 0
        self.cache_read_tokens = 0

    def _record_call(self, response) -> None:
        delta = {"input_tokens": 0, "output_tokens": 0}
        try:
            usage = response.get("usage") if isinstance(response, dict) else response.usage
            if usage:
                delta["input_tokens"] = getattr(usage, "prompt_tokens", 0) or usage.get("prompt_tokens", 0) if hasattr(usage, "get") else getattr(usage, "prompt_tokens", 0)
                delta["output_tokens"] = getattr(usage, "completion_tokens", 0) or usage.get("completion_tokens", 0) if hasattr(usage, "get") else getattr(usage, "completion_tokens", 0)
        except Exception:
            pass

        with self._counter_lock:
            self.call_count += 1
            self.input_tokens += delta["input_tokens"]
            self.output_tokens += delta["output_tokens"]

    def usage_summary(self) -> Dict[str, int]:
        with self._counter_lock:
            return {
                "calls": self.call_count,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_creation_tokens": self.cache_creation_tokens,
                "cache_read_tokens": self.cache_read_tokens,
            }

    def _user_to_string(self, user: Union[str, List[Dict[str, Any]]]) -> str:
        """Flatten cached-block user content into a plain string for LiteLLM."""
        if isinstance(user, str):
            return user
        parts = []
        for block in user:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n\n".join(parts)

    def call(
        self,
        system: str,
        user: Union[str, List[Dict[str, Any]]],
        max_tokens: int = 500,
        temperature: Optional[float] = None,
        retry_count: int = 0,
    ) -> Optional[str]:
        """Call the LLM. Returns response text or None on failure."""
        try:
            response = litellm.completion(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature if temperature is not None else self.temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": self._user_to_string(user)},
                ],
                timeout=120,
            )
            self._record_call(response)

            choice = response.choices[0]
            content = choice.message.content
            return content if content else None

        except Exception as e:
            if retry_count < 3 and _is_retryable(e):
                wait_time = (2 ** retry_count) + 1
                logger.warning(
                    f"LLM call failed ({type(e).__name__}: {e}). "
                    f"Retry {retry_count + 1}/3 after {wait_time:.0f}s"
                )
                time.sleep(wait_time)
                return self.call(system, user, max_tokens, temperature, retry_count + 1)
            logger.error(f"LLM call failed after retries: {e}")
            return None

    def call_with_tools(
        self,
        system: str,
        user: Union[str, List[Dict[str, Any]]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 500,
        temperature: Optional[float] = None,
        tool_choice: Optional[Dict[str, Any]] = None,
        retry_count: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Call the LLM with tool use. Returns the tool input dict or None on failure."""
        try:
            converted_tools = [_convert_tool(t) for t in tools]
            converted_choice = _convert_tool_choice(tool_choice)

            kwargs = {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature if temperature is not None else self.temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": self._user_to_string(user)},
                ],
                "tools": converted_tools,
            }
            if converted_choice:
                kwargs["tool_choice"] = converted_choice

            kwargs["timeout"] = 120
            response = litellm.completion(**kwargs)
            self._record_call(response)

            choice = response.choices[0]
            tool_calls = getattr(choice.message, "tool_calls", None)
            if tool_calls:
                args = tool_calls[0].function.arguments
                if isinstance(args, str):
                    return json.loads(args)
                return args

            logger.warning("LLM returned text instead of tool call")
            return None

        except Exception as e:
            if retry_count < 3 and _is_retryable(e):
                wait_time = (2 ** retry_count) + 1
                logger.warning(
                    f"LLM tool call failed ({type(e).__name__}: {e}). "
                    f"Retry {retry_count + 1}/3 after {wait_time:.0f}s"
                )
                time.sleep(wait_time)
                return self.call_with_tools(
                    system, user, tools, max_tokens, temperature, tool_choice, retry_count + 1
                )
            logger.error(f"LLM tool call failed after retries: {e}")
            return None


def _is_retryable(e: Exception) -> bool:
    """Rate limits, timeouts, and 5xx errors are retryable."""
    name = type(e).__name__.lower()
    msg = str(e).lower()
    return any(k in name for k in ("ratelimit", "timeout", "connection")) or \
           any(k in msg for k in ("rate limit", "timeout", "503", "502", "500", "overloaded"))
