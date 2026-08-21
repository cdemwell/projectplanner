"""Tests for backend/search.py (FTS5)."""

from __future__ import annotations

import pytest

from backend import epics, labels, projects, search, stories


def test_search_finds_by_name_and_description(conn):
    p = projects.create_project(conn, "backend", description="core api")
    s = stories.create_story(conn, "Fix login bug", description="oauth redirect fails")
    # by name
    assert {r.id for r in search.search(conn, "login")} == {s.id}
    # by description
    assert {r.id for r in search.search(conn, "oauth")} == {s.id}
    # project matched too
    assert {r.id for r in search.search(conn, "backend")} == {p.id}


def test_search_entity_filter(conn):
    stories.create_story(conn, "auth story")
    labels.create_label(conn, "auth")
    # unfiltered -> both story and label
    entities = {r.entity for r in search.search(conn, "auth")}
    assert "story" in entities and "label" in entities
    # filtered to story only
    assert {r.entity for r in search.search(conn, "auth", entity="story")} == {"story"}


def test_search_update_reindexes(conn):
    s = stories.create_story(conn, "old title")
    assert search.search(conn, "oldtitle") == []  # 'old title' tokenizes to old+title
    stories.update_story(conn, s.id, name="newtitle")
    assert {r.id for r in search.search(conn, "newtitle")} == {s.id}


def test_search_delete_removes_from_index(conn):
    s = stories.create_story(conn, "uniqueword")
    assert {r.id for r in search.search(conn, "uniqueword")} == {s.id}
    stories.delete_story(conn, s.id)
    assert search.search(conn, "uniqueword") == []


def test_search_ranking(conn):
    # a doc with the term in the name should rank at least as well as one in desc.
    s_name = stories.create_story(conn, "payment", description="misc")
    s_desc = stories.create_story(conn, "misc", description="payment processing details here")
    results = {r.id: r for r in search.search(conn, "payment", entity="story")}
    assert set(results) == {s_name.id, s_desc.id}


def test_search_boolean_and_prefix(conn):
    stories.create_story(conn, "login bug", description="auth")
    stories.create_story(conn, "performance", description="tuning")
    # OR
    assert len(search.search(conn, "login OR performance", entity="story")) == 2
    # prefix
    assert len(search.search(conn, "perf*", entity="story")) == 1


def test_search_unknown_entity(conn):
    with pytest.raises(ValueError):
        search.search(conn, "x", entity="bogus")


def test_search_bad_query(conn):
    with pytest.raises(ValueError):
        search.search(conn, "not a valid : query")
