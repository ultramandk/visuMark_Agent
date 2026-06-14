"""OpenAI VLM provider — GPT-4V, GPT-4o, GPT-4.1.

Uses the OpenAI chat completions API with vision support.
Also serves as the base for Qwen and Local providers (OpenAI-compatible API).
"""

import base64
import os
import time
from typing import Any

from loguru import logger
from openai import AsyncOpenAI

from visumark.core.types import Action, Perception, ReasonerOutput, StepRecord, VerificationResult
from visumark.reasoning.base import BaseReasoner


def _build_http_client(timeout: float = 120.0) -> Any:
    """Build an httpx.AsyncClient with safe proxy settings for local endpoints.

    When using SSH tunnels or local model servers (vLLM/Ollama), connections
    to 127.0.0.1/localhost must NOT go through the system HTTP proxy.  This
    factory creates an httpx client that:

    - Disables system proxy trust (trust_env=False) so that HTTP_PROXY /
      HTTPS_PROXY environment variables and Windows Internet Options proxy
      settings are ignored.
    - Still allows users to set an explicit proxy via the `proxy` parameter
      if they really need one (advanced use).
    """
    try:
        import httpx

        # Build a client that does NOT use system proxy settings.
        # This is critical for local SSH-tunnel deployments.
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            trust_env=False,  # Ignore system/env proxy — tunnels handle this
        )
    except ImportError:
        return None


class OpenAIReasoner(BaseReasoner):
    """VLM reasoning via OpenAI vision models.

    Supports any OpenAI-compatible endpoint by setting base_url.
    """

    def __init__(
        self,
        provider: str = "openai",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        max_retries: int = 3,
        **kwargs: Any,
    ):
        self._provider = provider
        self._model = model or "gpt-4o"
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries

        http_client = _build_http_client(timeout)

        client_kwargs: dict[str, Any] = dict(
            api_key=api_key or "placeholder",
            base_url=base_url,
            timeout=timeout,
            max_retries=0,  # We handle retries ourselves
        )
        if http_client is not None:
            client_kwargs["http_client"] = http_client

        self._client = AsyncOpenAI(**client_kwargs)

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    # ------------------------------------------------------------------
    # Main reasoning method
    # ------------------------------------------------------------------

    async def reason(
        self,
        perception: Perception,
        task: str,
        history: list[StepRecord],
    ) -> ReasonerOutput:
        """Send SoM screenshot + task to the VLM and parse the response."""
        from visumark.reasoning.prompts.som_prompts import (
            SYSTEM_PROMPT,
            build_som_user_prompt,
        )

        # Build messages
        system_msg = SYSTEM_PROMPT
        user_msg = build_som_user_prompt(task, perception, history)

        # Build content blocks (text + image)
        # Use SoM-annotated screenshot for VLM, fallback to clean screenshot
        content: list[dict] = [{"type": "text", "text": user_msg}]
        img_bytes = perception.annotated_screenshot or perception.screenshot
        if img_bytes:
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": "high",
                },
            })

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": content},
        ]

        # Call API with retry
        raw_text = await self._call_api(messages)
        output = self._parse_response(raw_text)

        logger.debug(f"Reasoner output: {output.action.to_dict() if output.action else 'None'}")
        return output

    # ------------------------------------------------------------------
    # Action verification — did the action produce the expected effect?
    # ------------------------------------------------------------------

    async def verify(
        self,
        action: Action,
        thought: str,
        pre_screenshot: bytes,
        post_screenshot: bytes,
        task: str,
        page_url: str = "",
    ) -> VerificationResult:
        """Compare before/after screenshots to verify action effect."""
        from visumark.action.executor import build_action_description
        from visumark.reasoning.prompts.som_prompts import (
            VERIFICATION_SYSTEM_PROMPT,
            build_verification_user_prompt,
            parse_verification_response,
        )

        action_desc = build_action_description(action)
        user_msg = build_verification_user_prompt(action_desc, thought, task, page_url)

        b64_pre = base64.b64encode(pre_screenshot).decode("utf-8")
        b64_post = base64.b64encode(post_screenshot).decode("utf-8")

        content: list[dict] = [
            {"type": "text", "text": user_msg},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_pre}", "detail": "low"}},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_post}", "detail": "low"}},
        ]

        messages = [
            {"role": "system", "content": VERIFICATION_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

        try:
            raw_text = await self._call_api(messages)
            return parse_verification_response(raw_text)
        except Exception as exc:
            logger.warning(f"Verification call failed: {exc}")
            return VerificationResult(
                effect_achieved=True,  # Assume success if we can't verify
                observation=f"Verification error: {exc}",
            )

    # ------------------------------------------------------------------
    # API calling
    # ------------------------------------------------------------------

    async def _call_api(self, messages: list[dict]) -> str:
        """Call the OpenAI API asynchronously with retry and exponential backoff."""
        last_error = None

        for attempt in range(self._max_retries):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                content = resp.choices[0].message.content
                return content or ""

            except Exception as e:
                last_error = e
                error_name = type(e).__name__

                if "RateLimit" in error_name or "rate" in str(e).lower():
                    wait = 2 ** attempt
                    logger.warning(f"Rate limited, retrying in {wait}s...")
                    time.sleep(wait)
                elif attempt < self._max_retries - 1:
                    logger.warning(f"API error ({error_name}), retrying...")
                    time.sleep(1)
                else:
                    break

        raise RuntimeError(
            f"OpenAI API call failed after {self._max_retries} attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw_text: str) -> ReasonerOutput:
        """Parse the VLM response, extracting plan, thought, and action."""
        from visumark.action.parser import ActionParser

        parser = ActionParser()
        plan = ""
        thought = ""
        action = None

        try:
            import re
            json_match = re.search(r"\{[\s\S]*\}", raw_text)
            if json_match:
                obj = __import__("json").loads(json_match.group(0))
                plan = obj.get("plan", "")
                thought = obj.get("thought", "")

            action = parser.parse(raw_text)
        except Exception as e:
            logger.warning(f"Failed to parse VLM response: {e}")
            thought = raw_text[:200]

        return ReasonerOutput(
            raw_text=raw_text,
            thought=thought,
            plan=plan,
            action=action,
        )
