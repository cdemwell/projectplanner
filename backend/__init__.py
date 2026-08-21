"""Project Planner backend — function-call API over a single local SQLite DB.

See CONTEXT.md for the full design. Every public function in this package takes a
``sqlite3.Connection`` (``conn``) as its first argument; the CLI and TUI are thin
layers over these functions.
"""
