"""Mind2Web Comparator — compares VLM predictions against ground truth.

Core algorithm:
    1. Resolve VLM's SoM number → backend_node_id via DOMBridge
    2. Check if backend_node_id is in the pos_candidates set
    3. Compare operation type and value (TYPE/SELECT use token-level F1)
    4. Step is successful only if BOTH element AND operation are correct
"""

from dataclasses import dataclass

from loguru import logger

from visumark.core.types import Action, ActionType
from visumark.perception.dom_bridge import DOMBridge


@dataclass
class StepComparison:
    """Result of comparing one predicted step against ground truth.

    element_correct is tri-state:
        True  — VLM picked an element in pos_candidates
        False — VLM picked an element NOT in pos_candidates
        None  — pos_candidates is empty (ground truth missing); N/A for this metric

    token_f1 is the continuous token-level F1 score for the operation value:
        - CLICK: 1.0 if op type matches, 0.0 otherwise
        - TYPE/SELECT: actual token F1 (0.0–1.0)
    """
    step: int
    element_correct: bool | None  # tri-state: True / False / None (N/A)
    operation_correct: bool
    step_success: bool | None  # None when element_correct is None (N/A)
    token_f1: float = 0.0
    predicted_node: str | None = None
    acceptable_nodes: set = None
    details: str = ""

    def __post_init__(self):
        if self.acceptable_nodes is None:
            self.acceptable_nodes = set()


class Mind2WebComparator:
    """Compare VLM predictions vs Mind2Web ground truth.

    Usage:
        comparator = Mind2WebComparator()
        cmp = comparator.compare_step(
            predicted_action=Action(CLICK, element_id="3"),
            gt_action={"operation": {"op": "CLICK"}, "pos_candidates": [...]},
            bridge=dom_bridge,
        )
    """

    # ------------------------------------------------------------------
    # Main comparison method
    # ------------------------------------------------------------------

    def compare_step(
        self,
        predicted_action: Action,
        gt_action: dict,
        bridge: DOMBridge,
        step: int = 0,
    ) -> StepComparison:
        """Compare a single predicted action against ground truth.

        Args:
            predicted_action: Action from VLM output.
            gt_action: Ground truth action dict from Mind2Web:
                {
                    "operation": {"op": "CLICK|TYPE|SELECT", "value": "..."},
                    "pos_candidates": [
                        {"backend_node_id": "node-42", "tag": "button", ...},
                        ...
                    ]
                }
            bridge: DOMBridge mapping SoM IDs to backend_node_ids.
            step: Step number (for display).

        Returns:
            StepComparison with element_correct, operation_correct, step_success.
        """
        # --- Element Accuracy ---
        acceptable = self._get_acceptable_nodes(gt_action)

        if not acceptable:
            # Ground truth has no pos_candidates — skip Element Accuracy for this step
            element_correct = None
            predicted_node = None
        else:
            predicted_node = bridge.som_id_to_backend_node(predicted_action.element_id)
            element_correct = predicted_node in acceptable if predicted_node else False

        # --- Operation Correctness ---
        gt_op = gt_action["operation"]["op"]  # "CLICK" | "TYPE" | "SELECT"
        gt_value = gt_action["operation"].get("value")
        token_f1 = self._check_operation(predicted_action, gt_op, gt_value)
        operation_correct = token_f1 >= 0.5  # Mind2Web paper threshold

        # --- Step Success ---
        if element_correct is None:
            step_success = None   # N/A — can't determine without pos_candidates
        else:
            step_success = element_correct and operation_correct

        details = self._format_details(
            predicted_action, predicted_node, acceptable,
            element_correct, operation_correct, token_f1,
        )

        return StepComparison(
            step=step,
            element_correct=element_correct,
            operation_correct=operation_correct,
            step_success=step_success,
            token_f1=token_f1,
            predicted_node=predicted_node,
            acceptable_nodes=acceptable,
            details=details,
        )

    # ------------------------------------------------------------------
    # Element accuracy
    # ------------------------------------------------------------------

    def _get_acceptable_nodes(self, gt_action: dict) -> set[str]:
        """Extract the set of acceptable backend_node_ids from ground truth.

        Mind2Web labels multiple equivalent elements (e.g., a button and its
        inner span both trigger the same click). Any of them is "correct".
        """
        candidates = gt_action.get("pos_candidates", [])
        return {c["backend_node_id"] for c in candidates if c.get("backend_node_id")}

    # ------------------------------------------------------------------
    # Operation correctness
    # ------------------------------------------------------------------

    def _check_operation(
        self,
        predicted: Action,
        gt_op: str,
        gt_value: str | None,
    ) -> float:
        """Compute token-level F1 for the predicted operation vs ground truth.

        Returns a continuous score (0.0–1.0):
            - CLICK: 1.0 if operation type matches, 0.0 otherwise
            - TYPE/SELECT: token-level F1 between predicted and GT values
            - Unknown GT op: 0.0
        """
        pred_op = predicted.action_type.value.upper()

        if gt_op == "CLICK":
            # In Mind2Web, HOVER and PRESS_ENTER are mapped to CLICK
            return 1.0 if pred_op in ("CLICK", "HOVER", "PRESS") else 0.0

        if gt_op in ("TYPE", "SELECT"):
            if pred_op != gt_op:
                return 0.0
            return self._token_f1(predicted.value, gt_value)

        return 0.0

    # ------------------------------------------------------------------
    # Token-level F1 (per Mind2Web paper formula)
    # ------------------------------------------------------------------

    def _token_f1(self, pred_val: str | None, gt_val: str | None) -> float:
        """Compute token-level F1 score for TYPE/SELECT values.

        From the Mind2Web paper:
            F1(predicted_value, ground_truth_value)
            where tokens are split by whitespace.
        """
        if not gt_val:
            return 1.0 if not pred_val else 0.0

        pred_tokens = set((pred_val or "").split())
        gt_tokens = set(gt_val.split())

        if not pred_tokens or not gt_tokens:
            return 0.0

        common = pred_tokens & gt_tokens
        p = len(common) / len(pred_tokens) if pred_tokens else 0.0
        r = len(common) / len(gt_tokens) if gt_tokens else 0.0

        if p + r == 0:
            return 0.0
        return 2 * p * r / (p + r)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_details(
        self,
        action: Action,
        predicted_node: str | None,
        acceptable: set[str],
        element_correct: bool | None,
        operation_correct: bool,
        token_f1: float = 0.0,
    ) -> str:
        """Build a human-readable comparison summary."""
        if element_correct is None:
            ele_mark = "N/A"
        elif element_correct:
            ele_mark = "✓"
        else:
            ele_mark = "✗"

        op_mark = "✓" if operation_correct else "✗"

        return (
            f"Element {ele_mark} (pred={predicted_node or '?'}, "
            f"acceptable={acceptable}), "
            f"Operation {op_mark} F1={token_f1:.2f} ({action.action_type.value}: {action.value or '-'})"
        )
