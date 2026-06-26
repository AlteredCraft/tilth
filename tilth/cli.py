"""Verb-routed CLI entry point.

Subcommands:

    tilth run       <feature-dir>
    tilth resume    [<session_id>]
    tilth push      [<session_id>] [--remote NAME]
    tilth pr        [<session_id>] [--base BRANCH] [--remote NAME] [--web]
    tilth reset     [<session_id>] [-y]
    tilth visualize [<session_id>] [--port N]
    tilth info      [<session_id>]
    tilth config

The feature is authored as markdown in a feature directory (conventionally
`<repo>/.tilth/<feature>/`): an `overview.md` plus one `T-NNN-*.md` per task —
see `tilth/tasks.py`. There is no separate prep step: `tilth run` is given that
directory's path, derives the enclosing git repo, creates a fresh session +
worktree, and runs the Ralph loop.

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

SUBCOMMANDS = frozenset(
    {"init", "run", "resume", "push", "pr", "reset", "visualize", "info", "config"}
)


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
        help="Run the worker loop against a feature directory.",
        description=(
            "Read a feature from the given directory (overview.md + one T-NNN-*.md "
            "per task), derive its git repo, create a fresh session + worktree, and "
            "run the Ralph loop. Fails fast with the templates if the directory has "
            "no feature."
        ),
    )
    run_p.add_argument(
        "feature_dir",
        type=Path,
        help="Path to the feature directory (e.g. <repo>/.tilth/<feature>/) "
        "holding overview.md + T-NNN-*.md.",
    )

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

    push_p = sub.add_parser(
        "push",
        help="Push a session's branch to a remote (default origin).",
        description=(
            "Push the session/<id> branch to a remote so the work can be reviewed "
            "off your machine. User-invoked and opt-in; Tilth never merges it for you."
        ),
    )
    push_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to push; defaults to the latest session.",
    )
    push_p.add_argument(
        "--remote", default="origin", help="Remote to push to (default: origin)."
    )

    pr_p = sub.add_parser(
        "pr",
        help="Push a session's branch and open a pull request.",
        description=(
            "Ensure the session/<id> branch is on the remote, then open a PR against "
            "the base branch. Uses the gh CLI when available; otherwise prints the "
            "GitHub compare URL so you can open the PR yourself. Never merges."
        ),
    )
    pr_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to open a PR for; defaults to the latest session.",
    )
    pr_p.add_argument(
        "--base",
        default=None,
        help="Base branch for the PR (default: the remote's default branch, then main).",
    )
    pr_p.add_argument(
        "--remote", default="origin", help="Remote to push to (default: origin)."
    )
    pr_p.add_argument(
        "--web",
        action="store_true",
        help="Skip gh; just print the compare URL to open the PR in a browser.",
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

    info_p = sub.add_parser(
        "info",
        help="Show sessions, or one session's full detail (incl. worktree location).",
        description=(
            "Without an id: list every session newest-first with status, task "
            "progress, and tokens. With an id: the full dossier — source repo, "
            "feature, the worktree folder and its git admin dir (the `.git` "
            "mapping), branch, and registration health. Read-only."
        ),
    )
    info_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to detail; omit to list all sessions.",
    )

    sub.add_parser(
        "config",
        help="Show resolved provider config and run caps (API keys masked).",
        description=(
            "Print the configuration the harness would run with — worker and "
            "evaluator endpoints/models, the per-task and per-run caps, and "
            "context files — plus which .env it resolved. API keys are masked. "
            "Works with a partial config; flags what's missing."
        ),
    )

    return parser


def _dispatch(args) -> int:
    if args.command == "init":
        return loop.do_init_cmd()
    if args.command == "run":
        return loop.do_run_cmd(args.feature_dir)
    if args.command == "resume":
        return loop.do_resume_cmd(args.session_id)
    if args.command == "push":
        return loop.do_push_cmd(args.session_id, remote=args.remote)
    if args.command == "pr":
        return loop.do_pr_cmd(
            args.session_id, base=args.base, remote=args.remote, web=args.web
        )
    if args.command == "reset":
        return loop.do_reset_cmd(args.session_id, args.yes)
    if args.command == "visualize":
        return loop.do_visualize_cmd(args.session_id, port=args.port)
    if args.command == "info":
        return loop.do_info_cmd(args.session_id)
    if args.command == "config":
        return loop.do_config_cmd()
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
