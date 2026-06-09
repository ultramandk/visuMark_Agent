"""Configuration loader with environment-variable interpolation.

Loads YAML config files and replaces ${ENV_VAR} placeholders with actual
environment variable values. Supports nested dicts and lists.
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate_env(value: Any) -> Any:
    """Recursively replace ${VAR} placeholders with environment variable values."""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var_name = m.group(1)
            return os.environ.get(var_name, "")

        return _ENV_PATTERN.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(v) for v in value]
    return value


def load_config(path: str | Path = "config/config.yaml") -> dict[str, Any]:
    """Load and resolve a YAML configuration file.

    Args:
        path: Path to the YAML config file (relative to project root).

    Returns:
        Dict with all ${ENV} placeholders resolved.

    Raises:
        FileNotFoundError: If the config file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
    """
    config_path = Path(path)
    if not config_path.is_absolute():
        # Resolve relative to the project root (where config/ lives)
        config_path = Path.cwd() / config_path

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if config is None:
        return {}

    return _interpolate_env(config)


def load_models_config(path: str | Path = "config/models.yaml") -> dict[str, Any]:
    """Load the model registry configuration.

    Args:
        path: Path to models.yaml.

    Returns:
        Dict with provider registrations.
    """
    return load_config(path)


def merge_configs(*configs: dict[str, Any]) -> dict[str, Any]:
    """Merge multiple config dicts, with later dicts overriding earlier ones.

    Only top-level keys are merged; nested dicts are replaced, not deep-merged.
    """
    merged: dict[str, Any] = {}
    for c in configs:
        merged.update(c)
    return merged
