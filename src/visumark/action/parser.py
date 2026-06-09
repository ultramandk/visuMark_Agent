"""Action Parser — converts VLM/LLM raw text output into structured Action objects.

Supports two parsing strategies (tried in order):
    1. JSON mode — extracts JSON object from response text
    2. Line mode  — matches patterns like "CLICK [5]" or "TYPE [3] 'hello'"
"""

import json
import re
from typing import Any

from loguru import logger

from visumark.core.types import Action, ActionType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_JSON_PATTERN = re.compile(r"\{[\s\S]*\}")

# Action keyword → ActionType mapping
_ACTION_MAP: dict[str, ActionType] = {
    "click": ActionType.CLICK,
    "type": ActionType.TYPE,
    "fill": ActionType.TYPE,       # Alias
    "select": ActionType.SELECT,
    "scroll down": ActionType.SCROLL,
    "scroll up": ActionType.SCROLL,
    "scroll": ActionType.SCROLL,
    "hover": ActionType.HOVER,
    "press": ActionType.PRESS,
    "goto": ActionType.GOTO,
    "wait": ActionType.WAIT,
    "answer": ActionType.ANSWER,
    "fail": ActionType.FAIL,
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class ParseError(ValueError):
    """Raised when the VLM/LLM output cannot be parsed into a valid Action."""


class ActionParser:
    """Parse VLM/LLM text output into a standardized Action object.

    Usage:
        parser = ActionParser()
        action = parser.parse('{"action": "click", "element_id": "3"}')
        action = parser.parse("CLICK [3]")
    """

    def parse(self, raw_text: str) -> Action:
        """Parse raw model output into an Action.

        Tries JSON first, then falls back to line matching.

        Args:
            raw_text: The raw text response from the VLM/LLM.

        Returns:
            A structured Action object.

        Raises:
            ParseError: If no valid action can be extracted.
        """
        text = raw_text.strip()
        if not text:
            raise ParseError("Empty response")

        # Strategy 1: Try JSON
        json_match = _JSON_PATTERN.search(text)
        if json_match:
            try:
                return self._parse_json(json_match.group(0))
            except ParseError:
                logger.debug("JSON parse failed, falling back to line mode")

        # Strategy 2: Line-based pattern matching
        return self._parse_lines(text)

    # ------------------------------------------------------------------
    # JSON parser
    # ------------------------------------------------------------------

    def _parse_json(self, json_str: str) -> Action:
        """Parse a JSON action object.

        Expected format:
            {"action": "click", "element_id": "3"}
            {"action": "type", "element_id": "5", "value": "hello"}
            {"action": "click", "mark": "3"}          # "mark" alias
            {"action": "type", "id": "5", "text": "hello"}  # "id"/"text" aliases
        """
        try:
            obj: dict[str, Any] = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ParseError(f"Invalid JSON: {e}")

        if not isinstance(obj, dict):
            raise ParseError(f"JSON is not an object: {type(obj).__name__}")

        # Resolve action type
        action_name = str(obj.get("action") or obj.get("type") or "").lower().strip()
        action_type = self._resolve_action_type(action_name)

        # Resolve element ID (supports: element_id, mark, id)
        element_id = str(
            obj.get("element_id")
            or obj.get("mark")
            or obj.get("id")
            or ""
        ) or None

        # Resolve value (supports: value, text)
        value = str(
            obj.get("value")
            or obj.get("text")
            or ""
        ) or None

        # Resolve scroll direction
        if action_type == ActionType.SCROLL and not value:
            value = "down"
            if "up" in action_name:
                value = "up"

        return Action(
            action_type=action_type,
            element_id=element_id,
            value=value,
            description=obj.get("description", ""),
        )

    # ------------------------------------------------------------------
    # Line-based parser (fallback)
    # ------------------------------------------------------------------

    def _parse_lines(self, text: str) -> Action:
        """Parse action from line-based text.

        Accepted formats:
            CLICK [5]
            TYPE [3] "search query"
            TYPE [3] search query
            SELECT [7] "option name"
            SCROLL down / SCROLL up
            ANSWER: done
            FAIL: reason
            GOTO https://...
            PRESS Enter
        """
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            lower = line.lower()

            for keyword, atype in _ACTION_MAP.items():
                if not lower.startswith(keyword):
                    continue

                # Extract element ID: [N]
                elem_match = re.search(r"\[(\d+)\]", line)
                element_id = str(elem_match.group(1)) if elem_match else None

                # Extract quoted value: "text"
                value_match = re.search(r'"([^"]*)"', line)
                value = value_match.group(1) if value_match else None

                # Handle scroll direction
                if atype == ActionType.SCROLL:
                    value = value or ("up" if "up" in lower else "down")

                # Handle answer/fail value (everything after colon)
                if atype in (ActionType.ANSWER, ActionType.FAIL) and not value:
                    parts = line.split(":", 1)
                    value = parts[1].strip() if len(parts) > 1 else ""

                # Handle goto value (URL after keyword)
                if atype == ActionType.GOTO and not value:
                    parts = line.split(None, 1)
                    value = parts[1].strip() if len(parts) > 1 else ""

                # Handle press value (key name after keyword)
                if atype == ActionType.PRESS and not value:
                    parts = line.split(None, 1)
                    value = parts[1].strip() if len(parts) > 1 else "Enter"

                description = line[:200]

                return Action(
                    action_type=atype,
                    element_id=element_id,
                    value=value,
                    description=description,
                )

        raise ParseError(f"Cannot parse action from text: {text[:200]}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_action_type(self, name: str) -> ActionType:
        """Resolve an action name string to ActionType.

        Handles aliases: "fill" → TYPE, "scroll down" → SCROLL, etc.
        """
        # Direct match first
        if name in _ACTION_MAP:
            return _ACTION_MAP[name]

        # Check for partial match (scroll down/up both → SCROLL)
        for keyword, atype in _ACTION_MAP.items():
            if keyword in name or name in keyword:
                return atype

        raise ParseError(f"Unknown action type: '{name}'")
