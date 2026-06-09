"""DOM Bridge — bidirectional mapping between SoM visual labels and DOM nodes.

This is the KEY module that solves the old SoM ID mismatch bug.

Problem: In the old code, `tag_elements()` and `ElementExtractor.extract()` used
the same CSS selector but different element orderings (one without visibility
filtering, one with). VLM said "click #3" but #3 pointed to different elements
in the SoM screenshot vs. the DOM — causing clicks on wrong elements.

Solution: A single DOMBridge built from the same element list used for SoM
annotation. All lookups (VLM prediction → DOM selector, SoM ID → backend_node_id)
go through this bridge, guaranteeing consistency.

Two use cases:
    1. LIVE EXECUTION:   VLM says "click #3" → bridge.som_id_to_selector("3")
                          → "button.search-btn" → Playwright clicks it
    2. MIND2WEB EVAL:    VLM says "click #3" → bridge.som_id_to_backend_node("3")
                          → "node-42" → comparator checks against pos_candidates
"""

from visumark.core.types import PageElement


class DOMBridge:
    """SoM visual label ↔ DOM node bidirectional mapping.

    Built once per step from the same PageElement list used for SoM annotation.
    All subsequent lookups (execution, evaluation) use this bridge.
    """

    def __init__(self):
        self._som_to_selector: dict[str, str] = {}    # SoM ID → Playwright CSS selector
        self._som_to_node: dict[str, str] = {}         # SoM ID → Mind2Web backend_node_id
        self._node_to_som: dict[str, str] = {}         # backend_node_id → SoM ID
        self._som_to_tag: dict[str, str] = {}          # SoM ID → element tag (for display)
        self._som_to_text: dict[str, str] = {}         # SoM ID → element text (for display)
        self._som_to_bbox: dict[str, tuple[float, float, float, float]] = {}  # SoM ID → bbox

    def build_from_elements(self, elements: list[PageElement]) -> "DOMBridge":
        """Build complete mapping from a list of PageElement objects.

        Call this once per step, right after ElementExtractor.extract().
        Use the SAME elements list for SoMMarker.annotate() to ensure
        visual labels match DOM mappings.

        Args:
            elements: Extracted page elements (already sorted, with SoM IDs assigned).

        Returns:
            self (for method chaining).
        """
        for elem in elements:
            som_id = elem.id

            # Execution mapping
            if elem.selector:
                self._som_to_selector[som_id] = elem.selector

            # Evaluation mapping (Mind2Web)
            if elem.backend_node_id:
                self._som_to_node[som_id] = elem.backend_node_id
                self._node_to_som[elem.backend_node_id] = som_id

            # Display metadata
            self._som_to_tag[som_id] = elem.tag
            self._som_to_text[som_id] = elem.text
            self._som_to_bbox[som_id] = elem.bbox

        return self

    # ------------------------------------------------------------------
    # Execution lookups
    # ------------------------------------------------------------------

    def som_id_to_selector(self, som_id: str) -> str | None:
        """VLM prediction → Playwright CSS selector for browser execution.

        Args:
            som_id: SoM label string (e.g. "3").

        Returns:
            CSS selector string or None if not found.
        """
        return self._som_to_selector.get(som_id)

    def get_id_to_selector_map(self) -> dict[str, str]:
        """Return full SoM ID → selector map for DOM injection."""
        return dict(self._som_to_selector)

    # ------------------------------------------------------------------
    # Evaluation lookups (Mind2Web)
    # ------------------------------------------------------------------

    def som_id_to_backend_node(self, som_id: str) -> str | None:
        """VLM prediction → Mind2Web backend_node_id for ground-truth comparison.

        Args:
            som_id: SoM label string (e.g. "3").

        Returns:
            backend_node_id string (e.g. "node-42") or None if not available.
        """
        return self._som_to_node.get(som_id)

    def backend_node_to_som_id(self, backend_node_id: str) -> str | None:
        """Mind2Web ground truth → SoM label (reverse lookup).

        Useful for debugging: "which SoM number should the VLM have picked?"
        """
        return self._node_to_som.get(backend_node_id)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def get_element_label(self, som_id: str) -> str:
        """Build a human-readable label for a SoM element.

        Returns something like: "CLICK #3 [button] Search" or "TYPE #5 [input] email"
        """
        tag = self._som_to_tag.get(som_id, "?")
        text = self._som_to_text.get(som_id, "")
        if text:
            return f"#{som_id} [{tag}] {text[:40]}"
        return f"#{som_id} [{tag}]"

    def get_bbox(self, som_id: str) -> tuple[float, float, float, float] | None:
        """Get the normalized bounding box for a SoM element."""
        return self._som_to_bbox.get(som_id)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._som_to_selector)

    def __repr__(self) -> str:
        return (
            f"DOMBridge(elements={len(self._som_to_selector)}, "
            f"with_backend_node={len(self._som_to_node)})"
        )
