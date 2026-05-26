"""Tool schemas + dispatch for the interview loop.

The seeder exposes a narrower surface than the worker: read-only inspection of
the source repo (read_file, glob, grep — reused from `tilth.tools`), plus two
new tools — `ask_user` (routes to the InterviewFrontend) and `write_seed` (the
terminal call, routes to the SeedSink).

The worker tools `bash`, `write_file`, and `edit_file` are deliberately absent:
the seeder must not mutate the workspace except via the atomic terminal write.
"""

from __future__ import annotations

from typing import Any

from tilth.tools import files as _files
from tilth.tools import search as _search

NAME_ASK_USER = "ask_user"
NAME_WRITE_SEED = "write_seed"


SCHEMA_ASK_USER: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME_ASK_USER,
        "description": (
            "Pose a question to the user and wait for an answer. "
            "Provide `options` (2-4 short, substantive strings) for menu-style "
            "multiple choice; omit for free-form input. Do NOT include an 'Other' "
            "or escape-hatch option — the frontend always surfaces one (the TTY "
            "renders `0) Other (I'll specify)` automatically). "
            "Returns the user's verbatim answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask."},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Menu options. Omit for free-form input.",
                },
            },
            "required": ["question"],
        },
    },
}


SCHEMA_WRITE_SEED: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME_WRITE_SEED,
        "description": (
            "TERMINAL CALL — write the seed bundle atomically and end the interview. "
            "Once you call this, no further tool calls are accepted. "
            "Provide ALL prd entries and ALL matching test files in a SINGLE call. "
            "The harness writes prd.json under sessions/<id>/ and the test files into "
            "<workspace>/tests/. Every prd entry must have a matching test file named "
            "test_<task-id-lower>_<slug>.py (e.g. T-001 → test_t001_<slug>.py); "
            "the harness's pytest filter skips files outside this pattern."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prd_entries": {
                    "type": "array",
                    "description": (
                        "Task list. Each entry: id (T-NNN), title, description, "
                        "acceptance_criteria."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "T-NNN with 3+ zero-padded digits (e.g. T-001).",
                            },
                            "title": {
                                "type": "string",
                                "description": "Short imperative title, verb-first.",
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "What needs to be done, concretely. Reference real file "
                                    "paths and symbols. Becomes the user message the worker reads."
                                ),
                            },
                            "acceptance_criteria": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "2-4 concrete, programmatically-checkable statements. "
                                    "Each maps 1:1 to a test assertion."
                                ),
                            },
                        },
                        "required": ["id", "title", "description", "acceptance_criteria"],
                    },
                },
                "test_files": {
                    "type": "object",
                    "description": (
                        "Map of filename → file content. Filenames must match "
                        "test_t<NNN>_<slug>.py. One file per task."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "tldr": {
                    "type": "string",
                    "description": "One-line-per-task summary shown to the user after writing.",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Things you guessed at or that the user was unsure about.",
                },
                "blockers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Contradictions / hard problems surfaced during the interview.",
                },
                "scope_notes": {
                    "type": "string",
                    "description": (
                        "Free-form scope clarifications worth preserving for the visualizer."
                    ),
                },
            },
            "required": ["prd_entries", "test_files", "tldr"],
        },
    },
}


SCHEMAS: list[dict[str, Any]] = [
    _files.SCHEMA_READ,
    _search.SCHEMA_GLOB,
    _search.SCHEMA_GREP,
    SCHEMA_ASK_USER,
    SCHEMA_WRITE_SEED,
]


# Names the worker registry exposes, made available read-only to the seeder.
# Dispatch routes calls to the existing implementations against the source repo.
READ_TOOLS = {
    _files.NAME_READ: _files.read,
    _search.NAME_GLOB: _search.glob_,
    _search.NAME_GREP: _search.grep,
}
