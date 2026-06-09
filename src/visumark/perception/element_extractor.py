"""Extract interactive elements from a web page for SoM annotation.

KEY FIX: Injects data-som-id directly via Playwright element handles during
extraction, eliminating the CSS selector mismatch that caused clicks on
wrong elements. The old approach of generating CSS selectors and later
injecting via querySelector was unreliable for elements without unique
identifiers (bare <a>, <button> without class/id, etc.).
"""

from loguru import logger
from playwright.async_api import Page, ElementHandle

from visumark.core.types import PageElement
from visumark.environment.dom_utils import (
    INTERACTIVE_SELECTOR,
    clean_text,
)


class ElementExtractor:
    """Extract clickable / interactable elements from a Playwright page.

    Uses DOM + optional Accessibility Tree fusion. Injects data-som-id
    attributes directly during extraction — no CSS selector dependency.
    """

    def __init__(self, max_elements: int = 200, min_element_size: int = 4):
        self.max_elements = max_elements
        self.min_element_size = min_element_size

    async def extract(
        self,
        page: Page,
        ats_nodes: list[dict] | None = None,
    ) -> list[PageElement]:
        """Extract interactive elements from the current page.

        Steps:
        1. Query all interactive elements
        2. Filter by visibility and size
        3. Extract attributes, text, bbox
        4. Sort by visual position
        5. Assign SoM IDs
        6. Inject data-som-id directly into DOM via handles (FIXED)
        """
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        vw, vh = viewport["width"], viewport["height"]

        # Build ATS lookup
        ats_by_node: dict[str, dict] = {}
        if ats_nodes:
            for node in ats_nodes:
                nid = node.get("backendDOMNodeId") or node.get("nodeId")
                if nid:
                    ats_by_node[str(nid)] = node

        # Collect candidate (element, handle) pairs.
        # Inject data-som-id IMMEDIATELY while handles are still fresh,
        # using a temporary ID. After sorting, we update to final IDs.
        candidates: list[tuple[PageElement, ElementHandle, str]] = []
        handles_list = await page.query_selector_all(INTERACTIVE_SELECTOR)
        temp_counter = 0

        for handle in handles_list:
            if len(candidates) >= self.max_elements:
                break
            try:
                elem = await self._extract_single(handle, vw, vh, ats_by_node)
                if elem is not None:
                    temp_id = f"som-{temp_counter}"
                    temp_counter += 1
                    # Inject immediately while handle is fresh
                    await handle.evaluate(
                        f"(el) => el.setAttribute('data-som-id', '{temp_id}')"
                    )
                    candidates.append((elem, handle, temp_id))
            except Exception as exc:
                logger.debug(f"Skipping element: {exc}")
                continue

        # Sort by visual position: top-to-bottom, then left-to-right
        candidates.sort(key=lambda t: (t[0].bbox[1], t[0].bbox[0]))

        # Assign final SoM IDs and update DOM attributes
        elements: list[PageElement] = []
        stale_count = 0
        for i, (elem, _handle, temp_id) in enumerate(candidates):
            som_id = str(i + 1)
            elem.id = som_id
            elements.append(elem)

            # Update from temporary to final SoM ID in the DOM
            try:
                await _handle.evaluate(
                    f"(el) => el.setAttribute('data-som-id', '{som_id}')"
                )
            except Exception:
                # Handle stale — use JS querySelector as fallback
                try:
                    result = await page.evaluate(
                        f"(() => {{"
                        f"  const el = document.querySelector('[data-som-id=\"{temp_id}\"]');"
                        f"  if (el) {{ el.setAttribute('data-som-id', '{som_id}'); return true; }}"
                        f"  return false;"
                        f"}})()"
                    )
                    if not result:
                        stale_count += 1
                except Exception:
                    stale_count += 1

        if stale_count > 0:
            logger.warning(
                f"{stale_count}/{len(elements)} elements could not be tagged "
                f"with data-som-id (stale handles) — coordinate fallback will be used"
            )

        logger.debug(f"Extracted {len(elements)} interactive elements")
        return elements

    # ------------------------------------------------------------------
    # Single element extraction
    # ------------------------------------------------------------------

    async def _extract_single(
        self,
        handle: ElementHandle,
        vw: int, vh: int,
        ats_by_node: dict[str, dict],
    ) -> PageElement | None:
        """Extract data from a single element handle. Returns None if skip."""

        # Visibility
        visible = await handle.is_visible()
        if not visible:
            return None

        # Bounding box
        box = await handle.bounding_box()
        if not box:
            return None
        bw, bh = box["width"], box["height"]
        if bw * bh < self.min_element_size:
            return None

        # Tag
        tag_raw = await handle.evaluate("el => el.tagName")
        tag = tag_raw.lower() if tag_raw else ""

        # Attributes
        attributes = await self._extract_attributes(handle)

        # Text
        text = await self._extract_text(handle, attributes)

        # Backend node ID
        backend_node_id = attributes.get("data-backend-node-id") or None

        # ATS fusion
        if ats_by_node and backend_node_id:
            ats_info = ats_by_node.get(backend_node_id)
            if ats_info:
                if not attributes.get("role") and ats_info.get("role"):
                    attributes["role"] = ats_info["role"]
                if not text and ats_info.get("name"):
                    text = clean_text(ats_info["name"])

        # Generate a best-effort CSS selector for the DOM bridge
        selector = self._build_fallback_selector(tag, attributes, handle)

        return PageElement(
            id="0",  # Placeholder, assigned after sorting
            tag=tag,
            text=text,
            bbox=(box["x"] / vw, box["y"] / vh, bw / vw, bh / vh),
            attributes=attributes,
            backend_node_id=backend_node_id,
            selector=selector,
        )

    # ------------------------------------------------------------------
    # Attribute extraction
    # ------------------------------------------------------------------

    async def _extract_attributes(self, handle: ElementHandle) -> dict:
        try:
            attrs_js = await handle.evaluate("""
                (el) => {
                    const attrs = {};
                    const keys = [
                        'id', 'class', 'name', 'type', 'href', 'placeholder',
                        'aria-label', 'aria-expanded', 'aria-haspopup',
                        'aria-checked', 'aria-selected', 'aria-describedby',
                        'role', 'value', 'title', 'alt', 'tabindex',
                        'disabled', 'checked', 'readonly', 'required',
                        'data-backend-node-id', 'data-id', 'data-testid',
                    ];
                    for (const k of keys) {
                        const v = el.getAttribute(k);
                        if (v !== null && v !== '') attrs[k] = v;
                    }
                    return attrs;
                }
            """)
            return attrs_js if attrs_js else {}
        except Exception:
            return {}

    async def _extract_text(self, handle: ElementHandle, attrs: dict) -> str:
        for attr in ("aria-label", "placeholder", "title", "value", "alt"):
            if attr in attrs and attrs[attr]:
                return clean_text(attrs[attr])
        try:
            text = await handle.evaluate("""
                (el) => {
                    if (el.tagName === 'INPUT') {
                        return el.placeholder || el.value || el.getAttribute('aria-label') || '';
                    }
                    if (el.tagName === 'SELECT') {
                        const opt = el.options[el.selectedIndex];
                        return (opt && opt.text) || el.getAttribute('aria-label') || '';
                    }
                    return (el.textContent || '').trim().substring(0, 80);
                }
            """)
            return clean_text(text)
        except Exception:
            return ""

    def _build_fallback_selector(
        self, tag: str, attrs: dict, handle: ElementHandle
    ) -> str:
        """Build a CSS selector for the DOM bridge (used as fallback, not for injection)."""
        if "id" in attrs and attrs["id"]:
            return f"{tag}#{_escape(attrs['id'])}"
        if "data-testid" in attrs:
            return f"[data-testid='{_escape(attrs['data-testid'])}']"
        if "data-backend-node-id" in attrs:
            return f"[data-backend-node-id='{_escape(attrs['data-backend-node-id'])}']"
        if "class" in attrs and attrs["class"]:
            cls = attrs["class"].strip().split()[0]
            return f"{tag}.{_escape(cls)}"
        if "aria-label" in attrs:
            return f"{tag}[aria-label='{_escape(attrs['aria-label'])}']"
        if "name" in attrs:
            return f"{tag}[name='{_escape(attrs['name'])}']"
        return tag


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
