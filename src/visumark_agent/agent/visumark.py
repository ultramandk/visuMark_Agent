"""VisuMark Agent — main agent loop: observe → mark → reason → act."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from visumark_agent.agent.prompts import SYSTEM_PROMPT, build_task_prompt
from visumark_agent.environment.actions import Action, ActionType
from visumark_agent.environment.browser import BrowserEnv
from visumark_agent.parser.action_parser import ActionParser, ParseError
from visumark_agent.som.extractor import ElementExtractor
from visumark_agent.som.marker import SoMMarker
from visumark_agent.vlm.base import BaseVLM


@dataclass
class StepResult:
    """Record of a single agent step."""

    step: int
    action: Action | None
    observation: str = ""
    vlm_output: str = ""
    success: bool = False
    timestamp: float = field(default_factory=time.time)


@dataclass
class TaskResult:
    """Outcome of a full task execution."""

    success: bool
    answer: str | None = None
    total_steps: int = 0
    steps: list[StepResult] = field(default_factory=list)
    error: str | None = None


class VisuMarkAgent:
    """SoM-enhanced VLM web agent.

    Pipeline for each step:
    1. Fetch the current page screenshot
    2. Extract interactive elements and draw SoM overlays
    3. Send the annotated screenshot to the VLM with the task prompt
    4. Parse the VLM response into an Action
    5. Execute the action in the browser
    6. Repeat until task complete or max steps exceeded
    """

    def __init__(
        self,
        vlm: BaseVLM,
        browser: BrowserEnv,
        marker: SoMMarker | None = None,
        extractor: ElementExtractor | None = None,
        parser: ActionParser | None = None,
        *,
        max_steps: int = 30,
        step_timeout: float = 60.0,
        retry_on_error: bool = True,
        max_retries: int = 3,
        screenshot_dir: str | Path = "./data/screenshots",
    ):
        self.vlm = vlm
        self.browser = browser
        self.marker = marker or SoMMarker()
        self.extractor = extractor or ElementExtractor()
        self.parser = parser or ActionParser()
        self.max_steps = max_steps
        self.step_timeout = step_timeout
        self.retry_on_error = retry_on_error
        self.max_retries = max_retries
        self.screenshot_dir = Path(screenshot_dir)

    async def run_task(
        self,
        task: str,
        start_url: str,
        *,
        output_dir: str | Path | None = None,
    ) -> TaskResult:
        """Execute a task end-to-end.

        Args:
            task: Natural-language task description (e.g. "Search for flights to Paris").
            start_url: The starting URL.
            output_dir: If set, save step screenshots here for debugging.

        Returns:
            TaskResult with the outcome and step history.
        """
        result = TaskResult(success=False)
        screenshot_dir = Path(output_dir) if output_dir else self.screenshot_dir
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        await self.browser.start()
        try:
            await self.browser.goto(start_url)

            for step in range(1, self.max_steps + 1):
                logger.info(f"--- Step {step}/{self.max_steps} ---")
                step_result = await self._execute_step(
                    step=step,
                    task=task,
                    screenshot_dir=screenshot_dir,
                )
                result.steps.append(step_result)

                if step_result.action is None:
                    result.error = "Failed to produce a valid action"
                    break

                if step_result.action.action_type == ActionType.ANSWER:
                    result.success = True
                    result.answer = step_result.action.value
                    logger.success(f"Task completed: {result.answer}")
                    break

                if step_result.action.action_type == ActionType.FAIL:
                    result.error = step_result.action.value or "Agent declared failure"
                    logger.error(f"Agent failed: {result.error}")
                    break

            else:
                result.error = f"Reached max steps ({self.max_steps}) without completing the task"

        except Exception as exc:
            logger.exception(f"Unexpected error: {exc}")
            result.error = str(exc)
        finally:
            await self.browser.stop()

        result.total_steps = len(result.steps)
        return result

    async def _execute_step(
        self,
        step: int,
        task: str,
        screenshot_dir: Path,
    ) -> StepResult:
        """Run a single observe→reason→act cycle."""
        page = self.browser.page
        if page is None:
            return StepResult(step=step, action=None, observation="No page", success=False)

        # 1. tag DOM for later targeting
        await self.browser.tag_elements()

        # 2. screenshot & SoM annotation
        raw_screenshot = await self.browser.screenshot()
        elements = await self.extractor.extract(page)
        vw = self.browser.viewport["width"]
        vh = self.browser.viewport["height"]
        annotated = self.marker.annotate(raw_screenshot, elements, vw, vh)

        # save for debugging
        screenshot_path = screenshot_dir / f"step_{step:03d}.png"
        screenshot_path.write_bytes(annotated)

        # 3. build prompt & query VLM
        title = await page.title()
        url = page.url
        prompt = build_task_prompt(task, title, url, step)

        vlm_output = ""
        action: Action | None = None
        for retry in range(self.max_retries if self.retry_on_error else 1):
            try:
                response = self.vlm.generate(
                    prompt=SYSTEM_PROMPT + "\n\n" + prompt,
                    images=[annotated],
                )
                vlm_output = response.text
                action = self.parser.parse(vlm_output)
                break
            except ParseError as e:
                logger.warning(f"Parse error (retry {retry + 1}): {e}")
                vlm_output = str(e)
            except Exception as e:
                logger.warning(f"VLM error (retry {retry + 1}): {e}")
                vlm_output = str(e)
                if not self.retry_on_error:
                    break

        if action is None:
            return StepResult(
                step=step,
                action=None,
                observation=vlm_output,
                vlm_output=vlm_output,
                success=False,
            )

        # 4. execute action
        success = await self.browser.execute(action)

        return StepResult(
            step=step,
            action=action,
            observation=vlm_output,
            vlm_output=vlm_output,
            success=success,
        )
