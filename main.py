"""Project Planner entry point.

- No args  -> interactive TUI (``tui/app.py``).
- With args -> one-shot CLI (``cli/commands.py``).

Both front-ends share one backend over a single local SQLite DB (``planner.db``).
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Dispatch to TUI or CLI based on arguments.

    If no arguments are provided (or only TUI flags like --auto-refresh),
    launches the interactive TUI. Otherwise, runs the one-shot CLI.

    Args:
        argv: Optional list of command-line arguments.
    Returns:
        Exit code.
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    # Route to the CLI if the first token is a resource subcommand or a CLI
    # flag. TUI-only flags (e.g. --auto-refresh) and no args launch the TUI.
    cli_resources = {"story", "epic", "iteration", "milestone", "project",
                     "label", "member", "group", "workflow", "task",
                     "comment", "link", "search", "plan", "config"}
    cli_flags = {"--help", "-h", "--version", "--json", "--format", "--db",
                 "--dry-run", "--rotate-backup", "--config", "--limit", "--offset"}
    first = argv[0] if argv else None
    if first and (first in cli_resources or first in cli_flags):
        from cli.commands import run as run_cli
        return run_cli(argv)

    # Otherwise: launch the interactive TUI (with any TUI-only flags).
    return _launch_tui(argv)


def _launch_tui(argv: list[str] | None = None) -> int:
    """Launch the full-screen Textual TUI (no args or TUI-only flags).

    Attempts to import the TUI application; if 'textual' is not installed,
    prints an installation hint and exits with 1.

    Args:
        argv: Optional TUI-specific flags (e.g., --auto-refresh).

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

    import argparse
    ap = argparse.ArgumentParser(prog="projectplanner", add_help=False)
    ap.add_argument("--auto-refresh", type=float, default=None,
                    help="Start with auto-refresh on, ticking every N seconds "
                         "(default: off; hotkey 'a' toggles it at a 1s interval)")
    tui_args, _ = ap.parse_known_args(argv or [])
    return run_tui(auto_refresh=tui_args.auto_refresh)


if __name__ == "__main__":
    sys.exit(main())
