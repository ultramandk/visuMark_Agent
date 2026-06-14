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


# ============================================================================
# Build user prompt for evaluation
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
