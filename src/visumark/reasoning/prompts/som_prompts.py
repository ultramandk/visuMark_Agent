"""SoM (Set-of-Mark) visual prompt templates.

The VLM receives an annotated screenshot with numbered bounding boxes.
It must output a JSON action object referencing element numbers.

Prompt design follows the ReAct pattern from the project proposal:
    Thought: (analyze the page)
    Action: {"action": "click", "element_id": "3"}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from visumark.core.types import Perception, StepRecord

if TYPE_CHECKING:
    from visumark.core.types import VerificationResult

# ---------------------------------------------------------------------------
# System prompt — sets up the agent's role and output format
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a web automation agent. You see screenshots of real web pages where interactive elements are marked with numbered colored bounding boxes (Set-of-Mark / SoM).

Your task: analyze the screenshot and decide the NEXT action to complete the user's goal.

## Response Format

Respond with a JSON object:

```json
{
    "plan": "Brief 1-sentence summary of your overall plan (e.g. '填写收件人、标题、正文，然后发送邮件')",
    "thought": "Brief analysis of what you see NOW and what needs to happen next",
    "action": "<action_type>",
    "element_id": "<number>",
    "value": "<text>"
}
```

The "plan" field should describe your HIGH-LEVEL goal — what you intend to accomplish.  Keep it updated: if the situation changes (error dialog, wrong page), update the plan accordingly.  The plan persists across steps and helps you remember what you were doing.

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
| captcha | Pause for human to handle CAPTCHA/login — task will CONTINUE after | action, value |
| answer | Task is FULLY COMPLETE — provide the final result, agent STOPS | action, value |
| fail | Task is IMPOSSIBLE — explain why, agent STOPS | action, value |

**!! KEY difference between captcha and answer:**
- `captcha` = pause, human helps, agent RESUME → task NOT finished
- `answer` = task DONE, agent STOPS → task IS finished
- DO NOT use "answer" when you hit a CAPTCHA — use "captcha"!

## Important Rules

1. Look at the NUMBERED colored boxes on the screenshot. Reference elements by their number.
2. If the target element is visible with a number, use click/type/select with that number.
3. If you need to find something not visible, use scroll down first.
4. !! TASK COMPLETION — use "answer" IMMEDIATELY when you see clear evidence the task is done:
   - "发送成功" / "已发送" / "邮件已发送" / "投递成功" → answer with "邮件发送成功"
   - "下单成功" / "支付成功" / "提交成功" → answer with the confirmation message
   - Search results showing the requested information → answer with the result
   - Do NOT keep browsing, clicking, or scrolling after the task is complete.
   - Include the success result in the "value" field.
5. If genuinely stuck after trying different approaches, use "fail" and explain why.
6. The "element_id" field must be a STRING matching the number on the screenshot (e.g., "3" not 3).
7. Action names must be lowercase: click, type, select, scroll, goto, press, answer, fail.
8. Output ONLY the JSON object — no markdown fences, no extra text.
9. Use EXACT format: {"action": "click", "element_id": "3"} — DO NOT use shorthand like "click #3".
10. If you see a CAPTCHA, human verification, slider, puzzle, SMS code, QR code scan, or any anti-bot challenge → output {"action": "captcha", "value": "需要人工完成验证"}. Do NOT attempt to solve it yourself.

11. !! CRITICAL — LOGIN PAGES !!
You do NOT have any usernames, passwords, or credentials. You CANNOT log in by yourself.

IMPORTANT — Before calling CAPTCHA, check whether the user is ALREADY logged in:
   - If you can see a user avatar, nickname, "退出/logout" link, inbox, or account dashboard
     → the user IS already logged in. Do NOT call CAPTCHA. Proceed with the task normally.
   - A "登录/sign in" link in the website header/nav does NOT mean the page requires login.
     Many sites show a login link even to logged-out users as normal navigation chrome.
   - Only call CAPTCHA when the page is ACTIVELY blocking you with a login wall:
     a) The page has a visible username AND password input that you must fill to proceed
     b) A QR code login prompt is the ONLY way to access the site
     c) A phone/SMS verification is required before you can continue
     d) The page explicitly says "请先登录" / "Please sign in to continue"

If none of (a)-(d) apply, do NOT call CAPTCHA. Try to find the information or complete the
task using the visible page content.

When login IS genuinely required → output {"action": "captcha", "value": "需要人工登录"}.
Do NOT click any login buttons. Do NOT try to fill in credentials. Do NOT attempt to navigate away. Just STOP and let the human handle it.

## CORRECT examples

{"plan": "搜索中山大学南校园的位置", "thought": "需要输入查询词", "action": "type", "element_id": "3", "value": "中山大学广州校区南校园在哪里"}
{"plan": "填写收件人地址后发送邮件", "thought": "需要先点击收件人输入框", "action": "click", "element_id": "5"}
{"plan": "关闭错误提示后修正收件人格式", "thought": "弹窗提示格式错误，先关闭", "action": "click", "element_id": "12"}
{"plan": "发送端午祝福邮件", "thought": "页面显示发送成功，任务完成", "action": "answer", "value": "邮件已成功发送"}

## WRONG examples (NEVER do this)

click #5              ← not JSON!
PRESS_ENTER            ← wrong action name
{"action": "click", "element_id": 5}   ← element_id must be string "5"

## Stuck / Failure Recovery

The Recent Actions section shows verification status for each past step:
- [✓] = action executed AND verified successful
- [⚠] = action executed but verification FAILED (page didn't change as expected)
- [✗] = action could not be executed at all

If you see steps marked ⚠ or ✗:
- DO NOT repeat the SAME action on the SAME element. It clearly isn't working.
- The ⚠ steps include a brief observation of what went wrong — use this to guide your next attempt.
- Try a DIFFERENT approach: a different element, scroll first, try pressing Enter after typing, or use goto to navigate directly.
- If an input field keeps failing, try clicking it first, then use press Enter/Tab to activate it.
- After 2-3 consecutive ⚠/✗ failures, consider using scroll down/up to reveal hidden elements, or use "answer" / "fail" if truly stuck."""


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

    # Include most recent plan for continuity
    if history:
        last_plan = None
        for rec in reversed(history):
            if rec.reasoner_output and rec.reasoner_output.plan:
                last_plan = rec.reasoner_output.plan
                break
        if last_plan:
            parts.append(f"## Current Plan\n{last_plan}")

    # Include recent step history for context (last 5 steps)
    if history:
        recent = history[-5:]
        lines = ["## Recent Actions (with verification results)"]
        exec_fail_count = 0
        verify_fail_count = 0
        for rec in recent:
            if rec.action:
                act_desc = _describe_action(rec.action)
                status, detail = _format_history_entry(rec)
                lines.append(f"- {status} Step {rec.step}: {act_desc}{detail}")
                if not rec.success:
                    exec_fail_count += 1
                if rec.verification is not None and not rec.verification.effect_achieved:
                    verify_fail_count += 1
        parts.append("\n".join(lines))

        # Legend
        parts.append(
            "\nStatus legend: [✓ verified] = action worked | "
            "[⚠ unverified] = action didn't produce expected change | "
            "[✗ error] = action could not be executed"
        )

        # Emphasize when previous actions failed
        if verify_fail_count >= 2 or exec_fail_count >= 2:
            parts.append(
                "\n⚠️  MULTIPLE RECENT ACTIONS FAILED. Do NOT repeat them. "
                "Try a completely different approach — scroll to find other elements, "
                "use press Enter to submit, or goto a different URL."
            )
        elif verify_fail_count == 1:
            parts.append(
                "\n⚠️  Your last action did NOT produce the expected change. "
                "Try a different element or approach."
            )

    parts.append(
        "## Instruction\n"
        "Look at the marked screenshot above. What is the NEXT action?\n"
        "If the Current Plan above still applies, keep it. If the situation has changed (errors, wrong page), update the plan to reflect your new goal.\n"
        "Return ONLY the JSON object with your plan, thought, and action."
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


def _format_history_entry(rec: StepRecord) -> tuple[str, str]:
    """Format a history entry with verification-aware status and detail.

    Returns:
        (status_marker, detail_suffix) tuple.
        Status markers: ✓ = verified OK, ⚠ = verification failed, ✗ = execution failed
    """
    if not rec.success:
        # Execution failed outright
        return "✗", " (execution error)"

    if rec.verification is None:
        # Executed OK but not verified (scroll, terminal, or verification disabled)
        return "✓", ""

    if rec.verification.effect_achieved:
        return "✓", " (verified)"

    # Verification failed — show why
    obs = rec.verification.observation[:80] if rec.verification.observation else "no change detected"
    return "⚠", f" (FAILED: {obs})"


# ---------------------------------------------------------------------------
# Action verification prompt — post-action before/after comparison
# ---------------------------------------------------------------------------

VERIFICATION_SYSTEM_PROMPT = """You verify whether a web automation action achieved its intended effect. You see two screenshots:

- **BEFORE** (top): The page before the action, with interactive elements marked by numbered boxes.
- **AFTER** (bottom): The page after the action was executed.

Your job: determine whether the action produced the EXPECTED change, and if not, suggest how to recover.

## !! CRITICAL: Action Format Rules !!

rollback_action and retry_action MUST be JSON objects, NEVER strings or shorthand. You MUST use ONLY these action types:

| action | Required fields | Example |
|--------|----------------|---------|
| click | action, element_id | {"action": "click", "element_id": "5"} |
| type | action, element_id, value | {"action": "type", "element_id": "3", "value": "hello"} |
| press | action, value | {"action": "press", "value": "Enter"} |
| scroll | action, value | {"action": "scroll", "value": "down"} |
| goto | action, value | {"action": "goto", "value": "https://cn.bing.com"} |
| select | action, element_id, value | {"action": "select", "element_id": "7", "value": "option1"} |

- element_id MUST be a string (e.g., "5" not 5), matching the number on the BEFORE screenshot.
- value for press MUST be one of: Enter, Escape, Tab, Backspace, Space.
- value for scroll MUST be exactly "down" or "up".
- action name MUST be one of: click, type, press, scroll, goto, select.
- DO NOT invent action names like "PRESS_ENTER", "click_element", "scroll_down". Use the EXACT names above.
- DO NOT use string shorthand like "click #5" or "press Enter". Always use the JSON object format.

## CORRECT examples:

{"action": "click", "element_id": "5"}
{"action": "press", "value": "Enter"}
{"action": "goto", "value": "https://cn.bing.com"}

## WRONG examples (NEVER do this):

"click #5"          ← string, not object!
"PRESS_ENTER"       ← wrong action name, use "press" with value "Enter"
{"action": "scroll_down", ...}  ← wrong, use "scroll" with value "down"

## Response Format

Respond with a JSON object ONLY (no markdown fences, no extra text):
{
    "effect_achieved": true,
    "observation": "Brief description of what changed or why it didn't",
    "should_retry": false,
    "rollback_action": null,
    "retry_action": null
}

## Rules

1. !! CRITICAL: If BEFORE and AFTER screenshots are IDENTICAL (same layout, same text, same elements) → the action had NO EFFECT.  MUST set effect_achieved: false.  Do NOT rationalize it as "the click registered" or "the scroll was smooth" — if you can't see a visible difference, the action FAILED.
2. If the page changed in the expected way (new content, navigation, modal opened, typed text appeared) → effect_achieved: true.
3. If the page shows an error, blank screen, or stuck loading → effect_achieved: false.
4. When effect_achieved is false, think about whether the action caused a WRONG state:
   - Navigated to wrong page → rollback_action: goto back to the original URL
   - Opened an unwanted modal → rollback_action: press Escape
   - Page state is fine but action didn't register → rollback_action: null
5. Only set should_retry: true when you can suggest a SPECIFIC alternative.
6. Be CONSERVATIVE — only flag as failed when the action clearly didn't work."""


def build_verification_user_prompt(
    action_desc: str,
    thought: str,
    task: str,
    page_url: str = "",
) -> str:
    """Build the user prompt for action verification.

    Args:
        action_desc: Human-readable description of the executed action.
        thought: The VLM's reasoning when choosing this action.
        task: The overall task description.
        page_url: The URL before the action (so VLM can suggest goto for rollback).

    Returns:
        Formatted user prompt string.
    """
    url_line = f"\nPage URL (before action): {page_url}" if page_url else ""
    return f"""## Task
{task}
{url_line}
## Action Performed
{action_desc}

## Reasoning
{thought or "(no reasoning provided)"}

## Question
Compare the BEFORE (top) and AFTER (bottom) screenshots.
Did the action achieve the intended effect?
If it went to a wrong page, suggest goto back to the original URL as rollback."""


def parse_verification_response(raw_text: str) -> VerificationResult:
    """Parse the VLM verification response into a VerificationResult.

    Gracefully handles malformed JSON — defaults to assuming success
    (don't block progress on a verification failure).
    """
    import json as _json
    import re as _re

    from loguru import logger

    from visumark.core.types import Action, ActionType, VerificationResult

    def _normalize_verification_action(action_name: str, ra: dict) -> tuple[ActionType, str | None, str | None]:
        """Normalize a verification action name into (ActionType, element_id, value).

        Handles VLM-variant names like PRESS_ENTER, PRESS_ESCAPE, CLICK_ELEMENT.
        """
        name = action_name.lower().strip()
        eid = str(ra.get("element_id", "")) if ra.get("element_id") else None
        val = ra.get("value")

        # PRESS_ENTER / PRESS_ESCAPE / PRESS_TAB → ActionType.PRESS
        if name.startswith("press_"):
            key = name[len("press_"):]
            key_map = {"enter": "Enter", "escape": "Escape", "tab": "Tab", "space": "Space"}
            return ActionType.PRESS, None, val or key_map.get(key, key.title())

        # Direct ActionType match
        try:
            return ActionType(name), eid, val
        except ValueError:
            pass

        # Heuristic matching
        name_to_type = {
            "click": ActionType.CLICK, "type": ActionType.TYPE, "fill": ActionType.TYPE,
            "select": ActionType.SELECT, "scroll": ActionType.SCROLL,
            "scroll_down": ActionType.SCROLL, "scroll_up": ActionType.SCROLL,
            "hover": ActionType.HOVER, "press": ActionType.PRESS,
            "goto": ActionType.GOTO, "wait": ActionType.WAIT,
            "answer": ActionType.ANSWER, "fail": ActionType.FAIL,
        }
        for key, atype in name_to_type.items():
            if key in name:
                if atype == ActionType.SCROLL:
                    return atype, eid, val or ("up" if "up" in name else "down")
                return atype, eid, val

        # Default: treat as click (safest fallback)
        logger.warning(f"Unknown verification action type '{action_name}', defaulting to CLICK")
        return ActionType.CLICK, eid, val

    def _parse_optional_action(key: str) -> Action | None:
        if not obj.get(key):
            return None
        ra = obj[key]

        # Guard: VLM may return action as a string like "click #5" or "press Enter"
        if isinstance(ra, str):
            logger.debug(f"Verification returned string action '{ra}' for '{key}', parsing...")
            try:
                from visumark.action.parser import ActionParser
                return ActionParser().parse(ra)
            except Exception:
                logger.warning(f"Could not parse string action '{ra}' for '{key}'")
                return None

        # Guard: skip non-dict values
        if not isinstance(ra, dict):
            logger.warning(f"Verification {key} is not a dict or string: {type(ra).__name__}")
            return None

        action_name = str(ra.get("action", "click"))
        atype, eid, val = _normalize_verification_action(action_name, ra)
        return Action(action_type=atype, element_id=eid, value=val)

    # Strategy 1: Try to extract JSON from response (handles ```json fences, stray text)
    try:
        # Strip markdown code fences first
        cleaned = _re.sub(r"```(?:json)?\s*", "", raw_text)
        cleaned = _re.sub(r"```", "", cleaned)

        json_match = _re.search(r"\{[\s\S]*\}", cleaned)
        if json_match:
            obj = _json.loads(json_match.group(0))
            return VerificationResult(
                effect_achieved=obj.get("effect_achieved", True),
                observation=obj.get("observation", ""),
                should_retry=obj.get("should_retry", False),
                rollback_action=_parse_optional_action("rollback_action"),
                retry_action=_parse_optional_action("retry_action"),
            )
    except Exception as exc:
        logger.debug(f"JSON parse attempt failed: {exc}")

    # Strategy 2: Heuristic — look for keywords in plain text
    lowered = raw_text.lower().strip()
    if any(w in lowered for w in ("effect_achieved", '"effect_achieved"')):
        # Has JSON-like keys but couldn't parse — likely truncated
        logger.warning(
            f"Verification response looks like JSON but couldn't parse — "
            f"possibly truncated. Raw: {raw_text[:300]}"
        )
    elif lowered:
        # Plain text response — try simple interpretation
        positive_words = ("yes", "success", "worked", "changed", "achieved", "true", "正确", "成功")
        negative_words = ("no", "fail", "unchanged", "identical", "false", "didn't", "错误", "失败", "没变")

        is_positive = any(w in lowered for w in positive_words)
        is_negative = any(w in lowered for w in negative_words)

        if is_negative and not is_positive:
            return VerificationResult(
                effect_achieved=False,
                observation=raw_text[:200],
                should_retry=False,
            )
        elif is_positive:
            return VerificationResult(
                effect_achieved=True,
                observation=raw_text[:200],
            )

    logger.warning(
        f"Could not parse verification response — assuming success. "
        f"Raw ({len(raw_text)} chars): {raw_text[:250]}"
    )

    return VerificationResult(
        effect_achieved=True,
        observation=f"Unparseable response (assuming success): {raw_text[:200]}",
    )
