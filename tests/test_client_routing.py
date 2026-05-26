"""LLMClient routes by model name and shapes per-provider request kwargs.

Two behaviours under test:
  - Each purpose (worker / judge / prep) is dispatched to its own OpenAI
    instance when its base_url+api_key differ from the worker's.
  - The OpenRouter `reasoning.enabled` opt-in is sent based on the
    *routed* purpose's base_url, not the worker's — so a worker on
    OpenRouter routing through a non-OpenRouter prep provider doesn't
    leak OpenRouter-specific syntax to the wrong endpoint.
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
    """Build a TilthConfig that mirrors `from_env`'s default-derivation: unset
    judge/prep base_url and api_key inherit from the worker's. Override only
    the fields you actually care about — the rest inherit so the test setup
    matches what a real user environment would produce."""
    base_url = over.pop("base_url", "https://worker.invalid/v1")
    api_key = over.pop("api_key", "wkey")
    worker_model = over.pop("worker_model", "worker-m")
    defaults = dict(
        base_url=base_url,
        api_key=api_key,
        worker_model=worker_model,
        judge_base_url=base_url,
        judge_api_key=api_key,
        judge_model=worker_model,
        prep_base_url=base_url,
        prep_api_key=api_key,
        prep_model=worker_model,
        max_iterations_per_task=8,
        max_judge_calls_per_task=0,
        max_wall_clock_minutes=120,
        max_tokens=2_000_000,
    )
    defaults.update(over)
    return TilthConfig(**defaults)


def test_no_overrides_shares_one_underlying_client():
    cfg = _cfg()
    client = LLMClient(cfg)
    # Same OpenAI instance reused for worker, judge, prep.
    assert client._worker is client._judge is client._prep


def test_judge_override_creates_distinct_client():
    cfg = _cfg(
        judge_base_url="https://judge.invalid/v1",
        judge_api_key="jkey",
        judge_model="judge-m",
    )
    client = LLMClient(cfg)
    assert client._judge is not client._worker
    assert client._prep is client._worker


def test_prep_override_creates_distinct_client():
    cfg = _cfg(
        prep_base_url="https://prep.invalid/v1",
        prep_api_key="pkey",
        prep_model="prep-m",
    )
    client = LLMClient(cfg)
    assert client._prep is not client._worker
    assert client._judge is client._worker


def test_chat_routes_judge_calls_to_judge_client():
    cfg = _cfg(
        judge_base_url="https://judge.invalid/v1",
        judge_api_key="jkey",
        judge_model="judge-m",
    )
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}], model="judge-m")
    assert client._judge.calls and not client._worker.calls


def test_chat_routes_prep_calls_to_prep_client():
    cfg = _cfg(
        prep_base_url="https://prep.invalid/v1",
        prep_api_key="pkey",
        prep_model="prep-m",
    )
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}], model="prep-m")
    assert client._prep.calls and not client._worker.calls


def test_openrouter_optin_sent_when_worker_is_openrouter():
    cfg = _cfg(base_url="https://openrouter.ai/api/v1")
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}])
    sent = client._worker.calls[0]
    assert sent.get("extra_body") == {"reasoning": {"enabled": True}}


def test_openrouter_optin_not_sent_to_non_openrouter_prep_provider():
    """Worker on OpenRouter, prep on a different provider: prep must NOT receive
    the OpenRouter-specific `reasoning.enabled` opt-in. Regression guard for the
    routing bug where the kwarg was keyed on worker base_url."""
    cfg = _cfg(
        base_url="https://openrouter.ai/api/v1",
        prep_base_url="https://api.anthropic-direct.invalid/v1",
        prep_api_key="pkey",
        prep_model="prep-m",
    )
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}], model="prep-m")
    sent = client._prep.calls[0]
    assert "extra_body" not in sent


def test_openrouter_optin_sent_to_openrouter_prep_when_worker_isnt():
    """Inverse case: worker on a plain provider, prep on OpenRouter — opt-in
    should follow the routed prep call, not be suppressed because the worker
    isn't OpenRouter."""
    cfg = _cfg(
        base_url="https://api.openai.invalid/v1",
        prep_base_url="https://openrouter.ai/api/v1",
        prep_api_key="pkey",
        prep_model="prep-m",
    )
    client = LLMClient(cfg)
    client.chat([{"role": "user", "content": "x"}], model="prep-m")
    sent = client._prep.calls[0]
    assert sent.get("extra_body") == {"reasoning": {"enabled": True}}
    # Worker (the default) shouldn't have been called at all.
    assert not client._worker.calls
