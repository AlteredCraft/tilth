"""LLM client wrapper.

Talks to any OpenAI-compatible endpoint via the `openai` SDK. Defaults point at
Ollama Cloud (`https://ollama.com/v1`); override `HARNESS_BASE_URL` to use
OpenRouter, Together, Groq, vLLM, LM Studio, or any other compatible provider.

Optional dual-client routing: set `HARNESS_JUDGE_BASE_URL` and
`HARNESS_JUDGE_API_KEY` to send judge calls to a different provider (e.g. a
cheaper / faster model) while the worker stays on the main provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI


@dataclass
class HarnessConfig:
    base_url: str
    api_key: str
    worker_model: str
    judge_base_url: str
    judge_api_key: str
    judge_model: str
    max_iterations_per_task: int
    max_wall_clock_minutes: int
    max_tokens: int

    @classmethod
    def from_env(cls) -> HarnessConfig:
        base_url = os.environ.get("HARNESS_BASE_URL", "https://ollama.com/v1").strip()
        api_key = os.environ.get("HARNESS_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "HARNESS_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        worker_model = os.environ.get("HARNESS_WORKER_MODEL", "gpt-oss:120b-cloud").strip()
        judge_model = os.environ.get("HARNESS_JUDGE_MODEL", "").strip() or worker_model
        judge_base_url = os.environ.get("HARNESS_JUDGE_BASE_URL", "").strip() or base_url
        judge_api_key = os.environ.get("HARNESS_JUDGE_API_KEY", "").strip() or api_key
        return cls(
            base_url=base_url,
            api_key=api_key,
            worker_model=worker_model,
            judge_base_url=judge_base_url,
            judge_api_key=judge_api_key,
            judge_model=judge_model,
            max_iterations_per_task=int(os.environ.get("HARNESS_MAX_ITERATIONS_PER_TASK", "8")),
            max_wall_clock_minutes=int(os.environ.get("HARNESS_MAX_WALL_CLOCK_MINUTES", "120")),
            max_tokens=int(os.environ.get("HARNESS_MAX_TOKENS", "2000000")),
        )


class LLMClient:
    """Thin wrapper over the OpenAI SDK with optional dual-client routing.

    `chat()` returns a normalised dict shape:
        {
            "message": {"role": ..., "content": ..., "tool_calls": [...]?},
            "usage":   {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int},
        }
    """

    def __init__(self, config: HarnessConfig):
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
