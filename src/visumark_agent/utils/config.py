"""Configuration loader with env-var interpolation."""

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate_env(value: Any) -> Any:
    """Replace ${VAR} placeholders in string values with environment variables."""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            return os.environ.get(m.group(1), "")
        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def load_config(path: str | Path = "config/config.yaml") -> dict[str, Any]:
    """Load and resolve a YAML configuration file."""
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return _interpolate_env(config)
