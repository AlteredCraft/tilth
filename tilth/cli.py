"""Verb-routed CLI entry point.

Subcommands (the canonical surface from Phase 3 of proposals/completed/prep-feature.md):

    tilth prep-feature <workspace> [--brief TEXT]
    tilth run          <workspace>
    tilth resume       [<session_id>]
    tilth reset        [<session_id>] [-y]
    tilth visualize    [<session_id>]

The Phase 2 flag surface (`--prep-feature`, `--resume`, `--reset`, `--visualize`)
and the pre-Phase-3 bare positional form (`tilth <workspace>`) still dispatch
through tilth.loop._legacy_main for one minor version. The bare positional
form emits a one-line deprecation pointer to `tilth run <workspace>`.

Dispatch order:
  1. No args at all     → print top-level help, exit 1.
  2. First arg is `-h`  → print help, exit 0.
  3. First arg is a known subcommand → parse with the subparser.
  4. Anything else      → hand to `_legacy_main` (with a deprecation note when
                          the user invoked the bare positional form).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from tilth import loop

console = Console()

SUBCOMMANDS = frozenset({"prep-feature", "run", "resume", "reset", "visualize"})
LEGACY_FLAGS = frozenset({"--resume", "--reset", "--visualize", "--prep-feature"})


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tilth",
        description="Tilth — a minimal long-running agent harness.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    prep = sub.add_parser(
        "prep-feature",
        help="Interview the reasoning model to produce a task seed.",
        description=(
            "Run an anchored interview against the source repo's code to "
            "produce a Tilth task seed (prd.json + matching tests). The seeded "
            "session is picked up by a subsequent `tilth run <workspace>`."
        ),
    )
    prep.add_argument("workspace", type=Path, help="Path to the source repo.")
    prep.add_argument(
        "--brief",
        default=None,
        metavar="TEXT",
        help="One-line feature/refactor brief. Prompted interactively if omitted.",
    )
    prep.add_argument(
        "--force",
        action="store_true",
        help=(
            "Auto-discard any blocking sessions (prepared/running/failed) for "
            "this workspace and proceed. Bypasses the interactive picker."
        ),
    )
    prep.add_argument(
        "--keep-existing",
        action="store_true",
        help=(
            "Start a new session alongside any existing in-flight sessions for "
            "this workspace (don't discard them). The next `tilth run` will "
            "refuse until only one prepared session remains. Mutually "
            "exclusive with --force."
        ),
    )

    run_p = sub.add_parser(
        "run",
        help="Run the worker loop against a workspace.",
        description=(
            "Pick up a prepared session for this workspace and run it. If no "
            "prepared session exists, starts a fresh one (which will fail at "
            "load-time unless prd.json is already in sessions/<id>/)."
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
            "Render sessions/<id>/events.jsonl (and seed-meta.json if present) "
            "as a single self-contained HTML page at sessions/<id>/chat.html."
        ),
    )
    viz_p.add_argument(
        "session_id",
        nargs="?",
        help="Session ID to render; defaults to the latest session.",
    )

    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "prep-feature":
        return loop.do_prep_feature_cmd(
            args.workspace,
            args.brief,
            force=args.force,
            keep_existing=args.keep_existing,
        )
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

    first = argv[0]

    # Subcommand path — the canonical Phase 3 surface.
    if first in SUBCOMMANDS:
        parser = _build_parser()
        args = parser.parse_args(argv)
        return _dispatch(args)

    # Top-level help.
    if first in {"-h", "--help"}:
        _build_parser().print_help()
        return 0

    # Back-compat: a legacy flag, or a bare positional workspace path. The
    # legacy entry point (loop._legacy_main) handles the old surface verbatim.
    # If the user gave a bare positional, point them at the new verb.
    if first not in LEGACY_FLAGS and not first.startswith("-"):
        console.print(
            f"[yellow]deprecated:[/yellow] bare `tilth {first}` — use "
            f"[bold]tilth run {first}[/bold]. Continuing as `run` for now."
        )
    return loop._legacy_main()


if __name__ == "__main__":
    sys.exit(main())
