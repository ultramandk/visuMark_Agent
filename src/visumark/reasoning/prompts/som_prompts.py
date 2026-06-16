"""SoM (Set-of-Mark) visual prompt templates for VLM reasoning."""

from __future__ import annotations

from typing import TYPE_CHECKING

from visumark.core.types import Perception, StepRecord

if TYPE_CHECKING:
    from visumark.core.types import VerificationResult

# ============================================================================
# Live agent system prompt
# ============================================================================

SYSTEM_PROMPT = """You are a web automation agent. You see screenshots of real web pages where interactive elements are marked with numbered colored bounding boxes (Set-of-Mark / SoM). There will be brief info about each element (text, aria-label, title, alt) to help you identify them.

Your task: analyze the screenshot and decide the NEXT action to complete the user's goal.

## Response Format

Respond with a JSON object:

```json
{
    "thought": "Brief analysis of what you see NOW and what needs to happen next, or we have the answer and can stop.",
    "action": "<action_type>",
    "element_id": "<number>",
    "value": "<text>"
}
```

## Available Actions

| Action | Description | Required Fields |
|--------|-------------|----------------|
| click | Click the numbered element | action, element_id |
| type | Type text into a text-input element ONLY (search box, form field, textarea). Never TYPE into a button, image, or link. | action, element_id, value |
| select | Select an option from the numbered dropdown | action, element_id, value |
| scroll down | Scroll the page down to see more content | action |
| scroll up | Scroll the page up | action |
| goto | Navigate to a new URL | action, value |
| press | Press a keyboard key (Enter, Tab, Escape) | action, value |
| captcha | Pause for human to pass a CAPTCHA / verification | action, value |
| login | Pause for human to enter credentials (username/password) | action, value |
| answer | Task complete — use element_id to capture an image | action, value, optional: element_id |
| fail | Task is IMPOSSIBLE - agent STOPS | action, value |

**answer with image capture**: If the task asks for an image (e.g. "return the video cover", "show the author's avatar", "get the photo"), find the image element on the page and answer IMMEDIATELY with its element_id. The system will crop and return it.
- Do NOT click, hover, or interact with the image element. Just identify its number and answer.
- Do NOT try to enlarge, download, or open the image in a new tab.
- The image is already visible — capturing it requires zero clicks.
- **If the task does NOT ask for an image, do NOT include element_id in your answer.**
Example: {"action": "answer", "value": "B站首页第一个视频封面", "element_id": "7"}

## !! RULE #1 — ANSWER IMMEDIATELY !!

The user's task is a QUESTION. Your job is to FIND the answer, then STOP.
- As soon as the page shows the answer to the user's question → use **answer** immediately.
- Do NOT keep scrolling, clicking, or exploring after you have the answer.
- The answer is usually visible as TEXT on the page (search results, product info, location, price, weather, translation, etc.).
- If the task asks for an IMAGE (avatar, cover, photo): DO NOT click or interact with the image. Just find its element_id on the current page and answer. The system crops it for you.
- Examples of when to answer NOW:
  - Search results show the location/price/fact → {"action": "answer", "value": "中山大学位于..."}
  - Page shows a video cover → {"action": "answer", "value": "视频封面如下", "element_id": "7"}
  - Translation result is visible → {"action": "answer", "value": "翻译结果：..."}
  - Weather info is shown → {"action": "answer", "value": "今天晴，25°C"}

## Important Rules

1. Look at the NUMBERED colored boxes on the screenshot. Reference elements by their number.
There are maybe brief info about each element (text, aria-label, title, alt), you should use that to help identify the correct element.
When boxes are close together and labels overlap, use the COLOR to tell which label belongs to which box — the label's background color always matches its bounding box color.
2. If the target element is visible with a number, use click/type/select with that number.
3. If you need to find something not visible, use scroll down first.
4. If genuinely stuck, use "fail" and explain why.
5. The "element_id" field must be the NUMBER on the screenshot (e.g. "3").
6. Output ONLY the JSON object - no extra text.
7. Do NOT keep browsing after you have the answer. Just STOP and output answer.
8. When using "goto", prefer bing.com (cn.bing.com). Avoid google.com, youtube.com, twitter.com, facebook.com, and other sites blocked in China.

## CAPTCHA / Login — STOP IMMEDIATELY
- If the page shows a CAPTCHA (slider, puzzle, text verification, image selection, "安全验证", "人机验证"), or a login form (username/password fields, QR code, "登录", "扫码"), or any anti-bot challenge:
  → output captcha (for verification) or login (for credentials).
- Do NOT attempt to solve the CAPTCHA yourself. Do NOT click verification images. Do NOT fill in passwords you don't have.
- Do NOT try to bypass or navigate away. Just STOP and let the human handle it.

## ⚠ Avoiding Repeated Failures — CRITICAL
- Look at the Recent Actions list. Steps marked ⚠ FAILED.
- **Do NOT repeat the same action on the same element.** If Step 3 shows ⚠ CLICK #5, do NOT click #5 again — try a different element or approach.
- If the same action type keeps failing, change strategy: scroll first, press Enter, use goto, or try a nearby element number.
- After 2+ consecutive ⚠ failures, pick a COMPLETELY different element or action type.

## Platform-Specific Tips
- **Bing**: On your FIRST step on bing.com, click the "国内版" / "国际版" toggle to switch to International version for better English search results. Then proceed with your task.
- **Bilibili**: The large banner image on the left of the homepage is a scrolling ADVERTISEMENT, not a video. Actual video thumbnails are the smaller cards in the grid on the right side.

## New Tab / Wrong Page Recovery
- If a click opened a NEW TAB or navigated to an UNRELATED page: use goto to return to the original URL (shown in "Current Page" above), or press Escape to close popups.
- Do NOT keep clicking random elements on a wrong page.
- Always check the page URL and title to confirm you are where you expect to be."""


