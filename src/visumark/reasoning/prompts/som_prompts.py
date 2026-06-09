"""SoM (Set-of-Mark) visual prompt templates.

The VLM receives an annotated screenshot with numbered bounding boxes.
It must output a JSON action object referencing element numbers.

Prompt design follows the ReAct pattern from the project proposal:
    Thought: (analyze the page)
    Action: {"action": "click", "element_id": "3"}
"""

from visumark.core.types import Perception, StepRecord

# ---------------------------------------------------------------------------
# System prompt — sets up the agent's role and output format
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a web automation agent. You see screenshots of real web pages where interactive elements are marked with numbered colored bounding boxes (Set-of-Mark / SoM).

Your task: analyze the screenshot and decide the NEXT action to complete the user's goal.

## Response Format

Respond with a JSON object. Include your reasoning in the "thought" field:

```json
{
    "thought": "Brief analysis of what you see and what needs to happen next",
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
| answer | Task is complete — provide the result | action, value |
| fail | Task cannot be completed — explain why | action, value |

## Important Rules

1. Look at the NUMBERED colored boxes on the screenshot. Reference elements by their number.
2. If the target element is visible with a number, use click/type/select with that number.
3. If you need to find something not visible, use scroll down first.
4. Only use "answer" when the task is fully complete. Include the result in "value".
5. If genuinely stuck, use "fail" and explain why.
6. The "element_id" field must be a STRING matching the number on the screenshot.
7. Output ONLY the JSON object — no extra text."""


# ---------------------------------------------------------------------------
# User prompt builder — assembles task + page context + history
# ---------------------------------------------------------------------------

def build_som_user_prompt(
    task: str,
    perception: Perception,
    history: list[StepRecord],
) -> str:
    """Assemble the user prompt with task, page context, and step history.

    Args:
        task: Natural language task description.
        perception: Current page perception.
        history: Previous step records (context/memory).

    Returns:
        Formatted prompt string.
    """
    parts = [
        f"## User Task\n{task}",
        f"## Current Page\nTitle: {perception.page_title}\nURL: {perception.page_url}",
    ]

    # Include recent step history for context (last 3 steps)
    if history:
        recent = history[-3:]
        lines = ["## Recent Actions"]
        for rec in recent:
            if rec.action:
                act_desc = _describe_action(rec.action)
                status = "✓" if rec.success else "✗"
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
    return str(atype.value)
