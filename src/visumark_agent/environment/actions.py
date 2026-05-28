"""Action type definitions for web interaction."""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    HOVER = "hover"
    PRESS = "press"          # key press (Enter, Tab, etc.)
    GOTO = "goto"            # navigate to URL
    WAIT = "wait"
    ANSWER = "answer"        # task complete — return answer
    FAIL = "fail"            # task impossible


@dataclass
class Action:
    """A single executable action derived from VLM output."""

    action_type: ActionType
    element_id: int | None = None    # SoM label id
    value: str | None = None         # text to type / URL / key name
    x: float | None = None           # normalized x coord (0–1)
    y: float | None = None           # normalized y coord (0–1)
    description: str = ""

    @classmethod
    def click(cls, element_id: int, description: str = "") -> "Action":
        return cls(ActionType.CLICK, element_id=element_id, description=description)

    @classmethod
    def type_text(cls, element_id: int, text: str) -> "Action":
        return cls(ActionType.TYPE, element_id=element_id, value=text)

    @classmethod
    def scroll(cls, direction: str = "down") -> "Action":
        return cls(ActionType.SCROLL, value=direction)

    @classmethod
    def answer(cls, answer_text: str) -> "Action":
        return cls(ActionType.ANSWER, value=answer_text)

    @classmethod
    def fail(cls, reason: str = "") -> "Action":
        return cls(ActionType.FAIL, value=reason)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the action for logging and replay."""
        return {
            "action": self.action_type.value,
            "element_id": self.element_id,
            "value": self.value,
            "x": self.x,
            "y": self.y,
        }
