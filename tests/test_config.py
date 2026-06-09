"""TilthConfig.from_env() must fail fast when required vars are missing.

The required trio is TILTH_BASE_URL, TILTH_API_KEY, TILTH_WORKER_MODEL. We don't
want defaults silently picking a provider/model that may not match the user's
account — that produces confusing 401/404s well after `tilth` starts running.
"""

from __future__ import annotations

import pytest

from tilth.client import TilthConfig

REQUIRED = ("TILTH_BASE_URL", "TILTH_API_KEY", "TILTH_WORKER_MODEL")
OPTIONAL = (
    "TILTH_EVALUATOR_MODEL",
    "TILTH_EVALUATOR_BASE_URL",
    "TILTH_EVALUATOR_API_KEY",
    "TILTH_CONTEXT_FILES",
)


def _clear(monkeypatch) -> None:
    for name in (*REQUIRED, *OPTIONAL):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("missing", REQUIRED)
def test_from_env_raises_when_required_missing(monkeypatch, missing):
    _clear(monkeypatch)
    for name in REQUIRED:
        if name != missing:
            monkeypatch.setenv(name, "x")
    with pytest.raises(RuntimeError) as excinfo:
        TilthConfig.from_env()
    assert missing in str(excinfo.value)


def test_from_env_lists_all_missing_vars(monkeypatch):
    _clear(monkeypatch)
    with pytest.raises(RuntimeError) as excinfo:
        TilthConfig.from_env()
    msg = str(excinfo.value)
    for name in REQUIRED:
        assert name in msg


def test_from_env_succeeds_with_required_set(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("TILTH_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setenv("TILTH_API_KEY", "k")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "test-model")
    cfg = TilthConfig.from_env()
    assert cfg.base_url == "https://test.invalid/v1"
    assert cfg.api_key == "k"
    assert cfg.worker_model == "test-model"
    assert cfg.evaluator_model == "test-model"
    assert cfg.evaluator_base_url == "https://test.invalid/v1"
    assert cfg.evaluator_api_key == "k"


def test_evaluator_overrides_independently_of_worker(monkeypatch):
    """TILTH_EVALUATOR_* overrides apply only to the evaluator; the worker is
    untouched and unset evaluator fields fall back to the worker's."""
    _clear(monkeypatch)
    monkeypatch.setenv("TILTH_BASE_URL", "https://worker.invalid/v1")
    monkeypatch.setenv("TILTH_API_KEY", "wkey")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "worker-m")
    monkeypatch.setenv("TILTH_EVALUATOR_BASE_URL", "https://evaluator.invalid/v1")
    monkeypatch.setenv("TILTH_EVALUATOR_API_KEY", "jkey")
    monkeypatch.setenv("TILTH_EVALUATOR_MODEL", "evaluator-m")
    cfg = TilthConfig.from_env()
    # Worker untouched.
    assert cfg.base_url == "https://worker.invalid/v1"
    assert cfg.worker_model == "worker-m"
    # Evaluator overrides applied.
    assert cfg.evaluator_base_url == "https://evaluator.invalid/v1"
    assert cfg.evaluator_api_key == "jkey"
    assert cfg.evaluator_model == "evaluator-m"


def _set_required(monkeypatch) -> None:
    monkeypatch.setenv("TILTH_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setenv("TILTH_API_KEY", "k")
    monkeypatch.setenv("TILTH_WORKER_MODEL", "test-model")


def test_context_files_defaults_to_agents_and_claude(monkeypatch):
    _clear(monkeypatch)
    _set_required(monkeypatch)
    cfg = TilthConfig.from_env()
    assert cfg.context_files == ["AGENTS.md", "CLAUDE.md"]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("AGENTS.md", ["AGENTS.md"]),
        ("CLAUDE.md,AGENTS.md", ["CLAUDE.md", "AGENTS.md"]),
        ("  FOO.md ,  BAR.md  ", ["FOO.md", "BAR.md"]),  # whitespace stripped
        ("", ["AGENTS.md", "CLAUDE.md"]),  # empty → default
        ("  ,  , ", ["AGENTS.md", "CLAUDE.md"]),  # only separators → default
    ],
)
def test_context_files_parsed_from_env(monkeypatch, raw, expected):
    _clear(monkeypatch)
    _set_required(monkeypatch)
    monkeypatch.setenv("TILTH_CONTEXT_FILES", raw)
    cfg = TilthConfig.from_env()
    assert cfg.context_files == expected
