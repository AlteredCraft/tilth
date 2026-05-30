"""Phase 4 visibility: the evaluator sees the real validator output.

Before Phase 4 the evaluator got a static "all passed" line. Now it sees the
actual ruff/pytest output so it can judge `weak_test`/`acceptance_gap` against
what actually ran. The evaluator is only called after a pass, so this is always
*passing* output — failures go to the worker, not here.
"""

from __future__ import annotations

from tilth.loop import VALIDATOR_OUTPUT_INJECT_CAP, _format_validator_section
from tilth.validators import ValidatorResult


def test_renders_each_validators_output_and_status():
    out = _format_validator_section([
        ValidatorResult("ruff", True, "All checks passed!"),
        ValidatorResult("pytest", True, "3 passed in 0.12s"),
    ])
    assert "ruff" in out and "pytest" in out
    assert "All checks passed!" in out
    assert "3 passed in 0.12s" in out
    assert "PASS" in out


def test_empty_results_render_empty_string():
    assert _format_validator_section([]) == ""


def test_blank_output_renders_placeholder():
    out = _format_validator_section([ValidatorResult("pytest", True, "")])
    assert "(no output)" in out


def test_oversized_output_is_truncated_with_total():
    big = "x" * (VALIDATOR_OUTPUT_INJECT_CAP + 500)
    out = _format_validator_section([ValidatorResult("pytest", True, big)])
    assert "truncated" in out
    assert str(len(big)) in out  # the total char count is reported
    assert len(out) < len(big) + 500
