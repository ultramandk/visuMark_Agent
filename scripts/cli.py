#!/usr/bin/env python3
"""VisuMark Agent — unified CLI entry point.

Subcommands:
    run       Execute a single task in live browser (SoM mode)
    evaluate  Run Mind2Web benchmark evaluation
    serve     Start the Web UI server

Usage:
    python scripts/cli.py run --task "搜索航班" --url "https://google.com/travel/flights"
    python scripts/cli.py evaluate --dataset mind2web --split test_cross_task --num 10
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
# evaluate — Mind2Web benchmark
# ============================================================================

def cmd_evaluate(args: argparse.Namespace) -> None:
    """Run Mind2Web evaluation."""
    config = _setup(args)

    from visumark.dataset.mind2web import Mind2WebDataset
    from visumark.environment.offline_env import OfflineEnvironment
    from visumark.perception.base import PerceptorFactory
    from visumark.reasoning.factory import ReasonerFactory
    from visumark.core.agent import Agent, StepCallbacks
    from visumark.evaluation.comparator import Mind2WebComparator
    from visumark.evaluation.metrics import MetricsCalculator
    from visumark.evaluation.reporter import format_table, save_results
    from visumark.core.types import StepRecord
    from visumark.perception.dom_bridge import DOMBridge
    from visumark.action.parser import ActionParser

    # Load dataset
    data_dir = args.data_dir or config.get("data", {}).get("mind2web_dir", "./data/mind2web")
    dataset = Mind2WebDataset(data_dir=data_dir, split=args.split, max_tasks=args.num)
    print(f"Loaded {len(dataset)} tasks from Mind2Web/{args.split}")

    # Setup components
    agent_cfg = config["agent"]
    env_cfg = config["environment"]
    perc_cfg = config.get("perception", {})
    reas_cfg = config["reasoning"]
    eval_cfg = config.get("evaluation", {})

    provider = args.provider or reas_cfg.get("provider", "qwen")
    model = args.model or reas_cfg.get("model")
    api_key = args.api_key or reas_cfg.get("api_key")
    base_url = args.base_url or reas_cfg.get("base_url")

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

    comparator = Mind2WebComparator()
    calculator = MetricsCalculator()

    class EvalCallback(StepCallbacks):
        """Step callback that compares predictions against Mind2Web ground truth."""
        def __init__(self, task, comparator, calculator):
            super().__init__()
            self.task = task
            self.comparator = comparator
            self.calculator = calculator
            self._step_idx = 0
            self._parser = ActionParser()

        async def on_step(self, record: StepRecord, bridge: DOMBridge) -> None:
            if not self.task.actions_gt or self._step_idx >= len(self.task.actions_gt):
                return
            gt = self.task.actions_gt[self._step_idx]
            self._step_idx += 1
            if record.action is None:
                return
            cmp = self.comparator.compare_step(
                predicted_action=record.action,
                gt_action=gt,
                bridge=bridge,
                step=record.step,
            )
            record.element_correct = cmp.element_correct
            record.operation_correct = cmp.operation_correct
            self.calculator.record_step(self.task.task_id, cmp)

    async def _evaluate():
        for i, task in enumerate(dataset):
            print(f"\n[{i+1}/{len(dataset)}] {task.description[:80]}...")

            env = OfflineEnvironment(
                viewport=(env_cfg["viewport_width"], env_cfg["viewport_height"]),
            )
            perceptor = PerceptorFactory.create(agent_cfg["mode"], perc_cfg)

            agent = Agent(
                perceptor=perceptor,
                reasoner=reasoner,
                env=env,
                max_steps=agent_cfg.get("max_steps", 30),
                screenshot_dir=Path(args.screenshot_dir or config.get("data", {}).get("screenshot_dir", "./data/screenshots")) / f"task_{i:03d}",
            )

            # Attach evaluation callback
            eval_cb = EvalCallback(task, comparator, calculator)
            await agent.run(task, callbacks=eval_cb)

        # Compute & report
        metrics = calculator.compute(args.split)
        print("\n" + format_table(metrics))

        output_dir = eval_cfg.get("output_dir", "./data/results")
        path = save_results(metrics, output_dir)
        print(f"\nResults saved to: {path}")

    asyncio.run(_evaluate())




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
    p_eval.add_argument("--dataset", choices=["mind2web"], default="mind2web")
    p_eval.add_argument("--data-dir", default=None, help="Mind2Web data directory")
    p_eval.add_argument("--split", default="test_cross_task",
                        choices=["test_cross_task", "test_cross_website", "test_cross_domain", "train"])
    p_eval.add_argument("--num", type=int, default=None, help="Max tasks to evaluate")
    p_eval.add_argument("--config", "-c", default="config/config.yaml")
    p_eval.add_argument("--provider", default=None,
                        choices=["qwen", "openai", "anthropic", "local"],
                        help="VLM provider (default: from config.yaml)")
    p_eval.add_argument("--model", "-m", default=None)
    p_eval.add_argument("--api-key", default=None)
    p_eval.add_argument("--base-url", default=None)
    p_eval.add_argument("--screenshot-dir", default=None)
    p_eval.add_argument("--log-file", default=None)
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
