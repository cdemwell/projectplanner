"""Full-text search across the major entities via FTS5.

FTS5 tables (``story_fts`` etc.) and their sync triggers are created in the v2
schema migration (``db.py``); this module only queries them. Results are ranked
by FTS5's bm25 score and carry their entity type + id + name.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Maps an entity name -> (fts table, source table, display label).
_ENTITIES = {
    "story": ("story_fts", "story", "story"),
    "epic": ("epic_fts", "epic", "epic"),
    "project": ("project_fts", "project", "project"),
    "milestone": ("milestone_fts", "milestone", "milestone"),
    "iteration": ("iteration_fts", "iteration", "iteration"),
    "label": ("label_fts", "label", "label"),
}


@dataclass
class SearchResult:
    """A result from an FTS5 search.

    Attributes:
        entity: str — the type of entity (e.g., "story", "project").
        id: int — the entity id.
        name: str — the entity name.
        description: str — the entity description.
        rank: float — the relevance score (higher is better).
    """
    entity: str
    id: int
    name: str
    description: str
    rank: float

    def to_dict(self) -> dict:
        """Convert the result to a dictionary.

        Returns:
            dict — result fields.
        """
        return {
            "entity": self.entity, "id": self.id, "name": self.name,
            "description": self.description, "rank": self.rank,
        }


def search(conn: sqlite3.Connection, query: str, *, entity: str | None = None) -> list[SearchResult]:
    """Search ``name`` + ``description`` across entities (or one ``entity``).

    ``query`` is passed straight to FTS5's MATCH, so it supports terms, prefix
    (``log*``), and boolean (``login AND bug``) syntax. a "phrase" in quotes
    matches exactly. Results are sorted by relevance (bm25, negated so
    bigger is better).

    Args:
        conn: sqlite3.Connection from db.connect().
        query: str — FTS5 MATCH query.
        entity: str | None — optional filter by entity type.
    Returns:
        list[SearchResult] — results ranked by relevance.
    Raises:
        ValueError: if an unknown entity is provided or the MATCH syntax is invalid.
    """
    targets: list[tuple[str, str, str]]
    if entity is not None:
        if entity not in _ENTITIES:
            raise ValueError(
                f"unknown entity {entity!r}; choose from {sorted(_ENTITIES)}")
        targets = [_ENTITIES[entity]]
    else:
        targets = list(_ENTITIES.values())

    results: list[SearchResult] = []
    for fts_table, src_table, label in targets:
        # bm25() returns lower scores for better matches; negate so bigger=better.
        sql = (f"SELECT f.rowid, f.name, f.description, bm25({fts_table}) AS score "
               f"FROM {fts_table} f WHERE {fts_table} MATCH ? ORDER BY score")
        try:
            rows = conn.execute(sql, (query,)).fetchall()
        except sqlite3.OperationalError:
            # Bad MATCH syntax (e.g. bare ':') — surface a clear error.
            raise ValueError(f"invalid search query {query!r}")
        for r in rows:
            results.append(SearchResult(
                entity=label, id=r[0], name=r[1], description=r[2] or "",
                rank=-r[3]))
    results.sort(key=lambda x: x.rank, reverse=True)
    return results
