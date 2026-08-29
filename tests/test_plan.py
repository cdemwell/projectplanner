"""Tests for backend/plan.py: export/import of the whole plan as JSON."""

from __future__ import annotations

import json

import pytest

from backend import (
    comments,
    db,
    epics,
    errors,
    iterations,
    labels,
    plan,
    projects,
    stories,
    story_links,
    tasks,
    workflows,
)


def _row_set(table: str, index: int, col: str, value):
    """Return a mutate function setting one snapshot column, for parametrize."""
    def mutate(data):
        data[table][index][col] = value
    return mutate


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
    """A snapshot missing its _meta header / schema_version is rejected untouched."""
    dst = tmp_path / "dst.db"
    bad = tmp_path / "bad.json"
    json.dump({"_meta": {"tables": []}}, open(bad, "w"))
    c = db.connect(dst)
    with pytest.raises(errors.ValidationError):
        plan.import_from_file(c, str(bad))
    # DB untouched (no members were wiped-and-reinserted)
    assert c.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 1
    c.close()


# --------------------------------------------------------------------------- #
# Untrusted-snapshot validation (story 109)
# --------------------------------------------------------------------------- #

def _snapshot_with_content(conn):
    """Seed ``conn`` (the fixture's already-seeded DB) with a project, epic and
    story, and export a snapshot dict.

    The story hangs off the epic so the snapshot carries a resolvable epic_id,
    making a dangling-FK mutation hit the "not in the snapshot" branch.
    """
    p = projects.create_project(conn, "backend")
    e = epics.create_epic(conn, "Auth", project_id=p.id)
    stories.create_story(conn, "Fix login", project_id=p.id, epic_id=e.id)
    return plan.export_plan(conn)


def test_import_rejects_unsupported_schema_version(conn):
    """A snapshot from an incompatible format version is refused, DB untouched."""
    data = _snapshot_with_content(conn)
    data["_meta"]["schema_version"] = 99
    with pytest.raises(errors.ValidationError, match="schema_version"):
        plan.import_plan(conn, data)
    assert [s.name for s in stories.list_stories(conn)] == ["Fix login"]


def test_import_rejects_missing_schema_version(conn):
    data = _snapshot_with_content(conn)
    del data["_meta"]["schema_version"]
    with pytest.raises(errors.ValidationError, match="schema_version"):
        plan.import_plan(conn, data)
    assert [s.name for s in stories.list_stories(conn)] == ["Fix login"]


def test_import_rejects_missing_meta_header(tmp_path, conn):
    bad = tmp_path / "nometa.json"
    bad.write_text(json.dumps({t: [] for t in plan._TABLES}))
    with pytest.raises(errors.ValidationError, match="_meta"):
        plan.import_from_file(conn, str(bad))


def test_import_missing_table_listed(conn):
    """A snapshot with a version header but no table bodies is refused."""
    data = {"_meta": {"schema_version": plan.SNAPSHOT_FORMAT_VERSION}}
    with pytest.raises(errors.ValidationError, match="missing tables"):
        plan.import_plan(conn, data)


@pytest.mark.parametrize("mutate,fragment", [
    (_row_set("member", 0, "mention_name", 123), "mention_name"),
    (_row_set("story", 0, "name", None), "NOT NULL"),
    (_row_set("story", 0, "story_type", "nonsense"), "story_type"),
    (_row_set("story", 0, "epic_id", 999), "not in the snapshot"),
    (_row_set("workflow", 0, "default_state_id", 4242), "default_state_id"),
])
def test_import_rejects_malformed_values_pre_wipe(conn, mutate, fragment):
    """Bad values/FKs abort before the wipe: existing rows survive."""
    data = _snapshot_with_content(conn)
    mutate(data)
    before = [s.name for s in stories.list_stories(conn)]
    with pytest.raises(errors.ValidationError, match=fragment):
        plan.import_plan(conn, data)
    assert [s.name for s in stories.list_stories(conn)] == before


def test_import_rejects_missing_not_null_column(conn):
    data = _snapshot_with_content(conn)
    del data["story"][0]["name"]
    with pytest.raises(errors.ValidationError, match="NOT NULL"):
        plan.import_plan(conn, data)
    assert [s.name for s in stories.list_stories(conn)] == ["Fix login"]


def test_import_rejects_non_list_table(conn):
    data = _snapshot_with_content(conn)
    data["label"] = {"name": "not a list"}
    with pytest.raises(errors.ValidationError, match="list of rows"):
        plan.import_plan(conn, data)


def test_import_rejects_non_row_object(conn):
    data = _snapshot_with_content(conn)
    data["label"] = ["not a row object"]
    with pytest.raises(errors.ValidationError, match="row must be an object"):
        plan.import_plan(conn, data)


