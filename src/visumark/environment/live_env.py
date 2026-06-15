"""Live Playwright browser environment for online task execution."""

import asyncio

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from visumark.core.types import Action, ActionType
from visumark.environment.base import BaseEnvironment
from visumark.perception.dom_bridge import DOMBridge


class LiveEnvironment(BaseEnvironment):
    """Real Chromium browser controlled via Playwright.

    Used for interactive task execution and live demos.
    Supports click, type, select, scroll, hover, press, goto, and wait actions.
    Exposes CDP session for screencast streaming to frontend.
    """

    def __init__(
        self,
        headless: bool = False,
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
        self._known_page_ids: set[int] = set()   # Track pages per-action
        self._cdp_session = None  # Lazily-created CDP session for screencast

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, url: str = "about:blank") -> None:
        self._playwright = await async_playwright().start()

        # ── Anti-detection: headless Chrome exposes many signals that
        #     distinguish it from real Chrome.  We configure launch args,
        #     browser context, and init scripts to produce a fingerprint
        #     indistinguishable from a normal headed browser.
        launch_args: list[str] = []
        if self._headless:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=Translate,OptimizationHints,MediaRouter",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
            ]

        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
            args=launch_args,
        )

        # Context options matching a real Windows desktop Chrome
        self._context = await self._browser.new_context(
            viewport={"width": self._viewport_w, "height": self._viewport_h},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            screen={"width": 1920, "height": 1080},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation"],
            geolocation={"latitude": 23.1291, "longitude": 113.2644},  # Guangzhou
            color_scheme="light",
        )
        self._page = await self._context.new_page()

        # Track pages for new-tab detection
        self._known_page_ids = {id(p) for p in self._context.pages}

        # Comprehensive navigator anti-detection.
        # Overrides key detection signals on Navigator.prototype before
        # any page JS runs.  plugins / mimeTypes / webdriver on the
        # prototype are configurable (verified on Playwright 1.60).
        if self._headless:
            await self._page.add_init_script("""
                (() => {
                const NavProto = Object.getPrototypeOf(navigator);

                // ── Fake PluginArray (headless has 0 plugins, real Chrome has 5) ──
                const _P = (name) => ({
                    get name() { return name; },
                    get filename() { return ''; },
                    get description() { return ''; },
                    get length() { return 1; },
                    item: () => null,
                    namedItem: () => null,
                });
                const fakePlugins = [
                    _P('Chrome PDF Plugin'),
                    _P('Chrome PDF Viewer'),
                    _P('Native Client'),
                    _P('Widevine Content Decryption Module'),
                    _P('Microsoft Edge PDF Plugin'),
                ];
                fakePlugins.item = (i) => fakePlugins[i] || null;
                fakePlugins.namedItem = () => null;
                fakePlugins.refresh = () => {};
                Object.defineProperty(NavProto, 'plugins', {
                    get: () => fakePlugins, configurable: true, enumerable: true,
                });

                // ── Fake MimeTypeArray ──
                const _M = () => ({ get type() { return 'application/pdf'; }, get suffixes() { return 'pdf'; }, get description() { return ''; } });
                const fakeMimeTypes = [_M(), _M(), _M(), _M()];
                fakeMimeTypes.item = (i) => fakeMimeTypes[i] || null;
                fakeMimeTypes.namedItem = () => null;
                Object.defineProperty(NavProto, 'mimeTypes', {
                    get: () => fakeMimeTypes, configurable: true, enumerable: true,
                });

                // ── webdriver ──
                Object.defineProperty(NavProto, 'webdriver', {
                    get: () => false, configurable: true,
                });

                // ── Instance-level overrides ──
                try { Object.defineProperty(navigator, 'platform', { get: () => 'Win32' }); } catch(e) {}
                try { Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 16 }); } catch(e) {}
                try { Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 }); } catch(e) {}
                try { Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en-US','en'] }); } catch(e) {}
                try { Object.defineProperty(navigator, 'language', { get: () => 'zh-CN' }); } catch(e) {}
                try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 }); } catch(e) {}

                // ── Permissions ──
                try {
                    const _q = navigator.permissions.query.bind(navigator.permissions);
                    navigator.permissions.query = (p) =>
                        (p.name === 'notifications' || p.name === 'geolocation')
                            ? Promise.resolve({ state: p.name === 'geolocation' ? 'granted' : 'prompt', onchange: null })
                            : _q(p);
                } catch(e) {}

                // ── window.chrome (MUST exist — headless lacks it) ──
                try {
                    window.chrome = {
                        runtime: { onConnect: { addListener: () => {} }, onMessage: { addListener: () => {} } },
                        loadTimes: () => ({}),
                        csi: () => ({}),
                        app: {},
                    };
                } catch(e) {}

                // ── Screen ──
                try { Object.defineProperty(screen, 'colorDepth', { get: () => 24 }); } catch(e) {}
                try { Object.defineProperty(screen, 'pixelDepth', { get: () => 24 }); } catch(e) {}
                if (screen.width < 1280) {
                    try { Object.defineProperty(screen, 'width', { get: () => 1920 }); } catch(e) {}
                    try { Object.defineProperty(screen, 'availWidth', { get: () => 1920 }); } catch(e) {}
                }
                if (screen.height < 720) {
                    try { Object.defineProperty(screen, 'height', { get: () => 1080 }); } catch(e) {}
                    try { Object.defineProperty(screen, 'availHeight', { get: () => 1040 }); } catch(e) {}
                }

                // ── Window dimensions ──
                try { Object.defineProperty(window, 'outerWidth', { get: () => 1920 }); } catch(e) {}
                try { Object.defineProperty(window, 'outerHeight', { get: () => 1040 }); } catch(e) {}
                })();
            """)
        if url and url != "about:blank":
            try:
                await self._page.goto(url, timeout=self._timeout, wait_until="domcontentloaded")
            except Exception:
                logger.warning(f"Initial navigation to {url} timed out, continuing anyway...")
            await self.wait_for_page_ready()
        logger.info(f"Live browser started (headless={self._headless})")

    async def stop(self) -> None:
        if self._cdp_session:
            try:
                await self._cdp_session.detach()
            except Exception:
                pass
            self._cdp_session = None
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
    # CDP session (screencast streaming)
    # ------------------------------------------------------------------

    async def get_cdp_session(self):
        """Get or create a CDP session for screencast streaming.

        Returns a Playwright CDPSession that can be used for
        Page.startScreencast / Input.dispatchMouseEvent etc.
        """
        if self._cdp_session is not None:
            return self._cdp_session
        if not self._page:
            raise RuntimeError("Browser not started — cannot create CDP session")
        from playwright.async_api import CDPSession
        self._cdp_session = await self._context.new_cdp_session(self._page)
        logger.info("CDP session created for screencast streaming")
        return self._cdp_session

    # ------------------------------------------------------------------
    # Page content
    # ------------------------------------------------------------------

    async def wait_for_page_ready(
        self,
        settle_ms: int = 2000,
        min_body_text: int = 30,
        max_polls: int = 10,
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

        # Phase 3: Poll for actual rendered content (main doc + iframes)
        for i in range(max_polls):
            try:
                result = await self._page.evaluate("""() => {
                    function countInDoc(doc) {
                        const body = doc.body;
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
                    }

                    // Main document
                    const main = countInDoc(document);
                    let totalText = main.text;
                    let totalElems = main.elems;
                    let hasSpinner = main.spinning;
                    let iframeCount = 0;
                    let iframeReady = 0;

                    // Also count content inside same‑origin iframes
                    const iframes = document.querySelectorAll('iframe');
                    for (const iframe of iframes) {
                        iframeCount++;
                        try {
                            const doc = iframe.contentDocument || iframe.contentWindow.document;
                            if (doc && doc.body) {
                                const f = countInDoc(doc);
                                totalText += f.text;
                                totalElems += f.elems;
                                if (f.spinning) hasSpinner = true;
                                if (f.text >= 50 || f.elems >= 3) iframeReady++;
                            }
                        } catch(e) {
                            // cross‑origin — can't access
                        }
                    }

                    return {
                        text: totalText,
                        elems: totalElems,
                        spinning: hasSpinner,
                        iframes: iframeCount,
                        iframesReady: iframeReady,
                    };
                }""")
                text_len = result.get("text", 0)
                elem_count = result.get("elems", 0)
                has_spinner = result.get("spinning", False)
                iframe_count = result.get("iframes", 0)
                iframe_ready = result.get("iframesReady", 0)

                if text_len >= min_body_text and elem_count >= 1 and not has_spinner:
                    iframe_info = f", {iframe_ready}/{iframe_count} iframes ready" if iframe_count else ""
                    logger.debug(f"Page ready: {text_len} chars, {elem_count} elements{iframe_info}")
                    break

                reason = ""
                if text_len < min_body_text:
                    reason = f"text={text_len}/{min_body_text}"
                elif elem_count < 1:
                    reason = f"elems={elem_count}/1"
                elif has_spinner:
                    reason = "spinner present"
                logger.debug(f"Waiting for page... ({reason}, {iframe_ready}/{iframe_count} iframes, poll {i + 1}/{max_polls})")
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

            elif atype in (ActionType.ANSWER, ActionType.FAIL, ActionType.CAPTCHA, ActionType.LOGIN):
                # Terminal actions — no browser operation needed
                return True

            else:
                logger.warning(f"Unknown action type: {atype}")
                return False

            # ── Post-action: detect new tab & navigation ──
            # CLICK may open a new browser tab (target="_blank").  Track
            # page creation per-action so a tab opened by step N doesn't
            # get detected by step N+1.
            if atype in (ActionType.CLICK, ActionType.PRESS, ActionType.TYPE):
                try:
                    # Wait briefly for the new tab to materialise
                    await asyncio.sleep(0.8)
                    all_pages = self._context.pages if self._context else []
                    new_pages = [p for p in all_pages if id(p) not in self._known_page_ids]
                    if new_pages:
                        newest = new_pages[-1]
                        logger.info(
                            f"New tab detected — switching: {newest.url[:100]}"
                        )
                        self._page = newest
                        await self.wait_for_page_ready()
                    # Update known set so we don't re-detect
                    self._known_page_ids = {id(p) for p in all_pages}
                except Exception:
                    pass

            # If the URL changed on the current page, wait for render
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

    async def _find_element_in_frames(self, element_id: str) -> tuple:
        """Search [data-som-id=X] across main document AND all iframes.

        QQ Mail and other webmail clients render compose/inbox views
        inside iframes.  The main-document querySelectorAll misses every
        element inside those frames.

        Returns:
            (frame, selector) tuple where `frame` is the Frame that
            contains the element (or the Page for main-document elements),
            or (None, None) if not found in any frame.
        """
        selector = f"[data-som-id='{element_id}']"

        # 1) Main document
        try:
            el = await self._page.query_selector(selector)
            if el:
                return self._page, selector
        except Exception:
            pass

        # 2) All sub-frames
        for frame in self._page.frames:
            if frame == self._page.main_frame:
                continue  # already checked above
            try:
                el = await frame.query_selector(selector)
                if el:
                    return frame, selector
            except Exception:
                continue

        return None, None

    async def _resolve_element_target(
        self, element_id: str, bridge: DOMBridge | None
    ) -> tuple[float, float]:
        """Resolve element center coordinates — live DOM query preferred.

        Searches across main document AND iframes (QQ Mail pattern).
        Bridge bbox can be stale after page changes, so we query the
        element's current position via data-som-id first.
        """
        # Primary: find element in main doc or frames
        frame, _ = await self._find_element_in_frames(element_id)
        if frame is not None:
            try:
                result = await frame.evaluate(f"""
                    (() => {{
                        const el = document.querySelector('[data-som-id="{element_id}"]');
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 && r.height === 0) return null;
                        return {{ x: r.left + r.width / 2, y: r.top + r.height / 2 }};
                    }})()
                """)
                if result and result["x"] is not None:
                    # Coordinates are relative to the frame's viewport.
                    # If this is a sub-frame, we need to add the iframe's offset
                    # so the returned coordinates are in page space.
                    if frame != self._page:
                        try:
                            iframe_offset = await self._page.evaluate(f"""
                                (() => {{
                                    const iframes = document.querySelectorAll('iframe');
                                    for (const if of iframes) {{
                                        try {{
                                            const doc = if.contentDocument || if.contentWindow.document;
                                            if (doc && doc.querySelector('[data-som-id="{element_id}"]')) {{
                                                const r = if.getBoundingClientRect();
                                                return {{ left: r.left, top: r.top }};
                                            }}
                                        }} catch(e) {{}}
                                    }}
                                    return null;
                                }})()
                            """)
                            if iframe_offset:
                                result["x"] += iframe_offset["left"]
                                result["y"] += iframe_offset["top"]
                        except Exception:
                            pass
                    return result["x"], result["y"]
            except Exception:
                pass

        # Fallback: bridge bbox (may be stale)
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

        Searches across main document AND iframes (QQ Mail pattern).
        Layered interaction approach:
        1. Dispatch full MouseEvent sequence (triggers JS listeners)
        2. Call el.click() for default navigation behavior
        3. For div/span containers: find inner <a> and click it
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
                // el.click() triggers default behavior (link navigation, form submit)
                if (typeof el.click === 'function') el.click();
                // For div/span containers: try clicking inner <a>
                const inner = el.querySelector('a');
                if (inner) { inner.scrollIntoView({block: 'center', inline: 'center', behavior: 'auto'}); inner.click(); }
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
                if (typeof el.select === 'function') {
                    el.select();
                } else if (el.setSelectionRange) {
                    el.setSelectionRange(0, el.value.length);
                } else if (el.getAttribute('contenteditable') === 'true' || el.isContentEditable) {
                    // Rich-text editor (e.g. QQ Mail compose body) —
                    // select all content so the following Backspace clears it
                    try {
                        const range = document.createRange();
                        range.selectNodeContents(el);
                        const sel = window.getSelection();
                        sel.removeAllRanges();
                        sel.addRange(range);
                    } catch(e) {
                        el.click();
                        el.focus();
                    }
                } else {
                    // Plain div/span — click to focus, then the keyboard
                    // events will be dispatched to the focused element
                    const r = el.getBoundingClientRect();
                    const cx = r.left + r.width / 2;
                    const cy = r.top + r.height / 2;
                    el.dispatchEvent(new MouseEvent('mousedown', {view: window, bubbles: true, cancelable: true, clientX: cx, clientY: cy}));
                    el.dispatchEvent(new MouseEvent('mouseup', {view: window, bubbles: true, cancelable: true, clientX: cx, clientY: cy}));
                    el.dispatchEvent(new MouseEvent('click', {view: window, bubbles: true, cancelable: true, clientX: cx, clientY: cy}));
                    if (typeof el.click === 'function') el.click();
                    el.focus();
                }
            """
        else:
            js_action = "el.click();"

        try:
            # Find the element in main document or any sub-frame
            frame, _ = await self._find_element_in_frames(element_id)
            if frame is None:
                return False

            result = await frame.evaluate(f"""
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
        """Click an element using 4-layer fallback, frame‑aware.

        Priority:
        1. Playwright native in the correct frame (trusted isTrusted=true)
        2. JS MouseEvent + el.click() via data-som-id (frame‑aware)
        3. href navigation: extract href and goto directly
        4. elementFromPoint at expected coordinates (survives DOM replacement)
        5. Coordinate scroll + center-click (last resort)
        """
        eid = action.element_id
        selector = f"[data-som-id='{eid}']"

        # 1) Playwright native click — frame‑aware
        frame, _ = await self._find_element_in_frames(eid)
        if frame is not None:
            try:
                if frame == self._page:
                    await self._page.click(selector, timeout=3000, force=True)
                else:
                    await frame.click(selector, timeout=3000, force=True)
                logger.debug(f"Click OK on #{eid} (Playwright)")
                return
            except Exception as e1:
                logger.debug(f"Playwright click failed on #{eid}: {e1}")

        # 2) JS MouseEvent + el.click() + inner <a> via data-som-id
        if await self._try_js_interact(eid, "click"):
            logger.debug(f"Click OK on #{eid} (JS MouseEvent)")
            return

        # 3) href navigation: extract href and goto directly.
        #    Uses bridge coordinates as fallback when data-som-id is stale.
        try:
            cx, cy = await self._resolve_element_target(eid, bridge)
            navigated = await self._navigate_to_href(eid, cx, cy)
            if navigated:
                logger.debug(f"Click OK on #{eid} (href navigation)")
                return
        except Exception:
            pass

        # 4) elementFromPoint: find the real element at the expected position.
        try:
            cx, cy = await self._resolve_element_target(eid, bridge)
            hit_ok = await self._click_at_point(cx, cy)
            if hit_ok:
                logger.debug(f"Click OK on #{eid} (elementFromPoint)")
                return
        except ValueError:
            pass

        # 5) Coordinate fallback: scroll center to point, click screen center
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

    async def _navigate_to_href(
        self, element_id: str, cx: float = 0, cy: float = 0
    ) -> bool:
        """Extract href from an element and navigate directly.

        Frame‑aware: searches main document AND iframes.
        Strategies (tried in order):
        1. Check [data-som-id] element for <a> tag or child <a>
        2. elementsFromPoint at the element's position (survives stale DOM)
        3. elementsFromPoint at provided cx, cy (fallback when DOM replaced)

        Bypasses click interception on sites like Bing.
        """
        # Frame‑aware lookup first
        frame, _ = await self._find_element_in_frames(element_id)

        try:
            # Use the frame that contains the element (or main page as fallback)
            eval_frame = frame if frame is not None else self._page
            href = await eval_frame.evaluate(f"""
                (() => {{
                    const el = document.querySelector('[data-som-id="{element_id}"]');

                    // Strategy 1: check element + children (fast path)
                    if (el) {{
                        if (el.tagName === 'A' && el.href && el.href.startsWith('http')) return el.href;
                        const inner = el.querySelector('a');
                        if (inner && inner.href && inner.href.startsWith('http')) return inner.href;
                        // elementsFromPoint at element position
                        const r = el.getBoundingClientRect();
                        return _scanPoint(r.left + r.width / 2, r.top + r.height / 2);
                    }}

                    // Strategy 2: elementsFromPoint at provided coordinates
                    return _scanPoint({cx}, {cy});

                    function _scanPoint(px, py) {{
                        if (px <= 0 || py <= 0) return null;
                        const all = document.elementsFromPoint(px, py);
                        for (const c of all) {{
                            if (c.tagName === 'A' && c.href && c.href.startsWith('http')) return c.href;
                            let p = c.parentElement;
                            for (let j = 0; j < 5 && p; j++) {{
                                if (p.tagName === 'A' && p.href && p.href.startsWith('http')) return p.href;
                                p = p.parentElement;
                            }}
                        }}
                        return null;
                    }}
                }})()
            """)
            if not href:
                logger.debug(f"_navigate_to_href #{element_id}: no href at ({cx:.0f},{cy:.0f})")
                return False
            logger.debug(f"_navigate_to_href #{element_id}: -> {href[:100]}")
            await self._page.goto(href, timeout=self._timeout, wait_until="domcontentloaded")
            await self.wait_for_page_ready()
            return True
        except Exception as exc:
            logger.debug(f"_navigate_to_href #{element_id}: error - {exc}")
            return False

    async def _click_at_point(self, cx: float, cy: float) -> bool:
        """Click whatever interactive element is at (cx, cy) using JS.

        Walks up from elementFromPoint to find a clickable ancestor
        (a, button, [role=button], [onclick]) and clicks it.
        Survives stale data-som-id because it uses live DOM position.
        """
        try:
            result = await self._page.evaluate(f"""
                (() => {{
                    const el = document.elementFromPoint({cx}, {cy});
                    if (!el || el === document.body || el === document.documentElement) return false;

                    // Walk up to find the nearest clickable ancestor
                    let current = el;
                    const MAX_DEPTH = 6;
                    for (let i = 0; i < MAX_DEPTH; i++) {{
                        const tag = current.tagName.toLowerCase();
                        const role = (current.getAttribute('role') || '').toLowerCase();
                        const hasClick = current.hasAttribute('onclick') || current.hasAttribute('tabindex');
                        if (tag === 'a' || tag === 'button' || tag === 'input' ||
                            role === 'button' || role === 'link' || hasClick) {{
                            break;
                        }}
                        if (!current.parentElement) break;
                        current = current.parentElement;
                    }}

                    current.scrollIntoView({{block: 'center', inline: 'center', behavior: 'auto'}});
                    current.focus();
                    current.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true, cancelable: true, clientX: {cx}, clientY: {cy}}}));
                    current.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true, cancelable: true, clientX: {cx}, clientY: {cy}}}));
                    current.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true, clientX: {cx}, clientY: {cy}}}));
                    if (typeof current.click === 'function') current.click();
                    return current.tagName;
                }})()
            """)
            return bool(result)
        except Exception:
            return False

    async def _type_in_element(
        self, action: Action, bridge: DOMBridge | None
    ) -> None:
        """Type text: JS focus + fill, data-som-id fill, or coordinate typing.

        Frame‑aware: searches main document AND iframes.
        """
        eid = action.element_id
        value = action.value or ""

        # 1) JS: focus & select all via data-som-id, then keyboard type
        if await self._try_js_interact(eid, "select_all"):
            await self._page.keyboard.press("Backspace")
            await self._page.keyboard.type(value)
            logger.debug(f"JS type OK on #{eid}: '{value[:30]}'")
            return

        # 2) Playwright fill via data-som-id — frame‑aware
        frame, _ = await self._find_element_in_frames(eid)
        if frame is not None:
            selector = f"[data-som-id='{eid}']"
            try:
                await frame.fill(selector, value, timeout=3000)
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
        """Select option: JS focus, data-som-id selector, or coordinate fallback.

        Frame‑aware: searches main document AND iframes.
        """
        eid = action.element_id
        value = action.value or ""

        # 1) JS: focus the element
        if await self._try_js_interact(eid, "focus"):
            await self._page.keyboard.type(value)
            await self._page.keyboard.press("Enter")
            logger.debug(f"JS select OK on #{eid}: '{value[:30]}'")
            return

        # 2) Playwright select_option via data-som-id — frame‑aware
        frame, _ = await self._find_element_in_frames(eid)
        if frame is not None:
            selector = f"[data-som-id='{eid}']"
            try:
                await frame.select_option(selector, value, timeout=3000)
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
