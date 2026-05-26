"""TTY implementation of InterviewFrontend.

Plain `input()` for free-form, numbered menu for options (with `0` for "Other"
to match the convention the prompts.md recommends). Running token totals are
cached from `update_tokens` and printed on the prompt line so the user sees
cost between turns. No external deps beyond `rich` (already a Tilth dep).
"""

from __future__ import annotations

from rich.console import Console


class TTYFrontend:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._prompt_total = 0
        self._completion_total = 0

    # --- InterviewFrontend ---------------------------------------------------

    def ask_user(self, question: str, options: list[str] | None = None) -> str:
        self._print_token_strip()
        self.console.print()
        self.console.print(f"[bold cyan]?[/bold cyan] {question}")

        if not options:
            try:
                return input("> ").strip()
            except EOFError:
                return ""

        for i, opt in enumerate(options, start=1):
            self.console.print(f"  [dim]{i})[/dim] {opt}")
        self.console.print("  [dim]0)[/dim] Other (I'll specify)")

        while True:
            try:
                raw = input("> ").strip()
            except EOFError:
                return ""
            if not raw:
                continue
            if raw == "0":
                try:
                    free = input("Other: ").strip()
                except EOFError:
                    return ""
                return free
            try:
                idx = int(raw)
            except ValueError:
                # Treat anything non-numeric as a verbatim answer (escape hatch
                # for users who'd rather type their own response than navigate
                # the menu).
                return raw
            if 1 <= idx <= len(options):
                return options[idx - 1]
            self.console.print(
                f"[yellow]pick 1-{len(options)}, 0 for free-form, or type your answer[/yellow]"
            )

    def show_summary(
        self,
        tldr: str,
        open_questions: list[str],
        blockers: list[str],
    ) -> None:
        self.console.print()
        self.console.print("[bold green]── seed written ──[/bold green]")
        if tldr.strip():
            self.console.print()
            self.console.print(tldr.rstrip())
        if open_questions:
            self.console.print()
            self.console.print("[bold]Open questions[/bold]")
            for q in open_questions:
                self.console.print(f"  • {q}")
        if blockers:
            self.console.print()
            self.console.print("[bold yellow]Blockers / contradictions[/bold yellow]")
            for b in blockers:
                self.console.print(f"  • {b}")
        self.console.print()

    def update_tokens(self, prompt_total: int, completion_total: int) -> None:
        self._prompt_total = prompt_total
        self._completion_total = completion_total

    # --- internals -----------------------------------------------------------

    def _print_token_strip(self) -> None:
        total = self._prompt_total + self._completion_total
        if total == 0:
            return
        self.console.print(
            f"[dim][interview · prompt {self._prompt_total:,} · "
            f"completion {self._completion_total:,} · total {total:,} tokens][/dim]"
        )
