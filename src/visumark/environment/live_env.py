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
            try:
                await self._page.goto(url, timeout=self._timeout, wait_until="domcontentloaded")
            except Exception:
                logger.warning(f"Initial navigation to {url} timed out, continuing anyway...")
            await self.wait_for_page_ready()
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

    async def wait_for_page_ready(
        self,
        settle_ms: int = 2000,
        min_body_text: int = 50,
        max_polls: int = 20,
    ) -> None:
        """Wait for the page to actually finish rendering before screenshot.

        Layered approach:
        1. domcontentloaded — DOM parsed
        2. networkidle — best-effort, may not fire on SPAs
        3. Poll for visible text + interactive elements in <body>
        4. Extra settle time for images, fonts, lazy components

        The content poll is KEY: it checks that the page body actually
        contains rendered text, not just a blank skeleton.  Uses
        innerText (respects CSS visibility) to avoid false-passing
        on hidden SEO text or JSON-LD script content.
        """
        if not self._page:
            return

        # Phase 1: DOM ready
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass

        # Phase 2: Best-effort network idle
        try:
            await self._page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

        # Phase 3: Poll for actual rendered content
        for i in range(max_polls):
            try:
                result = await self._page.evaluate("""() => {
                    const body = document.body;
                    if (!body) return {text: 0, elems: 0, spinning: false};
                    const visibleText = (body.innerText || '').trim();
                    const interactive = body.querySelectorAll(
                        'button, a, input, select, textarea, ' +
                        '[role="button"], [role="link"], [role="checkbox"], ' +
                        '[role="combobox"], [role="listbox"], [role="menuitem"], ' +
                        '[role="tab"], [role="switch"], [onclick], [tabindex]'
                    );
                    const spinners = body.querySelectorAll(
                        '[role="progressbar"], [aria-busy="true"], ' +
                        '.loading, .spinner, .skeleton, ' +
                        '[class*="loading"], [class*="spinner"], [class*="skeleton"]'
                    );
                    return {
                        text: visibleText.length,
                        elems: interactive.length,
                        spinning: spinners.length > 0,
                    };
                }""")
                text_len = result.get("text", 0)
                elem_count = result.get("elems", 0)
                has_spinner = result.get("spinning", False)

                if text_len >= min_body_text and elem_count >= 3 and not has_spinner:
                    logger.debug(f"Page ready: {text_len} chars, {elem_count} elements")
                    break

                reason = ""
                if text_len < min_body_text:
                    reason = f"text={text_len}/{min_body_text}"
                elif elem_count < 3:
                    reason = f"elems={elem_count}/3"
                elif has_spinner:
                    reason = "spinner present"
                logger.debug(f"Waiting for page... ({reason}, poll {i + 1}/{max_polls})")
                await self._page.wait_for_timeout(500)
            except Exception:
                await self._page.wait_for_timeout(500)

        # Phase 4: Extra settle for images, fonts, lazy hydration
        await self._page.wait_for_timeout(settle_ms)

    async def is_alive(self) -> bool:
        """Check if the browser / page is still responsive."""
        if not self._page or not self._browser:
            return False
        try:
            await self._page.evaluate("1 + 1")
            return True
        except Exception:
            return False

    async def load_html(self, html: str) -> None:
        if not self._page:
            raise RuntimeError("Browser not started")
        await self._page.set_content(html, wait_until="domcontentloaded")

    async def screenshot(self) -> bytes:
        if not self._page:
            raise RuntimeError("Browser not started")
        return await self._page.screenshot(full_page=False, type="png")

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

        # Record URL before action to detect navigation
        url_before = self._page.url

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
                await self.wait_for_page_ready()

            elif atype == ActionType.PRESS:
                key = self._normalize_key(action.value or "Enter")
                await self._page.keyboard.press(key)

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

            # ── Post-action: detect navigation & wait for new page ──
            # CLICK / TYPE+Enter / PRESS Enter can trigger page navigation
            # or form submission. If the URL changed, wait for the new page
            # to fully render so the next perception step doesn't get a
            # blank screenshot.
            url_after = self._page.url
            if url_after != url_before:
                logger.debug(f"URL changed: {url_before} → {url_after}")
                await self.wait_for_page_ready()
            elif atype in (ActionType.CLICK, ActionType.PRESS, ActionType.TYPE):
                # Even without URL change, these actions may trigger
                # DOM updates (modals, lazy content). Brief settle.
                await self._page.wait_for_timeout(800)

            logger.debug(f"Action OK: {action.to_dict()}")
            return True

        except Exception as exc:
            logger.error(f"Action failed: {action.to_dict()} — {exc}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_key(key: str) -> str:
        """Normalize key names for Playwright's keyboard.press().

        Playwright expects capitalized key names: Enter, Escape, Tab, Backspace, etc.
        VLM may output lowercase or variant names.
        """
        key_map = {
            "enter": "Enter", "return": "Enter",
            "esc": "Escape", "escape": "Escape",
            "tab": "Tab",
            "backspace": "Backspace", "delete": "Delete",
            "space": "Space", " ": "Space",
            "up": "ArrowUp", "down": "ArrowDown",
            "left": "ArrowLeft", "right": "ArrowRight",
            "pageup": "PageUp", "pagedown": "PageDown",
            "home": "Home", "end": "End",
            "ctrl": "Control", "alt": "Alt", "shift": "Shift",
        }
        return key_map.get(key.lower(), key)

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
        """Try to interact with an element via JavaScript.

        Dispatches proper MouseEvents with real element-center coordinates.
        Used as a FALLBACK when Playwright's native click fails.
        """
        if action == "click":
            js_action = """
                el.scrollIntoView({block: 'center', inline: 'center', behavior: 'auto'});
                const r = el.getBoundingClientRect();
                const cx = r.left + r.width / 2;
                const cy = r.top + r.height / 2;
                const opts = {view: window, bubbles: true, cancelable: true, clientX: cx, clientY: cy};
                el.dispatchEvent(new MouseEvent('mousedown', opts));
                el.dispatchEvent(new MouseEvent('mouseup', opts));
                el.dispatchEvent(new MouseEvent('click', opts));
                el.focus();
            """
        elif action == "focus":
            js_action = """
                el.scrollIntoView({block: 'center', inline: 'center', behavior: 'auto'});
                el.focus();
            """
        elif action == "select_all":
            js_action = """
                el.scrollIntoView({block: 'center', inline: 'center', behavior: 'auto'});
                el.focus();
                if (typeof el.select === 'function') { el.select(); }
                else if (el.setSelectionRange) { el.setSelectionRange(0, el.value.length); }
                else { el.click(); }
            """
        else:
            js_action = "el.click();"

        try:
            result = await self._page.evaluate(f"""
                (() => {{
                    try {{
                        const el = document.querySelector('[data-som-id="{element_id}"]');
                        if (!el) return false;
                        {js_action}
                        return true;
                    }} catch(e) {{ return false; }}
                }})()
            """)
            return bool(result)
        except Exception:
            return False

    async def _click_element(
        self, action: Action, bridge: DOMBridge | None
    ) -> None:
        """Click an element using 3-layer fallback.

        Priority: Playwright native → JS MouseEvent → coordinates.
        Playwright's native click produces trusted events (isTrusted=true)
        that websites cannot distinguish from real user clicks.
        """
        eid = action.element_id
        selector = f"[data-som-id='{eid}']"

        # 1) Playwright native click (force: bypass actionability checks)
        try:
            await self._page.click(selector, timeout=3000, force=True)
            logger.debug(f"Click OK on #{eid} (Playwright)")
            return
        except Exception as e1:
            logger.debug(f"Playwright click failed on #{eid}: {e1}")

        # 2) JS MouseEvent with proper coordinates
        if await self._try_js_interact(eid, "click"):
            logger.debug(f"Click OK on #{eid} (JS MouseEvent)")
            return

        # 3) Coordinate fallback
        try:
            cx, cy = await self._resolve_element_target(eid, bridge)
            await self._page.evaluate(f"""
                window.scrollBy({{ left: {cx} - window.innerWidth/2,
                                  top: {cy} - window.innerHeight/2,
                                  behavior: 'auto' }});
            """)
            await self._page.wait_for_timeout(100)
            await self._page.mouse.click(self._viewport_w / 2, self._viewport_h / 2)
            logger.debug(f"Click OK on #{eid} (coordinate)")
            return
        except ValueError:
            pass

        logger.warning(
            f"FAILED to click #{eid}: not found by any method"
        )

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
