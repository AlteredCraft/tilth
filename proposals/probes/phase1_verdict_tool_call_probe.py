"""Probe: can we make the judge's verdict a forced tool call?

Question this answers, per v1-implementation-plan.md Phase 1:
  Option 3 of the malformed-JSON discussion (verdict-as-tool-call) depends
  on the judge provider reliably:
    (a) emitting a tool call when `tool_choice` forces a named function,
    (b) honouring schema enums on `rejection_category`,
    (c) returning a response shape the existing loop.py tool-result code
        path (`tc.get("function").get("arguments")`) can read.

  This script runs the same Tilth judge config (from .env) against a tiny
  fabricated task and reports what the wire actually returns. CLAUDE.md's
  "verify, don't guess" rule — don't design Phase 1 around a tool-choice
  contract we haven't seen the provider honour.

Run:
  uv run python proposals/probes/phase1_verdict_tool_call_probe.py

Notes:
- Calls the OpenAI SDK directly (not Tilth's LLMClient.chat) so we can pass
  `tool_choice`, which `chat()` doesn't currently expose. The transport and
  base_url/api_key/model selection matches Tilth's judge path.
- Sends `extra_body={"reasoning": {"enabled": True}}` because that's what
  Tilth sends on OpenRouter today; we want the probe wire shape to match
  production wire shape.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

# --- expected verdict schema (Phase 1 of v1-implementation-plan.md) ---------

REJECTION_CATEGORIES = [
    "scope_creep",
    "acceptance_gap",
    "weak_test",
    "tests_pass_but_wrong",
    "half_finished",
    "spec_violation",
]

SUBMIT_VERDICT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_verdict",
        "description": (
            "Submit your final verdict on whether the worker's diff satisfies "
            "the task. Call this exactly once; do not also respond with prose."
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
                    "enum": REJECTION_CATEGORIES + [None],
                    "description": (
                        "If verdict is 'reject', name the category. "
                        "Must be null when verdict is 'accept'."
                    ),
                },
                "concern": {
                    "type": "string",
                    "description": "One to three sentences explaining the verdict.",
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

JUDGE_SYSTEM = (
    "You are an independent code reviewer judging whether a single development "
    "task was completed correctly. You see only the task description, the "
    "acceptance criteria, and the diff. Submit your verdict via the "
    "`submit_verdict` tool call. Do not respond with prose; the tool call "
    "is the only acceptable response."
)


# --- two minimal scenarios ---------------------------------------------------

ACCEPT_SCENARIO = """\
# Task: T-001 — Add a `greet` function

## Description
Create a function `greet(name: str) -> str` in `pkg/hello.py` that returns
exactly `f"Hello, {name}!"`.

## Acceptance criteria
- `pkg/hello.py` exists and defines `greet`.
- `greet("Sam")` returns `"Hello, Sam!"`.

## Diff
```diff
diff --git a/pkg/hello.py b/pkg/hello.py
new file mode 100644
+++ b/pkg/hello.py
@@ -0,0 +1,2 @@
+def greet(name: str) -> str:
+    return f"Hello, {name}!"
```

## Validator status
All validators (ruff, pytest) PASSED.
"""

REJECT_SCENARIO = """\
# Task: T-001 — Add a `greet` function

## Description
Create a function `greet(name: str) -> str` in `pkg/hello.py` that returns
exactly `f"Hello, {name}!"`. It must also handle an empty name by returning
`"Hello, friend!"`.

## Acceptance criteria
- `pkg/hello.py` exists and defines `greet`.
- `greet("Sam")` returns `"Hello, Sam!"`.
- `greet("")` returns `"Hello, friend!"`.

## Diff
```diff
diff --git a/pkg/hello.py b/pkg/hello.py
new file mode 100644
+++ b/pkg/hello.py
@@ -0,0 +1,2 @@
+def greet(name: str) -> str:
+    return f"Hello, {name}!"
```

