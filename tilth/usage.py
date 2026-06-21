"""Canonical token-and-cost usage record: the single source of truth for what
a model call cost and how to combine such records.

Every model call yields a `usage` block from the provider. Rather than collapse
it to two numbers at the call site, the harness reads the *full* detail into one
canonical dict and carries it — intact — through the event log, the live
session, the summary, and the visualizer. Lossy aggregation (the token cap, a
display string) happens only at the edges.

Canonical shape (Tilth names completion tokens "eval", matching the long-standing
`summary.tokens.eval` key):

    { "prompt": int, "eval": int, "total": int,
      "cached": int, "reasoning": int, "cost": float }

The load-bearing invariant: `cached` ⊆ `prompt` and `reasoning` ⊆ `eval` — they
are *subsets* of their parent bucket (cache hits among the prompt tokens;
thinking tokens among the completion tokens), never additive. The token cap is
`prompt + eval` (== `total` for a well-formed response); cached/reasoning must
never inflate it. `cost` is the provider's own USD figure — display-only, never
a cap.

Wire shape verified live against OpenRouter (deepseek-v4-flash, 2026-06):

    "usage": {
      "prompt_tokens": 11, "completion_tokens": 24, "total_tokens": 35,
      "prompt_tokens_details":     {"cached_tokens": 0, "cache_write_tokens": 0, ...},
      "completion_tokens_details": {"reasoning_tokens": 21, ...},
      "cost": 5.9e-06, "cost_details": {...}
    }

Other OpenAI-compatible providers may omit the `*_details` objects and `cost`;
`extract_usage` degrades to prompt/eval/total with the rest zeroed.
"""

from __future__ import annotations

from typing import Any

# The integer fields, in display order. `cost` is handled alongside as a float.
USAGE_INT_FIELDS = ("prompt", "eval", "total", "cached", "reasoning")


def zero_usage() -> dict[str, Any]:
    """A fresh all-zero canonical usage dict."""
    u: dict[str, Any] = {f: 0 for f in USAGE_INT_FIELDS}
    u["cost"] = 0.0
    return u


def _int(v: Any) -> int:
    """Coerce a possibly-null/absent wire value to a non-negative int."""
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def extract_usage(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Read a provider `usage` block into the canonical dict.

    Tolerant of the whole block being absent/None, of missing detail objects,
    and of null leaf fields (OpenRouter returns e.g.
    `accepted_prediction_tokens: null`). `total` falls back to `prompt + eval`
    when the provider omits `total_tokens`.
    """
    raw = raw or {}
    prompt = _int(raw.get("prompt_tokens"))
    eval_ = _int(raw.get("completion_tokens"))
    total = _int(raw.get("total_tokens")) or (prompt + eval_)
    p_details = raw.get("prompt_tokens_details") or {}
    c_details = raw.get("completion_tokens_details") or {}
    return {
        "prompt": prompt,
        "eval": eval_,
        "total": total,
        "cached": _int(p_details.get("cached_tokens")),
        "reasoning": _int(c_details.get("reasoning_tokens")),
        "cost": _float(raw.get("cost")),
    }


def from_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Read the flat `model_call` event keys back into the canonical dict.

    The mirror of `extract_usage` for the post-run path: events store the
    detail flat (`prompt_tokens`, `eval_tokens`, `cached_tokens`,
    `reasoning_tokens`, `cost`), so summary re-aggregation reads them here.
    `total` is derived as `prompt + eval` (the per-event total; the session's
    authoritative running total lives in `tokens_used_total`).
    """
    prompt = _int(payload.get("prompt_tokens"))
    eval_ = _int(payload.get("eval_tokens"))
    return {
        "prompt": prompt,
        "eval": eval_,
        "total": prompt + eval_,
        "cached": _int(payload.get("cached_tokens")),
        "reasoning": _int(payload.get("reasoning_tokens")),
        "cost": _float(payload.get("cost")),
    }


def add_usage(acc: dict[str, Any], u: dict[str, Any]) -> None:
    """Field-wise in-place sum of `u` into accumulator `acc`.

    The one combine primitive, shared by the live session and every summary
    aggregation so the breakdown can never drift between them.
    """
    for f in USAGE_INT_FIELDS:
        acc[f] = _int(acc.get(f)) + _int(u.get(f))
    acc["cost"] = _float(acc.get("cost")) + _float(u.get("cost"))


def format_cost(cost: float) -> str:
    """Format a USD cost so a cheap run doesn't round away to ``$0.00``.

    Shared by every Python-side display (CLI summary, event renderer) so the
    cost reads the same everywhere. The visualizer's JS mirrors this.
    """
    cost = _float(cost)
    if cost >= 0.005:
        return f"${cost:,.2f}"
    if cost >= 0.00005:
        return f"${cost:.4f}"
    return f"${cost:.6f}"


def phase_bucket(phase: str | None) -> str:
    """Map a model_call `phase` to its actor bucket.

    The worker omits `phase` by convention; only the evaluator tags its calls.
    Single home for the `"evaluator" if phase == "evaluator" else "worker"`
    rule that the renderer and fact extractor would otherwise each repeat.
    """
    return "evaluator" if phase == "evaluator" else "worker"
