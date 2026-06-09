"""Dataset abstraction — unified interface for Mind2Web and custom task formats."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TaskInstance:
    """Unified task representation — works for Mind2Web and custom formats.

    Attributes:
        task_id: Unique identifier.
        description: Natural language task description (high-level goal).
        start_url: The starting URL for the task.
        domain: Domain category (e.g. "travel", "shopping").
        website: Website name (e.g. "aa.com", "amazon.com").
        actions_gt: Ground-truth action sequence (Mind2Web only).
        metadata: Arbitrary extra data.
    """

    task_id: str
    description: str
    start_url: str = "about:blank"
    domain: str = ""
    website: str = ""
    actions_gt: list[dict] | None = None
    metadata: dict = field(default_factory=dict)


class BaseDataset(ABC):
    """Abstract dataset interface."""

    @abstractmethod
    def __len__(self) -> int:
        """Number of tasks in this dataset/split."""
        ...

    @abstractmethod
    def __getitem__(self, idx: int) -> TaskInstance:
        """Get a single task instance."""
        ...

    @abstractmethod
    def get_splits(self) -> dict[str, list[TaskInstance]]:
        """Return all available splits as {split_name: [tasks]}."""
        ...
