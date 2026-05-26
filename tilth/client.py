"""LLM client wrapper.

Talks to any OpenAI-compatible endpoint via the `openai` SDK. `TILTH_BASE_URL`,
`TILTH_API_KEY`, and `TILTH_WORKER_MODEL` are required — the harness fails fast
if they aren't set rather than silently picking a provider/model that may not
match your account.

Optional cross-purpose routing: each purpose (worker, judge, prep-feature
interview) can be pinned to a different provider via per-purpose env vars —
`TILTH_JUDGE_BASE_URL` / `TILTH_JUDGE_API_KEY` for the judge, and
`TILTH_PREP_BASE_URL` / `TILTH_PREP_API_KEY` for the prep-feature interview.
Each defaults to the worker's. Routing is by model name in `chat()`; the
matching base URL is then used to decide whether to send the OpenRouter
`reasoning.enabled` opt-in (so a worker on OpenRouter routing through a
non-OpenRouter judge doesn't send OpenRouter-specific syntax there).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _is_openrouter(base_url: str) -> bool:
    """OpenRouter's normalised `reasoning.enabled` request param is OpenRouter-
    specific syntax. Other OpenAI-compatible providers use different shapes
    (OpenAI's top-level `reasoning_effort`, Anthropic's `thinking`, etc.), so
    we only send the opt-in when the base URL is OpenRouter.
    """
    return "openrouter.ai" in base_url

_HISTORY_KEEP = frozenset({
    "role",
    "content",
    "tool_calls",
    "reasoning",
    "reasoning_details",
})


def parse_json_lenient(text: str) -> dict[str, Any] | None:
    """Try to parse a JSON object from a model response. Strips code fences."""
    candidates: list[str] = []
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text)
    for s in candidates:
        s = s.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def assistant_history_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Shape an assistant response for re-injection into the message history.

    Why: thinking-mode models reject the next request with HTTP 400 if the
    reasoning content from the prior assistant turn isn't echoed back.
    OpenRouter's normalised response carries it in `reasoning_details`
    (structured blocks, the documented form) and a flat `reasoning` string.
    We keep both — observed on the wire against deepseek/deepseek-v4-flash.
    Output-only metadata (refusal, annotations, audio, function_call) is
    dropped.

    Pair this with `extra_body={"reasoning": {"enabled": True}}` on the
    request side (see `LLMClient.chat`) — without that opt-in, OpenRouter
    sometimes omits reasoning on parallel-tool-call turns and there's
    nothing to echo.
    """
    return {k: v for k, v in msg.items() if k in _HISTORY_KEEP}


@dataclass
class TilthConfig:
    base_url: str
    api_key: str
    worker_model: str
    judge_base_url: str
    judge_api_key: str
    judge_model: str
    prep_base_url: str
    prep_api_key: str
    prep_model: str
    max_iterations_per_task: int
    max_judge_calls_per_task: int
    max_wall_clock_minutes: int
    max_tokens: int

    @classmethod
    def from_env(cls) -> TilthConfig:
        base_url = os.environ.get("TILTH_BASE_URL", "").strip()
        api_key = os.environ.get("TILTH_API_KEY", "").strip()
        worker_model = os.environ.get("TILTH_WORKER_MODEL", "").strip()
        missing = [
            name
            for name, value in (
                ("TILTH_BASE_URL", base_url),
                ("TILTH_API_KEY", api_key),
                ("TILTH_WORKER_MODEL", worker_model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in (see "
                "docs/getting-started/installation.md for known-good "
                "provider/model combinations)."
            )
        judge_model = os.environ.get("TILTH_JUDGE_MODEL", "").strip() or worker_model
        judge_base_url = os.environ.get("TILTH_JUDGE_BASE_URL", "").strip() or base_url
        judge_api_key = os.environ.get("TILTH_JUDGE_API_KEY", "").strip() or api_key
        prep_model = os.environ.get("TILTH_PREP_MODEL", "").strip() or worker_model
        prep_base_url = os.environ.get("TILTH_PREP_BASE_URL", "").strip() or base_url
        prep_api_key = os.environ.get("TILTH_PREP_API_KEY", "").strip() or api_key
        return cls(
            base_url=base_url,
            api_key=api_key,
            worker_model=worker_model,
            judge_base_url=judge_base_url,
            judge_api_key=judge_api_key,
            judge_model=judge_model,
            prep_base_url=prep_base_url,
            prep_api_key=prep_api_key,
            prep_model=prep_model,
            max_iterations_per_task=int(os.environ.get("TILTH_MAX_ITERATIONS_PER_TASK", "8")),
            max_judge_calls_per_task=int(
                os.environ.get("TILTH_MAX_JUDGE_CALLS_PER_TASK", "0") or "0"
            ),
            max_wall_clock_minutes=int(os.environ.get("TILTH_MAX_WALL_CLOCK_MINUTES", "120")),
            max_tokens=int(os.environ.get("TILTH_MAX_TOKENS", "2000000")),
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
        if (
            config.prep_base_url == config.base_url
            and config.prep_api_key == config.api_key
        ):
            self._prep = self._worker
        else:
            self._prep = OpenAI(base_url=config.prep_base_url, api_key=config.prep_api_key)

    def _client_and_url_for(self, model: str) -> tuple[OpenAI, str]:
        """Route by model name to (client, base_url).

        The base_url is returned so OpenRouter-specific request shaping in
        `chat()` is keyed on the actually-routed provider, not on the worker
        config — important when a non-worker purpose lives on a different
        gateway.
        """
        if model == self.config.judge_model and self._judge is not self._worker:
            return self._judge, self.config.judge_base_url
        if model == self.config.prep_model and self._prep is not self._worker:
            return self._prep, self.config.prep_base_url
        return self._worker, self.config.base_url

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        target = model or self.config.worker_model
        client, base_url = self._client_and_url_for(target)

        kwargs: dict[str, Any] = {"model": target, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if _is_openrouter(base_url):
            # OpenRouter-normalised opt-in for thinking-mode models. Without it,
            # parallel-tool-call turns sometimes return reasoning_details: null
            # — and the next request then 400s because the upstream protocol
            # expects reasoning to be echoed. With it, reasoning_details is
            # always populated and `assistant_history_message` echoes it back.
            # Only sent for OpenRouter base URLs since this is OpenRouter-
            # specific syntax (other gateways use different shapes); routing
            # uses the per-purpose base_url, not the worker's.
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
