"""Qwen-VL provider — 通义千问视觉模型 (Qwen3-VL-8B-Instruct, Qwen-VL-Max).

Uses the DashScope OpenAI-compatible API endpoint.
This is the PRIMARY provider per the project proposal.
"""

from visumark.reasoning.providers.openai import OpenAIReasoner


class QwenReasoner(OpenAIReasoner):
    """Qwen VLM reasoning via DashScope OpenAI-compatible API.

    Models: qwen3-vl-8b-instruct, qwen-vl-max, qwen-vl-plus
    Endpoint: https://dashscope.aliyuncs.com/compatible-mode/v1
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("provider", "qwen")
        kwargs.setdefault("model", "qwen3-vl-8b-instruct")
        kwargs.setdefault("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        super().__init__(**kwargs)
