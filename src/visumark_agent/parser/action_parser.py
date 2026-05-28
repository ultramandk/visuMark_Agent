"""Parse VLM text output into structured Action objects."""

import json
import re

from loguru import logger

from visumark_agent.environment.actions import Action, ActionType

_JSON_PATTERN = re.compile(r"\{[\s\S]*\}")
_ACTION_MAP: dict[str, ActionType] = {
    "click": ActionType.CLICK,
    "type": ActionType.TYPE,
    "scroll down": ActionType.SCROLL,
    "scroll up": ActionType.SCROLL,
    "hover": ActionType.HOVER,
    "press": ActionType.PRESS,
    "goto": ActionType.GOTO,
    "wait": ActionType.WAIT,
    "answer": ActionType.ANSWER,
    "fail": ActionType.FAIL,
}


class ParseError(ValueError):
    """Raised when the VLM output cannot be parsed into a valid Action."""


class ActionParser:
    """Convert free-form VLM text into a standardized Action."""

    def parse(self, raw_text: str) -> Action:
        """Try json-first, then fall back to regex line matching."""
        text = raw_text.strip()

        # JSON path
        json_match = _JSON_PATTERN.search(text)
        if json_match:
            try:
                return self._from_json(json_match.group(0))
            except ParseError:
                pass

        # structured line path
        return self._from_lines(text)

    def _from_json(self, json_str: str) -> Action:
        obj = json.loads(json_str)
        action_name = (obj.get("action") or obj.get("type") or "").lower()
        for key, atype in _ACTION_MAP.items():
            if key in action_name:
                return Action(
                    action_type=atype,
                    element_id=obj.get("element_id") or obj.get("id"),
                    value=obj.get("value") or obj.get("text"),
                    x=obj.get("x"),
                    y=obj.get("y"),
                    description=obj.get("description", ""),
                )
        raise ParseError(f"Unknown action in JSON: {obj}")

    def _from_lines(self, text: str) -> Action:
        # Accept formats like:
        #   CLICK [5]
        #   TYPE [3] "search query"
        #   SCROLL down
        #   ANSWER: done

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            lower = line.lower()

            for keyword, atype in _ACTION_MAP.items():
                if lower.startswith(keyword):
                    elem_match = re.search(r"\[(\d+)\]", line)
                    element_id = int(elem_match.group(1)) if elem_match else None

                    value_match = re.search(r'"([^"]*)"', line)
                    value = value_match.group(1) if value_match else None

                    # extract direction for scroll
                    if atype == ActionType.SCROLL:
                        if "up" in lower:
                            value = value or "up"
                        else:
                            value = value or "down"

                    return Action(
                        action_type=atype,
                        element_id=element_id,
                        value=value,
                        description=line,
                    )

        raise ParseError(f"Cannot parse action from text: {text[:200]}")
