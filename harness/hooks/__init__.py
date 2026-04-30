"""Lifecycle hooks: pre_tool gating and post_edit follow-up.

Two principles (per Osmani's harness post):
- Constraints are *earned* through real failures. Start small; add only when needed.
- "Success is silent, failures are verbose." Pass states inject nothing; failures
  inject error text into the tool result so the model self-corrects.
"""

from __future__ import annotations

from harness.hooks.post_edit import post_edit
from harness.hooks.pre_tool import pre_tool

__all__ = ["post_edit", "pre_tool"]
