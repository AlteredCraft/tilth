"""The agent must not see harness mechanics — including their *names*.

Regression context (session 20260610-105540-b05876): the worker-prompt section
header read "## Recent progress (last entries from progress.txt)". The worker
treated the filename as an affordance and went hunting — `glob **/progress*`,
a guessed `read_file .tilth/progress.txt`, then broad `**/*.txt` / `**/*.md`
globs — burning a handful of iterations on a file it can't (and shouldn't)
reach. The visibility boundary held at the filesystem; the *prompt wording*
leaked the mechanics.

This test encodes the class, not the instance: no harness-internal filename
may appear in any harness-authored agent-visible text — the assembled worker
prompt, the static prompt files, or the feedback strings the loop injects.
Naming workspace-owned files the worker may legitimately read (AGENTS.md,
CLAUDE.md, the task markdown) is fine and by design; naming session-side
artifacts is not. Describe the *content* ("recent progress"), never the
*container* ("progress.txt").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilth import memory
from tilth.case import parse_case
from tilth.loop import PROMPTS_DIR, WORKER_NO_CASE_NUDGE
from tilth.verdict import format_reject_feedback

# Session-side artifacts the harness owns. The worker reaching for any of these
# is a wild-goose chase at best and boundary-probing at worst.
HARNESS_FILENAMES = (
    "progress.txt",
    "events.jsonl",
    "checkpoint.json",
    "summary.json",
    "task-status.json",
    "seed-meta.json",
    "prd.json",
    "chat.html",
    "ledger/",
    "sessions/",
)


def _assert_clean(text: str, source: str) -> None:
    lowered = text.lower()
    for name in HARNESS_FILENAMES:
        assert name not in lowered, (
            f"harness filename {name!r} leaked into agent-visible text ({source}); "
            "describe the content, not the harness container"
        )


# ---- the assembled worker prompt ---------------------------------------------

@pytest.fixture
def worker_prompt(tmp_path: Path) -> str:
    """Assemble a worker prompt with every channel populated the way the
    harness populates it (progress lines via append_progress, a realistic
    ledger entry, a full plan, an overview)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("# Conventions\n\nUse stdlib only.\n")
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    memory.append_progress(session_dir, "T-001\tdone\tScaffold the package")
    memory.append_progress(session_dir, "T-002\tfailed:provider_failure\tAdd subcommand")

    prd = [
        {"id": "T-001", "title": "Scaffold", "description": "d", "status": "done",
         "acceptance_criteria": ["a"]},
        {"id": "T-002", "title": "Add", "description": "d", "status": "pending",
         "acceptance_criteria": ["a", "b"]},
    ]
    own_ledger = [{
        "iter": 3,
        "diff_summary": "1 file changed",
        "case": {"summary": "did the thing", "criteria": [], "uncertainties": []},
        "verdict": {"verdict": "reject", "rejection_category": "weak_evidence",
                    "concern": "c", "evidence": ["e"], "next_step": "n"},
    }]
    prompt, _ = memory.build_user_prompt(
        {"id": "T-002", "title": "Add", "description": "d",
         "acceptance_criteria": ["a", "b"]},
        workspace,
        session_dir,
        prd=prd,
        own_ledger=own_ledger,
        overview="Build a todo CLI.",
    )
    return prompt


def test_worker_prompt_names_no_harness_files(worker_prompt: str):
    _assert_clean(worker_prompt, "memory.build_user_prompt")


def test_worker_prompt_still_describes_progress(worker_prompt: str):
    # The channel itself must survive the rewording — content stays, name goes.
    assert "T-001\tdone\tScaffold the package" in worker_prompt
    assert "progress" in worker_prompt.lower()


# ---- static prompt files -------------------------------------------------------

@pytest.mark.parametrize("prompt_file", ["system.md", "evaluator.md"])
def test_prompt_files_name_no_harness_files(prompt_file: str):
    _assert_clean((PROMPTS_DIR / prompt_file).read_text(), prompt_file)


# ---- harness-authored feedback strings ----------------------------------------

def test_no_case_nudge_names_no_harness_files():
    _assert_clean(WORKER_NO_CASE_NUDGE, "WORKER_NO_CASE_NUDGE")


def test_case_parse_error_feedback_names_no_harness_files():
    msg = {
        "role": "assistant",
        "tool_calls": [{
            "id": "c1",
            "function": {"name": "submit_case", "arguments": '{"summary": broken'},
        }],
    }
    _case, err = parse_case(msg)
    assert err is not None
    _assert_clean(err, "parse_case error feedback")


def test_reject_feedback_names_no_harness_files():
    feedback = format_reject_feedback({
        "verdict": "reject",
        "rejection_category": "weak_evidence",
        "concern": "the criterion is not demonstrated",
        "evidence": ["no test output shown"],
        "next_step": "run the command and capture output",
    })
    _assert_clean(feedback, "format_reject_feedback")


def test_tool_schemas_name_no_harness_files():
    from tilth import tools
    from tilth.case import SUBMIT_CASE_TOOL

    for schema in [*tools.schemas(), SUBMIT_CASE_TOOL]:
        _assert_clean(json.dumps(schema), f"tool schema {schema['function']['name']}")
