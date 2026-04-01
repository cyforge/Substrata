"""Typed exceptions for Substrata foundation modules."""

from __future__ import annotations


class SubstrataError(Exception):
    """Base exception for project-specific errors."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ConfigurationError(SubstrataError):
    """Raised when runtime configuration is missing or invalid."""


class ValidationError(SubstrataError):
    """Raised when caller-provided data does not pass validation."""


class IntegrationError(SubstrataError):
    """Raised when an external dependency or integration fails."""


__all__ = [
    "ConfigurationError",
    "IntegrationError",
    "SubstrataError",
    "ValidationError",
]
