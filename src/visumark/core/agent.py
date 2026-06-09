"""VisuMark Agent — main ReAct loop: observe → reason → act → repeat.

Orchestrates the full pipeline:
    1. Perception: screenshot → SoM annotation → element list → DOM bridge
    2. Reasoning: annotated screenshot + task → VLM → structured action
    3. Action: parse → execute in browser (live) or record (offline eval)
    4. Loop: continue until ANSWER, FAIL, or max_steps

This is the single entry point for both live task execution and
Mind2Web offline evaluation. The only difference is the environment
(LiveEnvironment vs OfflineEnvironment) and whether a comparator
callback is attached.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from visumark.core.types import (
    ActionType,
    StepRecord,
    TaskRecord,
)
from visumark.environment.base import BaseEnvironment
from visumark.perception.base import BasePerceptor
from visumark.perception.dom_bridge import DOMBridge
from visumark.reasoning.base import BaseReasoner
from visumark.action.parser import ActionParser, ParseError
from visumark.action.executor import ActionExecutor, build_target_label
from visumark.dataset.base import TaskInstance

# ---------------------------------------------------------------------------
# Callback protocol
# ---------------------------------------------------------------------------

class StepCallbacks:
    """Hooks invoked after each agent step.

    Used for:
        - WebSocket streaming (push step data to frontend)
        - Saving screenshots to disk
        - Mind2Web evaluation (compare prediction vs ground truth)
    """

    async def on_step(
        self,
        record: StepRecord,
        bridge: DOMBridge,
    ) -> None:
        """Called after each step completes."""
        pass

    async def on_done(self, record: TaskRecord) -> None:
        """Called when the task finishes."""
        pass


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    """ReAct-mode web agent with SoM visual grounding.

    Usage:
        agent = Agent(config)
        result = await agent.run(task)
    """

    def __init__(
        self,
        perceptor: BasePerceptor,
        reasoner: BaseReasoner,
        env: BaseEnvironment,
        *,
        max_steps: int = 30,
        retry_on_error: bool = True,
        max_retries: int = 3,
        screenshot_dir: str | Path = "./data/screenshots",
    ):
        self.perceptor = perceptor
        self.reasoner = reasoner
        self.env = env
        self.max_steps = max_steps
        self.retry_on_error = retry_on_error
        self.max_retries = max_retries
        self.screenshot_dir = Path(screenshot_dir)

        self.parser = ActionParser()
        self.executor = ActionExecutor()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        task: TaskInstance,
        callbacks: StepCallbacks | None = None,
    ) -> TaskRecord:
        """Execute a task from start to finish.

        Args:
            task: Task description and metadata.
            callbacks: Optional hooks for streaming, saving, evaluation.

        Returns:
            TaskRecord with full step history and outcome.
        """
        callbacks = callbacks or StepCallbacks()
        result = TaskRecord(
            task_id=task.task_id,
            task_description=task.description,
            success=False,
        )

        await self.env.start(task.start_url)
        logger.info(f"Agent started — task: {task.description[:80]}")

        try:
            for step_num in range(1, self.max_steps + 1):
                logger.info(f"--- Step {step_num}/{self.max_steps} ---")

                record = await self._execute_step(
                    step=step_num,
                    task_description=task.description,
                )
                result.steps.append(record)

                # Callback
                bridge = DOMBridge().build_from_elements(
                    record.perception.elements
                )
                await callbacks.on_step(record, bridge)

                # Terminal check
                if record.action is None:
                    result.error = "Failed to produce a valid action"
                    break

                if record.action.action_type == ActionType.ANSWER:
                    result.success = True
                    result.answer = record.action.value
                    logger.success(f"Task completed: {result.answer}")
                    break

                if record.action.action_type == ActionType.FAIL:
                    result.error = record.action.value or "Agent declared failure"
                    logger.error(f"Agent failed: {result.error}")
                    break

            else:
                result.error = (
                    f"Reached max steps ({self.max_steps}) without completing the task"
                )
                logger.warning(result.error)

        except Exception as exc:
            logger.exception(f"Unexpected error: {exc}")
            result.error = str(exc)

        finally:
            await self.env.stop()

        result.total_steps = len(result.steps)
        await callbacks.on_done(result)
        return result

    # ------------------------------------------------------------------
    # Single step
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        step: int,
        task_description: str,
    ) -> StepRecord:
        """Run a single observe → reason → act cycle.

        Returns a StepRecord with full details for logging and evaluation.
        """
        t0 = time.time()

        # 1. PERCEIVE
        perception, bridge = await self.perceptor.perceive(self.env)

        # Save screenshot for debugging
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        if perception.screenshot:
            path = self.screenshot_dir / f"step_{step:03d}.png"
            path.write_bytes(perception.screenshot)

        # 2. REASON (with retry)
        reasoner_output = None
        for retry in range(self.max_retries if self.retry_on_error else 1):
            try:
                reasoner_output = await self.reasoner.reason(
                    perception,
                    task_description,
                    [],  # History not passed to VLM yet (future: add memory)
                )
                break
            except ParseError as e:
                logger.warning(f"Parse error (retry {retry + 1}): {e}")
            except Exception as e:
                logger.warning(f"Reasoner error (retry {retry + 1}): {e}")
                if not self.retry_on_error:
                    break

        if reasoner_output is None:
            return StepRecord(
                step=step,
                perception=perception,
                reasoner_output=reasoner_output or __import__("visumark.core.types").ReasonerOutput(raw_text=""),
                action=None,
                success=False,
            )

        action = reasoner_output.action

        # 3. ACT
        success = False
        if self.env.is_live and action is not None:
            success = await self.executor.execute(action, self.env, bridge)
        elif action is not None:
            success = True  # Offline mode: always "succeeds" (no real execution)

        # Build target label for display
        target_label = ""
        if action:
            target_label = build_target_label(action, bridge)

        elapsed = time.time() - t0
        logger.debug(
            f"Step {step}: {target_label} "
            f"(success={success}, {elapsed:.1f}s)"
        )

        return StepRecord(
            step=step,
            perception=perception,
            reasoner_output=reasoner_output,
            action=action,
            success=success,
            element_correct=None,   # Filled by evaluation callback
            operation_correct=None,  # Filled by evaluation callback
        )
