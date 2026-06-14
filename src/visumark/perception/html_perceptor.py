"""HTML text-mode Perceptor — extracts candidate elements from Mind2Web data.

This is the TEXT-BASED auxiliary path for Mind2Web evaluation.  Instead of
taking a screenshot and drawing SoM labels, it reads the Mind2Web candidate
list (pos_candidates + neg_candidates) directly from the ground-truth data,
extracts text content for each candidate from the cleaned_html, and produces
a numbered list that the LLM selects from.

Architecture:
    1. Receive candidate dicts (from Mind2Web JSON, not from DOM extraction)
    2. Parse each candidate's attributes JSON (class, aria_label, bbox, etc.)
    3. Extract visible text by searching cleaned_html for the backend_node_id
    4. Build PageElement objects and DOMBridge mapping
    5. Prompt builder (in html_prompts.py) formats them for the LLM
"""

import json
import re

from loguru import logger

from visumark.core.types import PageElement, Perception
from visumark.environment.base import BaseEnvironment
from visumark.perception.base import BasePerceptor
from visumark.perception.dom_bridge import DOMBridge
from visumark.environment.dom_utils import clean_text


class HTMLPerceptor(BasePerceptor):
    """Text-mode perception for Mind2Web evaluation.

    Uses the ground-truth candidate list from Mind2Web data directly,
    WITHOUT any DOM extraction or browser screenshot.  This is the
    approach closest to the original Mind2Web paper.

    Usage (eval):
        perceptor = HTMLPerceptor({"max_candidates": 200})
        perception, bridge = await perceptor.perceive(
            env, candidates=pos_candidates + neg_candidates
        )
        # perception.elements now contains numbered candidates
        # bridge maps indices → backend_node_ids
    """

    def __init__(self, config: dict):
        self.max_candidates = config.get("max_candidates", 200)
        self.cleaning_preset = config.get("cleaning_preset", "mind2web")

    async def perceive(
        self,
        env: BaseEnvironment,
        candidates: list[dict] | None = None,
    ) -> tuple[Perception, DOMBridge]:
        """Extract candidate elements from Mind2Web data.

        Args:
            env: Offline environment with cleaned_html loaded.
            candidates: Mind2Web candidate dicts, each with:
                tag, backend_node_id, attributes (JSON string), is_top_level_target

        Returns:
            (Perception, DOMBridge) — elements are the numbered candidates.
        """
        html = await env.get_page_html()
        title = await env.get_page_title()
        url = await env.get_page_url()

        if not candidates:
            logger.warning("No candidates provided — returning empty perception")
            return (
                Perception(page_title=title, page_url=url),
                DOMBridge(),
            )

        # Limit candidates
        candidates = candidates[:self.max_candidates]

        # Build PageElement for each candidate
        elements: list[PageElement] = []
        for i, cand in enumerate(candidates):
            attrs_str = cand.get("attributes", "{}")
            tag = cand.get("tag", "")
            backend_id = str(cand.get("backend_node_id", ""))

            # Parse the JSON attributes blob
            attrs: dict = {}
            try:
                attrs = json.loads(attrs_str) if isinstance(attrs_str, str) else attrs_str
            except (json.JSONDecodeError, TypeError):
                attrs = {}

            # Extract meaningful attributes
            class_name = attrs.get("class", "")
            elem_id = attrs.get("id", "")
            aria_label = attrs.get("aria_label", "")
            bbox_str = attrs.get("bounding_box_rect", "")

            # Parse bounding_box_rect: "x,y,w,h" (pixel coordinates)
            bbox = self._parse_bbox(bbox_str)

            # Extract visible text for this element from the HTML
            text = self._extract_element_text(html, backend_id)

            elements.append(PageElement(
                id=str(i + 1),
                tag=tag,
                text=clean_text(text),
                bbox=bbox,
                attributes={
                    "class": class_name,
                    "id": elem_id,
                    "aria_label": aria_label,
                    "is_top_level": cand.get("is_top_level_target", False),
                },
                backend_node_id=backend_id,
                selector=f'[data-backend-node-id="{backend_id}"]',
            ))

        bridge = DOMBridge().build_from_elements(elements)

        logger.debug(
            f"HTML perception complete: {len(elements)} candidates, "
            f"bridge={bridge}"
        )

        return (
            Perception(
                screenshot=None,            # Text mode — no screenshot
                annotated_screenshot=None,
                elements=elements,
                page_title=title,
                page_url=url,
            ),
            bridge,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_bbox(
        self, bbox_str: str
    ) -> tuple[float, float, float, float]:
        """Parse a Mind2Web bounding_box_rect string.

        Format: "left,top,width,height" (pixel values).

        Returns normalized bbox (x, y, w, h) in [0, 1].
        """
        try:
            parts = [float(x) for x in bbox_str.split(",")]
            if len(parts) == 4:
                l, t, w, h = parts
                # Normalize to [0, 1] using a reference viewport of 1280×720
                # (these were captured at that viewport in Mind2Web)
                return (l / 1280, t / 720, w / 1280, h / 720)
        except (ValueError, AttributeError):
            pass
        return (0.0, 0.0, 0.0, 0.0)

    def _extract_element_text(self, html: str, backend_node_id: str) -> str:
        """Extract visible text content for an element from the rendered HTML.

        Mind2Web cleaned_html uses <text backend_node_id="N">content</text>
        nodes to mark visible text.  After offline_env._inject_node_ids()
        processes the HTML, the attribute becomes data-backend-node-id.
        We search for BOTH forms.
        """
        if not backend_node_id:
            return ""

        # Find the element's opening tag in the RENDERED HTML.
        # After offline_env._inject_node_ids(), the attribute name is
        # data-backend-node-id (with hyphens), not backend_node_id.
        escaped_id = re.escape(backend_node_id)
        m = None
        for attr_name in ("data-backend-node-id", "backend_node_id"):
            m = re.search(
                r'<\w+\s[^>]*?\b' + attr_name + r'\s*=\s*"' + escaped_id + r'"',
                html,
            )
            if m:
                break
        if not m:
            return ""

        # Find the matching closing tag and extract text between them
        start = m.start()
        # Move past the opening tag
        tag_end = html.find(">", start) + 1
        if tag_end <= 0:
            return ""

        # Find matching close tag (naive — works for Mind2Web's clean flat HTML)
        depth = 1
        pos = tag_end
        while pos < len(html) and depth > 0:
            next_open = html.find("<", pos)
            if next_open == -1:
                break
            if html[next_open:next_open + 2] == "</":
                depth -= 1
                if depth == 0:
                    inner = html[tag_end:next_open]
                    return self._strip_tags(inner).strip()
                pos = next_open + 1
            else:
                # Self-closing? Skip
                if html[next_open:next_open + 4] in ("<br>", "<br/", "<hr>", "<img"):
                    pos = next_open + 1
                    continue
                depth += 1
                pos = next_open + 1

        return ""

    def _strip_tags(self, html_fragment: str) -> str:
        """Remove HTML tags, leaving only text content."""
        return re.sub(r"<[^>]+>", " ", html_fragment)
