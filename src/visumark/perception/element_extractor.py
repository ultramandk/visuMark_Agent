"""Extract interactive elements from a web page for SoM annotation.

All DOM access happens in a SINGLE atomic page.evaluate() call. This
completely eliminates the "stale handle" problem where Playwright element
handles become invalid because the DOM changed between querySelectorAll
and the final attribute update.

The old handle-iteration approach was fundamentally racy on dynamic pages
(SPAs, lazy-loaded content, hydration).
"""

import json

from loguru import logger
from playwright.async_api import Page

from visumark.core.types import PageElement
from visumark.environment.dom_utils import (
    INTERACTIVE_SELECTOR,
    INTERACTIVE_TAG_SET,
    clean_text,
)


class ElementExtractor:
    """Extract clickable / interactable elements from a Playwright page.

    Uses a single JS evaluation to:
    1. Query all interactive elements
    2. Filter by visibility and size
    3. Extract attributes, text, bbox
    4. Sort by visual position (top→bottom, left→right)
    5. Assign sequential SoM IDs
    6. Write data-som-id into the DOM — all atomically
    """

    def __init__(self, max_elements: int = 200, min_element_size: int = 4):
        self.max_elements = max_elements
        self.min_element_size = min_element_size

    async def extract(
        self,
        page: Page,
        ats_nodes: list[dict] | None = None,
    ) -> list[PageElement]:
        """Extract interactive elements from the current page.

        All DOM access happens in one atomic JS call → zero stale handles.
        """
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        vw, vh = viewport["width"], viewport["height"]

        # Build a lightweight ATS lookup: backend_node_id → { role, name }
        ats_map: dict[str, dict] = {}
        if ats_nodes:
            for node in ats_nodes:
                nid = node.get("backendDOMNodeId") or node.get("nodeId")
                if nid:
                    ats_map[str(nid)] = {
                        "role": node.get("role", ""),
                        "name": node.get("name", ""),
                    }

        # ── Single JS evaluation: extract everything atomically ──
        raw_elements = await page.evaluate(
            _build_extraction_js(viewport, ats_map, self.max_elements, self.min_element_size)
        )

        # Convert raw JS objects to PageElement dataclasses
        elements: list[PageElement] = []
        for raw in raw_elements:
            elements.append(PageElement(
                id=str(raw["id"]),
                tag=raw["tag"],
                text=clean_text(raw["text"]),
                bbox=(
                    raw["x"] / vw,
                    raw["y"] / vh,
                    raw["w"] / vw,
                    raw["h"] / vh,
                ),
                attributes=raw.get("attributes", {}),
                backend_node_id=raw.get("backend_node_id"),
                selector=raw.get("selector", raw["tag"]),
            ))

        logger.debug(f"Extracted {len(elements)} interactive elements (atomic JS)")
        return elements


