"""LLM client wrapper.

Talks to any OpenAI-compatible endpoint via the `openai` SDK. Defaults point at
Ollama Cloud (`https://ollama.com/v1`); override `TILTH_BASE_URL` to use
OpenRouter, Together, Groq, vLLM, LM Studio, or any other compatible provider.

Optional dual-client routing: set `TILTH_JUDGE_BASE_URL` and
`TILTH_JUDGE_API_KEY` to send judge calls to a different provider (e.g. a
cheaper / faster model) while the worker stays on the main provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

_REASONING_FALSY = {"false", "0", "no", "off"}


@dataclass
class TilthConfig:
    base_url: str
    api_key: str
    worker_model: str
    judge_base_url: str
    judge_api_key: str
    judge_model: str
    max_iterations_per_task: int
    max_judge_calls_per_task: int
    max_wall_clock_minutes: int
    max_tokens: int
    reasoning_enabled: bool

    @classmethod
    def from_env(cls) -> TilthConfig:
        base_url = os.environ.get("TILTH_BASE_URL", "https://ollama.com/v1").strip()
        api_key = os.environ.get("TILTH_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "TILTH_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        worker_model = os.environ.get("TILTH_WORKER_MODEL", "deepseek/deepseek-v4-pro").strip()
        judge_model = os.environ.get("TILTH_JUDGE_MODEL", "").strip() or worker_model
        judge_base_url = os.environ.get("TILTH_JUDGE_BASE_URL", "").strip() or base_url
        judge_api_key = os.environ.get("TILTH_JUDGE_API_KEY", "").strip() or api_key
        reasoning_raw = os.environ.get("TILTH_REASONING_ENABLED", "").strip().lower()
        reasoning_enabled = reasoning_raw not in _REASONING_FALSY
        return cls(
            base_url=base_url,
            api_key=api_key,
            worker_model=worker_model,
            judge_base_url=judge_base_url,
            judge_api_key=judge_api_key,
            judge_model=judge_model,
            max_iterations_per_task=int(os.environ.get("TILTH_MAX_ITERATIONS_PER_TASK", "8")),
            max_judge_calls_per_task=int(
                os.environ.get("TILTH_MAX_JUDGE_CALLS_PER_TASK", "0") or "0"
            ),
            max_wall_clock_minutes=int(os.environ.get("TILTH_MAX_WALL_CLOCK_MINUTES", "120")),
            max_tokens=int(os.environ.get("TILTH_MAX_TOKENS", "2000000")),
            reasoning_enabled=reasoning_enabled,
        )


class LLMClient:
    """Thin wrapper over the OpenAI SDK with optional dual-client routing.

    `chat()` returns a normalised dict shape:
        {
            "message": {"role": ..., "content": ..., "tool_calls": [...]?},
            "usage":   {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int},
        }
    """

    def __init__(self, config: TilthConfig):
        self.config = config
        self._worker = OpenAI(base_url=config.base_url, api_key=config.api_key)
        if (
            config.judge_base_url == config.base_url
            and config.judge_api_key == config.api_key
        ):
            self._judge = self._worker
        else:
            self._judge = OpenAI(base_url=config.judge_base_url, api_key=config.judge_api_key)

    def _client_for(self, model: str) -> OpenAI:
        # Route by model name: judge model -> judge client, anything else -> worker.
        if model == self.config.judge_model and self._judge is not self._worker:
            return self._judge
        return self._worker

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        target = model or self.config.worker_model
        client = self._client_for(target)

        kwargs: dict[str, Any] = {"model": target, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if self.config.reasoning_enabled:
            # OpenRouter-normalised opt-in for thinking-mode models. Without it,
            # parallel-tool-call turns sometimes return reasoning_details: null
            # — and the next request then 400s because the upstream protocol
            # expects reasoning to be echoed. With it, reasoning_details is
            # always populated and `_assistant_history_message` echoes it back.
            # Non-OpenRouter providers generally ignore this body field; set
            # TILTH_REASONING_ENABLED=false if yours rejects it.
            kwargs["extra_body"] = {"reasoning": {"enabled": True}}

        resp = client.chat.completions.create(**kwargs)
        return _normalise(resp)


def _normalise(resp: Any) -> dict[str, Any]:
    """Flatten an OpenAI ChatCompletion to the dict shape the loop expects."""
    d = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    choices = d.get("choices") or []
    choice = choices[0] if choices else {}
    return {
        "message": choice.get("message") or {},
        "usage": d.get("usage") or {},
        "finish_reason": choice.get("finish_reason"),
    }
