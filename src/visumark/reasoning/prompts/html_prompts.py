"""HTML text-mode prompt templates for Mind2Web evaluation.

The HTML mode follows the original Mind2Web paper's approach: the LLM
receives a text prompt with a structured candidate element list and selects
the correct element by number.  No screenshot — pure text reasoning.

Difference from SoM mode: the "numbered element" is a position in the
candidate list, NOT a visual label on a screenshot.  The DOMBridge maps
the candidate index to a backend_node_id for comparison.
"""

from visumark.core.types import PageElement


# ============================================================================
# Evaluation system prompt
# ============================================================================

HTML_EVAL_SYSTEM_PROMPT = """You are evaluating a web agent on a benchmark. You receive a list of numbered interactive elements from a web page, each with its HTML tag, CSS class, ARIA label, bounding box position, and visible text.

Your task: select the ONE element that should be interacted with next to make progress toward the task goal.

## Response Format

Respond ONLY with a short JSON object:

```json
{"thought": "<brief reasoning>", "element_id": "<number>"}
```

## Rules
1. Choose the element number that best matches the task goal.
2. Base your decision on: element tag, class name, text content, aria label, and position.
3. element_id must be the NUMBER of the candidate element (e.g. "3").
4. Output ONLY the JSON. No markdown, no code blocks.
5. Keep thought under 50 words."""


# ============================================================================
# Live agent system prompt (text mode)
# ============================================================================

HTML_SYSTEM_PROMPT = """You are a helpful assistant that is great at website design, navigation, and executing tasks for the user."""


HTML_LIVE_SYSTEM_PROMPT = """You are a web automation agent. You receive a numbered list of interactive elements from a web page, each with its HTML tag, CSS class, text content, ARIA label, and position.

Your task: decide the NEXT action to complete the user's goal.

## Response Format

Respond with a JSON object ONLY:
{
    "plan": "Brief 1-sentence summary of your overall plan",
    "thought": "Brief analysis of what you see and what needs to happen",
    "action": "<action_type>",
    "element_id": "<number>",
    "value": "<text>"
}

## Available Actions

| Action | Description | Required Fields |
|--------|-------------|----------------|
| click | Click the numbered element | action, element_id |
| type | Type text into the numbered input | action, element_id, value |
| scroll down | Scroll the page down | action |
| scroll up | Scroll the page up | action |
| goto | Navigate to a URL | action, value |
| press | Press a key (Enter, Escape, Tab) | action, value |
| captcha | Pause for human to pass verification | action, value |
| login | Pause for human to enter credentials | action, value |
| answer | Task is FULLY COMPLETE — agent STOPS | action, value |
| fail | Task is IMPOSSIBLE — agent STOPS | action, value |

## Rules

1. Pick the element number that best matches the task goal.
2. Base your decision on: element tag, class name, text content, aria-label, and position.
3. element_id must be the NUMBER shown before the element (e.g., "3").
4. If you need to type, include the exact text in "value".
5. If the target element is not in the list, use scroll down to reveal more.
6. thought MUST be under 50 words.
7. Output ONLY the JSON. No markdown, no code blocks.

## New Tab / Wrong Page Recovery
- If you navigated to a WRONG page: use goto to return to the original URL.
- Do NOT keep clicking random elements on a wrong page.
- Check the page URL and title shown above.

## Task Completion — use "answer" IMMEDIATELY
- If the page clearly shows the answer to the user's question → use **answer** with the result.
- Examples: search results showing the location, a product price displayed, a translation result, weather info.
- Do NOT keep clicking or scrolling after you have the answer.
- Example: {"plan": "搜索中山大学位置", "thought": "页面显示了中山大学南校园的地址", "action": "answer", "value": "中山大学广州校区南校园位于广州市海珠区新港西路135号"}

## CAPTCHA / Login
- If you see error messages about verification or security checks → use captcha.
- If you see a login form requiring credentials → use login.
- Do NOT attempt to solve CAPTCHAs or guess passwords."""


# ============================================================================
# Build user prompt for live web (HTML mode)
# ============================================================================

