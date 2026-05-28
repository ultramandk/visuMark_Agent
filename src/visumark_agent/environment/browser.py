"""Playwright-based browser environment for web agents."""

from pathlib import Path

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from visumark_agent.environment.actions import Action, ActionType


class BrowserEnv:
    """Manages a Playwright browser instance and executes agent actions."""

    def __init__(
        self,
        headless: bool = True,
        viewport: tuple[int, int] = (1280, 720),
        timeout: int = 30_000,
    ):
        self.headless = headless
        self.viewport = {"width": viewport[0], "height": viewport[1]}
        self.timeout = timeout
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def page(self) -> Page | None:
        return self._page

    async def start(self) -> None:
        """Launch the browser and create a context."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(viewport=self.viewport)
        self._page = await self._context.new_page()
        logger.info(f"Browser started (headless={self.headless})")

    async def goto(self, url: str) -> None:
        """Navigate to a URL and wait for the page to settle."""
        if not self._page:
            raise RuntimeError("Browser not started")
        await self._page.goto(url, timeout=self.timeout, wait_until="domcontentloaded")
        logger.info(f"Navigated to {url}")

    async def screenshot(self) -> bytes:
        """Take a full-viewport screenshot, return PNG bytes."""
        if not self._page:
            raise RuntimeError("Browser not started")
        return await self._page.screenshot(full_page=False)

    async def execute(self, action: Action) -> bool:
        """Execute a single action on the page. Returns True on success."""
        if not self._page:
            raise RuntimeError("Browser not started")

        try:
            if action.action_type == ActionType.CLICK and action.element_id is not None:
                await self._click_element(action.element_id)
            elif action.action_type == ActionType.TYPE and action.element_id is not None:
                await self._type_in_element(action.element_id, action.value or "")
            elif action.action_type == ActionType.SCROLL:
                direction = action.value or "down"
                delta = 500 if direction == "down" else -500
                await self._page.mouse.wheel(0, delta)
            elif action.action_type == ActionType.GOTO:
                await self.goto(action.value or "about:blank")
            elif action.action_type == ActionType.PRESS:
                await self._page.keyboard.press(action.value or "Enter")
            elif action.action_type == ActionType.HOVER:
                await self._page.mouse.move(
                    (action.x or 0.5) * self.viewport["width"],
                    (action.y or 0.5) * self.viewport["height"],
                )
            elif action.action_type == ActionType.WAIT:
                await self._page.wait_for_timeout(int(action.value or 1000))
            else:
                logger.warning(f"Unknown action type: {action.action_type}")
                return False

            logger.debug(f"Action ok: {action.to_dict()}")
            return True
        except Exception as exc:
            logger.error(f"Action failed: {action.to_dict()} — {exc}")
            return False

    async def _click_element(self, element_id: int) -> None:
        """Click an element by its SoM id (stored as a data attribute)."""
        if not self._page:
            return
        # tag interactive elements with data-som-id before querying
        selector = f"[data-som-id='{element_id}']"
        await self._page.click(selector, timeout=self.timeout)

    async def _type_in_element(self, element_id: int, text: str) -> None:
        """Type into an element referenced by SoM id."""
        if not self._page:
            return
        selector = f"[data-som-id='{element_id}']"
        await self._page.fill(selector, text, timeout=self.timeout)

    async def tag_elements(self) -> None:
        """Annotate the DOM with data-som-id attributes so actions can target elements.

        Uses the same selector as ElementExtractor.
        """
        if not self._page:
            return
        selector = (
            "button, a, input, select, textarea, "
            "[role='button'], [role='link'], [role='checkbox'], [role='radiogroup'], "
            "[role='combobox'], [role='listbox'], [role='menu'], [role='menuitem'], "
            "[role='tab'], [role='switch'], [role='slider'], "
            "[onclick], [tabindex]"
        )
        await self._page.evaluate(f"""(s) => {{
            document.querySelectorAll(s).forEach((el, i) => {{
                el.setAttribute('data-som-id', i + 1);
            }});
        }}""", selector)

    async def stop(self) -> None:
        """Tear down browser, context, and playwright."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")
