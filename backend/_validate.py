"""Shared input validation used across the backend service layer.

The CLI enforces its own argparse-level checks, but the backend is the code
contract that TUI, plan import, and AI agents all call — so it validates input
here rather than relying on any one presentation layer. Every rejection raises
``errors.ValidationError`` (a ``PlannerError``), never a bare builtin.
"""

from __future__ import annotations

import datetime

from . import errors

_LIKE_ESCAPE = "\\"


def require_name(name: str | None) -> str:
    """Return ``name`` unchanged, or raise if it is missing/blank.

    Args:
        name: str — the entity name as supplied by the caller.
    Returns:
        str — the name (stripped only if it was whitespace-only is an error,
        so a valid name passes through verbatim).
    Raises:
        ValidationError: if name is ``None``, empty, or all whitespace.
    """
    if not name or not name.strip():
        raise errors.ValidationError("name is required and must not be blank")
    return name


def require_iso_date(value: str | None, field: str) -> str | None:
    """Validate ``value`` as an ISO date (YYYY-MM-DD); return it unchanged.

    Args:
        value: str | None — the date string; ``None`` means "unset" and passes.
        field: str — the field name for the error message.
    Returns:
        str | None — the value unchanged.
    Raises:
        ValidationError: if value is not None and not a valid ISO date.
    """
    if value is None:
        return None
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise errors.ValidationError(
            f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}") from None
    return value


def check_limit_offset(limit: int | None, offset: int | None,
                       *, resource: str = "list") -> None:
    """Reject negative pagination values.

    SQLite treats ``LIMIT -N`` as "no limit" and ``OFFSET -N`` as 0; Python
    slicing has its own negative semantics. Either way a negative value here
    signals a caller bug or a crafty user, so reject it explicitly instead of
    letting it silently change the result set.

    Raises:
        ValidationError: if limit or offset is negative.
    """
    if limit is not None and limit < 0:
        raise errors.ValidationError(f"{resource} limit must be >= 0, got {limit}")
    if offset is not None and offset < 0:
        raise errors.ValidationError(f"{resource} offset must be >= 0, got {offset}")


def escape_like(value: str, escape: str = _LIKE_ESCAPE) -> str:
    """Escape SQL LIKE wildcards so ``value`` matches literally.

    Args:
        value: str — user-provided pattern text (no wildcards intended).
        escape: str — the LIKE ESCAPE character.
    Returns:
        str — value with \\, % and _ escaped; use with ``LIKE ? ESCAPE '\'``.
    """
    return (value.replace(escape, escape + escape)
                 .replace("%", escape + "%")
                 .replace("_", escape + "_"))
