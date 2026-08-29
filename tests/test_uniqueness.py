"""Case-insensitive name uniqueness (bugs 102/106).

The name resolvers match case-insensitively, so the database must refuse
case-variant duplicates — enforced by collation-scoped UNIQUE indexes added in
migration v5, with pre-existing collisions merged deterministically.
"""

from __future__ import annotations

import pytest

from backend import db, errors, labels, stories, workflows


class TestLabelCaseInsensitiveUnique:
    def test_create_rejects_case_variant_duplicate(self, conn):
        labels.create_label(conn, "Bug")
        for variant in ("bug", "BUG", "bUg"):
            with pytest.raises(errors.Conflict):
                labels.create_label(conn, variant)

    def test_update_rejects_case_variant_duplicate(self, conn):
        labels.create_label(conn, "Bug")
        other = labels.create_label(conn, "Wontfix")
        with pytest.raises(errors.Conflict):
            labels.update_label(conn, other.id, name="bug")

    def test_exact_duplicate_conflict_message(self, conn):
        labels.create_label(conn, "auth")
        with pytest.raises(errors.Conflict):
            labels.create_label(conn, "auth")


class TestWorkflowStateCaseInsensitiveUnique:
    def test_create_state_rejects_duplicate_name_in_workflow(self, conn):
        wf = workflows.create_workflow(
            conn, "W", states=[{"name": "Todo", "type": "unstarted"}])
        with pytest.raises(errors.Conflict):
            workflows.create_workflow_state(conn, wf.id, "todo", "started")

    def test_create_workflow_rejects_duplicate_state_names(self, conn):
        with pytest.raises(errors.Conflict):
            workflows.create_workflow(conn, "W2", states=[
                {"name": "Todo", "type": "unstarted"},
                {"name": "Todo", "type": "started"},
            ])

    def test_same_name_in_different_workflows_allowed(self, conn):
        workflows.create_workflow(conn, "A", states=[{"name": "Todo", "type": "unstarted"}])
        workflows.create_workflow(conn, "B", states=[{"name": "Todo", "type": "unstarted"}])
        names = [s.name for wf in workflows.list_workflows(conn)
                 for s in workflows.list_workflow_states(conn, wf.id)]
        assert names.count("Todo") == 2

    def test_update_state_rejects_case_variant_duplicate(self, conn):
        wf = workflows.create_workflow(conn, "W", states=[
            {"name": "Todo", "type": "unstarted"},
            {"name": "Doing", "type": "started"},
        ])
        states = workflows.list_workflow_states(conn, wf.id)
        doing = next(s for s in states if s.name == "Doing")
        with pytest.raises(errors.Conflict):
            workflows.update_workflow_state(conn, doing.id, name="todo")


class TestV5MigrationMergesDirtyData:
    """A database created at v4 with case-variant duplicates must migrate
    cleanly: collisions merge (lowest id keeps its spelling), references are
    repointed, and the unique indexes end up enforced."""

    def migrate_from_v4(self, monkeypatch, db_path):
        """Build a v4 schema, inject collisions, then migrate to v5.

        ``_migrate`` runs the whole ``_MIGRATIONS`` list regardless of
        ``CURRENT_SCHEMA_VERSION``, so a genuine v4 database is made by
        truncating that list for the initial connect.
        """
        full_migrations = list(db._MIGRATIONS)
        monkeypatch.setattr(db, "_MIGRATIONS", full_migrations[:4])
        c = db.connect(db_path)
        s = stories.create_story(c, "dirty story")  # something to attach labels to
        dirty_story = s.id
        c.execute("INSERT INTO label(name, color, description, created_at) "
                  "VALUES ('Bug', '', '', '2026-01-01T00:00:00')")
        c.execute("INSERT INTO label(name, color, description, created_at) "
                  "VALUES ('bug', '#ff0000', '', '2026-01-01T00:00:00')")
        # story_label rows for both duplicates must survive, repointed.
        keeper = c.execute("SELECT MIN(id) FROM label").fetchone()[0]
        loser = c.execute("SELECT MAX(id) FROM label").fetchone()[0]
        c.execute("INSERT INTO story_label(story_id, label_id) VALUES (?, ?)",
                  (dirty_story, keeper))
        c.execute("INSERT INTO story_label(story_id, label_id) VALUES (?, ?)",
                  (dirty_story, loser))
        # Duplicate workflow-state names within the seeded workflow.
        wf_id = c.execute("SELECT MIN(id) FROM workflow").fetchone()[0]
        c.execute("INSERT INTO workflow_state(workflow_id, name, type, position, created_at) "
                  "VALUES (?, 'unstarted', 'unstarted', 9.0, '2026-01-01T00:00:00')",
                  (wf_id,))
        dupe_id = c.execute("SELECT MAX(id) FROM workflow_state").fetchone()[0]
        c.execute("UPDATE story SET workflow_state_id = ? WHERE id = ?",
                  (dupe_id, dirty_story))
        c.commit()
        c.close()
        # Now reconnect under the untruncated migrations (v5 applies).
        monkeypatch.setattr(db, "_MIGRATIONS", full_migrations)
        conn = db.connect(db_path)
        return conn, dirty_story, keeper, loser, dupe_id

    def test_label_duplicates_merged_and_index_enforced(self, tmp_path, monkeypatch):
        conn, story_id, keeper, loser, _ = self.migrate_from_v4(
            monkeypatch, tmp_path / "dirty.db")
        names = [lab.name for lab in labels.list_labels(conn)]
        assert len([n for n in names if n.lower() == "bug"]) == 1
        # The survivor id is kept (lowest id, its spelling 'Bug'... note seeded
        # labels may also exist; keeper is the min of the two injected).
        injected = [lab for lab in labels.list_labels(conn) if lab.id in (keeper, loser)]
        assert len(injected) == 1 and injected[0].id == keeper
        # story_label now points at the survivor (or was dropped as a duplicate).
        rows = conn.execute(
            "SELECT label_id FROM story_label WHERE story_id = ?", (story_id,)).fetchall()
        label_ids = {r["label_id"] for r in rows}
        assert loser not in label_ids
        # And the constraint holds from here on.
        with pytest.raises(errors.Conflict):
            labels.create_label(conn, "bUg")

    def test_workflow_state_duplicates_merged(self, tmp_path, monkeypatch):
        conn, dirty_story, _k, _l, dupe_id = self.migrate_from_v4(
            monkeypatch, tmp_path / "dirty2.db")
        row = conn.execute(
            "SELECT workflow_state_id FROM story WHERE id = ?", (dirty_story,)).fetchone()
        assert row["workflow_state_id"] != dupe_id  # repointed to the survivor
        states = conn.execute(
            "SELECT name, count(*) n FROM workflow_state "
            "GROUP BY workflow_id, lower(name) HAVING n > 1").fetchall()
        assert states == []
