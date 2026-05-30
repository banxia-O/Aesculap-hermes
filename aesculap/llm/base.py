"""LLM provider thin-adapter interface (PRD §5.1, §14).

Provider-agnostic by design: the engine never hard-codes a model or vendor. A
provider is just "given a system + user prompt, return text". Adapters are tiny
and self-built (no litellm) to keep the self-healing tool itself simple and
dependency-light.

Keys are read from environment variables named in config (`api_key_env`); the
key value never lives in config or logs (aligns with PRD §8.3 key-safety).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Any failure talking to the provider (network, auth, bad response)."""


@dataclass
class LLMResponse:
    """A completion result. `text` is the raw model output."""

    text: str
    model: str = ""
    raw: object = None


class LLMProvider:
    """Base class for a provider adapter."""

    name: str = ""

    def __init__(self, model: str, api_key: str = "", base_url: str = ""):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> LLMResponse:
        raise NotImplementedError  # pragma: no cover


def resolve_api_key(api_key_env: str) -> str:
    """Read the key from the named env var. Empty if unset (caller decides)."""
    if not api_key_env:
        return ""
    return os.environ.get(api_key_env, "")
