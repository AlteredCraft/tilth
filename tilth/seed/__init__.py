"""Seed-the-task-list — `tilth prep-feature`'s interview engine.

Drives a fresh tool-use loop against the configured reasoning model. The model
runs an anchored interview against the user's source repo, then makes a single
terminal `write_seed` call. The engine persists the bundle atomically and flips
the session's checkpoint status to `prepared`. A subsequent `tilth run` picks
the session up, creates the worktree, and starts the worker loop.

This package is intentionally separate from the worker tool registry in
`tilth/tools/`: the seeder is read-only against the source repo until the
terminal write, and exposes only the tools that posture allows.

Public surface:
    InterviewFrontend, SeedSink   — protocols a frontend / sink implements
    TTYFrontend                    — terminal implementation of InterviewFrontend
    FileSeedSink                   — atomic on-disk implementation of SeedSink
    run_interview                  — drives the loop end-to-end
"""

from __future__ import annotations

from .frontend import InterviewFrontend, SeedSink
from .interview import InterviewResult, run_interview
from .sink import FileSeedSink, SeedWriteError
from .tty import TTYFrontend

__all__ = [
    "FileSeedSink",
    "InterviewFrontend",
    "InterviewResult",
    "SeedSink",
    "SeedWriteError",
    "TTYFrontend",
    "run_interview",
]
