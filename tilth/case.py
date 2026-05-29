"""Worker `submit_case` schema, parsing, and prompt rendering (v1 Phase 3).

The worker no longer signals "done" by ceasing to call tools. Instead, when
it believes the task is complete, it calls `submit_case` with a structured
argument: a summary, an explicit AC↔change mapping (`ac_coverage`), any
`work_arounds` it had to make, and `uncertainties` it wants flagged. The
evaluator reads this case alongside the diff and the ledger.

This module is the worker-side mirror of `tilth/verdict.py` (evaluator side):
same tool-call + defensive-parse + value-local-normalize + single-error
pattern. Bump `CASE_SCHEMA_VERSION` on shape changes; no migration.

`submit_case` is a *control-flow* tool — it ends the worker's turn — not a
worktree operation, so it is NOT in `tilth/tools` REGISTRY. Its schema is
offered to the worker via the `tools=` list and intercepted in
`loop._run_task`, parallel to how `submit_verdict` is intercepted on the
evaluator side.
"""

from __future__ import annotations

import json
import re
from typing import Any

CASE_SCHEMA_VERSION = 1
NAME_SUBMIT_CASE = "submit_case"

WORK_AROUNDS_CAP = 5  # OQ #2: force the worker to triage rather than list everything

_TOP_KEYS = frozenset({"summary", "ac_coverage", "work_arounds", "uncertainties"})
_AC_KEYS = frozenset({"criterion", "addressed_by", "evidence"})

SUBMIT_CASE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NAME_SUBMIT_CASE,
        "description": (
            "Submit your case that the task is complete. Call this exactly "
            "once, when the work is done and verified — it ends your turn and "
            "hands the case to an independent reviewer. Present the case "
            "honestly: map each acceptance criterion to the change that "
            "satisfies it, name any work-arounds you had to make, and flag "
            "anything you're unsure about. This is not a place to argue past "
            "a failing test — the mechanical checks run regardless."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "ac_coverage"],
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One to three sentences: what you did.",
                },
                "ac_coverage": {
                    "type": "array",
                    "description": (
                        "One entry per acceptance criterion you addressed."
                    ),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["criterion", "addressed_by"],
                        "properties": {
                            "criterion": {
                                "type": "string",
                                "description": "The AC text (or a clear paraphrase).",
                            },
                            "addressed_by": {
                                "type": "string",
                                "description": (
                                    "A file:symbol pointer with a brief "
                                    "annotation, e.g. "
                                    "'todo_cli/__main__.py:main() — argparse "
                                    "handles add'. A pointer, not prose."
                                ),
                            },
                            "evidence": {
                                "type": "string",
                                "description": (
                                    "Optional: the test that proves it, e.g. "
                                    "'tests/test_t002.py::test_add'."
                                ),
                            },
                        },
                    },
                },
                "work_arounds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Things you had to touch that the AC doesn't name "
                        "(e.g. side-effect files of an authorised command). "
                        f"Triage to the {WORK_AROUNDS_CAP} that matter most."
                    ),
                },
                "uncertainties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Ambiguities you resolved by guessing, or anything "
                        "you want the reviewer to double-check."
                    ),
                },
            },
        },
    },
}


# A string "looks like a pointer" unless it is clearly a prose sentence. We
# only reject the obviously-prose case (the sketch's mitigation #1) — terse
# pointers without a classic path token (e.g. "main() in __main__") pass.
_POINTER_RE = re.compile(r"[/]|::|\.[A-Za-z]{1,5}\b|\w+\.\w+|:\d+")


def _looks_like_pointer(s: str) -> bool:
    if _POINTER_RE.search(s):
        return True
    return len(s.split()) < 8


def _normalize(args: dict[str, Any]) -> dict[str, Any]:
    """Value-local cleanup before validation (mirrors verdict._normalize).

    Optional list fields absent/None → []; empty/whitespace-only strings are
    dropped from the list fields (an empty work-around is noise, not a claim).
    No cross-field heuristics.
    """
    out = dict(args)
    for key in ("work_arounds", "uncertainties"):
        val = out.get(key)
        if val is None:
            out[key] = []
        elif isinstance(val, list):
            out[key] = [s for s in val if not (isinstance(s, str) and not s.strip())]
    return out


