"""Regression test for forcing reasoning content on every chat completion.

Multi-tool-call turns from reasoning-mode models on OpenRouter sometimes return
`reasoning_details: null` even though the upstream model is in thinking mode.
The next request then fails with HTTP 400 because there's nothing to echo back.
The fix is to opt into reasoning explicitly via OpenRouter's normalised
`reasoning: { enabled: true }` request parameter — see
https://openrouter.ai/docs/guides/best-practices/reasoning-tokens.

This test pins that the parameter is on the wire by default and that
TILTH_REASONING_ENABLED=false turns it off for providers that reject it.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

from tilth.client import LLMClient, TilthConfig


def _make_client(monkeypatch, reasoning_env: str | None) -> tuple[LLMClient, MagicMock]:
    monkeypatch.setenv("TILTH_API_KEY", "test-key")
    monkeypatch.setenv("TILTH_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "test-model")
    monkeypatch.delenv("TILTH_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("TILTH_JUDGE_BASE_URL", raising=False)
    monkeypatch.delenv("TILTH_JUDGE_API_KEY", raising=False)
    if reasoning_env is None:
        monkeypatch.delenv("TILTH_REASONING_ENABLED", raising=False)
    else:
        monkeypatch.setenv("TILTH_REASONING_ENABLED", reasoning_env)

    config = TilthConfig.from_env()
    client = LLMClient(config)
    fake_response = {
        "choices": [
            {"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"},
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    create = MagicMock(return_value=MagicMock(model_dump=lambda: fake_response))
    client._worker.chat.completions.create = create
    return client, create


def _kwargs_for(create: MagicMock) -> dict[str, Any]:
    create.assert_called_once()
    return create.call_args.kwargs


def test_reasoning_param_default_enabled(monkeypatch):
    client, create = _make_client(monkeypatch, reasoning_env=None)
    client.chat([{"role": "user", "content": "hello"}])
    kwargs = _kwargs_for(create)
    assert kwargs["extra_body"] == {"reasoning": {"enabled": True}}


def test_reasoning_param_explicit_true(monkeypatch):
    client, create = _make_client(monkeypatch, reasoning_env="true")
    client.chat([{"role": "user", "content": "hello"}])
    assert _kwargs_for(create)["extra_body"] == {"reasoning": {"enabled": True}}


def test_reasoning_param_disabled(monkeypatch):
    client, create = _make_client(monkeypatch, reasoning_env="false")
    client.chat([{"role": "user", "content": "hello"}])
    kwargs = _kwargs_for(create)
    assert "extra_body" not in kwargs or not kwargs.get("extra_body")


def _set_required(monkeypatch) -> None:
    monkeypatch.setenv("TILTH_API_KEY", "k")
    monkeypatch.setenv("TILTH_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "test-model")


def test_tilthconfig_reasoning_field_default_true(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("TILTH_REASONING_ENABLED", raising=False)
    cfg = TilthConfig.from_env()
    assert cfg.reasoning_enabled is True


def test_tilthconfig_reasoning_field_disabled(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("TILTH_REASONING_ENABLED", "false")
    cfg = TilthConfig.from_env()
    assert cfg.reasoning_enabled is False


def test_tilthconfig_reasoning_field_accepts_truthy_strings(monkeypatch):
    _set_required(monkeypatch)
    for val, expected in [("true", True), ("1", True), ("yes", True),
                          ("false", False), ("0", False), ("no", False), ("", True)]:
        if val == "":
            os.environ.pop("TILTH_REASONING_ENABLED", None)
        else:
            os.environ["TILTH_REASONING_ENABLED"] = val
        cfg = TilthConfig.from_env()
        assert cfg.reasoning_enabled is expected, f"expected {expected} for {val!r}"
