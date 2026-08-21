"""Plain dataclasses for each core entity (local, trimmed shapes).

These are the return type of every backend ``get_*``/``list_*`` function. They are
*not* ORM objects — they hold a snapshot of a row. :class:`Model.from_row` maps a
``sqlite3.Row`` (by column name) onto the matching dataclass fields, ignoring any
extra columns the query returned.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from dataclasses import fields as _dc_fields
from typing import Any


@dataclass
class Model:
    """Base: provides ``from_row`` to build a dataclass from a ``sqlite3.Row``."""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Model:
        """Build a dataclass from a ``sqlite3.Row``.

        Maps row columns by name to the matching dataclass fields, ignoring
        any extra columns returned by the query.

        Args:
            row: The sqlite3.Row to map.

        Returns:
            Model: An instance of the dataclass.
        """
        names = cls._field_names()
        kwargs = {k: row[k] for k in row.keys() if k in names}
        return cls(**kwargs)  # type: ignore[arg-type]

    @classmethod
    def _field_names(cls) -> set[str]:
        return {f.name for f in _dc_fields(cls)}


# People ------------------------------------------------------------------ #
@dataclass
class Member(Model):
    """Snapshot of a member row.

    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    name: str
    mention_name: str
    created_at: str


@dataclass
class Group(Model):
    """Snapshot of a group row.

    Archived is stored as an integer (0/1).
    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    name: str
    description: str
    archived: int
    created_at: str


# Workflows --------------------------------------------------------------- #
@dataclass
class Workflow(Model):
    """Snapshot of a workflow row.

    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    name: str
    default_state_id: int | None
    created_at: str


@dataclass
class WorkflowState(Model):
    """Snapshot of a workflow state row.

    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    workflow_id: int
    name: str
    type: str
    position: float
    created_at: str


# Planning containers ----------------------------------------------------- #
@dataclass
class Project(Model):
    """Snapshot of a project row.

    Archived is stored as an integer (0/1).
    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    name: str
    description: str
    abbreviation: str
    color: str
    archived: int
    created_at: str


@dataclass
class Label(Model):
    """Snapshot of a label row.

    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    name: str
    color: str
    description: str
    created_at: str


@dataclass
class Milestone(Model):
    """Snapshot of a milestone row.

    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    name: str
    description: str
    state: str
    created_at: str
    completed_at: str | None


@dataclass
class Epic(Model):
    """Snapshot of an epic row.

    Timestamps are ISO-8601 UTC strings.
    FKs to milestones and projects are nullable.
    """
    id: int
    name: str
    description: str
    state: str
    milestone_id: int | None
    project_id: int | None
    created_at: str
    completed_at: str | None


@dataclass
class Iteration(Model):
    """Snapshot of an iteration row.

    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    name: str
    description: str
    status: str
    start_date: str | None
    end_date: str | None
    created_at: str


# Stories ----------------------------------------------------------------- #
@dataclass
class Story(Model):
    """Snapshot of a story row.

    Timestamps are ISO-8601 UTC strings.
    FKs to workflow state, epic, iteration, project, group,
    and requester are nullable.
    """
    id: int
    name: str
    description: str
    story_type: str
    workflow_state_id: int | None
    epic_id: int | None
    iteration_id: int | None
    project_id: int | None
    group_id: int | None
    requested_by_id: int | None
    deadline: str | None
    position: float
    created_at: str
    updated_at: str
    completed_at: str | None


# Child entities ---------------------------------------------------------- #
@dataclass
class Task(Model):
    """Snapshot of a task row.

    Complete is stored as an integer (0/1).
    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    story_id: int
    description: str
    complete: int
    position: float
    created_at: str
    completed_at: str | None


@dataclass
class StoryComment(Model):
    """Snapshot of a story comment row.

    Timestamps are ISO-8601 UTC strings.
    FKs to author and parent are nullable.
    """
    id: int
    story_id: int
    author_id: int | None
    text: str
    parent_id: int | None
    created_at: str
    updated_at: str


@dataclass
class StoryLink(Model):
    """Snapshot of a story link row.

    Timestamps are ISO-8601 UTC strings.
    """
    id: int
    subject_story_id: int
    verb: str
    object_story_id: int
    created_at: str


# --- Convenience: a Story with its relations expanded, for detail views ---- #
@dataclass
class StoryDetail:
    """A story plus its related rows, assembled for detail views/CLI output."""

    story: Story
    owners: list[Member]
    labels: list[Label]
    tasks: list[Task]
    workflow_state: WorkflowState | None

    def to_dict(self) -> dict[str, Any]:
        """Return a dictionary representation of the story detail.

        Used for JSON serialization or API responses.

        Returns:
            dict: Nested dictionary containing the story and its relations.
        """
        import dataclasses
        return {
            "story": dataclasses.asdict(self.story),
            "owners": [dataclasses.asdict(o) for o in self.owners],
            "labels": [dataclasses.asdict(lb) for lb in self.labels],
            "tasks": [dataclasses.asdict(t) for t in self.tasks],
            "workflow_state": dataclasses.asdict(self.workflow_state) if self.workflow_state else None,
        }
