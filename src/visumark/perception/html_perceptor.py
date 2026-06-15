"""HTML text-mode Perceptor — extracts candidate elements from Mind2Web data.

This is the TEXT-BASED auxiliary path for Mind2Web evaluation.  Instead of
taking a screenshot and drawing SoM labels, it reads the Mind2Web candidate
list (pos_candidates + neg_candidates) directly from the ground-truth data,
extracts text content for each candidate from the cleaned_html, and produces
a numbered list that the LLM selects from.

Architecture:
    1. Receive candidate dicts (from Mind2Web JSON, not from DOM extraction)
    2. Optionally rank candidates via BERT semantic similarity
    3. Parse each candidate's attributes JSON (class, aria_label, bbox, etc.)
    4. Extract visible text by searching cleaned_html for the backend_node_id
    5. Build PageElement objects and DOMBridge mapping
    6. Prompt builder (in html_prompts.py) formats them for the LLM
"""

import json
import re

from loguru import logger

from visumark.core.types import PageElement, Perception
from visumark.environment.base import BaseEnvironment
from visumark.perception.base import BasePerceptor
from visumark.perception.dom_bridge import DOMBridge
from visumark.environment.dom_utils import clean_text


# ============================================================================
# BERT zero-shot candidate ranking
# ============================================================================

class CandidateRanker:
    """Zero-shot BERT ranking of web element candidates.

    Uses a sentence-transformers model to compute semantic similarity
    between the task description and each candidate element's attributes
    (tag, text, aria_label, class).  No fine-tuning required.

    Model: all-MiniLM-L6-v2 (~80 MB, runs on CPU in ~0.1s per step)
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None

    @property
    def model(self):
        """Lazy-load the sentence-transformers model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading ranking model: {self._model_name}")
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def _candidate_text(self, cand: dict) -> str:
        """Build a text representation of a candidate element for ranking."""
        tag = cand.get("tag", "")
        attrs_str = cand.get("attributes", "{}")
        attrs: dict = {}
        try:
            attrs = json.loads(attrs_str) if isinstance(attrs_str, str) else attrs_str
        except (json.JSONDecodeError, TypeError):
            pass

        cls = attrs.get("class", "") or ""
        aria = attrs.get("aria_label", "") or ""
        elem_id = attrs.get("id", "") or ""

        parts = [tag]
        if aria:
            parts.append(aria)
        if elem_id:
            parts.append(f"#{elem_id}")
        if cls:
            # Take the first class name (most specific)
            parts.append(cls.split()[0] if cls.split() else cls)

        return " ".join(parts)

    def rank(
        self,
        task: str,
        candidates: list[dict],
        top_k: int = 150,
        keep_pos: list[dict] | None = None,
    ) -> list[dict]:
        """Rank candidates by semantic similarity to the task description.

        Args:
            task: Natural language task description.
            candidates: All candidate dicts to rank.
            top_k: Number of top candidates to return.
            keep_pos: pos_candidates that MUST be included (inserted at front).

        Returns:
            Ranked list of top_k candidates, with keep_pos prepended.
        """
        if not candidates:
            return []

        # Build text representations
        texts = [self._candidate_text(c) for c in candidates]

        # Encode task + candidates
        task_emb = self.model.encode([task], show_progress_bar=False)
        cand_embs = self.model.encode(texts, show_progress_bar=False)

        # Cosine similarity
        from sentence_transformers.util import cos_sim
        scores = cos_sim(task_emb, cand_embs)[0]  # shape: (N,)

        # Sort by score descending
        indices = scores.argsort(descending=True).tolist()

        # Take top-K (excluding pos, which are always included)
        pos_ids = set()
        if keep_pos:
            for p in keep_pos:
                pos_ids.add(p.get("backend_node_id", ""))

        result = []
        seen = set()
        # Always include pos_candidates first
        for p in (keep_pos or []):
            bid = p.get("backend_node_id", "")
            if bid not in seen:
                result.append(p)
                seen.add(bid)

        # Fill remaining with top-ranked neg
        for idx in indices:
            if len(result) >= top_k:
                break
            cand = candidates[idx]
            bid = cand.get("backend_node_id", "")
            if bid not in seen:
                result.append(cand)
                seen.add(bid)

        logger.debug(
            f"Ranked {len(candidates)} candidates, "
            f"top-{top_k} returned (incl {len(result) - len(indices[:top_k])} pos)"
        )
        return result


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
        """Extract candidate elements for live web or Mind2Web eval.

        - candidates provided → Mind2Web flow (pre-annotated data)
        - candidates is None  → live web: run ElementExtractor on the page
        """
        title = await env.get_page_title()
        url = await env.get_page_url()
        html = ""

        if not candidates:
            # ── Live web mode: extract elements from the real page ──
            page = env.page if hasattr(env, "page") else None
            if page is None:
                logger.warning("No Playwright page — returning empty perception")
                return (Perception(page_title=title, page_url=url), DOMBridge())

            from visumark.perception.element_extractor import ElementExtractor
            extractor = ElementExtractor(
                max_elements=self.max_candidates,
                min_element_size=4,
            )
            elements = await extractor.extract(page)

            # Extract visible page text — non-interactive elements like
            # search results and paragraphs aren't in the element list
            # but contain the answer the user is looking for.
            try:
                page_text = await page.evaluate(
                    "() => (document.body ? document.body.innerText : '').substring(0, 3000)"
                )
            except Exception:
                page_text = ""

            bridge = DOMBridge().build_from_elements(elements)
            logger.debug(
                f"HTML live perception: {len(elements)} elements extracted, "
                f"bridge={bridge}"
            )
            return (
                Perception(
                    screenshot=None,
                    annotated_screenshot=None,
                    elements=elements,
                    page_title=title,
                    page_url=url,
                    page_text=page_text,
                ),
                bridge,
            )

        # ── Mind2Web eval mode: use pre-annotated candidates ──
        html = await env.get_page_html()

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
