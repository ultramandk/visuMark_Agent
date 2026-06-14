"""Evaluation reporter — formats and exports metrics results.

Outputs results in the Mind2Web Table 2 format (plain text + JSON).
"""

import json
from pathlib import Path
from datetime import datetime

from visumark.evaluation.metrics import AggregateMetrics, TaskMetrics


def format_table(metrics: AggregateMetrics) -> str:
    """Format metrics as a plain-text table (Mind2Web paper style).

    Returns a multi-line string suitable for console output.
    """
    # Count N/A steps
    total_na = sum(t.num_element_na for t in metrics.task_metrics)

    lines = [
        "=" * 70,
        f"EVALUATION RESULTS — {metrics.split}",
        "=" * 70,
        f"  Total Tasks:        {metrics.total_tasks}",
        f"  Total Steps:        {metrics.total_steps}",
        f"",
        f"  Element Accuracy:   {metrics.element_accuracy:.1%}",
        f"  Operation F1:       {metrics.operation_f1:.1%}",
        f"  Step Success Rate:  {metrics.step_success_rate:.1%}",
        f"  Task Success Rate:  {metrics.task_success_rate:.1%}",
    ]
    if total_na > 0:
        lines.append(f"")
        lines.append(f"  Steps N/A (no GT):  {total_na}  (excluded from Element Acc / Step SR)")
    lines.append("=" * 70)

    # Per-task breakdown (top 10 + bottom 10 for diagnosis)
    tasks = sorted(metrics.task_metrics, key=lambda t: t.step_success_rate)
    if len(tasks) > 5:
        lines.append("\nBest performing tasks:")
        for t in tasks[-5:]:
            lines.append(
                f"  {t.task_id[:30]:<30} "
                f"Ele={t.element_accuracy:.0%} "
                f"OpF1={t.operation_f1:.0%} "
                f"StepSR={t.step_success_rate:.0%} "
                f"Task={'✓' if t.task_success else '✗'}"
            )

        lines.append("\nWorst performing tasks:")
        for t in tasks[:5]:
            lines.append(
                f"  {t.task_id[:30]:<30} "
                f"Ele={t.element_accuracy:.0%} "
                f"OpF1={t.operation_f1:.0%} "
                f"StepSR={t.step_success_rate:.0%} "
                f"Task={'✓' if t.task_success else '✗'}"
            )

    return "\n".join(lines)


def save_results(metrics: AggregateMetrics, output_dir: str | Path) -> Path:
    """Save evaluation results to a JSON file.

    Args:
        metrics: Computed aggregate metrics.
        output_dir: Directory to save results.

    Returns:
        Path to the saved JSON file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"results_{metrics.split}_{timestamp}.json"
    filepath = output_dir / filename

    # Build serializable output
    output = {
        "split": metrics.split,
        "timestamp": timestamp,
        "summary": {
            "total_tasks": metrics.total_tasks,
            "total_steps": metrics.total_steps,
            "element_accuracy": round(metrics.element_accuracy, 4),
            "operation_f1": round(metrics.operation_f1, 4),
            "step_success_rate": round(metrics.step_success_rate, 4),
            "task_success_rate": round(metrics.task_success_rate, 4),
        },
        "tasks": [
            {
                "task_id": t.task_id,
                "total_steps": t.total_steps,
                "element_accuracy": round(t.element_accuracy, 4),
                "operation_f1": round(t.operation_f1, 4),
                "step_success_rate": round(t.step_success_rate, 4),
                "task_success": t.task_success,
                "num_element_na": t.num_element_na,
                "num_step_na": t.num_step_na,
            }
            for t in metrics.task_metrics
        ],
    }

    filepath.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return filepath
