"""DOM manipulation helpers — selector generation, ATS parsing, element ID injection."""

import re


def generate_css_selector(tag: str, attributes: dict) -> str:
    """Generate a unique-ish CSS selector for an element from its tag + attributes.

    Priority order for disambiguation:
        1. id attribute       → "tag#id"
        2. data-backend-node-id → "[data-backend-node-id='...']"
        3. unique class combo → "tag.class1.class2"
        4. name attribute     → "tag[name='...']"
        5. aria-label         → "[aria-label='...']"
        6. fallback           → "tag:nth-child(...)"  (not generated here — caller handles)
    """
    # 1. ID
    if "id" in attributes and attributes["id"]:
        return f"{tag}#{_escape_css_string(attributes['id'])}"

    # 2. backend_node_id (Mind2Web)
    if "data-backend-node-id" in attributes and attributes["data-backend-node-id"]:
        bid = attributes["data-backend-node-id"]
        return f"[data-backend-node-id='{_escape_css_string(bid)}']"

    # 3. Unique class combination
    if "class" in attributes and attributes["class"]:
        classes = attributes["class"].strip().split()
        if classes:
            cls_selector = "".join(f".{_escape_css_string(c)}" for c in classes[:3])
            return f"{tag}{cls_selector}"

    # 4. Name attribute
    if "name" in attributes and attributes["name"]:
        return f"{tag}[name='{_escape_css_string(attributes['name'])}']"

    # 5. aria-label
    if "aria-label" in attributes and attributes["aria-label"]:
        al = attributes["aria-label"]
        return f"{tag}[aria-label='{_escape_css_string(al)}']"

    # 6. role + name from ATS
    if "role" in attributes and attributes["role"]:
        role = attributes["role"]
        if "aria-label" in attributes:
            return f"[role='{_escape_css_string(role)}'][aria-label='{_escape_css_string(attributes['aria-label'])}']"
        return f"[role='{_escape_css_string(role)}']"

    # 7. Bare tag fallback
    return tag


def _escape_css_string(s: str) -> str:
    """Escape special characters in a CSS string value."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


def parse_accessibility_tree(snapshot: dict) -> list[dict]:
    """Flatten a Playwright accessibility snapshot into a list of nodes.

    Each node: {role, name, value, description, checked, disabled, ...}
    """
    nodes = []

    def _walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        nodes.append(node)
        for child in node.get("children", []):
            _walk(child)

    if snapshot:
        _walk(snapshot)
    return nodes


def build_data_som_id_script(id_to_selector: dict[str, str]) -> str:
    """Build a JavaScript snippet that injects data-som-id attributes into DOM elements.

    Uses explicit ID → selector mapping (each element gets exactly one SoM ID),
    eliminating the old bug where tag_elements() and extractor used different
    element orderings.

    Args:
        id_to_selector: {"1": "button.btn", "2": "input#email", ...}

    Returns:
        JavaScript code string ready for page.evaluate().
    """
    entries = []
    for som_id, selector in id_to_selector.items():
        safe_sel = selector.replace("\\", "\\\\").replace("'", "\\'")
        entries.append(f"{{id:'{som_id}',sel:'{safe_sel}'}}")

    return f"""
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


# ---------------------------------------------------------------------------
# Interactive element selectors
# ---------------------------------------------------------------------------

# Primary: standard interactive HTML elements + ARIA roles
INTERACTIVE_SELECTOR = (
    "button, a, input, select, textarea, "
    "[contenteditable='true'], "   # Rich-text editors (e.g. QQ Mail compose body)
    "[role='button'], [role='link'], [role='checkbox'], [role='radio'], "
    "[role='radiogroup'], [role='combobox'], [role='listbox'], "
    "[role='menu'], [role='menuitem'], [role='tab'], [role='switch'], "
    "[role='slider'], [role='option'], [role='textbox'], [role='searchbox'], "
    "[onclick], [tabindex], "
    "[aria-label], "            # Icon buttons, search buttons, etc.
    "[data-backend-node-id]"    # Mind2Web marker
)

# Tags that are unambiguously interactive — lower size threshold applies
INTERACTIVE_TAG_SET = frozenset({
    "button", "a", "input", "select", "textarea", "summary", "details",
})

# Extended: also include structural elements that may be interactive
EXTENDED_SELECTOR = INTERACTIVE_SELECTOR + ", " + (
    "summary, details, label, [contenteditable='true'], [draggable='true']"
)


def clean_text(text: str | None, max_len: int = 80) -> str:
    """Clean and truncate element text for display and VLM context.

    - Strips whitespace
    - Collapses multiple spaces
    - Truncates to max_len with '…'
    """
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) > max_len:
        return cleaned[: max_len - 1] + "…"
    return cleaned
