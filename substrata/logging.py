"""Logging bootstrap helpers for the Substrata package."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO", *, logger_name: str = "substrata") -> logging.Logger:
    normalized_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=normalized_level, format="%(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(logger_name)
    logger.setLevel(normalized_level)
    return logger


__all__ = ["configure_logging"]
