"""Declarative parent->child chain model for the TUI browser.

A *chain* is a directed (parent, child) pair of entity kinds the browser can
show directly. Each chain maps to a resolver function ``(conn, parent_id) ->
list[child_row]`` that fetches the child rows for a given parent DB id. The
three-pane layout (stories 71+) consumes these chains to decide which kinds the
lower pane and detail pane may display for a selected parent row.

The valid direct chains are:

    project -> epic       project -> story
    milestone -> epic
    epic -> story
    iteration -> story
    group -> story
    workflow -> state
    label -> story
    member -> story
    story -> task

Multi-hop navigation (e.g. reaching an epic from a label) is achieved by
drilling from a story to its parent epic, not via a dedicated chain — the
``story -> epic`` parent hop is out of scope for this table.

Nothing in this module touches the layout; it is the data model + resolver
only. Child rows are the same model objects the backend ``list_*`` functions
return, so the caller can render them with ``ENTITY_COLUMNS``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from backend import epics, groups, milestones, stories, tasks, workflows

# Resolver: fetch the child rows for a parent identified by ``parent_id``.
Resolver = Callable[[sqlite3.Connection, int], list[Any]]


def _story_resolver(*, project_id=None, epic_id=None, iteration_id=None,
                    group_id=None, owner_id=None, label_id=None) -> Resolver:
    """Build a resolver that filters stories by exactly one parent filter."""
    def resolve(conn: sqlite3.Connection, parent_id: int) -> list[Any]:
        kw: dict[str, int] = {}
        if project_id is not None:
            kw["project_id"] = parent_id
        if epic_id is not None:
            kw["epic_id"] = parent_id
        if iteration_id is not None:
            kw["iteration_id"] = parent_id
        if group_id is not None:
            kw["group_id"] = parent_id
        if owner_id is not None:
            kw["owner_id"] = parent_id
        if label_id is not None:
            kw["label_id"] = parent_id
        return stories.list_stories(conn, **kw)

    return resolve


# Parent/child pairs, each with the resolver that returns the child rows.
CHAINS: dict[tuple[str, str], Resolver] = {
    # project -> epic
    ("project", "epic"):
        lambda conn, pid: epics.list_epics(conn, project_id=pid),
    # project -> story
    ("project", "story"):
        _story_resolver(project_id=True),
    # milestone -> epic
    ("milestone", "epic"):
        lambda conn, pid: milestones.list_milestone_epics(conn, pid),
    # epic -> story
    ("epic", "story"):
        lambda conn, pid: epics.list_epic_stories(conn, pid),
    # iteration -> story
    ("iteration", "story"):
        _story_resolver(iteration_id=True),
    # group -> story
    ("group", "story"):
        lambda conn, pid: groups.list_group_stories(conn, pid),
    # workflow -> state
    ("workflow", "workflow_state"):
        lambda conn, pid: workflows.list_workflow_states(conn, pid),
    # label -> story
    ("label", "story"):
        _story_resolver(label_id=True),
    # member -> story (via story_owner)
    ("member", "story"):
        _story_resolver(owner_id=True),
    # story -> task
    ("story", "task"):
        lambda conn, pid: tasks.list_tasks(conn, pid),
}


def resolve_children(conn: sqlite3.Connection, parent_entity: str,
                     parent_id: int, child_entity: str) -> list[Any]:
    """Return the child rows of ``parent_entity`` for ``parent_id``.

    If ``(parent_entity, child_entity)`` is not a valid chain, returns an empty
    list (the browser simply has nothing to show for that pair).
    """
    resolver = CHAINS.get((parent_entity, child_entity))
    if resolver is None:
        return []
    return resolver(conn, parent_id)


def valid_children(parent_entity: str) -> list[str]:
    """Return the child entity kinds reachable from ``parent_entity`` (ordered).

    Unknown parents yield ``[]``.
    """
    return sorted(child for (parent, child) in CHAINS if parent == parent_entity)
