"""
core/llm_client.py — Unified LLM client factory.

Primary: Claude Haiku 4.5 via Anthropic API (set ANTHROPIC_API_KEY).
Optional fallback: OpenRouter — set OPENROUTER_API_KEY to enable
  (key prefix: sk-or-v1-...; routes through openrouter.ai/api/v1).
Heuristic-only mode is used when no key is set.

Usage:
    from core.llm_client import get_client, is_available
    client = get_client()   # None if no key
"""

from __future__ import annotations

import os
from typing import Optional

# OpenRouter endpoint compatible with Anthropic SDK
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Model aliases for OpenRouter (maps Anthropic model names → OpenRouter names)
OPENROUTER_MODEL_MAP = {
    "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
    "claude-haiku-4-5-20250514": "anthropic/claude-haiku-4-5",
    "claude-3-haiku-20240307": "anthropic/claude-3-haiku",
    "claude-sonnet-4-5": "anthropic/claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022": "anthropic/claude-3-5-sonnet",
    "claude-3-5-sonnet-20240620": "anthropic/claude-3-5-sonnet",
}


def get_api_key() -> Optional[str]:
    """Return the configured API key (Anthropic or OpenRouter)."""
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENROUTER_API_KEY")


def is_openrouter_key(key: str) -> bool:
    """Return True if the key is an OpenRouter key."""
    return key.startswith("sk-or-")


def is_available() -> bool:
    """Return True if an LLM API key is configured."""
    key = get_api_key()
    return bool(key and len(key) > 10)


def get_client():
    """
    Return a configured Anthropic client.
    - Anthropic key: primary path, uses Anthropic API directly.
    - OpenRouter key: optional fallback, routes to openrouter.ai/api/v1.
    - If no key: returns None (heuristic-only mode).
    """
    key = get_api_key()
    if not key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    if is_openrouter_key(key):
        # OpenRouter exposes the Anthropic-compatible API at /api/v1
        return anthropic.Anthropic(
            api_key=key,
            base_url=_OPENROUTER_BASE_URL,
            default_headers={
                "HTTP-Referer": "https://github.com/chourasiavinit9-dev/APEX-Ai",
                "X-Title": "APEX Product Intelligence Pipeline",
            },
        )
    else:
        return anthropic.Anthropic(api_key=key)


def resolve_model(model_name: str, client=None) -> str:
    """
    Resolve a model name for the current provider.
    OpenRouter needs prefixed model names; Anthropic uses them directly.
    """
    key = get_api_key() or ""
    if is_openrouter_key(key):
        return OPENROUTER_MODEL_MAP.get(model_name, f"anthropic/{model_name}")
    return model_name