def build_html_live_user_prompt(
    task: str,
    elements: list,
    history: list,
    page_title: str = "",
    page_url: str = "",
    page_text: str = "",
) -> str:
    """Build a text prompt for live HTML mode with full context."""
    parts = [
        f"## User Task\n{task}",
        f"## Current Page\nTitle: {page_title}\nURL: {page_url}",
    ]

    # Include page text summary — the interactive element list alone
    # doesn't show search results, paragraphs, or answer text.
    body = page_text or _get_page_text(elements)
    if body:
        parts.append(f"## Page Content\n{body[:2000]}")
        parts.append("")

    # Include most recent plan
    if history:
        last_plan = None
        for rec in reversed(history):
            if rec.reasoner_output and rec.reasoner_output.plan:
                last_plan = rec.reasoner_output.plan
                break
        if last_plan:
            parts.append(f"## Current Plan\n{last_plan}")

        # Recent actions with verification
        recent = history[-5:]
        lines = ["## Recent Actions (with verification results)"]
        verify_fail = 0
        for rec in recent:
            if rec.action:
                act_desc = _html_describe_action(rec.action)
                status, detail = _html_format_history(rec)
                lines.append(f"- {status} Step {rec.step}: {act_desc}{detail}")
                if rec.verification and not rec.verification.effect_achieved:
                    verify_fail += 1
        parts.append("\n".join(lines))

        if verify_fail >= 2:
            parts.append("\n⚠️  MULTIPLE RECENT ACTIONS FAILED. Try a different approach.")

    # Element list
    parts.append(f"\n## Interactive Elements ({len(elements)} total)\n")
    parts.append("Select the ONE element to interact with next:\n")
    for e in elements:
        parts.append(_format_candidate(e))

    parts.append("\n## Instruction")
    parts.append("Return ONLY the JSON object with plan, thought, action, element_id, and value if needed.")
    return "\n".join(parts)


# ============================================================================
# Build user prompt for Mind2Web evaluation
# ============================================================================

def build_html_eval_user_prompt(
    task: str,
    elements: list[PageElement],
    step_idx: int,
    total_steps: int,
    website: str = "",
    domain: str = "",
) -> str:
    """Build a compact text prompt for Mind2Web evaluation.

    Lists candidate elements with key attributes.  The format is
    optimized for models with small context windows (e.g. 4096 tokens).
    """
    parts = [
        f"Task: {task}",
        f"Step {step_idx + 1}/{total_steps}.",
    ]
    if website:
        parts.append(f"Website: {website} ({domain})")

    # Top-level candidates first (more likely to be correct)
    top_level = [e for e in elements if e.attributes.get("is_top_level")]
    others = [e for e in elements if not e.attributes.get("is_top_level")]

    parts.append("")
    parts.append("Select the ONE element to interact with next:")
    parts.append("")

    for e in top_level:
        parts.append(_format_candidate(e))
    if top_level and others:
        parts.append("---")
    for e in others:
        parts.append(_format_candidate(e))

    parts.append("")
    parts.append('Return: {"thought": "...", "element_id": "<number>"}')
    return "\n".join(parts)


def _html_describe_action(action) -> str:
    """Short description of an action for history display."""
    atype = action.action_type
    eid = action.element_id or ""
    val = action.value or ""
    labels = {
        "click": f"CLICK #{eid}",
        "type": f"TYPE '{val}' into #{eid}",
        "select": f"SELECT '{val}' from #{eid}",
        "scroll": f"SCROLL {val or 'down'}",
        "goto": f"GOTO {val}",
        "press": f"PRESS {val or 'Enter'}",
        "answer": f"ANSWER: {val}",
        "fail": f"FAIL: {val}",
        "captcha": "CAPTCHA",
        "login": "LOGIN",
    }
    return labels.get(atype.value, f"{atype.value.upper()} #{eid}")


def _html_format_history(rec) -> tuple:
    """Format a history entry with verification-aware status."""
    if not rec.success:
        return ("✗", " (execution error)")
    if rec.verification is None:
        return ("✓", "")
    if rec.verification.effect_achieved:
        return ("✓", " (verified)")
    obs = (rec.verification.observation or "no change")[:60]
    return ("⚠", f" (FAILED: {obs})")


def _get_page_text(elements: list[PageElement]) -> str:
    """Extract a page-level text summary from element texts.

    Joins the visible text of all elements, deduplicates adjacent
    duplicates, and truncates to fit in context window.
    Can be overridden by passing actual page_text from the perceptor.
    """
    texts = []
    seen = set()
    for e in elements:
        t = (e.text or "").strip()
        if t and t not in seen and len(t) > 1:
            seen.add(t)
            texts.append(t)
    return "\n".join(texts[:80])  # limit to ~80 unique text snippets


def _format_candidate(e: PageElement) -> str:
    """Format a single candidate — compact single-line."""
    attrs = e.attributes
    tag = e.tag
    text = e.text[:50].replace("\n", " ") if e.text else ""
    cls = attrs.get("class", "")[:30]
    aria = attrs.get("aria_label", "")[:30]

    parts = [f"[{e.id}] <{tag}>"]
    if text:
        parts.append(f' "{text}"')
    if cls:
        parts.append(f" .{cls}")
    if aria:
        parts.append(f' aria="{aria}"')

    return " ".join(parts)
