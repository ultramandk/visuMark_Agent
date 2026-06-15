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
        max_tokens=reas_cfg.get("max_tokens", 512),
        timeout=reas_cfg.get("timeout", 120),
        max_retries=reas_cfg.get("max_retries", 3),
    )

    comparator = Mind2WebComparator()
    calculator = MetricsCalculator()

    # Run evaluation asynchronously
    mode = getattr(args, "mode", "som")
    html_screenshot = getattr(args, "html_screenshot", False)
    asyncio.run(_evaluate_all(
        dataset, config, reasoner, comparator, calculator, args.verbose,
        mode=mode, html_screenshot=html_screenshot,
    ))

    # Print results
    metrics = calculator.compute(args.split)
    print("\n" + format_table(metrics))
    output_dir = config.get("evaluation", {}).get("output_dir", "./data/results")
    path = save_results(metrics, output_dir)
    print(f"\nResults saved to: {path}")


async def _evaluate_all(
    dataset, config, reasoner, comparator, calculator, verbose: bool,
    mode: str = "som",
    html_screenshot: bool = False,
) -> None:
    """Run evaluation on all tasks in the dataset.

    Two modes:
        som  — SoM visual: screenshot + bounding-box annotation → VLM
        html — Text: candidate list with attributes → LLM/VLM (closest to paper)

    Args:
        mode: "som" (visual) or "html" (text-based).
        html_screenshot: If True, include a page screenshot alongside the
            text candidate list in HTML mode (for VLM identity).
    """
    from visumark.environment.offline_env import OfflineEnvironment
    from visumark.perception.som_perceptor import SoMPerceptor
    from visumark.environment.dom_utils import parse_accessibility_tree
    from loguru import logger

    perc_cfg = config.get("perception", {}).get("som", {})

    # ── Checkpoint: resume from interrupted runs ──
    import json as _json
    checkpoint_path = Path("data/results") / f".checkpoint_{dataset.split}.json"
    completed_ids: set[str] = set()
    if checkpoint_path.exists():
        try:
            ckpt = _json.loads(checkpoint_path.read_text(encoding="utf-8"))
            completed_ids = set(ckpt.get("completed", []))
            # Restore already-recorded steps into calculator
            for task_id, comparisons in ckpt.get("steps", {}).items():
                if task_id in completed_ids:
                    for cmp_dict in comparisons:
                        from visumark.evaluation.comparator import StepComparison
                        cmp = StepComparison(
                            step=cmp_dict["step"],
                            element_correct=cmp_dict.get("element_correct"),
                            operation_correct=cmp_dict["operation_correct"],
                            step_success=cmp_dict.get("step_success"),
                            token_f1=cmp_dict.get("token_f1", 0.0),
                            predicted_node=cmp_dict.get("predicted_node"),
                            details=cmp_dict.get("details", ""),
                        )
                        calculator.record_step(task_id, cmp)
            logger.info(f"Resumed from checkpoint: {len(completed_ids)} tasks already done")
        except Exception:
            logger.warning("Failed to load checkpoint, starting fresh")

    def _save_checkpoint():
        """Save current calculator state to checkpoint file."""
        ckpt_data = {
            "completed": sorted(completed_ids),
            "steps": {
                tid: [
                    {
                        "step": c.step,
                        "element_correct": c.element_correct,
                        "operation_correct": c.operation_correct,
                        "step_success": c.step_success,
                        "token_f1": c.token_f1,
                        "predicted_node": c.predicted_node,
                        "details": c.details,
                    }
                    for c in calculator._task_steps.get(tid, [])
                ]
                for tid in completed_ids
            },
        }
        checkpoint_path.write_text(_json.dumps(ckpt_data, ensure_ascii=False), encoding="utf-8")

    for task_idx, task in enumerate(dataset):
        # Skip already-completed tasks
        if task.task_id in completed_ids:
            continue

        task_desc = task.description[:80]
        print(f"\n[{task_idx + 1}/{len(dataset)}] {task_desc}...")
        logger.info(f"Evaluating task {task.task_id}: {task_desc}")

        # Fresh environment per task
        env = OfflineEnvironment(viewport=(1280, 720))
        await env.start()

        # Choose perception mode
        if mode == "html":
            from visumark.perception.html_perceptor import HTMLPerceptor
            perceptor = HTMLPerceptor({
                "max_candidates": 150,
            })
        else:
            perceptor = SoMPerceptor({
                "max_elements": 600,
                "font_size": 11,
                "use_accessibility_tree": True,
                "min_element_size": 4,
                "clip_to_viewport": False,
            })

        try:
            for step_idx, gt_action in enumerate(task.actions_gt):
                cleaned_html = gt_action.get("cleaned_html", "")
                if not cleaned_html:
                    logger.warning(f"  Step {step_idx + 1}: no cleaned_html, skipping")
                    continue

                # 1. Load HTML snapshot
                await env.load_html(cleaned_html)

                if mode == "html":
                    # ── HTML text mode: candidate list + screenshot → VLM ──
                    from visumark.core.types import ReasonerOutput
                    from visumark.action.parser import ActionParser
                    from visumark.reasoning.prompts.html_prompts import (
                        HTML_EVAL_SYSTEM_PROMPT,
                        build_html_eval_user_prompt,
                    )
                    import base64 as _base64

                    # Build candidate list from Mind2Web data.
                    # Shuffle to prevent the model from learning that
                    # correct answers are always at positions 1-5.
                    import random as _random
                    pos = list(gt_action.get("pos_candidates", []))
                    neg = list(gt_action.get("neg_candidates", []))
                    _random.shuffle(neg)
                    # Reserve space for all pos_candidates, fill rest with neg
                    limit = perceptor.max_candidates - len(pos)
                    candidates = pos + neg[:max(0, limit)]
                    _random.shuffle(candidates)
                    perception, bridge = await perceptor.perceive(
                        env, candidates=candidates
                    )

                    # Build text prompt
                    user_msg = build_html_eval_user_prompt(
                        task=task.description,
                        elements=perception.elements,
                        step_idx=step_idx,
                        total_steps=len(task.actions_gt),
                        website=task.website,
                        domain=task.domain,
                    )

                    # Build messages: text + optional screenshot
                    content: list[dict] = [{"type": "text", "text": user_msg}]

                    if html_screenshot:
                        from io import BytesIO as _BytesIO
                        from PIL import Image as _Image
                        screenshot = await env.screenshot()
                        if screenshot:
                            img = _Image.open(_BytesIO(screenshot))
                            if img.height > 2048:
                                scale = 2048 / img.height
                                img = img.resize(
                                    (int(img.width * scale), 2048),
                                    _Image.LANCZOS,
                                )
                                buf = _BytesIO()
                                img.save(buf, format="JPEG", quality=70)
                                screenshot = buf.getvalue()
                            b64 = _base64.b64encode(screenshot).decode("utf-8")
                            content.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                            })

                    messages = [
                        {"role": "system", "content": HTML_EVAL_SYSTEM_PROMPT},
                        {"role": "user", "content": content},
                    ]

                    # HTML mode only needs ~150 tokens for JSON response.
                    # Temporarily reduce max_tokens to stay within model's
                    # context window (input + output <= 4096).
                    saved_max_tokens = reasoner._max_tokens
                    reasoner._max_tokens = min(reasoner._max_tokens, 256)
                    raw_text = await reasoner._call_api(messages)
                    reasoner._max_tokens = saved_max_tokens

                    if step_idx == 0:
                        logger.info(
                            f"  [DIAG] Step 1 VLM raw output: {raw_text[:500]}"
                        )
                    else:
                        logger.debug(
                            f"  VLM raw (step {step_idx+1}): {raw_text[:300]}"
                        )

                else:
                    # ── SoM visual mode: screenshot + annotation → VLM ──
                    from visumark.core.types import ReasonerOutput
                    from visumark.action.parser import ActionParser
                    from visumark.reasoning.prompts.som_prompts import EVAL_SYSTEM_PROMPT
                    import base64 as _base64

                    perception, bridge = await perceptor.perceive(env)

                    # Bbox correction for full-page screenshots
                    from io import BytesIO as _BytesIO
                    from PIL import Image as _Image

                    MAX_IMAGE_HEIGHT = 4096
                    vw, vh = 1280, 720

                    if perception.screenshot:
                        img = _Image.open(_BytesIO(perception.screenshot))
                        iw, ih = img.size

                        for elem in perception.elements:
                            x, y, w, h = elem.bbox
                            elem.bbox = (
                                x * vw / iw,
                                y * vh / ih,
                                w * vw / iw,
                                h * vh / ih,
                            )

                        if ih > MAX_IMAGE_HEIGHT:
                            scale = MAX_IMAGE_HEIGHT / ih
                            new_w = int(iw * scale)
                            new_h = MAX_IMAGE_HEIGHT
                            img = img.resize((new_w, new_h), _Image.LANCZOS)
                            buf = _BytesIO()
                            img.save(buf, format="PNG")
                            perception.screenshot = buf.getvalue()
                            iw, ih = new_w, new_h
                        else:
                            buf = _BytesIO()
                            img.save(buf, format="PNG")
                            perception.screenshot = buf.getvalue()

                        annotated = perceptor.marker.annotate(
                            perception.screenshot,
                            perception.elements,
                            viewport_w=iw,
                            viewport_h=ih,
                        )
                        perception.annotated_screenshot = annotated

                    # Build image+text prompt
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

                    img_kb = len(img_bytes) / 1024 if img_bytes else 0
                    logger.debug(
                        f"  VLM raw (step {step_idx+1}, img={img_kb:.0f}KB): "
                        f"{raw_text[:300]}"
                    )
                    if step_idx == 0:
                        logger.info(
                            f"  [DIAG] Step 1 VLM raw output: {raw_text[:500]}"
                        )

                # ── Shared: parse response and compare with GT ──
                from visumark.core.types import ReasonerOutput
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
                        token_f1=0.0,
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
            # If the browser crashed, recreate environment for next task
            if "Page crashed" in str(exc) or "Target closed" in str(exc):
                try:
                    await env.stop()
                except Exception:
                    pass
                env = OfflineEnvironment(viewport=(1280, 720))
                await env.start()
                if mode == "html":
                    from visumark.perception.html_perceptor import HTMLPerceptor
                    perceptor = HTMLPerceptor({"max_candidates": 150})
                else:
                    perceptor = SoMPerceptor({
                        "max_elements": 600, "font_size": 11,
                        "use_accessibility_tree": True, "min_element_size": 4,
                        "clip_to_viewport": False,
                    })
        finally:
            # Mark as completed even on failure (don't retry failed tasks)
            completed_ids.add(task.task_id)
            _save_checkpoint()
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
    p_eval.add_argument("--mode", default="som",
                        choices=["som", "html"],
                        help="Perception mode: som (visual) or html (text-based)")
    p_eval.add_argument("--html-screenshot", action="store_true",
                        help="Include page screenshot in HTML mode (default: off)")
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
