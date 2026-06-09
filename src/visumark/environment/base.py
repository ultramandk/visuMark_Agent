"""Abstract environment interface — shared by live browser and offline snapshots."""

from abc import ABC, abstractmethod

from visumark.core.types import Action


class BaseEnvironment(ABC):
    """Unified interface for browser environments.

    Two implementations:
        LiveEnvironment  — real Playwright browser for online task execution
        OfflineEnvironment — cached MHTML snapshots for Mind2Web evaluation
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def start(self, url: str = "about:blank") -> None:
        """Launch the browser and navigate to the initial URL."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Tear down the browser and release all resources."""
        ...

    # ------------------------------------------------------------------
    # Page content
    # ------------------------------------------------------------------

    @abstractmethod
    async def load_html(self, html: str) -> None:
        """Load raw HTML content into the current page.

        This is the key method for OfflineEnvironment — it replaces goto()
        when working with cached Mind2Web snapshots.
        """
        ...

    @abstractmethod
    async def screenshot(self) -> bytes:
        """Take a screenshot of the current viewport, return PNG bytes."""
        ...

    @abstractmethod
    async def get_page_html(self) -> str:
        """Return the full HTML source of the current page."""
        ...

    @abstractmethod
    async def get_accessibility_tree(self) -> dict:
        """Return the page's Accessibility Tree as a nested dict.

        The ATS provides semantic role, name, and hierarchy information
        that complements raw DOM extraction (WebGUM approach).
        """
        ...

    # ------------------------------------------------------------------
    # Page metadata
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_page_title(self) -> str:
        """Return the current page title."""
        ...

    @abstractmethod
    async def get_page_url(self) -> str:
        """Return the current page URL."""
        ...

    @abstractmethod
    def get_viewport(self) -> dict[str, int]:
        """Return the viewport dimensions {'width': w, 'height': h}."""
        ...

    # ------------------------------------------------------------------
    # DOM manipulation
    # ------------------------------------------------------------------

    @abstractmethod
    async def tag_elements(self, id_to_selector: dict[str, str]) -> None:
        """Inject data-som-id attributes into DOM elements.

        Args:
            id_to_selector: Mapping from SoM label string → Playwright CSS selector.
                e.g. {"1": "button.search-btn", "2": "input#email"}
        """
        ...

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, action: Action, bridge=None) -> bool:
        """Execute a single action on the page. Returns True on success.

        Args:
            action: The action to execute.
            bridge: Optional DOMBridge for coordinate fallback on selector failure.
        """
        ...

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def is_live(self) -> bool:
        """True if this is a live browser, False if offline/snapshot."""
        ...
