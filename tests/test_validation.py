"""Backend input-validation contract (bugs 101/104/105/107/108).

These target the shared pattern — the service layer validates its own input
instead of deferring to argparse or to what SQLite happens to accept — using
one parametrized test per root-cause family.
"""

from __future__ import annotations

import pytest

from backend import (
    epics,
    errors,
    groups,
    iterations,
    labels,
    members,
    milestones,
    projects,
    search,
    stories,
    workflows,
)

# --- bug 104: names are required on both create and update ----------------- #

_NAMED_ENTITIES = [
    (stories.create_story, stories.update_story),
    (projects.create_project, projects.update_project),
    (epics.create_epic, epics.update_epic),
    (labels.create_label, labels.update_label),
    (iterations.create_iteration, iterations.update_iteration),
    (milestones.create_milestone, milestones.update_milestone),
    (groups.create_group, groups.update_group),
    (members.create_member, members.update_member),
    (workflows.create_workflow, workflows.update_workflow),
]


@pytest.mark.parametrize("create_fn", [c for c, _ in _NAMED_ENTITIES],
                         ids=lambda f: f.__name__)
class TestBlankNamesRejected:
    """No entity may be created with a missing or whitespace-only name."""

    @pytest.mark.parametrize("bad", ["", "   ", "\t"])
    def test_create_rejects_blank_name(self, conn, create_fn, bad):
        with pytest.raises(errors.ValidationError):
            create_fn(conn, bad)


@pytest.mark.parametrize("create_fn,update_fn", _NAMED_ENTITIES,
                         ids=lambda f: getattr(f, "__name__", "?"))
@pytest.mark.parametrize("bad", ["", "   "])
def test_update_name_rejects_blank(conn, create_fn, update_fn, bad):
    with pytest.raises(errors.ValidationError):
        obj = create_fn(conn, "x")
        update_fn(conn, obj.id, name=bad)


# --- bug 108: negative limit/offset rejected on every paging surface ------- #

_PAGING = [
    (stories.list_stories, {}),
    (projects.list_projects, {}),
    (epics.list_epics, {}),
    (iterations.list_iterations, {}),
    (milestones.list_milestones, {}),
    (labels.list_labels, {}),
    (groups.list_groups, {}),
    (members.list_members, {}),
    (lambda conn, **kw: search.search(conn, "story", **kw), {}),
]


@pytest.mark.parametrize("kw", [
    {"limit": -1}, {"limit": -5}, {"offset": -1}, {"offset": -5},
], ids=lambda k: ",".join(f"{a}={b}" for a, b in k.items()))
@pytest.mark.parametrize("fn", [f for f, _ in _PAGING], ids=lambda f: f.__name__)
def test_negative_limit_offset_rejected(conn, fn, kw):
    with pytest.raises(errors.ValidationError):
        fn(conn, **kw)


def test_zero_limit_offset_roundtrip(conn):
    """0 is a valid bound (empty page / from the top)."""
    stories.create_story(conn, "x")
    assert stories.list_stories(conn, limit=0) == []
    assert stories.list_stories(conn, limit=1, offset=0)


# --- bug 105: the q filter matches literally, never as a LIKE pattern ------ #

def test_q_filter_is_literal_not_pattern(conn):
    s1 = stories.create_story(conn, "alpha_beta")
    stories.create_story(conn, "plain name")
    s3 = stories.create_story(conn, "fifty%off")
    assert [s.id for s in stories.list_stories(conn, q="_")] == [s1.id]
    assert [s.id for s in stories.list_stories(conn, q="%")] == [s3.id]
    # a plain substring still matches
    assert len(stories.list_stories(conn, q="plain")) == 1


# --- bug 101: iteration ranges ------------------------------------------------ #

class TestIterationDateRange:
    def test_create_rejects_start_after_end(self, conn):
        with pytest.raises(errors.ValidationError):
            iterations.create_iteration(conn, "S", start_date="2026-12-01",
                                        end_date="2026-01-01")

    def test_update_rejects_inverted_range(self, conn):
        it = iterations.create_iteration(conn, "S", start_date="2026-01-01",
                                         end_date="2026-02-01")
        with pytest.raises(errors.ValidationError):
            iterations.update_iteration(conn, it.id, start_date="2027-01-01")

    def test_update_existing_end_conflict(self, conn):
        it = iterations.create_iteration(conn, "S", start_date="2026-01-01",
                                         end_date="2026-02-01")
        with pytest.raises(errors.ValidationError):
            iterations.update_iteration(conn, it.id, end_date="2025-12-31")

    def test_equal_dates_allowed(self, conn):
        it = iterations.create_iteration(conn, "S", start_date="2026-01-01",
                                         end_date="2026-01-01")
        assert it.start_date == "2026-01-01"

    def test_malformed_date_rejected(self, conn):
        with pytest.raises(errors.ValidationError):
            iterations.create_iteration(conn, "S", start_date="next tuesday")


# --- bug 107A: deadline must be an ISO date ----------------------------------- #

@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-40", "2026/01/01", "yesterday"])
def test_deadline_rejects_non_iso(conn, bad):
    with pytest.raises(errors.ValidationError):
        stories.create_story(conn, "x", deadline=bad)
    s = stories.create_story(conn, "y")
    with pytest.raises(errors.ValidationError):
        stories.update_story(conn, s.id, deadline=bad)


def test_deadline_roundtrip(conn):
    s = stories.create_story(conn, "x", deadline="2026-12-25")
    assert s.deadline == "2026-12-25"


# --- bug 107B: search validates its inputs as PlannerErrors ------------------- #

def test_search_empty_query_rejected(conn):
    for q in ("", "   ", "\t"):
        with pytest.raises(errors.ValidationError):
            search.search(conn, q)


def test_search_unknown_entity_rejected(conn):
    with pytest.raises(errors.ValidationError):
        search.search(conn, "x", entity="bogus")


def test_search_bad_match_syntax_is_planner_error(conn):
    with pytest.raises(errors.PlannerError):
        search.search(conn, "not a valid : query")
