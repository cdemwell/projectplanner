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
    with pytest.raises(errors.ValidationError, match="not in the snapshot"):
        plan.import_plan(c2, data)
    # Rolled back: the destination still has its seeded content only.
    assert c2.execute("SELECT COUNT(*) FROM story_comment").fetchone()[0] == 0
    assert c2.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 1
    c2.close()


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


def test_import_accepts_any_comment_order(conn):
    """Both parent-first and child-first orders import cleanly (order-invariant
    self-FK handling, story 100); threading survives the remap."""
    data = _snapshot_with_content(conn)
    sid = data["story"][0]["id"]
    ts = db.now()
    ordered = [
        {"id": 1, "story_id": sid, "author_id": None, "text": "parent",
         "parent_id": None, "created_at": ts, "updated_at": ts},
        {"id": 2, "story_id": sid, "author_id": None, "text": "reply",
         "parent_id": 1, "created_at": ts, "updated_at": ts},
    ]
    for rows, text_order in ((ordered, ["parent", "reply"]),
                             (list(reversed(ordered)), ["parent", "reply"])):
        data["story_comment"] = rows
        plan.import_plan(conn, data)
        # Import remaps ids, so find the imported story fresh.
        imported = next(s for s in stories.list_stories(conn) if s.name == "Fix login")
        texts = sorted(c.text for c in comments.list_comments(conn, imported.id))
        assert texts == text_order


def test_import_rejects_self_parent_comment(conn):
    data = _snapshot_with_content(conn)
    sid = data["story"][0]["id"]
    ts = db.now()
    data["story_comment"] = [{"id": 1, "story_id": sid, "author_id": None,
                             "text": "solo", "parent_id": 1,
                             "created_at": ts, "updated_at": ts}]
    with pytest.raises(errors.ValidationError, match="own parent"):
        plan.import_plan(conn, data)


def test_import_rejects_cross_story_comment_parent(conn):
    """Mirrors create_comment's rule: a reply's parent must be on same story."""
    p = projects.create_project(conn, "backend")
    s0 = stories.create_story(conn, "Fix login", project_id=p.id)
    s1 = stories.create_story(conn, "Second story", project_id=p.id)
    data = plan.export_plan(conn)
    ts = db.now()
    data["story_comment"] = [
        {"id": 1, "story_id": s1.id, "author_id": None, "text": "on second",
         "parent_id": None, "created_at": ts, "updated_at": ts},
        {"id": 2, "story_id": s0.id, "author_id": None, "text": "reply",
         "parent_id": 1, "created_at": ts, "updated_at": ts},
    ]
    with pytest.raises(errors.ValidationError, match="different story"):
        plan.import_plan(conn, data)


def test_import_rejects_duplicate_mention_name(conn):
    data = _snapshot_with_content(conn)
    mention = data["member"][0]["mention_name"]
    data["member"].append({"id": 99, "name": "other", "mention_name": mention,
                           "created_at": db.now()})
    with pytest.raises(errors.ValidationError, match="duplicate mention_name"):
        plan.import_plan(conn, data)


def test_import_rejects_case_variant_label_names(conn):
    """Mirrors the v5 label_name_ci unique index."""
    labels.create_label(conn, "auth")
    data = _snapshot_with_content(conn)
    data["label"].append({"id": 99, "name": "AUTH", "color": "",
                          "description": "", "created_at": db.now()})
    with pytest.raises(errors.ValidationError, match="case-insensitive"):
        plan.import_plan(conn, data)


def test_import_rejects_case_variant_state_names_in_one_workflow(conn):
    """Mirrors the v5 workflow_state_wf_name_ci unique index."""
    data = _snapshot_with_content(conn)
    wf_id = data["workflow"][0]["id"]
    row = dict(data["workflow_state"][0], id=99, name="uNstarted", position=9.0,
               description="")
    row["workflow_id"] = wf_id
    data["workflow_state"].append(row)
    with pytest.raises(errors.ValidationError, match="case-insensitive"):
        plan.import_plan(conn, data)


def test_import_allows_same_state_name_across_workflows(conn):
    """The v5 index scopes uniqueness to one workflow; cross-workflow names are
    fine even case-insensitively."""
    data = _snapshot_with_content(conn)
    state = dict(data["workflow_state"][0])
    ts = db.now()
    data["workflow"].append({"id": 77, "name": "Second", "created_at": ts})
    data["workflow_state"].append(dict(state, id=98, workflow_id=77))
    plan.import_plan(conn, data)
    names = sorted(r[0] for r in conn.execute("SELECT name FROM workflow_state"))
    assert names.count(state["name"]) == 2


def test_import_allows_non_ascii_case_variant_names(conn):
    """SQLite's NOCASE folds ASCII letters only, so non-ASCII case variants
    (the labels "Öl"/"öl") coexist legally in a real database. Validation must
    fold the same way, or it would reject the round-trip of the tool's own
    exports (Python's str.lower() would wrongly call them duplicates)."""
    labels.create_label(conn, "öl")
    labels.create_label(conn, "Öl")  # not a NOCASE collision
    wf_id = conn.execute("SELECT id FROM workflow LIMIT 1").fetchone()[0]
    workflows.create_workflow_state(conn, wf_id, "Öffnen", "started")
    workflows.create_workflow_state(conn, wf_id, "öffnen", "unstarted")
    data = plan.export_plan(conn)
    plan.import_plan(conn, data)  # must NOT be rejected
    got_labels = sorted(r[0] for r in conn.execute("SELECT name FROM label"))
    got_states = sorted(r[0] for r in conn.execute("SELECT name FROM workflow_state"))
    assert got_labels == ["Öl", "öl"]
    assert "Öffnen" in got_states and "öffnen" in got_states


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
