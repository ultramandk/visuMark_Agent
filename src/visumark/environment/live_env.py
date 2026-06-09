"""Live Playwright browser environment for online task execution."""

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from visumark.core.types import Action, ActionType
from visumark.environment.base import BaseEnvironment
from visumark.perception.dom_bridge import DOMBridge


class LiveEnvironment(BaseEnvironment):
    """Real Chromium browser controlled via Playwright.

    Used for interactive task execution and live demos.
    Supports click, type, select, scroll, hover, press, goto, and wait actions.
    """

    def __init__(
        self,
        headless: bool = True,
        viewport: tuple[int, int] = (1280, 720),
        timeout: int = 30_000,
    ):
        self._headless = headless
        self._viewport_w, self._viewport_h = viewport
        self._timeout = timeout

        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, url: str = "about:blank") -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            viewport={"width": self._viewport_w, "height": self._viewport_h}
        )
        self._page = await self._context.new_page()
        if url and url != "about:blank":
            await self._page.goto(url, timeout=self._timeout, wait_until="domcontentloaded")
        logger.info(f"Live browser started (headless={self._headless})")

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
        logger.info("Live browser stopped")

    # ------------------------------------------------------------------
    # Page content
    # ------------------------------------------------------------------

    async def load_html(self, html: str) -> None:
        if not self._page:
            raise RuntimeError("Browser not started")
        await self._page.set_content(html, wait_until="domcontentloaded")

    async def screenshot(self) -> bytes:
        if not self._page:
            raise RuntimeError("Browser not started")
        # Use JPEG at reduced size for fast API upload
        return await self._page.screenshot(full_page=False, type="jpeg", quality=65, scale="css")

    async def get_page_html(self) -> str:
        if not self._page:
            raise RuntimeError("Browser not started")
        return await self._page.content()

    async def get_accessibility_tree(self) -> dict:
        """Fetch the Chromium Accessibility Tree via CDP."""
        if not self._page:
            raise RuntimeError("Browser not started")
        try:
            cdp = await self._page.evaluate("""async () => {
                const root = await (window.cdc_adoQpoasnfa76pfcZLmcfl_Array ||
                    window.__playwright__chrome_devtools__);
                return null;
            }""")
            # Use Playwright's built-in accessibility snapshot
            snapshot = await self._page.accessibility.snapshot()
            return snapshot or {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Page metadata
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
        """Inject data-som-id attributes into DOM elements by their CSS selectors.

        Unlike the old approach (querySelectorAll + forEach index), this method
        uses explicit SoM ID → selector mapping, ensuring the same IDs used
        in the screenshot annotation match the DOM.
        """
        if not self._page:
            return

        js_parts = []
        for som_id, selector in id_to_selector.items():
            escaped_selector = selector.replace("\\", "\\\\").replace("'", "\\'")
            js_parts.append(f"""
                try {{
                    const el = document.querySelector('{escaped_selector}');
                    if (el) el.setAttribute('data-som-id', '{som_id}');
                }} catch(e) {{}}
            """)

        js_code = "\n".join(js_parts)
        await self._page.evaluate(f"(function() {{{js_code}}})()")

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    async def execute(self, action: Action, bridge: DOMBridge | None = None) -> bool:
        if not self._page:
            raise RuntimeError("Browser not started")

        try:
            atype = action.action_type

            if atype == ActionType.CLICK and action.element_id is not None:
                try:
                    selector = f"[data-som-id='{action.element_id}']"
                    await self._page.click(selector, timeout=3000)  # quick timeout for selector
                except Exception:
                    # Fallback: click by coordinates from the bridge
                    if bridge:
                        bbox = bridge.get_bbox(action.element_id)
                        if bbox:
                            cx = (bbox[0] + bbox[2] / 2) * self._viewport_w
                            cy = (bbox[1] + bbox[3] / 2) * self._viewport_h
                            await self._page.mouse.click(cx, cy)
                            logger.debug(f"Coordinate click OK at ({cx:.0f}, {cy:.0f})")
                        else:
                            raise
                    else:
                        raise

            elif atype == ActionType.TYPE and action.element_id is not None:
                selector = f"[data-som-id='{action.element_id}']"
                await self._page.fill(selector, action.value or "", timeout=self._timeout)

            elif atype == ActionType.SELECT and action.element_id is not None:
                selector = f"[data-som-id='{action.element_id}']"
                await self._page.select_option(selector, action.value or "", timeout=self._timeout)

            elif atype == ActionType.SCROLL:
                delta = 500 if (action.value or "down") == "down" else -500
                await self._page.mouse.wheel(0, delta)

            elif atype == ActionType.GOTO:
                await self._page.goto(
                    action.value or "about:blank",
                    timeout=self._timeout,
                    wait_until="domcontentloaded",
                )

            elif atype == ActionType.PRESS:
                await self._page.keyboard.press(action.value or "Enter")

            elif atype == ActionType.HOVER and action.element_id is not None:
                selector = f"[data-som-id='{action.element_id}']"
                await self._page.hover(selector, timeout=self._timeout)

            elif atype == ActionType.WAIT:
                ms = int(action.value or 1000)
                await self._page.wait_for_timeout(ms)

            elif atype in (ActionType.ANSWER, ActionType.FAIL):
                # Terminal actions — no browser operation needed
                return True

            else:
                logger.warning(f"Unknown action type: {atype}")
                return False

            logger.debug(f"Action OK: {action.to_dict()}")
            return True

        except Exception as exc:
            logger.error(f"Action failed: {action.to_dict()} — {exc}")
            return False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        return True

    @property
    def page(self) -> Page | None:
        return self._page
