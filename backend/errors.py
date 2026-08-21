"""Exception types raised by the backend.

All backend errors derive from :class:`PlannerError` so callers (CLI/TUI/tests)
can catch the family with a single ``except``. Each carries a ``message`` that is
safe to show to a human or an agent.
"""

from __future__ import annotations


class PlannerError(Exception):
    """Base class for all backend errors."""

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class NotFound(PlannerError):
    """A referenced entity does not exist."""

    def __init__(self, resource: str, id: object):
        self.resource = resource
        self.id = id
        super().__init__(f"{resource} {id!r} not found")


class ValidationError(PlannerError):
    """Invalid arguments or a CHECK/constraint violation."""


class Conflict(PlannerError):
    """A uniqueness or state conflict (e.g. a duplicate story link)."""