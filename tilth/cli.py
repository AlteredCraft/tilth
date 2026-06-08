"""Verb-routed CLI entry point.

Subcommands:

    tilth run       <workspace>
    tilth resume    [<session_id>]
    tilth reset     [<session_id>] [-y]
    tilth visualize [<session_id>]

The feature is authored as markdown under `<workspace>/.tilth/tasks/` (an
`overview.md` plus one `T-NNN-*.md` per task — see `tilth/tasks.py`). There is
no separate prep step: `tilth run` reads that directory, creates a fresh session
+ worktree, and runs the Ralph loop.

Dispatch:
  1. No args at all     → print top-level help, exit 1.
  2. First arg is `-h`  → print help, exit 0.
  3. A known subcommand → parse with the subparser and dispatch.
  4. Anything else      → argparse usage error.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv
from rich.console import Console

from tilth import loop

console = Console()

SUBCOMMANDS = frozenset({"run", "resume", "reset", "visualize"})


def _build_parser():
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="tilth",
        description="Tilth — a minimal long-running agent harness.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    run_p = sub.add_parser(
        "run",
        help="Run the worker loop against a workspace.",
        description=(
            "Read the feature from <workspace>/.tilth/tasks/ (overview.md + one "
            "T-NNN-*.md per task), create a fresh session + worktree, and run the "
            "Ralph loop. Fails fast with the templates if the tasks dir is missing."
        ),
    )
    run_p.add_argument("workspace", type=Path, help="Path to the source repo.")

    resume_p = sub.add_parser(
        "resume",
        help="Resume an interrupted session.",
        description=(
            "Resume a session that stopped on wall-clock / token-cap / "
            "interrupt / error. Trailing failed tasks are flipped back to "
            "pending and their FAILED placeholder commit is unwound."
        ),
    )
    resume_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to resume; defaults to the latest session.",
    )

    reset_p = sub.add_parser(
        "reset",
        help="Tear down a session (worktree, branch, session dir).",
        description=(
            "Remove a session's worktree (even if dirty), delete its "
            "session/<id> branch from the source repo, and drop sessions/<id>/."
        ),
    )
    reset_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to reset; defaults to the latest session.",
    )
    reset_p.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt."
    )

    viz_p = sub.add_parser(
        "visualize",
        help="Render a session's events.jsonl as a chat HTML page.",
        description=(
            "Render sessions/<id>/events.jsonl as a single self-contained HTML "
            "page at sessions/<id>/chat.html."
        ),
    )
    viz_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to render; defaults to the latest session.",
    )

    return parser


def _dispatch(args) -> int:
    if args.command == "run":
        return loop.do_run_cmd(args.workspace)
    if args.command == "resume":
        return loop.do_resume_cmd(args.session_id)
    if args.command == "reset":
        return loop.do_reset_cmd(args.session_id, args.yes)
    if args.command == "visualize":
        return loop.do_visualize_cmd(args.session_id)
    raise AssertionError(f"unknown subcommand {args.command!r}")


def main() -> int:
    load_dotenv()
    argv = sys.argv[1:]

    if not argv:
        _build_parser().print_help()
        return 1

    if argv[0] in {"-h", "--help"}:
        _build_parser().print_help()
        return 0

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    return _dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
