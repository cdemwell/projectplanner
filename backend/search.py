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
    entity: str
    id: int
    name: str
    description: str
    rank: float

    def to_dict(self) -> dict:
        return {
            "entity": self.entity, "id": self.id, "name": self.name,
            "description": self.description, "rank": self.rank,
        }


def search(conn: sqlite3.Connection, query: str, *, entity: str | None = None) -> list[SearchResult]:
    """Search ``name`` + ``description`` across entities (or one ``entity``).

    ``query`` is passed straight to FTS5's MATCH, so it supports terms, prefix
    (``log*``), and boolean (``login AND bug``) syntax. Results are sorted by
    relevance (bm25, lower is better).
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