def _build_extraction_js(
    viewport: dict,
    ats_map: dict,
    max_elements: int,
    min_size: int,
) -> str:
    """Build the JavaScript that runs inside the page atomically.

    Returns a JSON-serializable list of element dicts.
    """
    ats_json = json.dumps(ats_map)
    escaped_selector = INTERACTIVE_SELECTOR.replace('"', '\\"')
    interactive_tags_json = json.dumps(sorted(INTERACTIVE_TAG_SET))
    interactive_roles_json = json.dumps([
        "button", "link", "checkbox", "radio", "radiogroup", "combobox",
        "listbox", "menu", "menuitem", "tab", "switch", "slider",
        "option", "textbox", "searchbox",
    ])

    return f"""(() => {{
    const MAX = {max_elements};
    const MIN_SIZE = {min_size};
    const INTERACTIVE_TAGS = new Set({interactive_tags_json});
    const INTERACTIVE_ROLES = new Set({interactive_roles_json});
    const ATS_MAP = {ats_json};
    const SELECTOR = "{escaped_selector}";
    const VW = {viewport["width"]};
    const VH = {viewport["height"]};

    const KEY_ATTRS = [
        'id', 'class', 'name', 'type', 'href', 'placeholder',
        'aria-label', 'aria-expanded', 'aria-haspopup',
        'aria-checked', 'aria-selected', 'aria-describedby',
        'role', 'value', 'title', 'alt', 'tabindex',
        'disabled', 'checked', 'readonly', 'required',
        'data-backend-node-id', 'data-id', 'data-testid',
    ];

    function getText(el) {{
        const tag = el.tagName.toLowerCase();
        const aria = el.getAttribute('aria-label');
        if (aria && aria.trim()) return aria.trim().substring(0, 80);
        const ph = el.getAttribute('placeholder');
        if (ph && ph.trim()) return ph.trim().substring(0, 80);
        const ttl = el.getAttribute('title');
        if (ttl && ttl.trim()) return ttl.trim().substring(0, 80);
        if (tag === 'input' && el.value) return el.value.substring(0, 80);
        const alt = el.getAttribute('alt');
        if (alt && alt.trim()) return alt.trim().substring(0, 80);
        if (tag === 'select') {{
            const opt = el.options && el.options[el.selectedIndex];
            if (opt && opt.text) return opt.text.trim().substring(0, 80);
        }}
        const tc = (el.textContent || '').trim();
        return tc.substring(0, 80);
    }}

    function buildSelector(tag, attrs) {{
        if (attrs['id']) return tag + '#' + CSS.escape(attrs['id']);
        if (attrs['data-testid']) return '[data-testid="' + CSS.escape(attrs['data-testid']) + '"]';
        if (attrs['data-backend-node-id']) return '[data-backend-node-id="' + CSS.escape(attrs['data-backend-node-id']) + '"]';
        if (attrs['class']) {{
            const cls = attrs['class'].trim().split(/\\s+/)[0];
            if (cls) return tag + '.' + CSS.escape(cls);
        }}
        if (attrs['aria-label']) return tag + '[aria-label="' + CSS.escape(attrs['aria-label']) + '"]';
        if (attrs['name']) return tag + '[name="' + CSS.escape(attrs['name']) + '"]';
        return tag;
    }}

    // 1. Query all interactive elements via CSS selectors
    const all = document.querySelectorAll(SELECTOR);
    const candidates = [];
    const seenElements = new Set();

    for (let el of all) {{
        if (candidates.length >= MAX) break;
        seenElements.add(el);

        // Visibility check
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        if (style.opacity === '0') continue;

        let tag = el.tagName.toLowerCase();
        let rect = el.getBoundingClientRect();
        let area = rect.width * rect.height;

        // ── Size check with relaxed thresholds ──
        // Interactive HTML tags (button, a, input, etc.) and aria-label
        // elements are often small icon buttons. Use a lower threshold.
        const hasAriaLabel = !!el.getAttribute('aria-label');
        // cursor: pointer + short text catches framework buttons
        // (React/Vue event delegation where DOM has zero interactive markers).
        // Restrict to short text (≤8 chars) to avoid false positives from
        // decorative elements and long text links that happen to have pointer cursor.
        const rawText = (el.innerText || el.textContent || '').trim();
        const hasPointerCursor = style.cursor === 'pointer';
        const isFrameworkButton = hasPointerCursor && rawText.length >= 1 && rawText.length <= 8;
        const isKnownInteractive = INTERACTIVE_TAGS.has(tag)
            || (!!el.getAttribute('role') && INTERACTIVE_ROLES.has(el.getAttribute('role')))
            || hasAriaLabel
            || isFrameworkButton;
        const sizeThreshold = isKnownInteractive ? 1 : MIN_SIZE;

        // ── Parent bbox inheritance for tiny children ──
        // An SVG icon inside a button may have a tiny rect but its
        // clickable area is the parent's bbox. Walk up to find it.
        if (area < sizeThreshold && (tag === 'svg' || tag === 'path' || tag === 'circle' || tag === 'rect' || tag === 'g' || tag === 'i' || tag === 'span')) {{
            let parent = el.parentElement;
            const MAX_DEPTH = 5;
            let depth = 0;
            while (parent && depth < MAX_DEPTH) {{
                const parentTag = parent.tagName.toLowerCase();
                const parentRole = (parent.getAttribute('role') || '').toLowerCase();
                const parentAria = !!parent.getAttribute('aria-label');
                const isParentInteractive = INTERACTIVE_TAGS.has(parentTag)
                    || (parentRole && (INTERACTIVE_ROLES.has(parentRole) || parentRole === 'button'))
                    || parentAria
                    || parent.hasAttribute('onclick')
                    || parent.hasAttribute('tabindex');
                if (isParentInteractive) {{
                    const pr = parent.getBoundingClientRect();
                    const pa = pr.width * pr.height;
                    if (pa >= sizeThreshold) {{
                        // Use parent as the labeled element instead
                        el = parent;
                        tag = parentTag;
                        rect = pr;
                        area = pa;
                        break;
                    }}
                }}
                parent = parent.parentElement;
                depth++;
            }}
            // If no suitable parent found, skip this tiny element
            if (area < sizeThreshold) continue;
        }} else if (area < sizeThreshold) {{
            continue;
        }}

        // Off-screen check
        if (rect.bottom < -500 || rect.top > VH + 500) continue;
        if (rect.right < -500 || rect.left > VW + 500) continue;

        // Attributes
        const attrs = {{}};
        for (const k of KEY_ATTRS) {{
            const v = el.getAttribute(k);
            if (v !== null && v !== '') attrs[k] = v;
        }}

        const text = getText(el);
        const backendId = attrs['data-backend-node-id'] || null;

        // ATS fusion
        if (ATS_MAP && backendId && ATS_MAP[backendId]) {{
            const ats = ATS_MAP[backendId];
            if (!attrs['role'] && ats.role) attrs['role'] = ats.role;
        }}

        const selector = buildSelector(tag, attrs);

        candidates.push({{
            tag: tag,
            text: text,
            x: rect.x,
            y: rect.y,
            w: rect.width,
            h: rect.height,
            attributes: attrs,
            backend_node_id: backendId,
            selector: selector,
        }});
    }}

    // ── SPA supplement: scan ALL visible elements with cursor:pointer
    //     that were missed by CSS selectors.  React/Vue SPAs render
    //     buttons as <span>/<div> with zero HTML attributes — only
    //     cursor:pointer distinguishes them from plain text.
    if (candidates.length < MAX) {{
        const allElements = document.querySelectorAll('*');
        for (const el of allElements) {{
            if (candidates.length >= MAX) break;
            if (seenElements.has(el)) continue;

            const tag = el.tagName.toLowerCase();
            // Skip structural / non-interactive tags
            if (tag === 'html' || tag === 'body' || tag === 'head' ||
                tag === 'script' || tag === 'style' || tag === 'meta' ||
                tag === 'link' || tag === 'br' || tag === 'hr') continue;

            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') continue;
            if (style.cursor !== 'pointer') continue;

            const rect = el.getBoundingClientRect();
            const area = rect.width * rect.height;
            if (area < 1 || area > 500000) continue;
            if (rect.bottom < -500 || rect.top > VH + 500) continue;
            if (rect.right < -500 || rect.left > VW + 500) continue;

            const text = getText(el);
            if (!text) continue;  // Must have visible text

            const attrs = {{}};
            for (const k of KEY_ATTRS) {{
                const v = el.getAttribute(k);
                if (v !== null && v !== '') attrs[k] = v;
            }}
            const backendId = attrs['data-backend-node-id'] || null;
            const selector = buildSelector(tag, attrs);

            candidates.push({{
                tag: tag, text: text,
                x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                attributes: attrs,
                backend_node_id: backendId,
                selector: selector,
            }});
        }}
        console.debug('SPA fallback scan: found', candidates.length, 'cursor-pointer elements');
    }}

    // 2. Sort by visual position: top→bottom, left→right
    candidates.sort((a, b) => {{
        const dy = a.y - b.y;
        if (Math.abs(dy) > 10) return dy;
        return a.x - b.x;
    }});

    // 3. Assign sequential IDs and inject data-som-id into DOM
    for (let i = 0; i < candidates.length; i++) {{
        const id = i + 1;
        candidates[i].id = id;

        // Tag the EXACT element with data-som-id using elementFromPoint.
        // CSS selectors are ambiguous for SPA pages where many elements
        // share the same tag/class (e.g. multiple <div.xmail-ui-btn>).
        // elementFromPoint at bbox center finds the actual element under
        // the cursor, not the first match of a generic selector.
        try {{
            const cx = candidates[i].x + candidates[i].w / 2;
            const cy = candidates[i].y + candidates[i].h / 2;
            const atPoint = document.elementFromPoint(cx, cy);
            if (atPoint && atPoint !== document.body && atPoint !== document.documentElement) {{
                atPoint.setAttribute('data-som-id', String(id));
            }} else {{
                // Fallback: CSS selector
                const el = document.querySelector(candidates[i].selector);
                if (el) el.setAttribute('data-som-id', String(id));
            }}
        }} catch(e) {{
            // Element gone — but data is still valid for annotation
        }}
    }}

    return candidates;
}})()"""
