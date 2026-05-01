"""
Thin LiteLLM wrapper with optional prompt caching, tool-use, and retries.

All LLM traffic in core/ goes through here so caching / routing / retries are
applied consistently.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import litellm

from core.config import settings

logger = logging.getLogger(__name__)

litellm.drop_params = True

_ANTHROPIC_PREFIXES = ("anthropic/", "claude-")


def _is_anthropic(model: str) -> bool:
    m = model.lower()
    return any(m.startswith(p) for p in _ANTHROPIC_PREFIXES)


def _wrap_cache_system(system: str) -> Any:
    """
    Shape the system message so providers that support prompt caching
    (Anthropic via LiteLLM) can cache it. Falls through as plain string
    for providers that don't.
    """
    if not settings.PROMPT_CACHE_ENABLED or not system:
        return system
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def complete(
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    temperature: float = 0.0,
    max_retries: int = 4,
) -> str:
    """Single-shot completion. Returns the assistant message content."""
    model = model or settings.query_model
    messages: list[dict] = []
    if system:
        if _is_anthropic(model):
            messages.append({"role": "system", "content": _wrap_cache_system(system)})
        else:
            messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = litellm.completion(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM error (%s), attempt %d/%d: %s", model, attempt + 1, max_retries, e)
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_err}")


def tool_complete(
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
    system: str | None = None,
    temperature: float = 0.0,
    max_retries: int = 4,
) -> Any:
    """
    Tool-use completion. Returns the raw choice message (may have tool_calls).
    Caller drives the agent loop.
    """
    model = model or settings.agent_model
    full_messages: list[dict] = []
    if system:
        if _is_anthropic(model):
            full_messages.append({"role": "system", "content": _wrap_cache_system(system)})
        else:
            full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = litellm.completion(
                model=model,
                messages=full_messages,
                tools=tools,
                temperature=temperature,
            )
            return resp.choices[0].message
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("LLM tool error (%s), attempt %d/%d: %s", model, attempt + 1, max_retries, e)
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"LLM tool call failed after {max_retries} retries: {last_err}")