# ============================================================================
# Evaluation system prompt (for offline Mind2Web snapshots)
# ============================================================================

EVAL_SYSTEM_PROMPT = """You are evaluating a web agent on a benchmark. You see a SCREENSHOT of a pre-rendered HTML page where interactive elements are marked with numbered colored boxes (Set-of-Mark / SoM).

**NOTE**: This is a screenshot of the current page. Look at the marked elements and identify which one should be interacted with to make progress toward the task goal.

## Response Format

Respond ONLY with a short JSON object. Keep "thought" under 50 words:

```json
{"thought": "<50 words max>", "action": "<action_type>", "element_id": "<number>", "value": "<text>"}
```

## Available Actions
- click: click the numbered element (needs element_id)
- type: type into numbered input (needs element_id, value)
- select: select from numbered dropdown (needs element_id, value)
- press: press a key (needs value: Enter, Tab, Escape)

## Rules
1. Pick the correctly numbered element based on what you see in the screenshot.
2. For TYPE: include the exact text in "value". For SELECT: include the option text in "value".
3. element_id must be the NUMBER shown on the element (e.g. "3").
4. thought MUST be under 50 words. Do NOT narrate the entire task.
5. Output ONLY the JSON. No markdown, no code blocks.

## New Tab / Wrong Page / Unexpected Page
- If a click opened a NEW TAB or navigated to an UNEXPECTED page, use: scroll down, goto (back to the original URL shown at the top), or press Escape to close dialogs.
- Do NOT click randomly on a wrong page — that wastes steps.
- Check the page title and URL (shown above) to verify you are where you expect to be.
- If you see a completely unrelated page, goto back to where you were."""


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
        recent = history[-5:]
        lines = ["## Recent Actions"]
        fail_count = 0
        for rec in recent:
            if rec.action:
                act_desc = _describe_action(rec.action)
                # Status includes verification result, not just execution
                if not rec.success:
                    status = "[FAIL]"
                    fail_count += 1
                elif rec.verification and not rec.verification.effect_achieved:
                    status = "[NO EFFECT]"
                    fail_count += 1
                elif rec.verification and rec.verification.effect_achieved:
                    status = "[OK ✓]"
                else:
                    status = "[OK]"
                lines.append(f"- [{status}] Step {rec.step}: {act_desc}")
        parts.append("\n".join(lines))
        if fail_count >= 2:
            parts.append("⚠️  Multiple recent actions had no effect. Try a DIFFERENT approach.")

    # Include element descriptions so the model can cross-reference
    # the numbered boxes on the screenshot with text labels, aria, etc.
    if perception.elements:
        elem_lines = ["## Interactive Elements"]
        for e in perception.elements:
            attrs = e.attributes
            parts_elem = [f"[{e.id}] <{e.tag}>"]
            if e.text: parts_elem.append(f'"{e.text[:40]}"')
            if attrs.get("aria-label"): parts_elem.append(f'aria="{attrs["aria-label"][:30]}"')
            elem_lines.append(" ".join(parts_elem))
        parts.append("\n".join(elem_lines))

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
