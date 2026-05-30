"""Provider adapters (PRD §5.1, §14): OpenAI, Anthropic, OpenAI-compatible.

Each adapter lazily imports its SDK so the package installs without every
vendor SDK present (extras in pyproject). The OpenAI-compatible adapter covers
self-hosted / local endpoints (vLLM, Ollama, etc.) via a configurable base_url,
satisfying the confirmed "OpenAI + Anthropic + OpenAI-compatible" scope.
"""

from __future__ import annotations

from aesculap.config import TriageConfig, SelfFixConfig
from aesculap.llm.base import LLMError, LLMProvider, LLMResponse, resolve_api_key


class OpenAIProvider(LLMProvider):
    name = "openai"

    def _client(self):
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover - env-dependent
            raise LLMError("openai SDK not installed (`pip install openai`)") from e
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return OpenAI(**kwargs)

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> LLMResponse:
        try:
            client = self._client()
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            text = resp.choices[0].message.content or ""
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - normalize SDK errors
            raise LLMError(f"openai completion failed: {e}") from e
        return LLMResponse(text=text, model=self.model, raw=resp)


class OpenAICompatibleProvider(OpenAIProvider):
    """OpenAI-compatible endpoint (vLLM/Ollama/etc.); requires base_url."""

    name = "openai_compatible"

    def _client(self):
        if not self.base_url:
            raise LLMError("openai_compatible provider requires `base_url`")
        return super()._client()


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def _client(self):
        try:
            from anthropic import Anthropic
        except ImportError as e:  # pragma: no cover - env-dependent
            raise LLMError("anthropic SDK not installed (`pip install anthropic`)") from e
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return Anthropic(**kwargs)

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> LLMResponse:
        try:
            client = self._client()
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            # Concatenate text blocks.
            text = "".join(
                block.text for block in resp.content
                if getattr(block, "type", None) == "text"
            )
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"anthropic completion failed: {e}") from e
        return LLMResponse(text=text, model=self.model, raw=resp)


_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}


def build_provider(
    provider: str, model: str, api_key_env: str = "", base_url: str = ""
) -> LLMProvider:
    """Construct a provider adapter from config values."""
    if provider not in _PROVIDERS:
        raise LLMError(
            f"unknown provider {provider!r}; known: {sorted(_PROVIDERS)}"
        )
    if not model:
        raise LLMError(f"provider {provider!r} requires a model")
    return _PROVIDERS[provider](
        model=model, api_key=resolve_api_key(api_key_env), base_url=base_url
    )


def provider_from_triage(cfg: TriageConfig) -> LLMProvider:
    return build_provider(cfg.provider, cfg.model, cfg.api_key_env, cfg.base_url)


def provider_from_selffix(cfg: SelfFixConfig) -> LLMProvider:
    return build_provider(cfg.provider, cfg.model, cfg.api_key_env, cfg.base_url)
