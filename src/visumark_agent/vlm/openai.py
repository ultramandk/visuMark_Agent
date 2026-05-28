"""OpenAI-compatible VLM provider (GPT-4V, GPT-4o, etc.)."""

import base64
import time
from pathlib import Path

import openai

from visumark_agent.vlm.base import BaseVLM, VLMResponse


class OpenAIVLM(BaseVLM):
    """VLM backed by OpenAI vision models or any OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ):
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.client = openai.OpenAI(
            api_key=api_key or "placeholder",
            base_url=base_url,
            timeout=timeout,
        )

    def generate(
        self,
        prompt: str,
        images: list[bytes] | None = None,
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> VLMResponse:
        content: list[dict] = [{"type": "text", "text": prompt}]
        if images:
            for img in images:
                b64 = base64.b64encode(img).decode("utf-8")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
                })

        messages = [{"role": "user", "content": content}]
        return self._call_api(messages, temperature, max_tokens)

    def generate_multimodal(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> VLMResponse:
        return self._call_api(messages, temperature, max_tokens)

    def _call_api(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
    ) -> VLMResponse:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return VLMResponse(
                    text=resp.choices[0].message.content or "",
                    raw_response=resp,
                )
            except openai.RateLimitError as e:
                last_error = e
                time.sleep(2 ** attempt)
            except openai.APIError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(1)
            except Exception as e:
                last_error = e
                break

        raise RuntimeError(
            f"OpenAI VLM call failed after {self.max_retries} attempts: {last_error}"
        )
