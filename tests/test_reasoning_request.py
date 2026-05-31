"""Regression test for the OpenRouter-only reasoning opt-in.

Multi-tool-call turns from reasoning-mode models on OpenRouter sometimes return
`reasoning_details: null` even though the upstream model is in thinking mode.
The next request then fails with HTTP 400 because there's nothing to echo back.
The fix is to opt into reasoning explicitly via OpenRouter's normalised
`reasoning: { enabled: true }` request parameter — see
https://openrouter.ai/docs/guides/best-practices/reasoning-tokens.

That parameter is OpenRouter-specific syntax (OpenAI uses a top-level
`reasoning_effort`; Anthropic uses `thinking`), so we only put it on the wire
when `TILTH_BASE_URL` points at OpenRouter. These tests pin both branches.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from tilth.client import LLMClient, TilthConfig, _is_openrouter


def _make_client(monkeypatch, base_url: str) -> tuple[LLMClient, MagicMock]:
    monkeypatch.setenv("TILTH_API_KEY", "test-key")
    monkeypatch.setenv("TILTH_BASE_URL", base_url)
    monkeypatch.setenv("TILTH_WORKER_MODEL", "test-model")
    monkeypatch.delenv("TILTH_EVALUATOR_MODEL", raising=False)
    monkeypatch.delenv("TILTH_EVALUATOR_BASE_URL", raising=False)
    monkeypatch.delenv("TILTH_EVALUATOR_API_KEY", raising=False)

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


def test_reasoning_param_sent_for_openrouter(monkeypatch):
    client, create = _make_client(monkeypatch, base_url="https://openrouter.ai/api/v1")
    client.chat([{"role": "user", "content": "hello"}])
    assert _kwargs_for(create)["extra_body"] == {"reasoning": {"enabled": True}}


def test_reasoning_param_omitted_for_non_openrouter(monkeypatch):
    client, create = _make_client(monkeypatch, base_url="https://api.openai.com/v1")
    client.chat([{"role": "user", "content": "hello"}])
    kwargs = _kwargs_for(create)
    assert "extra_body" not in kwargs or not kwargs.get("extra_body")


def test_is_openrouter_recognises_canonical_host():
    assert _is_openrouter("https://openrouter.ai/api/v1") is True


def test_is_openrouter_rejects_other_hosts():
    assert _is_openrouter("https://api.openai.com/v1") is False
    assert _is_openrouter("https://ollama.com/v1") is False
    assert _is_openrouter("http://localhost:8000/v1") is False
