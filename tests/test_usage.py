"""tilth.usage is the canonical token/cost record: how a provider `usage` block
is read into one dict, how the flat event keys are read back, and how records
combine. The wire fixtures below are the *real* OpenRouter shape (probed live,
deepseek-v4-flash) — not a guessed contract.
"""

from __future__ import annotations

import pytest

from tilth import usage

# Verbatim from a live OpenRouter response (usage accounting on). The leaf nulls
# and the cache_write_tokens/image_tokens extras are part of the real shape.
REAL_OPENROUTER_USAGE = {
    "completion_tokens": 24,
    "prompt_tokens": 11,
    "total_tokens": 35,
    "completion_tokens_details": {
        "accepted_prediction_tokens": None,
        "audio_tokens": 0,
        "reasoning_tokens": 21,
        "rejected_prediction_tokens": None,
        "image_tokens": 0,
    },
    "prompt_tokens_details": {
        "audio_tokens": 0,
        "cached_tokens": 8,
        "cache_write_tokens": 0,
        "video_tokens": 0,
    },
    "cost": 5.9e-06,
    "is_byok": False,
    "cost_details": {"upstream_inference_cost": 5.9e-06},
}


def test_extract_real_openrouter_block():
    u = usage.extract_usage(REAL_OPENROUTER_USAGE)
    assert u == {
        "prompt": 11,
        "eval": 24,
        "total": 35,
        "cached": 8,
        "reasoning": 21,
        "cost": 5.9e-06,
    }


def test_subset_invariant_holds_on_real_shape():
    """cached ⊆ prompt and reasoning ⊆ eval — the records the harness emits must
    never violate this, or the cap/total would be double-counted."""
    u = usage.extract_usage(REAL_OPENROUTER_USAGE)
    assert u["cached"] <= u["prompt"]
    assert u["reasoning"] <= u["eval"]


def test_extract_none_yields_zeros():
    assert usage.extract_usage(None) == usage.zero_usage()


def test_extract_plain_provider_without_details():
    """A non-OpenRouter OpenAI-compatible block: standard fields only, no detail
    objects, no cost. Degrades to prompt/eval/total with the rest zeroed."""
    u = usage.extract_usage(
        {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}
    )
    assert u == {
        "prompt": 100, "eval": 40, "total": 140,
        "cached": 0, "reasoning": 0, "cost": 0.0,
    }


def test_total_falls_back_to_prompt_plus_eval():
    u = usage.extract_usage({"prompt_tokens": 10, "completion_tokens": 5})
    assert u["total"] == 15


def test_extract_tolerates_null_leaves():
    u = usage.extract_usage(
        {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "completion_tokens_details": {"reasoning_tokens": None},
            "prompt_tokens_details": {"cached_tokens": None},
            "cost": None,
        }
    )
    assert u["reasoning"] == 0
    assert u["cached"] == 0
    assert u["cost"] == 0.0


def test_from_event_reads_flat_keys():
    u = usage.from_event(
        {
            "prompt_tokens": 100, "eval_tokens": 40,
            "cached_tokens": 30, "reasoning_tokens": 12, "cost": 0.0007,
        }
    )
    assert u == {
        "prompt": 100, "eval": 40, "total": 140,
        "cached": 30, "reasoning": 12, "cost": 0.0007,
    }


def test_from_event_missing_keys_default_zero():
    u = usage.from_event({"prompt_tokens": 10, "eval_tokens": 5})
    assert u == {
        "prompt": 10, "eval": 5, "total": 15,
        "cached": 0, "reasoning": 0, "cost": 0.0,
    }


def test_add_usage_sums_fieldwise():
    acc = usage.zero_usage()
    usage.add_usage(acc, {"prompt": 10, "eval": 5, "total": 15,
                          "cached": 2, "reasoning": 3, "cost": 0.001})
    usage.add_usage(acc, {"prompt": 20, "eval": 7, "total": 27,
                          "cached": 1, "reasoning": 4, "cost": 0.002})
    assert acc == {
        "prompt": 30, "eval": 12, "total": 42,
        "cached": 3, "reasoning": 7, "cost": pytest.approx(0.003),
    }


def test_add_usage_tolerates_partial_records():
    acc = usage.zero_usage()
    usage.add_usage(acc, {"prompt": 10, "eval": 5})  # no detail/cost keys
    assert acc["prompt"] == 10
    assert acc["cached"] == 0
    assert acc["cost"] == 0.0


@pytest.mark.parametrize(
    "phase,expected",
    [("evaluator", "evaluator"), (None, "worker"), ("worker", "worker"), ("", "worker")],
)
def test_phase_bucket(phase, expected):
    assert usage.phase_bucket(phase) == expected
