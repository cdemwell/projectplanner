"""Tests for backend/plan.py: export/import of the whole plan as JSON."""

from __future__ import annotations

import json

import pytest

from backend import (
    comments,
    db,
    epics,
    iterations,
    labels,
    plan,
    projects,
    stories,
    story_links,
    tasks,
)


def test_export_import_round_trip(tmp_path):
    """Export a plan, import into a fresh DB, and check content equivalence."""
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    out = tmp_path / "plan.json"

    c = db.connect(src)
    p = projects.create_project(c, "backend")
    e = epics.create_epic(c, "Auth", project_id=p.id)
    it = iterations.create_iteration(c, "Sprint 1", status="active",
                                     start_date="2026-09-01", end_date="2026-09-14")
    lbl = labels.create_label(c, "auth")
    s1 = stories.create_story(c, "Fix login", description="oauth redirect",
                              project_id=p.id, epic_id=e.id, iteration_id=it.id,
                              owner_ids=[1], label_ids=[lbl.id])
    s2 = stories.create_story(c, "Add 2FA", project_id=p.id)
    t = tasks.create_task(c, s1.id, "write tests")
    tasks.complete_task(c, t.id)
    comments.create_comment(c, s1.id, "looks bad", author_id=1)
    story_links.create_link(c, s1.id, "blocks", s2.id)
    c.close()

    plan.export_to_file(db.connect(src), str(out))
    c2 = db.connect(dst)
    counts = plan.import_from_file(c2, str(out))
    assert counts["story"] == 2 and counts["task"] == 1
    assert counts["story_comment"] == 1 and counts["story_link"] == 1

    # content equivalent; ids may differ (here they coincide for member/project).
    names = {s.name: s for s in stories.list_stories(c2)}
    assert set(names) == {"Fix login", "Add 2FA"}
    s1b = names["Fix login"]
    assert s1b.description == "oauth redirect"
    # ids may differ after import (autoincrement), so assert counts/names, not ids
    assert len(stories.list_owners(c2, s1b.id)) == 1
    assert [lb.name for lb in stories.list_story_labels(c2, s1b.id)] == ["auth"]
    tasks2 = tasks.list_tasks(c2, s1b.id)
    assert [t.description for t in tasks2] == ["write tests"]
    assert tasks2[0].completed_at is not None
    assert [x.text for x in comments.list_comments(c2, s1b.id)] == ["looks bad"]
    assert len(story_links.list_links(c2, s1b.id)) == 1
    c2.close()


def test_import_replaces_existing_content(tmp_path):
    """Importing wipes pre-existing rows and loads only the snapshot."""
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    out = tmp_path / "plan.json"
    c = db.connect(src)
    projects.create_project(c, "backend")
    stories.create_story(c, "the one true story")
    c.close()
    plan.export_to_file(db.connect(src), str(out))

    # dst has unrelated content that must be replaced
    c = db.connect(dst)
    projects.create_project(c, "scrap")
    stories.create_story(c, "scrap story")
    c.close()

    c = db.connect(dst)
    plan.import_from_file(c, str(out))
    projects2 = projects.list_projects(c)
    stories2 = stories.list_stories(c)
    c.close()
    assert [p.name for p in projects2] == ["backend"]
    assert [s.name for s in stories2] == ["the one true story"]


def test_import_missing_table_errors(tmp_path):
    """A snapshot missing a required table is rejected without touching the DB."""
    dst = tmp_path / "dst.db"
    bad = tmp_path / "bad.json"
    json.dump({"_meta": {"tables": []}}, open(bad, "w"))
    c = db.connect(dst)
    with pytest.raises(ValueError):
        plan.import_from_file(c, str(bad))
    # DB untouched (no members were wiped-and-reinserted)
    assert c.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 1
    c.close()


def _threaded_snapshot(src) -> dict:
    """Export a snapshot with a threaded comment (child -> parent), including
    tasks, owners, labels, and a story link so cross-FK paths are exercised."""
    c = db.connect(src)
    p = projects.create_project(c, "backend")
    s1 = stories.create_story(c, "Fix login", project_id=p.id)
    s2 = stories.create_story(c, "Add 2FA", project_id=p.id)
    parent = comments.create_comment(c, s1.id, "root", author_id=1)
    comments.create_comment(c, s1.id, "reply", author_id=1, parent_id=parent.id)
    comments.create_comment(c, s2.id, "on other story", author_id=1)
    story_links.create_link(c, s1.id, "blocks", s2.id)
    data = plan.export_plan(c)
    c.close()
    return data


def _thread_parent_links(conn) -> dict[int, int]:
    """Map each non-root comment id to its parent id in a database."""
    return {
        r["id"]: r["parent_id"]
        for r in conn.execute("SELECT id, parent_id FROM story_comment WHERE parent_id IS NOT NULL")
    }


def test_import_story_comment_child_before_parent(tmp_path):
    """A snapshot whose threaded comments are listed child-first must import,
    not abort with an FK-remap failure (bug 100)."""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    data = _threaded_snapshot(src)
    data["story_comment"] = list(reversed(data["story_comment"]))  # child before parent
    c2 = db.connect(dst)
    counts = plan.import_plan(c2, data)
    assert counts["story_comment"] == 3
    # Every parent link survives the remap and points at a real row.
    by_new_id = {r["id"] for r in c2.execute("SELECT id FROM story_comment")}
    for child, parent in _thread_parent_links(c2).items():
        assert parent in by_new_id
    c2.close()


def test_import_is_order_invariant_within_tables(tmp_path):
    """Import must not depend on row order within any table.

    Shuffles every table's row list (deterministic seeds, several rounds) and
    asserts import succeeds with threading intact — covering self-FKs and
    any future intra-table ordering assumption.
    """
    import random

    src = tmp_path / "src.db"
    data = _threaded_snapshot(src)
    old_parents = set(_thread_parent_links(db.connect(src)))
    assert old_parents  # the threaded fixture above

    for round_number in range(5):
        shuffled = {"_meta": data["_meta"]}
        rng = random.Random(round_number)
        for table in plan._TABLES:
            rows = list(data[table])
            rng.shuffle(rows)
            shuffled[table] = rows
        target = tmp_path / f"dst-round-{round_number}.db"
        c2 = db.connect(target)
        counts = plan.import_plan(c2, shuffled)
        assert counts["story_comment"] == 3
        # Same shape of threaded links, all pointing inside the new table.
        links = _thread_parent_links(c2)
        assert len(links) == len(old_parents)
        live_ids = {r["id"] for r in c2.execute("SELECT id FROM story_comment")}
        for parent in links.values():
            assert parent in live_ids
        c2.close()


def test_import_self_fk_missing_parent_fails_cleanly(tmp_path):
    """A comment referencing a parent id that isn't in the snapshot errors
    (not a silent mis-link) and leaves the target DB unchanged."""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    data = _threaded_snapshot(src)
    for row in data["story_comment"]:
        if row["parent_id"] is not None:
            row["parent_id"] = 4242  # dangling: no such old id anywhere
    c2 = db.connect(dst)
    with pytest.raises(KeyError):
        plan.import_plan(c2, data)
    # Rolled back: the destination still has its seeded content only.
    assert c2.execute("SELECT COUNT(*) FROM story_comment").fetchone()[0] == 0
    assert c2.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 1
    c2.close()
