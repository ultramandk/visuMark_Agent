"""Offline environment — loads cached HTML snapshots for Mind2Web evaluation.

Key differences from LiveEnvironment:
    - Uses page.set_content(html) instead of page.goto(url)
    - Read-only: execute() always succeeds (no real browser interaction)
    - Injects backend_node_id markers for DOM bridge mapping
"""

from loguru import logger
from playwright.async_api import async_playwright

from visumark.core.types import Action, ActionType
from visumark.environment.base import BaseEnvironment


class OfflineEnvironment(BaseEnvironment):
    """Offline browser for Mind2Web snapshot evaluation.

    Loads cleaned_html from Mind2Web JSON into a Playwright page.
    The page is rendered just like a real browser, allowing screenshots
    and DOM queries — but no real network requests or user interactions.
    """

    def __init__(
        self,
        viewport: tuple[int, int] = (1280, 720),
        timeout: int = 30_000,
    ):
        self._viewport_w, self._viewport_h = viewport
        self._timeout = timeout

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, url: str = "about:blank") -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(
            viewport={"width": self._viewport_w, "height": self._viewport_h}
        )
        self._page = await self._context.new_page()
        logger.debug("Offline environment started")

    async def stop(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    # ------------------------------------------------------------------
    # Page content — the key method for offline eval
    # ------------------------------------------------------------------

    async def load_html(self, html: str) -> None:
        """Load a Mind2Web HTML snapshot into the page.

        The HTML is first preprocessed to inject backend_node_id markers
        so that extracted elements can be mapped to Mind2Web node IDs.
        """
        if not self._page:
            raise RuntimeError("Offline environment not started")

        processed = self._inject_node_ids(html)
        await self._page.set_content(processed, wait_until="domcontentloaded")
        logger.debug(f"Loaded HTML snapshot ({len(html)} chars)")

    def _inject_node_ids(self, html: str) -> str:
        """Inject data-backend-node-id attributes into Mind2Web HTML.

        Mind2Web cleaned_html may have elements with backend_node_id in a
        data-id attribute or similar. We normalize them to data-backend-node-id.
        """
        # Already has our marker — skip
        if "data-backend-node-id" in html[:2000]:
            return html

        # Convert common Mind2Web marker formats:
        #   data-id="node-42"   → data-backend-node-id="node-42"
        #   data-node-id="..."  → data-backend-node-id="..."
        import re

        html = re.sub(
            r'data-(?:id|node-id)\s*=\s*"([^"]*)"',
            r'data-backend-node-id="\1"',
            html,
        )
        return html

    async def screenshot(self) -> bytes:
        if not self._page:
            raise RuntimeError("Offline environment not started")
        return await self._page.screenshot(full_page=False, type="jpeg", quality=75)

    async def get_page_html(self) -> str:
        if not self._page:
            return ""
        return await self._page.content()

    async def get_accessibility_tree(self) -> dict:
        if not self._page:
            return {}
        try:
            snapshot = await self._page.accessibility.snapshot()
            return snapshot or {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    async def get_page_title(self) -> str:
        if not self._page:
            return ""
        return await self._page.title()

    async def get_page_url(self) -> str:
        if not self._page:
            return ""
        return self._page.url

    def get_viewport(self) -> dict[str, int]:
        return {"width": self._viewport_w, "height": self._viewport_h}

    # ------------------------------------------------------------------
    # DOM manipulation
    # ------------------------------------------------------------------

    async def tag_elements(self, id_to_selector: dict[str, str]) -> None:
        """Inject data-som-id into DOM elements."""
        if not self._page:
            return
        entries = []
        for som_id, selector in id_to_selector.items():
            safe_sel = selector.replace("\\", "\\\\").replace("'", "\\'")
            entries.append(f"{{id:'{som_id}',sel:'{safe_sel}'}}")
        js = f"""
        (function() {{
            const mappings = [{','.join(entries)}];
            for (const m of mappings) {{
                try {{
                    const el = document.querySelector(m.sel);
                    if (el) el.setAttribute('data-som-id', m.id);
                }} catch(e) {{}}
            }}
        }})()
        """
        await self._page.evaluate(js)

    # ------------------------------------------------------------------
    # Action execution — read-only (no real interaction)
    # ------------------------------------------------------------------

    async def execute(self, action: Action, bridge=None) -> bool:
        """In offline mode, actions are never actually executed.

        The HTML snapshot is static — we can't click or type.
        For evaluation, the comparator checks predictions against
        ground truth without needing real execution.
        """
        if action.action_type in (ActionType.ANSWER, ActionType.FAIL):
            return True
        # Offline: always succeed — evaluation is done by the comparator
        return True

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        return False

    @property
    def page(self):
        return self._page
