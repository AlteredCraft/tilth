"""LLMClient routes by model name and shapes per-provider request kwargs.

Two behaviours under test:
  - The evaluator is dispatched to its own OpenAI instance when its
    base_url+api_key differ from the worker's (otherwise it shares the worker's).
  - The OpenRouter `reasoning.enabled` opt-in is sent based on the *routed*
    purpose's base_url, not the worker's — so a worker on OpenRouter routing
    through a non-OpenRouter evaluator doesn't leak OpenRouter-specific syntax
    to the wrong endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from tilth.client import LLMClient, TilthConfig


@dataclass
class _Choice:
    message: Any
    finish_reason: str = "stop"


@dataclass
class _Resp:
    choices: list
    usage: dict

    def model_dump(self) -> dict:
        return {
            "choices": [
                {"message": c.message, "finish_reason": c.finish_reason}
                for c in self.choices
            ],
            "usage": self.usage,
        }


class _FakeChatCompletions:
    def __init__(self, parent):
        self.parent = parent

    def create(self, **kwargs):
        self.parent.calls.append(kwargs)
        return _Resp(
            choices=[_Choice(message={"role": "assistant", "content": "ok"})],
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )


class _FakeChatNamespace:
    def __init__(self, parent):
        self.completions = _FakeChatCompletions(parent)


class _FakeOpenAI:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.calls: list[dict[str, Any]] = []
        self.chat = _FakeChatNamespace(self)


@pytest.fixture(autouse=True)
def patch_openai(monkeypatch):
    """Replace the real OpenAI constructor so no network calls happen."""
    import tilth.client as client_mod

    monkeypatch.setattr(client_mod, "OpenAI", _FakeOpenAI)


def _cfg(**over) -> TilthConfig:
    """Build a TilthConfig that mirrors `from_env`'s default-derivation: an unset
    evaluator base_url/api_key inherits from the worker's. Override only the
    fields you actually care about."""
    base_url = over.pop("base_url", "https://worker.invalid/v1")
    api_key = over.pop("api_key", "wkey")
    worker_model = over.pop("worker_model", "worker-m")
    defaults = dict(
        base_url=base_url,
        api_key=api_key,
        worker_model=worker_model,
        evaluator_base_url=base_url,
        evaluator_api_key=api_key,
        evaluator_model=worker_model,
        max_iterations_per_task=8,
        max_evaluator_calls_per_task=0,
        max_wall_clock_minutes=120,
        max_token_dollar_spend=10.0,
    )
    defaults.update(over)
    return TilthConfig(**defaults)


def test_no_overrides_shares_one_underlying_client():
    cfg = _cfg()
    client = LLMClient(cfg)
    # Same OpenAI instance reused for worker and evaluator.
    assert client._worker is client._evaluator


def test_evaluator_override_creates_distinct_client():
    cfg = _cfg(
        evaluator_base_url="https://evaluator.invalid/v1",
        evaluator_api_key="jkey",
        evaluator_model="evaluator-m",
    )
    client = LLMClient(cfg)
    assert client._evaluator is not client._worker


def test_chat_routes_evaluator_calls_to_evaluator_client():
    cfg = _cfg(
        evaluator_base_url="https://evaluator.invalid/v1",
        evaluator_api_key="jkey",
        evaluator_model="evaluator-m",
    )
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}], model="evaluator-m")
    assert client._evaluator.calls and not client._worker.calls


def test_openrouter_optin_sent_when_worker_is_openrouter():
    cfg = _cfg(base_url="https://openrouter.ai/api/v1")
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}])
    sent = client._worker.calls[0]
    assert sent.get("extra_body") == {
        "reasoning": {"enabled": True},
        "usage": {"include": True},
    }


def test_openrouter_optin_not_sent_to_non_openrouter_evaluator_provider():
    """Worker on OpenRouter, evaluator on a different provider: the evaluator must
    NOT receive the OpenRouter-specific `reasoning.enabled` opt-in. Regression
    guard for the routing bug where the kwarg was keyed on worker base_url."""
    cfg = _cfg(
        base_url="https://openrouter.ai/api/v1",
        evaluator_base_url="https://api.anthropic-direct.invalid/v1",
        evaluator_api_key="jkey",
        evaluator_model="evaluator-m",
    )
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}], model="evaluator-m")
    sent = client._evaluator.calls[0]
    assert "extra_body" not in sent


def test_openrouter_optin_sent_to_openrouter_evaluator_when_worker_isnt():
    """Inverse case: worker on a plain provider, evaluator on OpenRouter — opt-in
    should follow the routed evaluator call, not be suppressed because the worker
    isn't OpenRouter."""
    cfg = _cfg(
        base_url="https://api.openai.invalid/v1",
        evaluator_base_url="https://openrouter.ai/api/v1",
        evaluator_api_key="jkey",
        evaluator_model="evaluator-m",
    )
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}], model="evaluator-m")
    sent = client._evaluator.calls[0]
    assert sent.get("extra_body") == {
        "reasoning": {"enabled": True},
        "usage": {"include": True},
    }
    # Worker (the default) shouldn't have been called at all.
    assert not client._worker.calls
