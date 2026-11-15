"""Shared temporal and authorization policy constants."""

from datetime import date

BLIND_START = date(2025, 1, 1)


class DateBoundaryError(ValueError):
    """A request crosses into held-out dates."""


class AuthorizationError(PermissionError):
    """The caller is not authorized to request a final-test opening."""
