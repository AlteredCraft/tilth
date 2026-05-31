"""Evaluator verdict schema, parsing, and worker-visible feedback assembly.

This module owns three things:

1. The `submit_verdict` **tool definition** the evaluator model is given (OpenAI
   tool-call shape). Schema lives here, not in the prompt — that's the
   point of Phase 1's "verdict as a tool call" decision in
   `proposals/completed/v1-implementation-plan.md`.

2. The defensive **parser** that pulls a verdict out of an assistant
   message's `tool_calls`. Iterates the list, takes the first call that
   names `submit_verdict`, parses as JSON, and validates against the
   schema. Designed against the DSML-leakage finding from the probe
   (`proposals/probes/phase1_verdict_tool_call_probe.py`) — corrupted
   sibling tool calls are skipped, not fatal.

3. The **worker-visible feedback template** built from a structured
   reject verdict. The worker sees `concern + evidence + next_step` as
   discrete fields, not free prose — that's the actionable shape Phase 1
   is buying.

Bump `VERDICT_SCHEMA_VERSION` when the schema changes shape. No migration
of in-flight sessions (v0/v1 sessions are not cross-compatible by design).
"""

from __future__ import annotations

import json
from typing import Any

VERDICT_SCHEMA_VERSION = 1

REJECTION_CATEGORIES: tuple[str, ...] = (
    "scope_creep",
    "acceptance_gap",
    "weak_test",
    "tests_pass_but_wrong",
    "half_finished",
    "spec_violation",
)

_ALLOWED_KEYS = frozenset(
    {"verdict", "rejection_category", "concern", "evidence", "next_step"}
)

SUBMIT_VERDICT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_verdict",
        "description": (
            "Submit your final verdict on whether the worker's diff "
            "satisfies the task. Call this exactly once. The tool call is "
            "the only acceptable response — do not also reply with prose."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "concern", "evidence"],
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["accept", "reject"],
                    "description": "The gate: accept or reject.",
                },
                "rejection_category": {
                    "type": ["string", "null"],
                    "enum": [*REJECTION_CATEGORIES, None],
                    "description": (
                        "If verdict is 'reject', name the category. "
                        "Must be null when verdict is 'accept'."
                    ),
                },
                "concern": {
                    "type": "string",
                    "description": (
                        "One to three sentences explaining the verdict."
                    ),
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Pointers like 'path/to/file.py:42' or "
                        "'tests/test_x.py::test_y'. Cite, don't argue."
                    ),
                },
                "next_step": {
                    "type": ["string", "null"],
                    "description": (
                        "If verdict is 'reject', concrete remediation the "
                        "worker can act on. Null when accept."
                    ),
                },
            },
        },
    },
}


def _normalize(args: dict[str, Any]) -> dict[str, Any]:
    """Value-local cleanup applied before validation.

    Models intermittently emit `""` for an optional field instead of
    omitting it or sending JSON `null` (observed on accepts where the
    model filled `rejection_category`/`next_step` with empty strings).
    An empty string is unambiguously "no value", so we coerce it to None.

    Deliberately *not* a cross-field heuristic: we do not null a field
    based on the value of `verdict`. An accept that carries a real
    category (e.g. `"scope_creep"`) is a genuine contradiction and must
    still fail validation — we don't paper over it.
    """
    out = dict(args)
    for key in ("rejection_category", "next_step"):
        if isinstance(out.get(key), str) and not out[key].strip():
            out[key] = None
    return out


def _validate(args: dict[str, Any]) -> str | None:
    """Return the first schema violation, or None if the payload is clean.

    Single-error-only by design: the model gets one focused complaint to
    fix at a time, which matches how the existing tool-arg recovery
    feedback works (see `tilth/seed/interview.py:138-184`).
    """
    extra = set(args) - _ALLOWED_KEYS
    if extra:
        return f"unexpected keys: {sorted(extra)}"

    verdict = args.get("verdict")
    if verdict is None:
        return "missing required field 'verdict'"
    if verdict not in ("accept", "reject"):
        return (
            f"'verdict' must be 'accept' or 'reject'; got {verdict!r}"
        )

    concern = args.get("concern")
    if not isinstance(concern, str) or not concern.strip():
        return "'concern' must be a non-empty string"

    evidence = args.get("evidence")
    if not isinstance(evidence, list) or any(not isinstance(e, str) for e in evidence):
        return "'evidence' must be a list of strings"

    cat = args.get("rejection_category")
    if verdict == "accept":
        if cat is not None:
            return (
                "'rejection_category' must be null when verdict is 'accept'"
            )
    else:
        if cat is None:
            return (
                "'rejection_category' is required when verdict is 'reject'"
            )
        if cat not in REJECTION_CATEGORIES:
            return (
                f"'rejection_category' must be one of "
                f"{list(REJECTION_CATEGORIES)}; got {cat!r}"
            )

    next_step = args.get("next_step")
    if verdict == "reject":
        if not isinstance(next_step, str) or not next_step.strip():
            return "'next_step' must be a non-empty string when verdict is 'reject'"

    return None


