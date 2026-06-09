"""Abstract perceptor interface and factory.

The Perceptor is the "eyes" of the agent — it transforms a raw browser page
into a structured Perception that the Reasoner can consume.

Two modes:
    SoMPerceptor  — visual: screenshot + SoM bounding box annotation
    HTMLPerceptor — text: cleaned HTML + candidate element list
"""

from abc import ABC, abstractmethod

from visumark.core.types import Perception
from visumark.environment.base import BaseEnvironment
from visumark.perception.dom_bridge import DOMBridge


class BasePerceptor(ABC):
    """Abstract interface for page perception.

    Each implementation defines HOW the agent "sees" a page.
    """

    @abstractmethod
    async def perceive(
        self, env: BaseEnvironment
    ) -> tuple[Perception, DOMBridge]:
        """Perceive the current page and return a structured representation.

        Args:
            env: The browser environment (live or offline).

        Returns:
            Tuple of (Perception, DOMBridge).
            - Perception: what the reasoner consumes (screenshot, elements, metadata)
            - DOMBridge: SoM ID ↔ DOM node mapping for execution and evaluation
        """
        ...


class PerceptorFactory:
    """Create the appropriate perceptor based on configuration."""

    @staticmethod
    def create(mode: str, config: dict) -> BasePerceptor:
        """Factory method.

        Args:
            mode: "som" or "html"
            config: Perception configuration dict.

        Returns:
            Concrete BasePerceptor instance.

        Raises:
            ValueError: If mode is not recognized.
        """
        if mode == "som":
            from visumark.perception.som_perceptor import SoMPerceptor
            return SoMPerceptor(config.get("som", {}))

        if mode == "html":
            from visumark.perception.html_perceptor import HTMLPerceptor
            return HTMLPerceptor(config.get("html", {}))

        raise ValueError(
            f"Unknown perception mode: '{mode}'. Expected 'som' or 'html'."
        )
