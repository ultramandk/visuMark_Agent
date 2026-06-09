"""Anthropic Claude provider — Claude 3.5/4 Sonnet, Opus, Haiku.

Uses the Anthropic Messages API directly (not OpenAI-compatible).
Supports vision input for Claude 3+ models.
"""

import base64
import time
from typing import Any

from loguru import logger

from visumark.core.types import Perception, ReasonerOutput, StepRecord
from visumark.reasoning.base import BaseReasoner


class AnthropicReasoner(BaseReasoner):
    """VLM reasoning via Anthropic Claude models."""

    def __init__(
        self,
        provider: str = "anthropic",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        max_retries: int = 3,
        **kwargs: Any,
    ):
        self._provider = provider
        self._model = model or "claude-sonnet-4-6"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries

        try:
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=0,
            )
        except ImportError:
            raise ImportError(
                "anthropic package required. Install with: pip install anthropic"
            )

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    async def reason(
        self,
        perception: Perception,
        task: str,
        history: list[StepRecord],
    ) -> ReasonerOutput:
        from visumark.reasoning.prompts.som_prompts import (
            SYSTEM_PROMPT,
            build_som_user_prompt,
        )

        system_msg = SYSTEM_PROMPT
        user_msg = build_som_user_prompt(task, perception, history)

        # Build content blocks
        content: list[dict] = [{"type": "text", "text": user_msg}]
        if perception.screenshot:
            b64 = base64.b64encode(perception.screenshot).decode("utf-8")
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64,
                },
            })

        messages = [{"role": "user", "content": content}]

        # Call API
        raw_text = await self._call_api(system_msg, messages)
        output = self._parse_response(raw_text)
        return output

    async def _call_api(self, system: str, messages: list[dict]) -> str:
        last_error = None
        for attempt in range(self._max_retries):
            try:
                resp = self._client.messages.create(
                    model=self._model,
                    system=system,
                    messages=messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                # Extract text from first content block
                for block in resp.content:
                    if block.type == "text":
                        return block.text
                return ""

            except Exception as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    break

        raise RuntimeError(
            f"Anthropic API call failed after {self._max_retries} attempts: {last_error}"
        )

    def _parse_response(self, raw_text: str) -> ReasonerOutput:
        from visumark.action.parser import ActionParser
        import re, json

        parser = ActionParser()
        thought = ""
        action = None
        try:
            json_match = re.search(r"\{[\s\S]*\}", raw_text)
            if json_match:
                obj = json.loads(json_match.group(0))
                thought = obj.get("thought", "")
            action = parser.parse(raw_text)
        except Exception as e:
            logger.warning(f"Failed to parse Claude response: {e}")
            thought = raw_text[:200]

        return ReasonerOutput(raw_text=raw_text, thought=thought, action=action)
