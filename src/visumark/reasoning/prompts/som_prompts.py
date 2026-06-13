"""SoM (Set-of-Mark) visual prompt templates for VLM reasoning."""

from __future__ import annotations

from typing import TYPE_CHECKING

from visumark.core.types import Perception, StepRecord

if TYPE_CHECKING:
    from visumark.core.types import VerificationResult

# ============================================================================
# Live agent system prompt
# ============================================================================

SYSTEM_PROMPT = """You are a web automation agent. You see screenshots of real web pages where interactive elements are marked with numbered colored bounding boxes (Set-of-Mark / SoM).

Your task: analyze the screenshot and decide the NEXT action to complete the user's goal.

## Response Format

Respond with a JSON object:

```json
{
    "plan": "Brief 1-sentence summary of your overall plan",
    "thought": "Brief analysis of what you see NOW and what needs to happen next",
    "action": "<action_type>",
    "element_id": "<number>",
    "value": "<text>"
}
```

## Available Actions

| Action | Description | Required Fields |
|--------|-------------|----------------|
| click | Click the numbered element | action, element_id |
| type | Type text into the numbered input field | action, element_id, value |
| select | Select an option from the numbered dropdown | action, element_id, value |
| scroll down | Scroll the page down to see more content | action |
| scroll up | Scroll the page up | action |
| goto | Navigate to a new URL | action, value |
| press | Press a keyboard key (Enter, Tab, Escape) | action, value |
| captcha | Pause for human to handle CAPTCHA/login | action, value |
| answer | Task is FULLY COMPLETE - agent STOPS | action, value |
| fail | Task is IMPOSSIBLE - agent STOPS | action, value |

## Important Rules

1. Look at the NUMBERED colored boxes on the screenshot. Reference elements by their number.
2. If the target element is visible with a number, use click/type/select with that number.
3. If you need to find something not visible, use scroll down first.
4. Only use "answer" when the task is fully complete. Include the result in "value".
5. If genuinely stuck, use "fail" and explain why.
6. The "element_id" field must be the NUMBER on the screenshot (e.g. "3").
7. Output ONLY the JSON object - no extra text."""


# ============================================================================
# Evaluation system prompt (for offline Mind2Web snapshots)
# ============================================================================

EVAL_SYSTEM_PROMPT = """You are evaluating a web agent on a benchmark. You see a SCREENSHOT of a pre-rendered HTML page where interactive elements are marked with numbered colored boxes (Set-of-Mark / SoM).

**CRITICAL**: This is a STATIC full-page snapshot - the ENTIRE page is already rendered and visible. You do NOT need to scroll. Simply look at the marked elements and identify which one should be clicked, typed into, or selected to make progress toward the task goal.

## Response Format

Respond with a JSON object:

```json
{
    "thought": "What element needs to be interacted with next and why",
    "action": "<action_type>",
    "element_id": "<number>",
    "value": "<text>"
}
```

## Available Actions

| Action | Description | Required Fields |
|--------|-------------|----------------|
| click | Click the numbered element | action, element_id |
| type | Type text into the numbered input field | action, element_id, value |
| select | Select an option from the numbered dropdown | action, element_id, value |
| press | Press a keyboard key (Enter, Tab, Escape) | action, value |

## Rules
1. The page is fully rendered - NO scrolling is needed. Just pick the correct element.
2. Look carefully at ALL numbered elements. The target element is marked.
3. For TYPE: include the exact text in "value".
4. For SELECT: include the option text in "value".
5. element_id must be the NUMBER shown on the element (e.g. "3").
6. Output ONLY the JSON object."""


# ============================================================================
# Verification prompt
# ============================================================================

VERIFICATION_SYSTEM_PROMPT = """You are an action verification assistant. You compare two screenshots of a web page: one taken BEFORE an action was performed, and one taken AFTER.

Your task: determine if the action had the expected effect."""


def build_verification_user_prompt(
    action_desc: str,
    thought: str,
    task: str,
    page_url: str = "",
) -> str:
    """Build verification prompt comparing before/after screenshots."""
    parts = [
        f"## Task\n{task}",
        f"## Action Performed\n{action_desc}",
        f"## Expected Effect\n{thought}",
        "## Screenshots\n",
        "**BEFORE (SoM annotated):** The first image shows the page with numbered elements before the action.",
        "**AFTER (raw page):** The second image shows the page after the action was executed.",
        "",
        "Did the action achieve its intended effect? Respond with JSON:",
        '{"effect_achieved": true/false, "observation": "what changed or didn\'t change", "should_retry": false}',
    ]
    if page_url:
        parts.insert(2, f"## Current URL\n{page_url}")
    return "\n".join(parts)


def parse_verification_response(raw_text: str) -> "VerificationResult":
    """Parse verification VLM response."""
    import json as _json
    import re as _re
    from visumark.core.types import VerificationResult

    try:
        match = _re.search(r"\{[\s\S]*\}", raw_text)
        if match:
            obj = _json.loads(match.group(0))
            return VerificationResult(
                effect_achieved=obj.get("effect_achieved", True),
                observation=obj.get("observation", ""),
                should_retry=obj.get("should_retry", False),
            )
    except Exception:
        pass
    return VerificationResult(effect_achieved=True, observation="Could not parse verification")


# ============================================================================
# User prompt builder
# ============================================================================

def build_som_user_prompt(
    task: str,
    perception: Perception,
    history: list[StepRecord],
) -> str:
    """Assemble the user prompt with task, page context, and step history."""
    parts = [
        f"## User Task\n{task}",
        f"## Current Page\nTitle: {perception.page_title}\nURL: {perception.page_url}",
    ]

    if history:
        recent = history[-3:]
        lines = ["## Recent Actions"]
        for rec in recent:
            if rec.action:
                act_desc = _describe_action(rec.action)
                status = "[OK]" if rec.success else "[FAIL]"
                lines.append(f"- [{status}] Step {rec.step}: {act_desc}")
        parts.append("\n".join(lines))

    parts.append(
        "## Instruction\n"
        "Look at the marked screenshot above. What is the NEXT action?\n"
        "Return ONLY the JSON object with your thought and action."
    )

    return "\n\n".join(parts)


def _describe_action(action) -> str:
    """Build a short human-readable description of an action."""
    from visumark.core.types import ActionType

    atype = action.action_type
    eid = action.element_id or ""
    val = action.value or ""

    if atype == ActionType.CLICK:
        return f"CLICK #{eid}"
    elif atype == ActionType.TYPE:
        return f"TYPE '{val}' into #{eid}"
    elif atype == ActionType.SELECT:
        return f"SELECT '{val}' from #{eid}"
    elif atype == ActionType.SCROLL:
        return f"SCROLL {val}"
    elif atype == ActionType.GOTO:
        return f"GOTO {val}"
    elif atype == ActionType.PRESS:
        return f"PRESS {val}"
    elif atype == ActionType.ANSWER:
        return f"ANSWER: {val}"
    elif atype == ActionType.FAIL:
        return f"FAIL: {val}"
    elif atype == ActionType.CAPTCHA:
        return f"CAPTCHA: {val}"
    return str(atype.value)
