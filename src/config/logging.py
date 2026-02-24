"""Loguru logging configuration."""

from __future__ import annotations

import sys

from loguru import logger


def setup_logging(debug: bool = False) -> None:
    """Configure loguru logger for the application."""
    # Remove default handler
    logger.remove()

    # Console handler
    log_level = "DEBUG" if debug else "INFO"
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File handler (rotate daily, keep 7 days)
    logger.add(
        "logs/brainstorm_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} - "
            "{message}"
        ),
    )

    logger.info("Logging initialized (level={})", log_level)
