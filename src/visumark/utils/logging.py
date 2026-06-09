"""Structured logging configuration via loguru."""

import sys
from pathlib import Path

from loguru import logger


def setup_logger(
    level: str = "INFO",
    log_file: str | Path | None = None,
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> None:
    """Configure loguru logger with console and optional file output.

    Args:
        level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
        log_file: Path to a file for persistent logs. Disabled when None.
        rotation: When to rotate the log file (size or time string).
        retention: How long to keep rotated logs.
    """
    logger.remove()

    # Console output — colored, compact format
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File output — detailed, machine-readable
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_path),
            level="DEBUG",
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
                "{level: <8} | "
                "{name}:{function}:{line} | "
                "{message}"
            ),
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
        )

    logger.debug(f"Logger initialized (level={level})")
