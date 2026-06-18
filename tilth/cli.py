"""Verb-routed CLI entry point.

Subcommands:

    tilth run       <workspace>
    tilth resume    [<session_id>]
    tilth reset     [<session_id>] [-y]
    tilth visualize [<session_id>] [--port N]

The feature is authored as markdown under `<workspace>/.tilth/tasks/` (an
`overview.md` plus one `T-NNN-*.md` per task — see `tilth/tasks.py`). There is
no separate prep step: `tilth run` reads that directory, creates a fresh session
+ worktree, and runs the Ralph loop.

Dispatch:
  1. No args at all     → print config locations + top-level help, exit 1.
  2. First arg is `-h`  → print config locations + help, exit 0.
  3. A known subcommand → parse with the subparser and dispatch.
  4. Anything else      → argparse usage error.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv
from rich.console import Console

from tilth import loop, paths

console = Console()

SUBCOMMANDS = frozenset({"init", "run", "resume", "reset", "visualize"})


def _load_env() -> None:
    """Load the resolved .env (first hit in the search order), if any. No file is
    not an error — `tilth init` and `tilth visualize` don't need provider config."""
    env_file = paths.resolve_env_file()
    if env_file is not None:
        load_dotenv(env_file, override=False)


def _print_config_locations() -> None:
    """Show resolved Tilth home and .env on top-level help."""
    home = paths.tilth_home()
    env_file = paths.resolve_env_file()
    write_target = paths.env_file_write_target()

    console.print("[bold]Config locations[/bold]")
    if home.is_dir():
        console.print(f"  Tilth home:  {home}", soft_wrap=True)
    else:
        console.print(
            f"  Tilth home:  {home}  "
            "[dim](not found — run [bold]tilth init[/bold])[/dim]",
            soft_wrap=True,
        )

    if env_file is not None:
        console.print(f"  .env:        {env_file}", soft_wrap=True)
    else:
        console.print(
            f"  .env:        {write_target}  "
            "[dim](not found — run [bold]tilth init[/bold])[/dim]",
            soft_wrap=True,
        )
    console.print()


def _print_help(parser) -> None:
    _print_config_locations()
    parser.print_help()


def _build_parser():
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="tilth",
        description="Tilth — a minimal long-running agent harness.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser(
        "init",
        help="Scaffold ~/.tilth so the installed tool runs from anywhere.",
        description=(
            "Create the Tilth home directory ($TILTH_HOME, default ~/.tilth) with "
            "a sessions/ dir and a .env from the template. Does not overwrite an "
            "existing .env. Run once after `uv tool install`."
        ),
    )

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
        help="Serve the live session viewer (reads sessions/ in near-realtime).",
        description=(
            "Start a read-only local web app over the sessions/ directory: an "
            "index of every run, and a per-session chat view that tails "
            "events.jsonl while a run is active. Loopback-only."
        ),
    )
    viz_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to deep-link on startup; defaults to the latest session.",
    )
    viz_p.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind on 127.0.0.1 (default: 8765).",
    )

    return parser


def _dispatch(args) -> int:
    if args.command == "init":
        return loop.do_init_cmd()
    if args.command == "run":
        return loop.do_run_cmd(args.workspace)
    if args.command == "resume":
        return loop.do_resume_cmd(args.session_id)
    if args.command == "reset":
        return loop.do_reset_cmd(args.session_id, args.yes)
    if args.command == "visualize":
        return loop.do_visualize_cmd(args.session_id, port=args.port)
    raise AssertionError(f"unknown subcommand {args.command!r}")


def main() -> int:
    _load_env()
    # Re-resolve after the .env is loaded so a .env-provided $TILTH_SESSIONS_DIR
    # (or $TILTH_HOME) takes effect; loop.SESSIONS_DIR was set at import time.
    loop.SESSIONS_DIR = paths.sessions_dir()
    argv = sys.argv[1:]

    parser = _build_parser()

    if not argv:
        _print_help(parser)
        return 1

    if argv[0] in {"-h", "--help"}:
        _print_help(parser)
        return 0

    args = parser.parse_args(argv)
    if args.command is None:
        _print_help(parser)
        return 1
    return _dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
