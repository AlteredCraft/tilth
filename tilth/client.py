"""LLM client wrapper.

Talks to any OpenAI-compatible endpoint via the `openai` SDK. `TILTH_BASE_URL`,
`TILTH_API_KEY`, and `TILTH_WORKER_MODEL` are required — the harness fails fast
if they aren't set rather than silently picking a provider/model that may not
match your account.

Optional cross-purpose routing: the evaluator can be pinned to a different
provider than the worker via `TILTH_EVALUATOR_BASE_URL` /
`TILTH_EVALUATOR_API_KEY` (each defaults to the worker's). Routing is by model
name in `chat()`; the matching base URL is then used to decide whether to send
the OpenRouter `reasoning.enabled` opt-in (so a worker on OpenRouter routing
through a non-OpenRouter evaluator doesn't send OpenRouter-specific syntax there).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from .memory import DEFAULT_CONTEXT_FILES

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
    evaluator_base_url: str
    evaluator_api_key: str
    evaluator_model: str
    max_iterations_per_task: int
    max_evaluator_calls_per_task: int
    max_wall_clock_minutes: int
    max_tokens: int
    context_files: list[str] = field(default_factory=lambda: list(DEFAULT_CONTEXT_FILES))

    def limits(self) -> dict[str, int]:
        """The configured caps as a flat dict, recorded in `session_start` so
        the read-only viewer can show utilization against them. Single source
        for the recorded shape — keep it in step with `_stop_reason` and the
        per-task caps in `loop._run_task`."""
        return {
            "max_tokens": self.max_tokens,
            "max_wall_clock_minutes": self.max_wall_clock_minutes,
            "max_iterations_per_task": self.max_iterations_per_task,
            "max_evaluator_calls_per_task": self.max_evaluator_calls_per_task,
        }

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
                f"Missing required configuration: {', '.join(missing)}."
            )
        evaluator_model = os.environ.get("TILTH_EVALUATOR_MODEL", "").strip() or worker_model
        evaluator_base_url = os.environ.get("TILTH_EVALUATOR_BASE_URL", "").strip() or base_url
        evaluator_api_key = os.environ.get("TILTH_EVALUATOR_API_KEY", "").strip() or api_key
        context_files = [
            f.strip() for f in os.environ.get("TILTH_CONTEXT_FILES", "").split(",") if f.strip()
        ] or list(DEFAULT_CONTEXT_FILES)
        return cls(
            base_url=base_url,
            api_key=api_key,
            worker_model=worker_model,
            evaluator_base_url=evaluator_base_url,
            evaluator_api_key=evaluator_api_key,
            evaluator_model=evaluator_model,
            max_iterations_per_task=int(os.environ.get("TILTH_MAX_ITERATIONS_PER_TASK", "32")),
            max_evaluator_calls_per_task=int(
                os.environ.get("MAX_EVALUATOR_CALLS_PER_TASK", "0") or "0"
            ),
            max_wall_clock_minutes=int(os.environ.get("TILTH_MAX_WALL_CLOCK_MINUTES", "120")),
            max_tokens=int(os.environ.get("TILTH_MAX_TOKENS", "2000000")),
            context_files=context_files,
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
            config.evaluator_base_url == config.base_url
            and config.evaluator_api_key == config.api_key
        ):
            self._evaluator = self._worker
        else:
            self._evaluator = OpenAI(
                base_url=config.evaluator_base_url, api_key=config.evaluator_api_key
            )

    def _client_and_url_for(self, model: str) -> tuple[OpenAI, str]:
        """Route by model name to (client, base_url).

        The base_url is returned so OpenRouter-specific request shaping in
        `chat()` is keyed on the actually-routed provider, not on the worker
        config — important when the evaluator lives on a different gateway.
        """
        if model == self.config.evaluator_model and self._evaluator is not self._worker:
            return self._evaluator, self.config.evaluator_base_url
        return self._worker, self.config.base_url

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = model or self.config.worker_model
        client, base_url = self._client_and_url_for(target)

        kwargs: dict[str, Any] = {"model": target, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
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
    """Flatten an OpenAI ChatCompletion to the dict shape the loop expects.

    Provider-health evidence is retained, not flattened away: `error` (OpenRouter
    surfaces upstream mid-generation failures as an error object on the choice
    or at the top level, with HTTP 200), plus `provider` / `response_id` /
    `model` for post-run forensics and support tickets. Omitted when absent so
    events stay slim. The SDK's pydantic models carry these through as extras.
    """
    d = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    choices = d.get("choices") or []
    choice = choices[0] if choices else {}
    out: dict[str, Any] = {
        "message": choice.get("message") or {},
        "usage": d.get("usage") or {},
        "finish_reason": choice.get("finish_reason"),
    }
    error = choice.get("error") or d.get("error")
    if error:
        out["error"] = error
    for src, dst in (("provider", "provider"), ("id", "response_id"), ("model", "model")):
        if d.get(src):
            out[dst] = d[src]
    return out


def response_health(resp: dict[str, Any]) -> tuple[str, str | None]:
    """Classify a normalised response by the provider's own signals.

    Returns (health, detail). Health:
        "ok"             — a real turn; safe to append to history.
        "provider_error" — the provider says generation failed (`error` object
                           or `finish_reason: "error"`). Any partial payload
                           (e.g. a truncated reasoning trace) is corrupt — it
                           must not become a conversation turn.
        "empty"          — nothing at all came back (no content, no tool calls,
                           no reasoning). Observed as OpenRouter 200s with zero
                           usage during warm-up/scale-up windows.

    The provider signals are checked *before* any shape heuristic. The
    2026-06-10 incident (session 20260610-100626-8c0142) is the cautionary
    tale: a `finish_reason: "error"` turn carrying partial reasoning passed a
    shape-only empty check, poisoned the history, and drew a false "you went
    quiet" nudge. Health is the provider's word, not the message's silhouette.
    """
    error = resp.get("error")
    if error:
        msg = error.get("message") if isinstance(error, dict) else None
        return "provider_error", str(msg or error)
    if resp.get("finish_reason") == "error":
        return "provider_error", "finish_reason=error (provider failed mid-generation)"
    if _is_empty_message(resp.get("message") or {}):
        return "empty", "no content, no tool calls, no reasoning"
    return "ok", None


def _is_empty_message(msg: dict[str, Any]) -> bool:
    """A message with *nothing* in it — no tool calls, no content, no reasoning.

    Distinct from a turn that goes quiet with prose (content present, no tool
    call) — that is a real turn and routes to the loop's no-case nudge.
    """
    if msg.get("tool_calls"):
        return False
    if (msg.get("content") or "").strip():
        return False
    if msg.get("reasoning_details"):
        return False
    if isinstance(msg.get("reasoning"), str) and msg["reasoning"].strip():
        return False
    return True
