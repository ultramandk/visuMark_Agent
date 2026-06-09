"""Perception layer — how the agent "sees" the page (SoM visual + HTML text)."""

from visumark.perception.base import BasePerceptor
from visumark.perception.dom_bridge import DOMBridge
from visumark.perception.som_marker import SoMMarker
from visumark.perception.element_extractor import ElementExtractor, PageElement

__all__ = [
    "BasePerceptor",
    "DOMBridge",
    "SoMMarker",
    "ElementExtractor",
    "PageElement",
]
