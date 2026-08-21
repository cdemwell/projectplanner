"""Exception types raised by the backend.

All backend errors derive from :class:`PlannerError` so callers (CLI/TUI/tests)
can catch the family with a single ``except``. Each carries a ``message`` that is
safe to show to a human or an agent.
"""

from __future__ import annotations


class PlannerError(Exception):
    """Base class for all backend errors.

    All backend errors derive from this class so callers can catch them
    collectively.

    Attributes:
        message: str — a human-readable explanation of the error.
    """

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class NotFound(PlannerError):
    """Raised when a referenced entity does not exist in the database.

    Attributes:
        resource: str — the name of the entity type (e.g. 'Story').
        id: object — the identifier of the missing entity.
    """

    def __init__(self, resource: str, id: object):
        self.resource = resource
        self.id = id
        super().__init__(f"{resource} {id!r} not found")


class ValidationError(PlannerError):
    """Raised on invalid arguments or a database CHECK/constraint violation."""


class Conflict(PlannerError):
    """Raised on uniqueness or state conflicts, such as duplicate story links."""