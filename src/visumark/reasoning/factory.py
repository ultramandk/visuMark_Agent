"""Reasoner factory — creates VLM/LLM provider instances from configuration."""

from typing import Any

from visumark.reasoning.base import BaseReasoner


class ReasonerFactory:
    """Factory that resolves a provider string to a concrete Reasoner instance.

    All providers accept a standard set of parameters:
        model, api_key, base_url, temperature, max_tokens, timeout, max_retries

    Usage:
        reasoner = ReasonerFactory.create(
            provider="qwen",
            model="qwen3-vl-8b-instruct",
            api_key="...",
            ...
        )
    """

    _registry: dict[str, type[BaseReasoner]] = {}

    @classmethod
    def register(cls, provider: str, reasoner_cls: type[BaseReasoner]) -> None:
        """Register a provider class (for extensibility)."""
        cls._registry[provider] = reasoner_cls

    @classmethod
    def create(cls, provider: str, model: str | None = None, **kwargs: Any) -> BaseReasoner:
        """Create a reasoner instance for the given provider.

        Args:
            provider: Provider name ("openai", "anthropic", "qwen", "local").
            model: Model name override (uses provider default if not specified).
            **kwargs: Additional provider-specific parameters (api_key, base_url,
                      temperature, max_tokens, timeout, max_retries).

        Returns:
            A concrete BaseReasoner instance.

        Raises:
            ValueError: If the provider is not registered.
        """
        # Lazy import to avoid circular dependencies
        cls._ensure_registered()

        provider_lower = provider.lower()
        if provider_lower not in cls._registry:
            available = ", ".join(cls._registry.keys())
            raise ValueError(
                f"Unknown provider: '{provider}'. Available: {available}"
            )

        reasoner_cls = cls._registry[provider_lower]
        return reasoner_cls(provider=provider_lower, model=model, **kwargs)

    @classmethod
    def _ensure_registered(cls) -> None:
        """Lazy-register built-in providers."""
        if cls._registry:
            return  # Already registered

        from visumark.reasoning.providers.openai import OpenAIReasoner
        from visumark.reasoning.providers.qwen import QwenReasoner
        from visumark.reasoning.providers.anthropic import AnthropicReasoner
        from visumark.reasoning.providers.local import LocalReasoner

        cls._registry.update({
            "openai": OpenAIReasoner,
            "qwen": QwenReasoner,
            "anthropic": AnthropicReasoner,
            "local": LocalReasoner,
        })