def test_import_rejects_bad_id_type(conn):
    data = _snapshot_with_content(conn)
    data["label"].append({"id": "one", "name": "x"})
    with pytest.raises(errors.ValidationError, match=r"\bid\b"):
        plan.import_plan(conn, data)


def test_import_per_table_row_cap(conn, monkeypatch):
    monkeypatch.setattr(plan, "MAX_ROWS_PER_TABLE", 2)
    data = _snapshot_with_content(conn)
    with pytest.raises(errors.ValidationError, match="row cap"):
        plan.import_plan(conn, data)


def test_import_total_row_cap(conn, monkeypatch):
    monkeypatch.setattr(plan, "MAX_SNAPSHOT_ROWS", 3)
    data = _snapshot_with_content(conn)
    with pytest.raises(errors.ValidationError, match="row cap"):
        plan.import_plan(conn, data)


def test_import_rejects_non_object_json(tmp_path, conn):
    bad = tmp_path / "arr.json"
    bad.write_text("[]")
    with pytest.raises(errors.ValidationError, match="JSON object"):
        plan.import_from_file(conn, str(bad))


def test_import_rejects_invalid_json(tmp_path, conn):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json")
    with pytest.raises(errors.ValidationError, match="invalid JSON"):
        plan.import_from_file(conn, str(bad))


def test_import_rejects_deeply_nested_json(tmp_path, conn):
    bad = tmp_path / "deep.json"
    # 50k nesting is past the json parser's recursion limit but under the
    # byte cap, so this must be caught as a validation error, not a traceback.
    bad.write_text("[" * 50_000 + "]" * 50_000)
    with pytest.raises(errors.ValidationError, match="too deeply"):
        plan.import_from_file(conn, str(bad))


def test_import_rejects_oversized_file(tmp_path, conn, monkeypatch):
    monkeypatch.setattr(plan, "MAX_SNAPSHOT_BYTES", 16)
    big = tmp_path / "ok.json"
    big.write_text(json.dumps({"_meta": {"schema_version": 1}}))
    with pytest.raises(errors.ValidationError, match="byte cap"):
        plan.import_from_file(conn, str(big))


def test_import_rejects_missing_file(tmp_path, conn):
    with pytest.raises(errors.ValidationError, match="not found"):
        plan.import_from_file(conn, str(tmp_path / "nope.json"))


def test_import_still_round_trips_valid_snapshot(conn):
    """The happy path is unchanged: a valid export imports cleanly."""
    data = _snapshot_with_content(conn)
    counts = plan.import_plan(conn, data)
    assert counts["story"] == 1
    assert [s.name for s in stories.list_stories(conn)] == ["Fix login"]


# --------------------------------------------------------------------------- #
# Responses to the first review (duplicates, ordering, ownership)
# --------------------------------------------------------------------------- #

def test_export_writes_snapshot_format_version(conn):
    """The writer emits the same version constant the reader gates on."""
    data = plan.export_plan(conn)
    assert data["_meta"]["schema_version"] == plan.SNAPSHOT_FORMAT_VERSION


def test_import_rejects_boolean_schema_version(conn):
    """True == 1 in Python, so the version gate must reject bools explicitly."""
    data = _snapshot_with_content(conn)
    data["_meta"]["schema_version"] = True
    with pytest.raises(errors.ValidationError, match="must be an int"):
        plan.import_plan(conn, data)


def test_import_rejects_duplicate_ids(conn):
    data = _snapshot_with_content(conn)
    dup = dict(data["member"][0])
    data["member"].append(dup)  # same id, different row
    with pytest.raises(errors.ValidationError, match="duplicate id"):
        plan.import_plan(conn, data)


def test_import_rejects_reply_before_parent(conn):
    """A reply listed before its parent would fail the FK mid-import; reject."""
    data = _snapshot_with_content(conn)
    sid = data["story"][0]["id"]
    ts = db.now()
    data["story_comment"] = [
        {"id": 2, "story_id": sid, "author_id": None, "text": "reply",
         "parent_id": 1, "created_at": ts, "updated_at": ts},
        {"id": 1, "story_id": sid, "author_id": None, "text": "parent",
         "parent_id": None, "created_at": ts, "updated_at": ts},
    ]
    with pytest.raises(errors.ValidationError, match="before its parent"):
        plan.import_plan(conn, data)


def test_import_rejects_self_parent_comment(conn):
    data = _snapshot_with_content(conn)
    sid = data["story"][0]["id"]
    ts = db.now()
    data["story_comment"] = [{"id": 1, "story_id": sid, "author_id": None,
                             "text": "solo", "parent_id": 1,
                             "created_at": ts, "updated_at": ts}]
    with pytest.raises(errors.ValidationError, match="own parent"):
        plan.import_plan(conn, data)


