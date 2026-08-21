"""Project Planner entry point.

- No args  -> interactive TUI (``tui/app.py``).
- With args -> one-shot CLI (``cli/commands.py``).

Both front-ends share one backend over a single local SQLite DB (``planner.db``).
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # No arguments: launch the interactive TUI.
    if not argv:
        return _launch_tui()

    # Otherwise: behave as a one-shot CLI.
    from cli.commands import run as run_cli
    return run_cli(argv)


def _launch_tui() -> int:
    """Launch the full-screen TUI.

    The TUI library is the one open design decision (CONTEXT.md §10/§15). Until
    that's confirmed we surface the situation clearly rather than guessing.
    """
    print(
        "projectplanner: interactive TUI is not yet built.\n"
        "\n"
        "  The CLI is ready to use. Examples:\n"
        "    python main.py story list\n"
        "    python main.py project create --name backend\n"
        "    python main.py story create --name \"Fix login\" --project backend --type bug\n"
        "    python main.py search \"login\"\n"
        "\n"
        "  To build the TUI, decide the library per CONTEXT.md §15 (Textual vs\n"
        "  prompt_toolkit), then implement tui/app.py."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())