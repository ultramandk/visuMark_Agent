"""Mind2Web dataset loader.

Loads Mind2Web JSON task files and converts them into unified TaskInstance objects.

Data source: https://huggingface.co/datasets/osunlp/Mind2Web
Expected directory structure:
    data/mind2web/
    ├── train/           # 1,009 training tasks
    ├── test_task/       # 252 Cross-Task tasks
    ├── test_website/    # 177 Cross-Website tasks
    └── test_domain/     # 912 Cross-Domain tasks
"""

import json
from pathlib import Path

from loguru import logger

from visumark.dataset.base import BaseDataset, TaskInstance


class Mind2WebDataset(BaseDataset):
    """Mind2Web dataset — 2,350 tasks from 137 real-world websites.

    Supports the 4 standard splits:
        - train
        - test_cross_task
        - test_cross_website
        - test_cross_domain
    """

    SPLIT_MAP = {
        "train": "train",
        "test_cross_task": "test_task",
        "test_cross_website": "test_website",
        "test_cross_domain": "test_domain",
    }

    def __init__(
        self,
        data_dir: str | Path = "./data/mind2web",
        split: str = "test_cross_task",
        max_tasks: int | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.split = split
        self.max_tasks = max_tasks
        self.tasks: list[TaskInstance] = self._load(split)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, split: str) -> list[TaskInstance]:
        """Load JSON files for the given split."""
        dir_name = self.SPLIT_MAP.get(split)
        if dir_name is None:
            raise ValueError(
                f"Unknown split: '{split}'. Available: {list(self.SPLIT_MAP.keys())}"
            )

        split_dir = self.data_dir / dir_name
        if not split_dir.exists():
            raise FileNotFoundError(
                f"Mind2Web split directory not found: {split_dir}\n"
                f"Download the dataset from: https://huggingface.co/datasets/osunlp/Mind2Web"
            )

        json_files = sorted(split_dir.glob("*.json"))
        if not json_files:
            raise FileNotFoundError(f"No JSON files found in {split_dir}")

        tasks = []
        for fpath in json_files:
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                task = self._parse_instance(data)
                tasks.append(task)
            except Exception as e:
                logger.warning(f"Failed to load {fpath.name}: {e}")

            if self.max_tasks and len(tasks) >= self.max_tasks:
                break

        logger.info(
            f"Loaded {len(tasks)} tasks from Mind2Web/{split}"
            + (f" (limited to {self.max_tasks})" if self.max_tasks else "")
        )
        return tasks

    def _parse_instance(self, data: dict) -> TaskInstance:
        """Parse a single Mind2Web JSON instance into a TaskInstance."""
        # Extract start URL from annotations or metadata
        start_url = "about:blank"
        if "actions" in data and len(data["actions"]) > 0:
            # Try to get URL from the first action's raw HTML or metadata
            first_action = data["actions"][0]
            raw_html = first_action.get("raw_html", "")
            if raw_html:
                # Attempt to extract URL from HTML (heuristic)
                import re
                url_match = re.search(
                    r'(?:https?://)?(?:www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}',
                    raw_html
                )
                if url_match:
                    start_url = url_match.group(0)
                    if not start_url.startswith("http"):
                        start_url = "https://" + start_url

        return TaskInstance(
            task_id=data.get("annotation_id", ""),
            description=data.get("confirmed_task", ""),
            start_url=start_url,
            domain=data.get("domain", ""),
            website=data.get("website", ""),
            actions_gt=data.get("actions", []),
            metadata={
                "subdomain": data.get("subdomain", ""),
                "action_reprs": data.get("action_reprs", []),
            },
        )

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.tasks)

    def __getitem__(self, idx: int) -> TaskInstance:
        return self.tasks[idx]

    def get_splits(self) -> dict[str, list[TaskInstance]]:
        return {self.split: self.tasks}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict:
        """Compute basic dataset statistics."""
        domains = set()
        websites = set()
        total_actions = 0
        for t in self.tasks:
            if t.domain:
                domains.add(t.domain)
            if t.website:
                websites.add(t.website)
            if t.actions_gt:
                total_actions += len(t.actions_gt)

        return {
            "split": self.split,
            "total_tasks": len(self.tasks),
            "unique_domains": len(domains),
            "unique_websites": len(websites),
            "total_actions": total_actions,
            "avg_actions_per_task": total_actions / len(self.tasks) if self.tasks else 0,
        }
