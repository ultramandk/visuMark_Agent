"""SoM (Set-of-Mark) Visual Perceptor — the primary perception path.

This is the core module implementing the project proposal's §3.1 architecture:

    Browser screenshot + Accessibility Tree
        → Element extraction (DOM + ATS fusion)
        → SoM visual annotation (colored bounding boxes + numeric labels)
        → DOM bridge mapping (SoM ID ↔ DOM node ↔ Playwright selector)

The VLM then looks at the annotated screenshot and says "click #3".
The DOMBridge resolves #3 to a Playwright selector for execution,
or to a backend_node_id for Mind2Web evaluation.

Key fix vs old code:
    - tag_elements() now uses the SAME element list as the screenshot annotation
    - Element extraction happens ONCE per step, producing both the visual labels
      and the DOM mappings from a single consistent source
"""

from loguru import logger

from visumark.core.types import Perception
from visumark.environment.base import BaseEnvironment
from visumark.perception.base import BasePerceptor
from visumark.perception.dom_bridge import DOMBridge
from visumark.perception.element_extractor import ElementExtractor
from visumark.perception.som_marker import SoMMarker, marker_factory
from visumark.environment.dom_utils import parse_accessibility_tree


class SoMPerceptor(BasePerceptor):
    """SoM visual perception — the primary path.

    Pipeline:
        1. Take a screenshot
        2. Get the Accessibility Tree
        3. Extract interactive elements (DOM + ATS fusion)
        4. Draw SoM bounding boxes and labels on the screenshot
        5. Build DOMBridge (SoM ID ↔ selector ↔ backend_node_id)
        6. Tag DOM elements with data-som-id for later execution
    """

    def __init__(self, config: dict):
        self.max_elements = config.get("max_elements", 50)
        self.use_ats = config.get("use_accessibility_tree", True)
        self.min_element_size = config.get("min_element_size", 4)

        self.marker = marker_factory(config)
        self.extractor = ElementExtractor(
            max_elements=self.max_elements,
            min_element_size=self.min_element_size,
        )

    async def perceive(
        self, env: BaseEnvironment
    ) -> tuple[Perception, DOMBridge]:
        """Run the full SoM perception pipeline and return structured results.

        Args:
            env: Browser environment (live Playwright or offline snapshot).

        Returns:
            (Perception, DOMBridge) tuple — ready for the reasoner.
        """
        # 1. Take screenshot
        screenshot = await env.screenshot()

        # 2. Get Accessibility Tree (optional but recommended)
        ats_nodes = None
        if self.use_ats:
            try:
                ats_snapshot = await env.get_accessibility_tree()
                if ats_snapshot:
                    ats_nodes = parse_accessibility_tree(ats_snapshot)
                    logger.debug(f"ATS: {len(ats_nodes)} nodes")
            except Exception as exc:
                logger.debug(f"ATS unavailable: {exc}")

        # 3. Extract interactive elements
        page = env.page if hasattr(env, "page") else None
        if page is None:
            logger.warning("No Playwright page available — returning empty perception")
            return (
                Perception(screenshot=screenshot),
                DOMBridge(),
            )

        elements = await self.extractor.extract(page, ats_nodes)
        logger.debug(f"Extracted {len(elements)} elements")

        # If page seems empty (still loading), wait and retry once
        if len(elements) < 3 and hasattr(env, "is_live") and env.is_live:
            logger.debug(f"Page appears empty ({len(elements)} elements), waiting for content...")
            await page.wait_for_timeout(2000)
            elements = await self.extractor.extract(page, ats_nodes)
            logger.debug(f"Retry: extracted {len(elements)} elements")

        # 4. Draw SoM annotation on screenshot
        vp = env.get_viewport()
        annotated = self.marker.annotate(
            screenshot, elements,
            viewport_w=vp["width"],
            viewport_h=vp["height"],
        )

        # 5. Build DOM bridge (CRITICAL: same elements list as step 4!)
        bridge = DOMBridge().build_from_elements(elements)

        # 6. Tag DOM elements for later execution.
        #    PRIMARY: data-som-id is already injected directly via element
        #    handles during extract() — this is the reliable path.
        #    FALLBACK: Also call tag_elements() for environments that may
        #    need re-injection (offline snapshots, dynamic pages).
        try:
            id_to_selector = bridge.get_id_to_selector_map()
            if id_to_selector:
                await env.tag_elements(id_to_selector)
        except Exception as exc:
            logger.debug(f"tag_elements fallback skipped: {exc}")

        # 7. Gather metadata
        title = await env.get_page_title()
        url = await env.get_page_url()

        perception = Perception(
            screenshot=screenshot,            # Clean — for UI / frontend
            annotated_screenshot=annotated,   # SoM — for VLM (not shown to user)
            elements=elements,
            page_title=title,
            page_url=url,
        )

        logger.debug(
            f"SoM perception complete: {len(elements)} elements, "
            f"bridge={bridge}"
        )
        return perception, bridge
