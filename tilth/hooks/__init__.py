"""Lifecycle hooks: pre_tool gating.

Two principles (per Osmani's harness post):
- Constraints are *earned* through real failures. Start small; add only when needed.
- "Success is silent, failures are verbose." Pass states inject nothing; failures
  inject error text into the tool result so the model self-corrects.

The post-edit ruff hook was removed in the prompt-driven refactor — the harness
no longer codifies lint/test checks. The worker may run tools via `bash`; nothing
is enforced between iterations.
"""

from __future__ import annotations

from tilth.hooks.pre_tool import pre_tool

__all__ = ["pre_tool"]
