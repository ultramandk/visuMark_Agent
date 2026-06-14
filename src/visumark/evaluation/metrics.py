"""Evaluation metrics — Element Accuracy, Operation F1, Step SR, Task SR.

Implements the exact formulas from the Mind2Web paper (Section 4.2) and
the project proposal (Section 3.4).
"""

from dataclasses import dataclass, field

from loguru import logger


@dataclass
class TaskMetrics:
    """Per-task metrics."""
    task_id: str
    total_steps: int
    element_accuracy: float        # Fraction of evaluable steps with correct element
    operation_f1: float             # Average operation F1 across all steps
    step_success_rate: float        # Fraction of evaluable steps that are fully successful
    task_success: bool              # All evaluable steps successful
    num_element_na: int = 0         # Steps skipped for Element Acc (pos_candidates empty)
    num_step_na: int = 0            # Steps skipped for Step SR (pos_candidates empty)


@dataclass
class AggregateMetrics:
    """Aggregated metrics across all tasks (macro average per task).

    Matches the output format of Mind2Web Table 2.
    """
    split: str
    total_tasks: int
    total_steps: int
    element_accuracy: float         # Macro average across tasks
    operation_f1: float             # Macro average across tasks
    step_success_rate: float        # Macro average across tasks
    task_success_rate: float        # Fraction of fully successful tasks
    task_metrics: list[TaskMetrics] = field(default_factory=list)


class MetricsCalculator:
    """Compute evaluation metrics from step comparison results.

    Usage:
        calc = MetricsCalculator()
        for task in tasks:
            for step in task.actions:
                cmp = comparator.compare_step(...)
                calc.record_step(task_id, cmp)
        metrics = calc.compute("test_cross_task")
    """

    def __init__(self):
        self._task_steps: dict[str, list] = {}  # task_id → [StepComparison]

    def record_step(self, task_id: str, comparison) -> None:
        """Record a single step comparison result."""
        from visumark.evaluation.comparator import StepComparison
        if task_id not in self._task_steps:
            self._task_steps[task_id] = []
        self._task_steps[task_id].append(comparison)

    def compute(self, split: str = "unknown") -> AggregateMetrics:
        """Compute aggregate metrics from all recorded steps.

        Returns metrics in the Mind2Web paper format.
        """
        task_metrics_list = []
        for task_id, comparisons in self._task_steps.items():
            tm = self._compute_task_metrics(task_id, comparisons)
            task_metrics_list.append(tm)

        if not task_metrics_list:
            return AggregateMetrics(
                split=split,
                total_tasks=0,
                total_steps=0,
                element_accuracy=0.0,
                operation_f1=0.0,
                step_success_rate=0.0,
                task_success_rate=0.0,
            )

        n = len(task_metrics_list)
        return AggregateMetrics(
            split=split,
            total_tasks=n,
            total_steps=sum(tm.total_steps for tm in task_metrics_list),
            # Macro average: average of per-task averages
            element_accuracy=sum(tm.element_accuracy for tm in task_metrics_list) / n,
            operation_f1=sum(tm.operation_f1 for tm in task_metrics_list) / n,
            step_success_rate=sum(tm.step_success_rate for tm in task_metrics_list) / n,
            task_success_rate=sum(1 for tm in task_metrics_list if tm.task_success) / n,
            task_metrics=task_metrics_list,
        )

    def _compute_task_metrics(
        self, task_id: str, comparisons: list
    ) -> TaskMetrics:
        """Compute per-task metrics from step comparisons.

        Steps with element_correct=None (pos_candidates empty) are excluded
        from Element Accuracy and Step Success Rate denominators.
        """
        total = len(comparisons)
        if total == 0:
            return TaskMetrics(
                task_id=task_id,
                total_steps=0,
                element_accuracy=0.0,
                operation_f1=0.0,
                step_success_rate=0.0,
                task_success=False,
            )

        # Element Accuracy — only count evaluable steps
        elem_evaluable = [c for c in comparisons if c.element_correct is not None]
        num_na = total - len(elem_evaluable)
        element_acc = (
            sum(1 for c in elem_evaluable if c.element_correct) / len(elem_evaluable)
            if elem_evaluable else 0.0
        )

        # Operation F1 — continuous token F1 average across all steps
        op_f1 = sum(c.token_f1 for c in comparisons) / total

        # Step Success Rate — only count evaluable steps
        step_evaluable = [c for c in comparisons if c.step_success is not None]
        num_step_na = total - len(step_evaluable)
        step_sr = (
            sum(1 for c in step_evaluable if c.step_success) / len(step_evaluable)
            if step_evaluable else 0.0
        )

        # Task Success: no evaluable step should be a failure
        # (N/A steps don't block success)
        task_success = all(c.step_success is not False for c in comparisons)

        return TaskMetrics(
            task_id=task_id,
            total_steps=total,
            element_accuracy=element_acc,
            operation_f1=op_f1,
            step_success_rate=step_sr,
            task_success=task_success,
            num_element_na=num_na,
            num_step_na=num_step_na,
        )

    def reset(self) -> None:
        """Clear all recorded steps for a fresh evaluation run."""
        self._task_steps.clear()
