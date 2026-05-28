#!/usr/bin/env python3
"""CLI entry point for running the VisuMark agent."""

import argparse
import asyncio
import os
import sys

# Allow running directly from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from visumark_agent import VisuMarkAgent, OpenAIVLM, BrowserEnv, SoMMarker, load_config
from visumark_agent.utils.logging import setup_logger


async def main_async(args: argparse.Namespace) -> None:
    config = load_config(args.config)

    setup_logger(
        level="DEBUG" if args.verbose else (config.get("logging", {}).get("level", "INFO")),
        log_file=args.log_file or config.get("logging", {}).get("file"),
    )

    vlm_cfg = config["vlm"]
    vlm = OpenAIVLM(
        model=args.model or vlm_cfg.get("model", "gpt-4o"),
        api_key=args.api_key or vlm_cfg.get("api_key"),
        base_url=args.base_url or vlm_cfg.get("base_url"),
        timeout=vlm_cfg.get("timeout", 60),
    )

    env_cfg = config["environment"]
    browser = BrowserEnv(
        headless=not args.show_browser if hasattr(args, "show_browser") else env_cfg.get("headless", True),
        viewport=(
            env_cfg.get("viewport_width", 1280),
            env_cfg.get("viewport_height", 720),
        ),
        timeout=env_cfg.get("timeout", 30000),
    )

    marker_cfg = config.get("som", {})
    marker = SoMMarker(
        font_size=marker_cfg.get("label_font_size", 14),
        border_color=marker_cfg.get("bounding_box_color", "#FF0000"),
        show_labels=marker_cfg.get("show_labels", True),
    )

    agent_cfg = config["agent"]
    agent = VisuMarkAgent(
        vlm=vlm,
        browser=browser,
        marker=marker,
        max_steps=args.max_steps or agent_cfg.get("max_steps", 30),
        step_timeout=agent_cfg.get("step_timeout", 60),
        retry_on_error=agent_cfg.get("retry_on_error", True),
        max_retries=agent_cfg.get("max_retries", 3),
        screenshot_dir=args.screenshot_dir or agent_cfg.get("screenshot_dir", "./data/screenshots"),
    )

    result = await agent.run_task(
        task=args.task,
        start_url=args.url,
        output_dir=args.output_dir,
    )

    print("\n" + "=" * 60)
    print("TASK RESULT")
    print("=" * 60)
    print(f"  Success:     {result.success}")
    print(f"  Answer:      {result.answer or 'N/A'}")
    print(f"  Total steps: {result.total_steps}")
    if result.error:
        print(f"  Error:       {result.error}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="VisuMark Agent — VLM-based web automation with Set-of-Mark visual grounding",
    )
    parser.add_argument("--task", "-t", required=True, help="Task description (e.g. 'Search for flights to Paris')")
    parser.add_argument("--url", "-u", required=True, help="Starting URL")
    parser.add_argument("--config", "-c", default="config/config.yaml", help="Path to YAML config")
    parser.add_argument("--model", "-m", default=None, help="VLM model name override")
    parser.add_argument("--api-key", default=None, help="API key override")
    parser.add_argument("--base-url", default=None, help="API base URL override (for proxies)")
    parser.add_argument("--max-steps", type=int, default=None, help="Max steps override")
    parser.add_argument("--screenshot-dir", default=None, help="Directory for step screenshots")
    parser.add_argument("--output-dir", "-o", default=None, help="Directory for run outputs")
    parser.add_argument("--log-file", default=None, help="Log file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
