#!/usr/bin/env python3
"""Evaluation script for VisuMark Agent on benchmark tasks."""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from visumark_agent import VisuMarkAgent, OpenAIVLM, BrowserEnv, SoMMarker, load_config
from visumark_agent.utils.logging import setup_logger


async def evaluate(
    tasks: list[dict],
    config: dict,
    output_dir: Path,
    verbose: bool = False,
) -> dict:
    """Run the agent on each task and collect metrics."""

    vlm_cfg = config["vlm"]
    vlm = OpenAIVLM(
        model=vlm_cfg.get("model", "gpt-4o"),
        api_key=vlm_cfg.get("api_key"),
        base_url=vlm_cfg.get("base_url"),
        timeout=vlm_cfg.get("timeout", 60),
    )

    env_cfg = config["environment"]
    marker_cfg = config.get("som", {})
    agent_cfg = config["agent"]

    results = []
    for i, task in enumerate(tasks, 1):
        print(f"\n[{i}/{len(tasks)}] {task['description'][:80]}...")

        browser = BrowserEnv(
            headless=env_cfg.get("headless", True),
            viewport=(env_cfg.get("viewport_width", 1280), env_cfg.get("viewport_height", 720)),
            timeout=env_cfg.get("timeout", 30000),
        )
        marker = SoMMarker(
            font_size=marker_cfg.get("label_font_size", 14),
            show_labels=marker_cfg.get("show_labels", True),
        )
        agent = VisuMarkAgent(
            vlm=vlm,
            browser=browser,
            marker=marker,
            max_steps=agent_cfg.get("max_steps", 30),
            screenshot_dir=output_dir / f"task_{i:03d}",
        )

        result = await agent.run_task(
            task=task["description"],
            start_url=task["url"],
            output_dir=output_dir / f"task_{i:03d}",
        )

        results.append({
            "task_id": task.get("id", i),
            "description": task["description"],
            "success": result.success,
            "answer": result.answer,
            "total_steps": result.total_steps,
            "error": result.error,
            "step_success_rate": sum(1 for s in result.steps if s.success) / max(len(result.steps), 1),
        })

    # aggregate metrics
    n = len(results)
    success_count = sum(1 for r in results if r["success"])
    metrics = {
        "total_tasks": n,
        "task_success_rate": success_count / n if n else 0,
        "avg_steps": sum(r["total_steps"] for r in results) / n if n else 0,
        "avg_step_success_rate": sum(r["step_success_rate"] for r in results) / n if n else 0,
        "results": results,
    }
    return metrics


async def main_async(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    setup_logger(level="DEBUG" if args.verbose else "INFO")

    with open(args.tasks, encoding="utf-8") as f:
        tasks = json.load(f)

    output_dir = Path(args.output_dir or config.get("evaluation", {}).get("output_dir", "./data/results"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = output_dir / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics = await evaluate(tasks, config, output_dir, verbose=args.verbose)

    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 60)
    print("EVALUATION COMPLETE")
    print("=" * 60)
    print(f"  Task Success Rate:  {metrics['task_success_rate']:.2%}")
    print(f"  Avg Steps:          {metrics['avg_steps']:.1f}")
    print(f"  Avg Step SR:        {metrics['avg_step_success_rate']:.2%}")
    print(f"  Results saved to:   {metrics_path}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate VisuMark Agent on a task suite")
    parser.add_argument("--tasks", "-t", required=True, help="Path to JSON task file")
    parser.add_argument("--config", "-c", default="config/config.yaml")
    parser.add_argument("--output-dir", "-o", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
