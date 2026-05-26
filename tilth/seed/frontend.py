"""Frontend / sink protocols for the interview engine.

`InterviewFrontend` is what `ask_user` calls route through, plus the closing
`show_summary` and the running-token surface. `SeedSink` is the terminal
`write_seed` target. Two protocols, not one, because the frontend is about
*user interaction* (TTY, future TUI/web) while the sink is about *persistence*
(filesystem, or an in-memory test double).

Keeping them as Protocols (not ABCs) lets tests pass tiny stubs without
inheritance ceremony.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class InterviewFrontend(Protocol):
    """How the interview talks to a human (or a test stub)."""

    def ask_user(self, question: str, options: list[str] | None = None) -> str:
        """Pose a question and wait for the answer.

        `options` is None for free-form input. Otherwise it's a list of menu
        choices the user picks from (the implementation may also accept a
        free-form fallback). Returns the user's answer verbatim — for menu
        choices, the chosen option string.
        """
        ...

    def show_summary(
        self,
        tldr: str,
        open_questions: list[str],
        blockers: list[str],
    ) -> None:
        """Render the closing summary after a successful seed write."""
        ...

    def update_tokens(self, prompt_total: int, completion_total: int) -> None:
        """Called after every model turn with running prompt/completion totals.

        Frontend renders or stashes for the next ask_user prompt. Total = sum.
        """
        ...


class SeedSink(Protocol):
    """How the interview persists its output."""

    def write_seed(
        self,
        session_dir: Path,
        workspace: Path,
        prd_entries: list[dict[str, Any]],
        test_files: dict[str, str],
        meta: dict[str, Any],
    ) -> None:
        """Atomically persist the seed bundle.

        Either all four pieces (prd.json, seed-meta.json, every test file in
        `test_files`) land on disk, or none of them do. Raises on contract
        violation or filesystem failure.
        """
        ...
