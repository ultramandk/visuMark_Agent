"""Local model provider — Ollama, vLLM, or any OpenAI-compatible local endpoint.

For Ollama:
    base_url = http://localhost:11434/v1
    model = qwen3-vl:8b  (or any model pulled in Ollama)

For vLLM:
    base_url = http://localhost:8000/v1
    model = Qwen/Qwen3-VL-8B-Instruct
"""

from visumark.reasoning.providers.openai import OpenAIReasoner


class LocalReasoner(OpenAIReasoner):
    """Local VLM reasoning via OpenAI-compatible endpoint (Ollama/vLLM).

    Requires:
        - Ollama: `ollama pull qwen3-vl:8b`
        - vLLM:  `vllm serve Qwen/Qwen3-VL-8B-Instruct`
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("provider", "local")
        kwargs.setdefault("model", "qwen3-vl:8b")
        kwargs.setdefault("base_url", "http://localhost:11434/v1")
        kwargs.setdefault("api_key", "not-needed")
        super().__init__(**kwargs)