def parse_verdict(
    msg: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Pick the first valid `submit_verdict` tool call from an assistant message.

    Returns `(verdict_dict, None)` on success or `(None, error_for_model)`
    on failure. The error string is designed to be forwarded to the model
    as `tool_result` content so the next attempt has a fix-it-yourself
    hint, mirroring the seed interview's parse-error recovery pattern.
    """
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        return None, (
            "No `submit_verdict` tool call in your response. "
            "Submit your verdict by calling `submit_verdict` — the tool "
            "call is the only acceptable response."
        )

    candidate_errors: list[str] = []
    saw_submit_verdict = False
    for tc in tool_calls:
        fn = tc.get("function") or {}
        if fn.get("name") != "submit_verdict":
            continue
        saw_submit_verdict = True
        raw_args = fn.get("arguments")
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                candidate_errors.append(f"JSON parse: {exc}")
                continue
        else:
            candidate_errors.append(
                f"arguments was {type(raw_args).__name__}, expected str or dict"
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

    if not saw_submit_verdict:
        return None, (
            "No `submit_verdict` tool call in your response. "
            "Submit your verdict by calling `submit_verdict`."
        )

    return None, (
        "Your `submit_verdict` tool call(s) could not be parsed: "
        + " | ".join(candidate_errors)
        + ". Call `submit_verdict` again with a valid payload."
    )


_REJECT_TEMPLATE = """\
An independent reviewer rejected the work.

Rejection category: {category}

Concern: {concern}
{evidence_section}\
Next step: {next_step}

Read the concern and next step carefully, apply the next step, and continue \
working. Submit a new summary only when the issue is resolved."""


def format_reject_feedback(verdict: dict[str, Any]) -> str:
    """Build the worker-visible message that surfaces a structured reject.

    The shape is intentionally legible to a downstream reviewer too: the
    rejection_category, the concern, the evidence pointers, and the
    next_step are all on their own labelled lines — not buried in prose.
    """
    category = verdict.get("rejection_category") or "(unspecified)"
    concern = (verdict.get("concern") or "").strip()
    next_step = (verdict.get("next_step") or "").strip() or "(none provided)"
    evidence = verdict.get("evidence") or []

    if evidence:
        bullets = "\n".join(f"- {item}" for item in evidence)
        evidence_section = f"\nEvidence:\n{bullets}\n"
    else:
        evidence_section = ""

    return _REJECT_TEMPLATE.format(
        category=category,
        concern=concern,
        evidence_section=evidence_section,
        next_step=next_step,
    )


_LEDGER_HEADER = "## Prior iterations on this task"


def format_ledger_section(
    entries: list[dict[str, Any]], header: str = _LEDGER_HEADER
) -> str:
    """Render prior ledger entries for injection into a prompt.

    Oldest first. Each line carries the iteration, the verdict (+ category on
    a reject), the concern, the next_step given, and the diff summary at that
    point — enough for the evaluator to recognise repeats and escalate without
    re-reading old diffs. Empty input → empty string (no section injected).

    The evaluator uses the default header; Phase 4 reuses this to show the
    worker its *own* task ledger under a clarifying header (`from the
    evaluator`). Data only — guidance on *how* to use this lives in the
    respective prompt (evaluator.md / system.md).
    """
    if not entries:
        return ""

    lines = [header, ""]
    for n, entry in enumerate(entries, start=1):
        verdict = entry.get("verdict") or {}
        v = verdict.get("verdict") or "?"
        category = verdict.get("rejection_category")
        head = f"{v} · {category}" if (v == "reject" and category) else v
        iter_n = entry.get("iter", "?")
        lines.append(f"{n}. [iter {iter_n}] {head}")

        concern = (verdict.get("concern") or "").strip()
        if concern:
            lines.append(f"   concern: {concern}")
        next_step = (verdict.get("next_step") or "").strip()
        if next_step:
            lines.append(f"   next step given: {next_step}")
        diff_summary = (entry.get("diff_summary") or "").strip()
        if diff_summary:
            lines.append(f"   diff at that point: {diff_summary}")
    return "\n".join(lines)
