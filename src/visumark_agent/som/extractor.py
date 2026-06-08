"""Extract interactive elements from a web page for SoM annotation."""

from dataclasses import dataclass


@dataclass
class PageElement:
    """An interactive DOM element with its bounding box and metadata."""

    id: int
    tag: str                     # button, input, a, select, textarea, etc.
    text: str                    # visible text or aria label
    bbox: tuple[float, float, float, float]  # (x, y, w, h) normalized
    attributes: dict


class ElementExtractor:
    """Extract clickable / interactable elements from a Playwright page."""

    INTERACTIVE_SELECTOR = (
        "button, a, input, select, textarea, "
        "[role='button'], [role='link'], [role='checkbox'], [role='radiogroup'], "
        "[role='combobox'], [role='listbox'], [role='menu'], [role='menuitem'], "
        "[role='tab'], [role='switch'], [role='slider'], "
        "[onclick], [tabindex]"
    )

    def __init__(self, max_elements: int = 200):
        self.max_elements = max_elements

    async def extract(self, page, *, tag_dom: bool = True) -> list[PageElement]:
        """Extract interactive elements visible on the current page.

        Args:
            page: Playwright Page object.
            tag_dom: If True, annotate each extracted element in the DOM with
                ``data-som-id`` so that later actions (click/type) can target
                the same element using the ID shown on the screenshot.
        """
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        vw, vh = viewport["width"], viewport["height"]

        elements: list[PageElement] = []
        idx = 0

        handles = await page.query_selector_all(self.INTERACTIVE_SELECTOR)
        for handle in handles:
            if idx >= self.max_elements:
                break

            try:
                visible = await handle.is_visible()
                if not visible:
                    continue

                box = await handle.bounding_box()
                if not box or box["width"] * box["height"] < 4:
                    continue

                tag = await handle.evaluate("el => el.tagName.toLowerCase()")
                text = await handle.evaluate(
                    "el => el.textContent?.trim()?.slice(0, 80) || el.getAttribute('aria-label') || el.getAttribute('placeholder') || ''"
                )

                element_id = idx + 1

                # Tag the DOM element so browser actions can target it
                if tag_dom:
                    await handle.evaluate(
                        f"el => el.setAttribute('data-som-id', '{element_id}')"
                    )

                elements.append(PageElement(
                    id=element_id,
                    tag=tag,
                    text=text,
                    bbox=(box["x"] / vw, box["y"] / vh, box["width"] / vw, box["height"] / vh),
                    attributes={},
                ))
                idx += 1
            except Exception:
                continue

        return elements
