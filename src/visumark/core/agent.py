"""VisuMark Agent — main ReAct loop: observe → reason → act → verify → repeat.

Orchestrates the full pipeline:
    1. Perception: screenshot → SoM annotation → element list → DOM bridge
    2. Reasoning: annotated screenshot + task → VLM → structured action
    3. Action: parse → execute in browser (live) or record (offline eval)
    4. Verification: compare before/after screenshots via VLM to check effect
    5. Loop: continue until ANSWER, FAIL, or max_steps

This is the single entry point for both live task execution and
Mind2Web offline evaluation. The only difference is the environment
(LiveEnvironment vs OfflineEnvironment) and whether a comparator
callback is attached.
"""

import time
from pathlib import Path

from loguru import logger

from visumark.core.types import (
    Action,
    ActionType,
    ReasonerOutput,
    StepRecord,
    TaskRecord,
    VerificationResult,
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
        verify_actions: bool = True,
        max_verify_retries: int = 1,
    ):
        self.perceptor = perceptor
        self.reasoner = reasoner
        self.env = env
        self.max_steps = max_steps
        self.retry_on_error = retry_on_error
        self.max_retries = max_retries
        self.screenshot_dir = Path(screenshot_dir)
        self.verify_actions = verify_actions
        self.max_verify_retries = max_verify_retries

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
                    history=result.steps,
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
            # Clean up screenshots after task completes
            self._cleanup_screenshots()

        result.total_steps = len(result.steps)
        await callbacks.on_done(result)
        return result

    def _cleanup_screenshots(self) -> None:
        """Clear all screenshots from the screenshot directory after task completion."""
        if not self.screenshot_dir.exists():
            return
        try:
            for f in self.screenshot_dir.iterdir():
                if f.is_file():
                    f.unlink()
            logger.debug(f"Cleared screenshots in: {self.screenshot_dir}")
        except Exception as exc:
            logger.debug(f"Failed to clear screenshots: {exc}")

    # ------------------------------------------------------------------
    # Action verification — post-action before/after comparison
    # ------------------------------------------------------------------

    async def _verify_action(
        self,
        action: Action,
        thought: str,
        pre_screenshot: bytes,
        task: str,
        page_url: str = "",
    ) -> tuple[VerificationResult | None, bytes]:
        """Verify whether the executed action achieved its intended effect.

        Takes a post-action screenshot and asks the VLM to compare
        before (pre_screenshot, SoM-annotated) vs after (raw).

        Returns:
            (verification_result, post_screenshot) tuple.
            post_screenshot is always returned (even if verification fails)
            so the frontend can display before/after comparison.
        """
        empty = b""
        if not self.env.is_live:
            return None, empty

        page = self.env.page if hasattr(self.env, "page") else None
        if page is None:
            return None, empty

        # Wait for page to actually finish rendering before screenshot.
        # Uses the same layered approach as perception: DOM ready → network
        # idle → poll for visible content → extra settle for images/fonts.
        # Shorter timeouts than perception since the page should already be
        # mostly loaded from the execute() post-action wait.
        if hasattr(self.env, "wait_for_page_ready"):
            try:
                await self.env.wait_for_page_ready(
                    settle_ms=800,       # shorter settle than perception's 2000ms
                    min_body_text=30,    # lower bar — just need the page to exist
                    max_polls=8,         # max ~4s wait vs perception's ~10s
                )
            except Exception:
                pass
        else:
            try:
                await page.wait_for_timeout(800)
            except Exception:
                pass

        # Take post-action screenshot
        try:
            post_screenshot = await self.env.screenshot()
        except Exception as exc:
            logger.warning(f"Failed to take post-action screenshot: {exc}")
            return None, empty

        # Guard against blank post-screenshot (page crashed)
        from visumark.utils.image import is_blank_screenshot

        if is_blank_screenshot(post_screenshot, variance_threshold=20.0):
            return VerificationResult(
                effect_achieved=False,
                observation="Post-action screenshot is blank — page may have crashed",
                should_retry=False,
            ), post_screenshot

        try:
            result = await self.reasoner.verify(
                action=action,
                thought=thought,
                pre_screenshot=pre_screenshot,
                post_screenshot=post_screenshot,
                task=task,
                page_url=page_url,
            )
            return result, post_screenshot
        except Exception as exc:
            logger.warning(f"Verification VLM call failed: {exc}")
            return None, post_screenshot

    # ------------------------------------------------------------------
    # Single step
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        step: int,
        task_description: str,
        history: list,
    ) -> StepRecord:
        """Run a single observe → reason → act cycle.

        Returns a StepRecord with full details for logging and evaluation.
        """
        t0 = time.time()

        # 1. PERCEIVE
        perception, bridge = await self.perceptor.perceive(self.env)

        # Save screenshots for debugging (both clean and SoM-annotated)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        if perception.screenshot:
            path_clean = self.screenshot_dir / f"step_{step:03d}_clean.jpg"
            path_clean.write_bytes(perception.screenshot)
        if perception.annotated_screenshot:
            path_anno = self.screenshot_dir / f"step_{step:03d}_som.jpg"
            path_anno.write_bytes(perception.annotated_screenshot)

        # 2. REASON (with retry)
        reasoner_output = None
        for retry in range(self.max_retries if self.retry_on_error else 1):
            try:
                reasoner_output = await self.reasoner.reason(
                    perception,
                    task_description,
                    history,  # Pass history so VLM knows what failed
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
                reasoner_output=reasoner_output or ReasonerOutput(raw_text=""),
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

        # 4. VERIFY — compare before/after to check if the action worked
        verification = None
        post_screenshot = None
        if (
            self.verify_actions
            and action is not None
            and not action.is_terminal
            and self.env.is_live
            and perception.annotated_screenshot
        ):
            pre_img = perception.annotated_screenshot
            for v_retry in range(self.max_verify_retries + 1):
                verification, post_screenshot = await self._verify_action(
                    action=action,
                    thought=reasoner_output.thought,
                    pre_screenshot=pre_img,
                    task=task_description,
                    page_url=perception.page_url,
                )
                if verification is None:
                    break  # Technical failure — skip verification

                if verification.effect_achieved:
                    logger.debug(f"Verification OK: {verification.observation[:120]}")
                    break

                logger.warning(
                    f"Verification FAILED [{v_retry + 1}]: {verification.observation[:120]}"
                )

                # 4a. ROLLBACK — undo the failed action's side effects
                if verification.rollback_action is not None:
                    logger.info(
                        f"Rolling back: {verification.rollback_action.to_dict()}"
                    )
                    rollback_ok = await self.executor.execute(
                        verification.rollback_action, self.env, bridge
                    )
                    if rollback_ok:
                        logger.debug("Rollback OK")
                        # Brief settle after rollback
                        try:
                            page = self.env.page if hasattr(self.env, "page") else None
                            if page:
                                await page.wait_for_timeout(400)
                        except Exception:
                            pass

                # 4b. RETRY — try the alternative action
                if verification.should_retry and verification.retry_action:
                    retry_action = verification.retry_action

                    # ── Stale element guard: if the page navigated, element IDs
                    #     from the BEFORE screenshot no longer point to the same
                    #     elements.  Replace element-based retries with press Enter
                    #     which is the safest non-element action after navigation.
                    try:
                        current_url = await self.env.get_page_url()
                    except Exception:
                        current_url = ""
                    url_changed = current_url and current_url != perception.page_url

                    if url_changed and retry_action.element_id is not None:
                        logger.warning(
                            f"URL changed ({perception.page_url} → {current_url}), "
                            f"element #{retry_action.element_id} is stale — "
                            f"replacing retry with press Enter"
                        )
                        retry_action = Action(
                            action_type=ActionType.PRESS, value="Enter"
                        )

                    logger.info(
                        f"Retrying with: {retry_action.to_dict()}"
                    )
                    retry_ok = await self.executor.execute(
                        retry_action, self.env, bridge
                    )
                    if retry_ok:
                        action = retry_action
                        success = True
                else:
                    break  # No retry suggested — move on

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
            verification=verification,
            post_screenshot=post_screenshot,
        )
