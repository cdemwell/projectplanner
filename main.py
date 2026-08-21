"""Project Planner entry point.

- No args  -> interactive TUI (``tui/app.py``).
- With args -> one-shot CLI (``cli/commands.py``).

Both front-ends share one backend over a single local SQLite DB (``planner.db``).
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Dispatch to TUI or CLI based on arguments.

    If no arguments are provided, launches the interactive TUI.
    Otherwise, runs the one-shot CLI.

    Args:
        argv: Optional list of command-line arguments.
    Returns:
        Exit code.
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    # No arguments: launch the interactive TUI.
    if not argv:
        return _launch_tui()

    # Otherwise: behave as a one-shot CLI.
    from cli.commands import run as run_cli
    return run_cli(argv)


def _launch_tui() -> int:
    """Launch the full-screen Textual TUI (no args).

    Attempts to import the TUI application; if 'textual' is not installed,
    prints an installation hint and exits with 1.

    Returns:
        Exit code.
    """
    try:
        from tui.app import run as run_tui
    except ImportError as e:
        print(
            "projectplanner: the TUI needs the 'textual' package, which isn't\n"
            "installed in this interpreter.\n"
            "\n"
            "  Install it, e.g.:  pip install textual\n"
            "  (or use the project venv: .venv/bin/python main.py)\n"
            "\n"
            f"  missing module: {e.name}\n"
            "\n"
            "  The CLI still works without Textual — e.g.\n"
            "    python main.py story list\n"
            "    python main.py search \"login\""
        )
        return 1
    return run_tui()


if __name__ == "__main__":
    sys.exit(main())
