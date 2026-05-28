"""VLM base class and message types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass
class VLMResponse:
    """Structured output from a vision-language model."""

    text: str
    raw_response: object = None


class BaseVLM(ABC):
    """Abstract interface for vision-language model providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        images: list[bytes] | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> VLMResponse:
        """Send prompt + images to the model and return the response."""
        ...

    @abstractmethod
    def generate_multimodal(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> VLMResponse:
        """Send a chat-style multimodal message list."""
        ...
