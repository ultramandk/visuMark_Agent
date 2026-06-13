"""Action Executor — transforms parsed Actions into browser operations.

Bridges the gap between VLM decisions (SoM numbers) and Playwright commands
(CSS selectors) via the DOMBridge.
"""

from loguru import logger

from visumark.core.types import Action, ActionType
from visumark.environment.base import BaseEnvironment
from visumark.perception.dom_bridge import DOMBridge


class ActionExecutor:
    """Execute actions in the browser environment.

    Uses DOMBridge to resolve SoM element IDs to Playwright CSS selectors.
    This is the ONLY path from VLM output to browser operation — ensuring
    the SoM number in the screenshot corresponds exactly to the DOM element.
    """

    async def execute(
        self,
        action: Action,
        env: BaseEnvironment,
        bridge: DOMBridge | None = None,
    ) -> bool:
        """Execute a single action.

        Args:
            action: The parsed action from VLM output.
            env: The browser environment.
            bridge: DOMBridge for SoM ID → selector resolution.
                    If None, falls back to data-som-id attribute selectors.

        Returns:
            True if execution succeeded, False otherwise.
        """
        if action.action_type in (ActionType.ANSWER, ActionType.FAIL, ActionType.CAPTCHA):
            return True  # Terminal — no browser operation

        # Resolve element selector via DOMBridge (preferred) or data-som-id fallback
        selector = None
        if action.element_id is not None:
            if bridge:
                selector = bridge.som_id_to_selector(action.element_id)
            if not selector:
                # Fallback: data-som-id attribute (injected during perception)
                selector = f"[data-som-id='{action.element_id}']"

        try:
            return await env.execute(action, bridge)
        except Exception as exc:
            logger.error(
                f"Execute failed: {action.to_dict()} — {exc}"
            )
            return False


def build_action_description(action: Action) -> str:
    """Build a human-readable action description without bridge dependency.

    Used for verification prompts where DOMBridge may not be available.
    """
    atype = action.action_type
    eid = f" #{action.element_id}" if action.element_id else ""
    val = f" '{action.value}'" if action.value else ""

    labels = {
        ActionType.CLICK: f"CLICK{eid}",
        ActionType.TYPE: f"TYPE{val} into{eid}",
        ActionType.SELECT: f"SELECT{val} from{eid}",
        ActionType.SCROLL: f"SCROLL {action.value or 'down'}",
        ActionType.HOVER: f"HOVER{eid}",
        ActionType.PRESS: f"PRESS {action.value or 'Enter'}",
        ActionType.GOTO: f"GOTO {action.value or ''}",
        ActionType.WAIT: f"WAIT {action.value or '1000'}ms",
        ActionType.ANSWER: f"ANSWER: {action.value or ''}",
        ActionType.FAIL: f"FAIL: {action.value or ''}",
    }
    return labels.get(atype, f"{atype.value.upper()}{eid}{val}")


def build_target_label(action: Action, bridge: DOMBridge | None = None) -> str:
    """Build a human-readable label for display in the UI.

    Examples:
        "CLICK #5 [button] Search"
        "TYPE 'hello' → #3 [input] email"
        "SCROLL down"
    """
    atype = action.action_type

    if atype in (ActionType.ANSWER, ActionType.FAIL):
        return f"{atype.value.upper()}: {action.value or ''}"

    if atype == ActionType.SCROLL:
        return f"SCROLL {action.value or 'down'}"

    if atype == ActionType.GOTO:
        return f"GOTO {action.value or ''}"

    if atype == ActionType.PRESS:
        return f"PRESS {action.value or ''}"

    # Element-based actions
    elem_label = ""
    if action.element_id and bridge:
        elem_label = bridge.get_element_label(action.element_id)

    if atype == ActionType.CLICK:
        return f"CLICK {elem_label}"
    elif atype == ActionType.TYPE:
        return f"TYPE '{action.value or ''}' → {elem_label}"
    elif atype == ActionType.SELECT:
        return f"SELECT '{action.value or ''}' → {elem_label}"
    elif atype == ActionType.HOVER:
        return f"HOVER {elem_label}"

    return f"{atype.value.upper()} #{action.element_id or '?'}"