def test_import_accepts_parent_before_child_reply(conn):
    """The valid ordering (parent listed first) imports cleanly."""
    data = _snapshot_with_content(conn)
    sid = data["story"][0]["id"]
    ts = db.now()
    data["story_comment"] = [
        {"id": 1, "story_id": sid, "author_id": None, "text": "parent",
         "parent_id": None, "created_at": ts, "updated_at": ts},
        {"id": 2, "story_id": sid, "author_id": None, "text": "reply",
         "parent_id": 1, "created_at": ts, "updated_at": ts},
    ]
    plan.import_plan(conn, data)
    # Import remaps ids, so find the imported story fresh.
    imported = next(s for s in stories.list_stories(conn) if s.name == "Fix login")
    texts = sorted(c.text for c in comments.list_comments(conn, imported.id))
    assert texts == ["parent", "reply"]


def test_import_rejects_duplicate_mention_name(conn):
    data = _snapshot_with_content(conn)
    mention = data["member"][0]["mention_name"]
    data["member"].append({"id": 99, "name": "other", "mention_name": mention,
                           "created_at": db.now()})
    with pytest.raises(errors.ValidationError, match="duplicate mention_name"):
        plan.import_plan(conn, data)


def test_import_rejects_duplicate_junction_pair(conn):
    data = _snapshot_with_content(conn)
    sid, mid = data["story"][0]["id"], data["member"][0]["id"]
    row = {"id": 1, "story_id": sid, "member_id": mid}
    data["story_owner"] = [row, dict(row, id=2)]
    with pytest.raises(errors.ValidationError, match="duplicate"):
        plan.import_plan(conn, data)


def test_import_rejects_self_link(conn):
    data = _snapshot_with_content(conn)
    sid = data["story"][0]["id"]
    data["story_link"] = [{"id": 1, "subject_story_id": sid, "verb": "relates_to",
                           "object_story_id": sid, "created_at": db.now()}]
    with pytest.raises(errors.ValidationError, match="link to itself"):
        plan.import_plan(conn, data)


def test_import_rejects_duplicate_link_triple(conn):
    data = _snapshot_with_content(conn)
    sid = data["story"][0]["id"]
    # A second story row so the link target is inside the snapshot.
    second = dict(data["story"][0], id=99, name="Add 2FA")
    data["story"].append(second)
    row = {"id": 1, "subject_story_id": sid, "verb": "relates_to",
           "object_story_id": 99, "created_at": db.now()}
    data["story_link"] = [row, dict(row, id=2)]
    with pytest.raises(errors.ValidationError, match="duplicate"):
        plan.import_plan(conn, data)


def test_import_rejects_cross_workflow_default_state(conn):
    """A workflow's default state must belong to that same workflow."""
    data = _snapshot_with_content(conn)
    state_id = data["workflow_state"][0]["id"]
    data["workflow"].append({"id": 99, "name": "Other",
                             "default_state_id": state_id,
                             "created_at": db.now()})
    with pytest.raises(errors.ValidationError, match="different workflow"):
        plan.import_plan(conn, data)


def test_import_rejects_idless_cross_workflow_default_state(conn):
    """The ownership check must not skip id-less workflow rows."""
    data = _snapshot_with_content(conn)
    state_id = data["workflow_state"][0]["id"]
    data["workflow"].append({"name": "Ghost", "default_state_id": state_id,
                             "created_at": db.now()})
    with pytest.raises(errors.ValidationError, match="different workflow"):
        plan.import_plan(conn, data)


def test_import_rejects_non_utf8_snapshot(tmp_path, conn):
    """A binary file is a clear ValidationError, not a traceback."""
    bad = tmp_path / "binary.json"
    bad.write_bytes(b'{"_meta": {"schema_version": 1, "x": "\xff\xfe"}}')
    with pytest.raises(errors.ValidationError, match="UTF-8"):
        plan.import_from_file(conn, str(bad))


def test_import_preserves_workflow_state_description(tmp_path):
    """schema v4's workflow_state.description must survive the round-trip."""
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    c = db.connect(src)
    p = projects.create_project(c, "backend")
    stories.create_story(c, "Fix login", project_id=p.id)
    wf_id = c.execute("SELECT id FROM workflow LIMIT 1").fetchone()[0]
    workflows.create_workflow_state(c, wf_id, "In Review", "started",
                                    description="keep me")
    data = plan.export_plan(c)
    c.close()

    c2 = db.connect(dst)
    plan.import_plan(c2, data)
    rows = [(r["name"], r["description"]) for r in
            c2.execute("SELECT name, description FROM workflow_state")]
    c2.close()
    assert ("In Review", "keep me") in rows


def test_import_accepts_pre_v4_snapshot_without_state_description(conn):
    """Old exports lack workflow_state.description; the DB default applies."""
    data = _snapshot_with_content(conn)
    for row in data["workflow_state"]:
        del row["description"]
    plan.import_plan(conn, data)
    assert {r[0] for r in conn.execute("SELECT description FROM workflow_state")} == {""}
