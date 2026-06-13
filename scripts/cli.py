#!/usr/bin/env python3
"""VisuMark Agent — unified CLI entry point.

Subcommands:
    run       Execute a single task in live browser (SoM mode)
    evaluate  Run Mind2Web benchmark evaluation
    serve     Start the Web UI server

Usage:
    python scripts/cli.py run --task "搜索航班" --url "https://google.com/travel/flights"
    python scripts/cli.py evaluate --split test_cross_task --num 10
    python scripts/cli.py serve --port 8000
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure src/ is on path
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))


# ============================================================================
# Shared setup
# ============================================================================

def _setup(args: argparse.Namespace) -> dict:
    """Load config and setup logging. Returns config dict."""
    from visumark.utils.config import load_config
    from visumark.utils.logging import setup_logger

    config = load_config(args.config)
    verbose = getattr(args, "verbose", False)
    log_level = "DEBUG" if verbose else "INFO"
    log_file = getattr(args, "log_file", None)
    setup_logger(level=log_level, log_file=log_file)
    return config


def _create_components(config: dict, args: argparse.Namespace):
    """Create agent components from config + CLI args."""
    from visumark.environment.live_env import LiveEnvironment
    from visumark.perception.base import PerceptorFactory
    from visumark.reasoning.factory import ReasonerFactory
    from visumark.core.agent import Agent

    agent_cfg = config["agent"]
    env_cfg = config["environment"]
    perc_cfg = config.get("perception", {})
    reas_cfg = config["reasoning"]

    # CLI overrides
    provider = getattr(args, "provider", None) or reas_cfg.get("provider", "qwen")
    model = getattr(args, "model", None) or reas_cfg.get("model")
    api_key = getattr(args, "api_key", None) or reas_cfg.get("api_key")
    base_url = getattr(args, "base_url", None) or reas_cfg.get("base_url")
    max_steps = getattr(args, "max_steps", None) or agent_cfg.get("max_steps", 30)
    headless = not getattr(args, "show_browser", False) if hasattr(args, "show_browser") else env_cfg.get("headless", True)

    env = LiveEnvironment(
        headless=headless,
        viewport=(env_cfg["viewport_width"], env_cfg["viewport_height"]),
        timeout=env_cfg.get("timeout", 30000),
    )

    perceptor = PerceptorFactory.create(agent_cfg["mode"], perc_cfg)

    reasoner = ReasonerFactory.create(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=reas_cfg.get("temperature", 0.0),
        max_tokens=reas_cfg.get("max_tokens", 4096),
        timeout=reas_cfg.get("timeout", 60),
        max_retries=reas_cfg.get("max_retries", 3),
    )

    agent = Agent(
        perceptor=perceptor,
        reasoner=reasoner,
        env=env,
        max_steps=max_steps,
        screenshot_dir=getattr(args, "screenshot_dir", None) or config.get("data", {}).get("screenshot_dir", "./data/screenshots"),
    )

    return agent


# ============================================================================
# run — single task in live browser
# ============================================================================

def cmd_run(args: argparse.Namespace) -> None:
    """Execute a single task with live browser."""
    config = _setup(args)

    from visumark.dataset.base import TaskInstance

    agent = _create_components(config, args)

    task = TaskInstance(
        task_id="cli-task",
        description=args.task,
        start_url=args.url,
    )

    async def _run():
        result = await agent.run(task)
        print("\n" + "=" * 60)
        print("TASK RESULT")
        print("=" * 60)
        print(f"  Success:     {result.success}")
        print(f"  Answer:      {result.answer or 'N/A'}")
        print(f"  Total steps: {result.total_steps}")
        if result.error:
            print(f"  Error:       {result.error}")
        print("=" * 60)

    asyncio.run(_run())


# ============================================================================
# evaluate — Mind2Web benchmark (offline HTML snapshots + SoM + VLM)
# ============================================================================

def cmd_evaluate(args: argparse.Namespace) -> None:
    """Run Mind2Web evaluation using offline HTML snapshots.

    For each task:
      1. For each step, load cleaned_html into an OfflineEnvironment
      2. Render + screenshot + SoM annotation
      3. VLM predicts: which SoM number to click + what operation
      4. Compare prediction vs ground truth (pos_candidates + operation)
      5. Compute: Element Acc / Operation F1 / Step SR / Task SR
    """
    config = _setup(args)

    from visumark.dataset.mind2web import Mind2WebDataset
    from visumark.environment.offline_env import OfflineEnvironment
    from visumark.perception.som_perceptor import SoMPerceptor
    from visumark.reasoning.factory import ReasonerFactory
    from visumark.evaluation.comparator import Mind2WebComparator
    from visumark.evaluation.metrics import MetricsCalculator
    from visumark.evaluation.reporter import format_table, save_results

    # Load dataset
    data_dir = args.data_dir or "./test"
    dataset = Mind2WebDataset(data_dir=data_dir, split=args.split, max_tasks=args.num)
    print(f"Loaded {len(dataset)} tasks from {args.split}")
    print(f"  Stats: {dataset.stats}")

    # Setup shared components
    reas_cfg = config["reasoning"]
    provider = args.provider or reas_cfg.get("provider", "qwen")
    model = args.model or reas_cfg.get("model")
    api_key = args.api_key or reas_cfg.get("api_key")
    base_url = args.base_url or reas_cfg.get("base_url")

    reasoner = ReasonerFactory.create(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0.0,
        max_tokens=4096,
        timeout=reas_cfg.get("timeout", 120),
        max_retries=reas_cfg.get("max_retries", 3),
    )

    comparator = Mind2WebComparator()
    calculator = MetricsCalculator()

    # Run evaluation asynchronously
    asyncio.run(_evaluate_all(
        dataset, config, reasoner, comparator, calculator, args.verbose
    ))

    # Print results
    metrics = calculator.compute(args.split)
    print("\n" + format_table(metrics))
    output_dir = config.get("evaluation", {}).get("output_dir", "./data/results")
    path = save_results(metrics, output_dir)
    print(f"\nResults saved to: {path}")


async def _evaluate_all(
    dataset, config, reasoner, comparator, calculator, verbose: bool
) -> None:
    """Run evaluation on all tasks in the dataset.

    For each task, iterates through its action steps:
    - Load cleaned_html → screenshot → SoM → VLM → compare with GT
    """
    from visumark.environment.offline_env import OfflineEnvironment
    from visumark.perception.som_perceptor import SoMPerceptor
    from visumark.environment.dom_utils import parse_accessibility_tree
    from loguru import logger

    perc_cfg = config.get("perception", {}).get("som", {})

    for task_idx, task in enumerate(dataset):
        task_desc = task.description[:80]
        print(f"\n[{task_idx + 1}/{len(dataset)}] {task_desc}...")
        logger.info(f"Evaluating task {task.task_id}: {task_desc}")

        # Fresh environment per task
        env = OfflineEnvironment(viewport=(1280, 720))
        await env.start()
        perceptor = SoMPerceptor({
            "max_elements": 600,
            "font_size": 11,
            "use_accessibility_tree": True,
            "min_element_size": 4,
            "clip_to_viewport": False,  # Offline: capture ALL elements in full-page snapshot
        })

        try:
            for step_idx, gt_action in enumerate(task.actions_gt):
                cleaned_html = gt_action.get("cleaned_html", "")
                if not cleaned_html:
                    logger.warning(f"  Step {step_idx + 1}: no cleaned_html, skipping")
                    continue

                # 1. Load HTML snapshot
                await env.load_html(cleaned_html)

                # 2. SoM perception (screenshot + element extraction + annotation)
                perception, bridge = await perceptor.perceive(env)

                # 3. VLM reasoning with evaluation-specific prompt
                from visumark.core.types import ReasonerOutput
                from visumark.action.parser import ActionParser
                from visumark.reasoning.prompts.som_prompts import EVAL_SYSTEM_PROMPT
                import base64 as _base64

                # Build eval prompt: system + user message
                user_msg = (
                    f"Task: {task.description}\n\n"
                    f"Step {step_idx + 1} of {len(task.actions_gt)}.\n"
                    f"Website: {task.website} ({task.domain}).\n"
                    f"Look at the marked screenshot. Which numbered element should be interacted with NEXT?\n"
                    f"Return ONLY the JSON object."
                )

                content: list[dict] = [{"type": "text", "text": user_msg}]
                img_bytes = perception.annotated_screenshot or perception.screenshot
                if img_bytes:
                    b64 = _base64.b64encode(img_bytes).decode("utf-8")
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
                    })

                messages = [
                    {"role": "system", "content": EVAL_SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ]

                raw_text = await reasoner._call_api(messages)

                # Parse response
                parser = ActionParser()
                import re as _re
                thought = ""
                try:
                    json_match = _re.search(r"\{[\s\S]*\}", raw_text)
                    if json_match:
                        obj = __import__("json").loads(json_match.group(0))
                        thought = obj.get("thought", "")
                except Exception:
                    pass

                action = None
                try:
                    action = parser.parse(raw_text)
                except Exception as e:
                    logger.warning(f"  Parse error: {e}")

                reasoner_output = ReasonerOutput(
                    raw_text=raw_text,
                    thought=thought,
                    action=action,
                )

                if reasoner_output.action is None:
                    logger.warning(f"  Step {step_idx + 1}: VLM produced no action")
                    # Record as failure
                    from visumark.evaluation.comparator import StepComparison
                    cmp = StepComparison(
                        step=step_idx + 1,
                        element_correct=False,
                        operation_correct=False,
                        step_success=False,
                        predicted_node=None,
                        details="VLM produced no valid action",
                    )
                    calculator.record_step(task.task_id, cmp)
                    continue

                # 4. Compare prediction vs ground truth
                cmp = comparator.compare_step(
                    predicted_action=reasoner_output.action,
                    gt_action=gt_action,
                    bridge=bridge,
                    step=step_idx + 1,
                )
                calculator.record_step(task.task_id, cmp)

                status = "✓" if cmp.step_success else "✗"
                logger.info(
                    f"  Step {step_idx + 1}: {status} {cmp.details}"
                )

        except Exception as exc:
            logger.error(f"  Task {task.task_id} failed: {exc}")
        finally:
            await env.stop()


# ============================================================================
# serve — Web UI
# ============================================================================

def cmd_serve(args: argparse.Namespace) -> None:
    """Start the Web UI server."""
    import uvicorn
    import os
    import sys

    # Add src/ to path so both visumark and web packages are importable
    sys.path.insert(0, str(_REPO_ROOT / "src"))
    os.chdir(str(_REPO_ROOT))

    uvicorn.run(
        "web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="VisuMark Agent — VLM-powered web agent with Set-of-Mark visual grounding",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ---- run ----
    p_run = sub.add_parser("run", help="Execute a single task in live browser")
    p_run.add_argument("--task", "-t", required=True, help="Task description")
    p_run.add_argument("--url", "-u", required=True, help="Starting URL")
    p_run.add_argument("--config", "-c", default="config/config.yaml")
    p_run.add_argument("--provider", default=None,
                       choices=["qwen", "openai", "anthropic", "local"],
                       help="VLM provider (default: from config.yaml)")
    p_run.add_argument("--model", "-m", default=None, help="Model override")
    p_run.add_argument("--api-key", default=None, help="API key override")
    p_run.add_argument("--base-url", default=None, help="API base URL override")
    p_run.add_argument("--max-steps", type=int, default=None, help="Max steps")
    p_run.add_argument("--show-browser", action="store_true", help="Show browser window")
    p_run.add_argument("--screenshot-dir", default=None, help="Screenshot output dir")
    p_run.add_argument("--log-file", default=None, help="Log file path")
    p_run.add_argument("--verbose", "-v", action="store_true")
    p_run.set_defaults(func=cmd_run)

    # ---- evaluate ----
    p_eval = sub.add_parser("evaluate", help="Run Mind2Web benchmark evaluation")
    p_eval.add_argument("--data-dir", default="./test", help="Mind2Web data directory")
    p_eval.add_argument("--split", default="test_cross_task",
                        choices=["test_cross_task", "test_cross_website", "test_cross_domain"])
    p_eval.add_argument("--num", type=int, default=None, help="Max tasks to evaluate")
    p_eval.add_argument("--config", "-c", default="config/config.yaml")
    p_eval.add_argument("--provider", default=None,
                        choices=["qwen", "openai", "anthropic", "local"],
                        help="VLM provider (default: from config.yaml)")
    p_eval.add_argument("--model", "-m", default=None)
    p_eval.add_argument("--api-key", default=None)
    p_eval.add_argument("--base-url", default=None)
    p_eval.add_argument("--verbose", "-v", action="store_true")
    p_eval.set_defaults(func=cmd_evaluate)

    # ---- serve ----
    p_serve = sub.add_parser("serve", help="Start the Web UI server")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", "-p", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="Auto-reload (dev)")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
