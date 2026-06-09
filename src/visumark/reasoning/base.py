"""Abstract Reasoner interface for VLM/LLM model calling.

The Reasoner is the "brain" of the agent — it takes a Perception (what the
agent sees) plus the task and history, and produces a ReasonerOutput with
a predicted Action.
"""

from abc import ABC, abstractmethod

from visumark.core.types import Perception, ReasonerOutput, StepRecord


class BaseReasoner(ABC):
    """Abstract interface for model reasoning.

    Each provider (OpenAI, Anthropic, Qwen, Local) implements this interface.
    The prompt assembly (system prompt + task + history + perception) is
    handled by the concrete implementations, which select the appropriate
    prompt template based on perception mode (SoM visual vs HTML text).
    """

    @abstractmethod
    async def reason(
        self,
        perception: Perception,
        task: str,
        history: list[StepRecord],
    ) -> ReasonerOutput:
        """Send perception + task context to the model and return structured output.

        Args:
            perception: What the agent currently sees.
            task: The natural language task description.
            history: Previous step records (for context/memory).

        Returns:
            ReasonerOutput with the model's raw response, extracted thought,
            and parsed action.
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name (e.g. 'openai', 'qwen')."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The specific model being used (e.g. 'gpt-4o', 'qwen3-vl-8b-instruct')."""
        ...
