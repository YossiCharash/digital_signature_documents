"""Logging configuration and utilities."""

import logging
import sys

from app.config import settings


def setup_logger(name: str | None = None) -> logging.Logger:
    """Set up and return a configured logger."""
    logger = logging.getLogger(name or __name__)
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


# Default logger instance
logger = setup_logger("document_delivery")
