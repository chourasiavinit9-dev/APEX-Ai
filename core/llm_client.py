"""
core/llm_client.py — Unified LLM client factory.

Primary: Claude Haiku 4.5 via Anthropic API (set ANTHROPIC_API_KEY).
Optional fallback: OpenRouter — set OPENROUTER_API_KEY (or ANTHROPIC_API_KEY with sk-or- prefix).
Heuristic-only mode is used when no key is set.

Usage:
    from core.llm_client import get_client, is_available
    client = get_client()   # None if no key
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    load_dotenv(Path(__file__).parent.parent / ".env.local")
except Exception:
    pass

from core.constants import CLASSIFICATION_MODEL, EXTRACTION_MODEL

# OpenRouter endpoint compatible with Anthropic SDK
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

# Model aliases for OpenRouter (maps Anthropic model names → verified OpenRouter names)
OPENROUTER_MODEL_MAP = {
    CLASSIFICATION_MODEL: "anthropic/claude-3-haiku",
    f"{CLASSIFICATION_MODEL}-20250514": "anthropic/claude-3-haiku",
    "claude-3-haiku-20240307": "anthropic/claude-3-haiku",
    EXTRACTION_MODEL: "anthropic/claude-3-haiku",
    "claude-3-5-sonnet-20241022": "anthropic/claude-3-haiku",
    "claude-3-5-sonnet-20240620": "anthropic/claude-3-haiku",
    "claude-sonnet-4-8": "anthropic/claude-3-haiku",
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


class _OpenRouterContentBlock:
    def __init__(self, text: str):
        self.text = text


class _OpenRouterMessageResponse:
    def __init__(self, text: str):
        self.content = [_OpenRouterContentBlock(text)]


class _OpenRouterMessages:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def create(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        max_tokens: int = 1000,
        **kwargs: Any,
    ) -> _OpenRouterMessageResponse:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": str(system)})
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):
                # If multipart/multimodal, extract text parts
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = " ".join(text_parts) if text_parts else str(content)
            api_messages.append({"role": m.get("role", "user"), "content": str(content)})

        resolved_model = resolve_model(model)
        payload = {
            "model": resolved_model,
            "messages": api_messages,
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            _OPENROUTER_BASE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/chourasiavinit9-dev/APEX-Ai",
                "X-Title": "APEX Product Intelligence Platform",
            },
            data=json.dumps(payload).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choice = data["choices"][0]["message"]
            text_out = choice.get("content") or ""
            return _OpenRouterMessageResponse(text_out)


class OpenRouterClient:
    """Drop-in Anthropic client wrapper for OpenRouter."""
    def __init__(self, api_key: str):
        self.messages = _OpenRouterMessages(api_key)


def get_client():
    """
    Return a configured Anthropic-compatible client.
    - OpenRouter key (sk-or-...): routes via OpenRouter API adapter.
    - Anthropic key: native Anthropic SDK client.
    - If no key: returns None (heuristic-only mode).
    """
    key = get_api_key()
    if not key:
        return None

    if is_openrouter_key(key):
        return OpenRouterClient(api_key=key)

    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None


def resolve_model(model_name: str, client=None) -> str:
    """Resolve a model name for the current provider."""
    key = get_api_key() or ""
    if is_openrouter_key(key):
        return OPENROUTER_MODEL_MAP.get(model_name, "anthropic/claude-3-haiku")
    return model_name