## Validator status
All validators (ruff, pytest) PASSED. (The seed test only covered the
non-empty case; the empty-name AC is not exercised by tests.)
"""


# --- probe core --------------------------------------------------------------


def validate_against_schema(args: dict[str, Any]) -> list[str]:
    """Return a list of validation errors; empty list = clean."""
    errs: list[str] = []
    verdict = args.get("verdict")
    if verdict not in ("accept", "reject"):
        errs.append(f"verdict not in enum: {verdict!r}")
    cat = args.get("rejection_category")
    if verdict == "accept" and cat not in (None, ""):
        errs.append(f"accept verdict has non-null rejection_category: {cat!r}")
    if verdict == "reject" and cat not in REJECTION_CATEGORIES:
        errs.append(f"reject verdict has bad rejection_category: {cat!r}")
    if not isinstance(args.get("concern"), str) or not args["concern"].strip():
        errs.append("concern missing or empty")
    if not isinstance(args.get("evidence"), list):
        errs.append("evidence missing or not a list")
    next_step = args.get("next_step")
    if verdict == "reject" and (not isinstance(next_step, str) or not next_step.strip()):
        errs.append("reject verdict has no usable next_step")
    extra = set(args) - {"verdict", "rejection_category", "concern", "evidence", "next_step"}
    if extra:
        errs.append(f"unexpected extra keys: {sorted(extra)}")
    return errs


def run_scenario(
    client: OpenAI,
    model: str,
    base_url: str,
    label: str,
    user_prompt: str,
    *,
    force_tool: bool,
) -> None:
    print(f"\n{'=' * 72}")
    print(f"SCENARIO: {label}  (force_tool={force_tool})")
    print("=" * 72)

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [SUBMIT_VERDICT_TOOL],
    }
    if force_tool:
        kwargs["tool_choice"] = {
            "type": "function",
            "function": {"name": "submit_verdict"},
        }
    if "openrouter.ai" in base_url:
        kwargs["extra_body"] = {"reasoning": {"enabled": True}}

    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:
        print(f"  REQUEST FAILED: {type(exc).__name__}: {exc}")
        return

    d = resp.model_dump()
    choices = d.get("choices") or []
    choice = choices[0] if choices else {}
    msg = choice.get("message") or {}
    usage = d.get("usage") or {}

    print(f"  finish_reason: {choice.get('finish_reason')!r}")
    print(f"  usage: prompt={usage.get('prompt_tokens')} "
          f"completion={usage.get('completion_tokens')} "
          f"total={usage.get('total_tokens')}")
    content = msg.get("content")
    if content and content.strip():
        print(f"  WARNING: message.content was non-empty (model produced prose): "
              f"{content[:200]!r}")
    else:
        print("  message.content: (empty, as expected)")

    tool_calls = msg.get("tool_calls") or []
    print(f"  tool_calls: {len(tool_calls)} emitted")
    if not tool_calls:
        print("  RESULT: no tool call. This kills option 3 for this provider.")
        return

    for i, tc in enumerate(tool_calls):
        fn = (tc.get("function") or {})
        name = fn.get("name")
        raw_args = fn.get("arguments")
        print(f"    [{i}] function.name: {name!r}")
        print(f"    [{i}] arguments (raw, {len(raw_args) if isinstance(raw_args, str) else '?'} chars):")
        if isinstance(raw_args, str):
            print(f"         {raw_args[:500]}")
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                print(f"    [{i}] JSON PARSE FAILED: {exc}")
                continue
        elif isinstance(raw_args, dict):
            parsed = raw_args
            print("         (already-parsed dict; some SDKs do this)")
        else:
            print(f"    [{i}] unexpected arguments type: {type(raw_args)}")
            continue

        print(f"    [{i}] parsed:")
        print(json.dumps(parsed, indent=6))
        errs = validate_against_schema(parsed)
        if errs:
            print(f"    [{i}] SCHEMA VIOLATIONS: {errs}")
        else:
            print(f"    [{i}] SCHEMA OK")


def main() -> int:
    load_dotenv()
    base_url = (
        os.environ.get("TILTH_JUDGE_BASE_URL", "").strip()
        or os.environ.get("TILTH_BASE_URL", "").strip()
    )
    api_key = (
        os.environ.get("TILTH_JUDGE_API_KEY", "").strip()
        or os.environ.get("TILTH_API_KEY", "").strip()
    )
    model = (
        os.environ.get("TILTH_JUDGE_MODEL", "").strip()
        or os.environ.get("TILTH_WORKER_MODEL", "").strip()
    )
    if not (base_url and api_key and model):
        print("Missing env: TILTH_JUDGE_BASE_URL/TILTH_BASE_URL, "
              "TILTH_JUDGE_API_KEY/TILTH_API_KEY, "
              "TILTH_JUDGE_MODEL/TILTH_WORKER_MODEL. Source .env or export them.")
        return 2

    print(f"Probe target: {model}  via  {base_url}")
    client = OpenAI(base_url=base_url, api_key=api_key)

    # 1. forced tool call, accept scenario — does the schema/enum hold?
    run_scenario(client, model, base_url, "accept-case (forced)",
                 ACCEPT_SCENARIO, force_tool=True)

    # 2. forced tool call, reject scenario — does rejection_category land in enum?
    run_scenario(client, model, base_url, "reject-case (forced)",
                 REJECT_SCENARIO, force_tool=True)

    # 3. unforced — would the model volunteer the tool call without tool_choice?
    #    Informational only; option 3 will set tool_choice in production.
    run_scenario(client, model, base_url, "reject-case (unforced)",
                 REJECT_SCENARIO, force_tool=False)

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
