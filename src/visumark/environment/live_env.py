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
        """Inject data-som-id attributes into DOM elements as a fallback.

        Only sets data-som-id on elements that don't already have one,
        preventing the fallback from corrupting correct tags set by the
        extractor via handle.evaluate().
        """
        if not self._page:
            return

        js_parts = []
        for som_id, selector in id_to_selector.items():
            escaped_selector = selector.replace("\\", "\\\\").replace("'", "\\'")
            js_parts.append(f"""
                try {{
                    const el = document.querySelector('{escaped_selector}');
                    if (el && !el.hasAttribute('data-som-id')) {{
                        el.setAttribute('data-som-id', '{som_id}');
                    }}
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
                await self._click_element(action, bridge)

            elif atype == ActionType.TYPE and action.element_id is not None:
                await self._type_in_element(action, bridge)

            elif atype == ActionType.SELECT and action.element_id is not None:
                await self._select_in_element(action, bridge)

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
                await self._click_element(action, bridge)  # hover = click without side effects
                # Then do actual hover
                try:
                    selector = f"[data-som-id='{action.element_id}']"
                    await self._page.hover(selector, timeout=3000)
                except Exception:
                    if bridge:
                        bbox = bridge.get_bbox(action.element_id)
                        if bbox:
                            cx = (bbox[0] + bbox[2] / 2) * self._viewport_w
                            cy = (bbox[1] + bbox[3] / 2) * self._viewport_h
                            await self._page.mouse.move(cx, cy)

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
    # Element interaction helpers (with coordinate fallback)
    # ------------------------------------------------------------------

    async def _resolve_element_target(
        self, element_id: str, bridge: DOMBridge | None
    ) -> tuple[float, float]:
        """Resolve element center coordinates from bridge, or raise ValueError."""
        if bridge is None:
            raise ValueError(f"No bridge available for coordinate fallback")
        bbox = bridge.get_bbox(element_id)
        if bbox is None:
            raise ValueError(f"Element #{element_id} not found in bridge")
        cx = (bbox[0] + bbox[2] / 2) * self._viewport_w
        cy = (bbox[1] + bbox[3] / 2) * self._viewport_h
        return cx, cy

    async def _try_js_interact(
        self, element_id: str, action: str = "click"
    ) -> bool:
        """Try to interact with an element via JavaScript (scrolls into view first).

        This is the most reliable path — bypasses Playwright's actionability
        checks and coordinate issues. Works even for off-screen elements.

        Returns True if the element was found and interacted with.
        """
        js_action = {
            "click": "el.scrollIntoView({block:'center',inline:'center',behavior:'instant'}); el.focus(); el.click();",
            "focus": "el.scrollIntoView({block:'center',inline:'center',behavior:'instant'}); el.focus();",
            "select_all": "el.scrollIntoView({block:'center',inline:'center',behavior:'instant'}); el.focus(); el.select();",
        }.get(action, "el.click();")

        result = await self._page.evaluate(f"""
            (() => {{
                const el = document.querySelector('[data-som-id="{element_id}"]');
                if (!el) return false;
                {js_action}
                return true;
            }})()
        """)
        return bool(result)

    async def _click_element(
        self, action: Action, bridge: DOMBridge | None
    ) -> None:
        """Click an element: JS click first, then data-som-id selector, then coordinates."""
        eid = action.element_id

        # 1) JS click via data-som-id (most reliable — scrolls into view)
        if await self._try_js_interact(eid, "click"):
            logger.debug(f"JS click OK on #{eid}")
            return

        # 2) Playwright click via data-som-id (scrolls into view automatically)
        selector = f"[data-som-id='{eid}']"
        try:
            await self._page.click(selector, timeout=3000)
            logger.debug(f"Selector click OK on #{eid}")
            return
        except Exception:
            pass

        # 3) Coordinate fallback
        cx, cy = await self._resolve_element_target(eid, bridge)
        # Scroll page to center on target area before clicking
        await self._page.evaluate(f"""
            window.scrollBy({{ left: {cx} - window.innerWidth/2, top: {cy} - window.innerHeight/2, behavior: 'instant' }});
        """)
        await self._page.wait_for_timeout(100)
        await self._page.mouse.click(self._viewport_w / 2, self._viewport_h / 2)
        logger.debug(f"Coordinate click OK for #{eid} (scrolled to center)")

    async def _type_in_element(
        self, action: Action, bridge: DOMBridge | None
    ) -> None:
        """Type text: JS focus + fill, data-som-id fill, or coordinate typing."""
        eid = action.element_id
        value = action.value or ""

        # 1) JS: focus & select all via data-som-id, then keyboard type
        if await self._try_js_interact(eid, "select_all"):
            await self._page.keyboard.press("Backspace")
            await self._page.keyboard.type(value)
            logger.debug(f"JS type OK on #{eid}: '{value[:30]}'")
            return

        # 2) Playwright fill via data-som-id
        selector = f"[data-som-id='{eid}']"
        try:
            await self._page.fill(selector, value, timeout=3000)
            logger.debug(f"Selector fill OK on #{eid}")
            return
        except Exception:
            pass

        # 3) Coordinate fallback: scroll to center, triple-click, type
        cx, cy = await self._resolve_element_target(eid, bridge)
        await self._page.evaluate(f"""
            window.scrollBy({{ left: {cx} - window.innerWidth/2, top: {cy} - window.innerHeight/2, behavior: 'instant' }});
        """)
        await self._page.wait_for_timeout(100)
        await self._page.mouse.click(self._viewport_w / 2, self._viewport_h / 2, click_count=3)
        await self._page.keyboard.press("Backspace")
        await self._page.keyboard.type(value)
        logger.debug(f"Coordinate type OK for #{eid}: '{value[:30]}'")

    async def _select_in_element(
        self, action: Action, bridge: DOMBridge | None
    ) -> None:
        """Select option: JS focus, data-som-id selector, or coordinate fallback."""
        eid = action.element_id
        value = action.value or ""

        # 1) JS: focus the element
        if await self._try_js_interact(eid, "focus"):
            await self._page.keyboard.type(value)
            await self._page.keyboard.press("Enter")
            logger.debug(f"JS select OK on #{eid}: '{value[:30]}'")
            return

        # 2) Playwright select_option via data-som-id
        selector = f"[data-som-id='{eid}']"
        try:
            await self._page.select_option(selector, value, timeout=3000)
            logger.debug(f"Selector select OK on #{eid}")
            return
        except Exception:
            pass

        # 3) Coordinate fallback
        cx, cy = await self._resolve_element_target(eid, bridge)
        await self._page.evaluate(f"""
            window.scrollBy({{ left: {cx} - window.innerWidth/2, top: {cy} - window.innerHeight/2, behavior: 'instant' }});
        """)
        await self._page.wait_for_timeout(100)
        await self._page.mouse.click(self._viewport_w / 2, self._viewport_h / 2)
        await self._page.keyboard.type(value)
        await self._page.keyboard.press("Enter")
        logger.debug(f"Coordinate select OK for #{eid}: '{value[:30]}'")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        return True

    @property
    def page(self) -> Page | None:
        return self._page