def _validate(args: dict[str, Any]) -> str | None:
    """Return the first schema violation, or None. Single-error by design."""
    extra = set(args) - _TOP_KEYS
    if extra:
        return f"unexpected keys: {sorted(extra)}"

    summary = args.get("summary")
    if summary is None:
        return "missing required field 'summary'"
    if not isinstance(summary, str) or not summary.strip():
        return "'summary' must be a non-empty string"

    ac = args.get("ac_coverage")
    if ac is None:
        return "missing required field 'ac_coverage'"
    if not isinstance(ac, list):
        return "'ac_coverage' must be a list"
    for i, entry in enumerate(ac):
        if not isinstance(entry, dict):
            return f"ac_coverage[{i}] must be an object"
        extra = set(entry) - _AC_KEYS
        if extra:
            return f"ac_coverage[{i}] has unexpected keys: {sorted(extra)}"
        crit = entry.get("criterion")
        if not isinstance(crit, str) or not crit.strip():
            return f"ac_coverage[{i}] missing non-empty 'criterion'"
        addr = entry.get("addressed_by")
        if not isinstance(addr, str) or not addr.strip():
            return f"ac_coverage[{i}] missing non-empty 'addressed_by'"
        if not _looks_like_pointer(addr):
            return (
                f"ac_coverage[{i}] 'addressed_by' reads as prose, not a "
                "file:symbol pointer — cite where the work lives "
                "(e.g. 'todo_cli/__main__.py:main()'), don't describe it"
            )
        ev = entry.get("evidence")
        if ev is not None and not isinstance(ev, str):
            return f"ac_coverage[{i}] 'evidence' must be a string"

    for key in ("work_arounds", "uncertainties"):
        val = args.get(key, [])
        if not isinstance(val, list) or any(not isinstance(s, str) for s in val):
            return f"'{key}' must be a list of strings"
    if len(args.get("work_arounds", [])) > WORK_AROUNDS_CAP:
        return (
            f"too many 'work_arounds' (max {WORK_AROUNDS_CAP}); triage to the "
            "ones that actually matter"
        )

    return None


def parse_case(
    msg: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Pick the first valid `submit_case` tool call from an assistant message.

    Returns `(case_dict, None)` on success or `(None, error_for_model)` on
    failure. The error is forwarded to the model as `tool_result` content so
    the next attempt can self-correct — the `verdict.parse_verdict` pattern.
    """
    tool_calls = msg.get("tool_calls") or []
    candidate_errors: list[str] = []
    saw = False
    for tc in tool_calls:
        fn = tc.get("function") or {}
        if fn.get("name") != NAME_SUBMIT_CASE:
            continue
        saw = True
        raw = fn.get("arguments")
        if isinstance(raw, dict):
            args = raw
        elif isinstance(raw, str):
            try:
                args = json.loads(raw)
            except json.JSONDecodeError as exc:
                candidate_errors.append(f"JSON parse: {exc}")
                continue
        else:
            candidate_errors.append(
                f"arguments was {type(raw).__name__}, expected str or dict"
            )
            continue
        if not isinstance(args, dict):
            candidate_errors.append("arguments did not parse to a JSON object")
            continue
        args = _normalize(args)
        err = _validate(args)
        if err is None:
            return args, None
        candidate_errors.append(err)

    if not saw:
        return None, (
            "No `submit_case` tool call in your response. When the task is "
            "complete and verified, call `submit_case` to present it."
        )
    return None, (
        "Your `submit_case` call could not be accepted: "
        + " | ".join(candidate_errors)
        + ". Call `submit_case` again with a corrected payload."
    )


def format_case_section(case: dict[str, Any]) -> str:
    """Render the worker's case for injection into the evaluator's prompt."""
    lines = ["## Worker's case", "", f"Summary: {(case.get('summary') or '').strip()}"]

    ac = case.get("ac_coverage") or []
    if ac:
        lines += ["", "AC coverage (worker's claim):"]
        for entry in ac:
            crit = (entry.get("criterion") or "").strip()
            addr = (entry.get("addressed_by") or "").strip()
            ev = (entry.get("evidence") or "").strip()
            line = f"- {crit} → {addr}"
            if ev:
                line += f" [evidence: {ev}]"
            lines.append(line)

    work_arounds = case.get("work_arounds") or []
    if work_arounds:
        lines += ["", "Work-arounds the worker claims (treat skeptically):"]
        lines += [f"- {w}" for w in work_arounds]

    uncertainties = case.get("uncertainties") or []
    if uncertainties:
        lines += ["", "Uncertainties the worker flagged:"]
        lines += [f"- {u}" for u in uncertainties]

    return "\n".join(lines)
