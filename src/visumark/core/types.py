"""Core type definitions for VisuMark Agent.

All dataclasses, enums, and type aliases used across the entire project.
Serves as the single source of truth for data structures.
"""

from dataclasses import dataclass, field
from enum import Enum
from time import time


# ============================================================================
# Action Types
# ============================================================================

class ActionType(str, Enum):
    """Action space — superset of Mind2Web operations + browser navigation.

    Maps to Mind2Web paper operations:
        CLICK   → CLICK (also covers HOVER, PRESS_ENTER from original_op)
        TYPE    → TYPE  (requires value)
        SELECT  → SELECT (requires value)
    """

    CLICK = "click"        # Click an element (also hover, press enter)
    TYPE = "type"          # Type text into an input field
    SELECT = "select"      # Select an option from a dropdown
    SCROLL = "scroll"      # Scroll the page up/down
    HOVER = "hover"        # Hover over an element
    PRESS = "press"        # Press a keyboard key (Enter, Tab, Escape)
    GOTO = "goto"          # Navigate to a URL
    WAIT = "wait"          # Wait for a duration
    ANSWER = "answer"      # Task completed — return answer
    FAIL = "fail"          # Task impossible — return reason
    CAPTCHA = "captcha"    # CAPTCHA detected — pause for manual intervention


@dataclass
class Action:
    """A single executable action, either from VLM output or ground truth.

    Attributes:
        action_type: The type of action to perform.
        element_id: SoM label number (e.g. "3") — mapped to DOM via DOMBridge.
        value: Text to type (TYPE) or option to select (SELECT).
        description: Human-readable description for logging and display.
    """

    action_type: ActionType
    element_id: str | None = None
    value: str | None = None
    description: str = ""

    def to_dict(self) -> dict:
        """Serialize for JSON/logging."""
        d: dict = {"action": self.action_type.value}
        if self.element_id is not None:
            d["element_id"] = self.element_id
        if self.value is not None:
            d["value"] = self.value
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def click(cls, element_id: str, description: str = "") -> "Action":
        return cls(ActionType.CLICK, element_id=element_id, description=description)

    @classmethod
    def type_text(cls, element_id: str, text: str) -> "Action":
        return cls(ActionType.TYPE, element_id=element_id, value=text)

    @classmethod
    def select(cls, element_id: str, option: str) -> "Action":
        return cls(ActionType.SELECT, element_id=element_id, value=option)

    @classmethod
    def scroll(cls, direction: str = "down") -> "Action":
        return cls(ActionType.SCROLL, value=direction)

    @classmethod
    def answer(cls, text: str) -> "Action":
        return cls(ActionType.ANSWER, value=text)

    @classmethod
    def fail(cls, reason: str = "") -> "Action":
        return cls(ActionType.FAIL, value=reason)

    @classmethod
    def goto(cls, url: str) -> "Action":
        return cls(ActionType.GOTO, value=url)

    @classmethod
    def press(cls, key: str) -> "Action":
        return cls(ActionType.PRESS, value=key)

    @property
    def is_terminal(self) -> bool:
        """Whether this action ends the task."""
        return self.action_type in (ActionType.ANSWER, ActionType.FAIL)


# ============================================================================
# Page Element
# ============================================================================

@dataclass
class PageElement:
    """An interactive element on a web page.

    Unified representation used by both SoM (visual) and HTML (text) perception modes.

    Attributes:
        id: SoM label number as string ("1", "2", ...).
        tag: HTML tag name (button, input, a, select, etc.).
        text: Visible text, aria-label, or placeholder.
        bbox: Normalized bounding box (x, y, w, h) — each in [0, 1].
        attributes: Key attributes (href, type, role, aria-*, placeholder, etc.).
        backend_node_id: Mind2Web DOM node identifier (for evaluation mapping).
        selector: Playwright CSS/XPath locator (for execution).
    """

    id: str
    tag: str
    text: str
    bbox: tuple[float, float, float, float]  # (x, y, w, h) normalized
    attributes: dict = field(default_factory=dict)
    backend_node_id: str | None = None
    selector: str | None = None


# ============================================================================
# Perception & Reasoning Output
# ============================================================================

@dataclass
class Perception:
    """Result of page perception — what the agent "sees".

    In SoM mode:
        - screenshot: clean screenshot for UI display (user sees the real page)
        - annotated_screenshot: SoM-annotated with bounding boxes + labels for VLM
    In HTML mode: both screenshots are None (text-only reasoning).
    """

    screenshot: bytes | None = None          # Clean screenshot for UI display
    annotated_screenshot: bytes | None = None  # SoM-annotated for VLM (not shown to user)
    elements: list[PageElement] = field(default_factory=list)
    page_title: str = ""
    page_url: str = ""


@dataclass
class ReasonerOutput:
    """Output from VLM/LLM reasoning.

    Attributes:
        raw_text: The raw text response from the model.
        thought: Chain-of-thought reasoning extracted from the response.
        plan: High-level plan for future steps — carries intent forward
            so the model remembers "what I was trying to do" across steps.
        action: Parsed action, or None if parsing failed.
    """

    raw_text: str
    thought: str = ""
    plan: str = ""
    action: Action | None = None


# ============================================================================
# Action Verification
# ============================================================================


@dataclass
class VerificationResult:
    """Post-action verification — did the action achieve its intended effect?

    Generated by a lightweight VLM call that compares the pre-action
    (SoM-annotated) screenshot against a post-action (raw) screenshot.

    Attributes:
        effect_achieved: Whether the page changed as expected.
        observation: What the VLM observed about the page change.
        should_retry: Whether a retry with an alternative action is advised.
        rollback_action: Action to undo/revert before retrying (e.g. press Escape
            to close an unwanted modal, goto to return to previous page).
        retry_action: The suggested alternative action (if should_retry).
    """

    effect_achieved: bool
    observation: str = ""
    should_retry: bool = False
    rollback_action: "Action | None" = None
    retry_action: "Action | None" = None


# ============================================================================
# Step & Task Recording
# ============================================================================

@dataclass
class StepRecord:
    """Record of a single agent step — used for history, logging, and evaluation.

    Attributes:
        step: Step number (1-indexed).
        perception: What the agent saw.
        reasoner_output: What the model responded.
        action: The parsed action (may be None if parsing failed).
        success: Whether the action executed successfully.
        element_correct: (Evaluation) whether the selected element matches ground truth.
        operation_correct: (Evaluation) whether operation type and value match ground truth.
        timestamp: Unix timestamp when the step was recorded.
    """

    step: int
    perception: Perception
    reasoner_output: ReasonerOutput
    action: Action | None
    success: bool
    element_correct: bool | None = None
    operation_correct: bool | None = None
    verification: "VerificationResult | None" = None
    post_screenshot: bytes | None = None
    timestamp: float = field(default_factory=time)

    @property
    def step_success(self) -> bool | None:
        """Step is successful only if both element and operation are correct."""
        if self.element_correct is None or self.operation_correct is None:
            return None
        return self.element_correct and self.operation_correct


@dataclass
class TaskRecord:
    """Outcome of a full task execution.

    Attributes:
        task_id: Unique identifier for the task.
        task_description: Natural language task description.
        success: Whether the task completed successfully (ANSWER action).
        answer: The answer text if task succeeded.
        total_steps: Number of steps executed.
        steps: Detailed step records.
        error: Error message if task failed unexpectedly.
    """

    task_id: str
    task_description: str
    success: bool
    answer: str | None = None
    total_steps: int = 0
    steps: list[StepRecord] = field(default_factory=list)
    error: str | None = None
